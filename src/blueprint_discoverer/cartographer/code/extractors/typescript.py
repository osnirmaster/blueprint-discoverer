"""TypeScript / JavaScript extractor (tree-sitter, tsx grammar parses plain JS too).

Recognised facts:
  * NestJS @Controller + @Get/@Post/...                   -> inbound endpoints
  * Express/Fastify app.get('/x') / router.post('/x')     -> inbound endpoints
  * axios/fetch/got/ky calls with resolvable URLs          -> outbound HTTP calls
  * kafkajs producer.send({topic}) / consumer.subscribe()  -> publications / subscriptions
Module-level constants such as `const BASE = process.env.X ?? "http://svc:8080"` are propagated
into template strings so that `${BASE}/accounts` resolves deterministically.
"""

from __future__ import annotations

import re

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from ....model import CodeSymbol, Endpoint, OutboundCall, OutboundKind
from .base import HTTP_METHODS, URL_RE, FileContext, FileFacts, join_paths, line_of, text, unquote, walk

_TS = Language(tsts.language_typescript())
_TSX = Language(tsts.language_tsx())

NEST_METHODS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "All": "*"}
HTTP_CLIENTS = {"axios", "http", "got", "ky", "superagent", "request", "client", "httpClient", "api"}
ROUTERS = {"app", "router", "server", "api", "fastify", "routes"}
FRAMEWORK_IMPORTS = {
    "express": "express", "@nestjs/common": "nestjs", "fastify": "fastify", "kafkajs": "kafkajs",
    "axios": "axios", "react": "react", "@angular/core": "angular", "vue": "vue", "next": "nextjs",
    "amqplib": "rabbitmq", "pg": "postgresql", "mongoose": "mongodb", "typeorm": "typeorm", "prisma": "prisma",
}
ENV_DEFAULT_RE = re.compile(r"process\.env\.(\w+)")


def _string_value(node: Node, consts: dict[str, str]) -> str | None:
    if node.type == "string":
        return unquote(text(node))
    if node.type == "template_string":
        out = []
        for c in node.children:
            if c.type == "string_fragment":
                out.append(text(c))
            elif c.type == "template_substitution":
                expr = next((x for x in c.children if x.type not in ("${", "}")), None)
                name = text(expr)
                out.append(consts.get(name, "{" + name + "}"))
        return "".join(out)
    if node.type == "identifier":
        return consts.get(text(node))
    if node.type == "binary_expression":  # process.env.X ?? "default" | "a" + b
        parts = [_string_value(c, consts) for c in node.children if c.type not in ("??", "||", "+")]
        literal = [p for p in parts if p]
        if text(node.child(1)) in ("??", "||"):
            return literal[-1] if literal else None
        return "".join(literal) if literal else None
    if node.type == "member_expression":
        env = ENV_DEFAULT_RE.match(text(node))
        return "${" + env.group(1) + "}" if env else None
    return None


def _decorator_call(dec: Node) -> tuple[str, list[Node]]:
    call = next((c for c in dec.children if c.type in ("call_expression", "identifier")), None)
    if call is None:
        return "", []
    if call.type == "identifier":
        return text(call), []
    args = call.child_by_field_name("arguments")
    return text(call.child_by_field_name("function")), [a for a in (args.children if args else []) if a.is_named]


def _preceding_decorators(node: Node) -> list[Node]:
    decs: list[Node] = [c for c in node.children if c.type == "decorator"]
    sib = node.prev_named_sibling
    while sib is not None and sib.type == "decorator":
        decs.append(sib)
        sib = sib.prev_named_sibling
    return decs


def _object_pairs(obj: Node) -> dict[str, Node]:
    out = {}
    for p in obj.children:
        if p.type == "pair":
            out[unquote(text(p.child_by_field_name("key")))] = p.child_by_field_name("value")
    return out


class TypeScriptExtractor:
    language = "typescript"
    extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

    def __init__(self) -> None:
        self._ts = Parser(_TS)
        self._tsx = Parser(_TSX)

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        parser = self._ts if ctx.path.endswith(".ts") else self._tsx
        root = parser.parse(ctx.source).root_node
        consts: dict[str, str] = {}

        # pass 1: imports + constants (deterministic constant propagation)
        for node in walk(root):
            if node.type == "import_statement":
                src = node.child_by_field_name("source")
                mod = unquote(text(src))
                facts.imports.add(mod)
                for prefix, fw in FRAMEWORK_IMPORTS.items():
                    if mod == prefix or mod.startswith(prefix + "/"):
                        facts.frameworks.add(fw)
            elif node.type == "call_expression" and text(node.child_by_field_name("function")) == "require":
                args = node.child_by_field_name("arguments")
                mod = unquote(text(args.named_children[0])) if args and args.named_children else ""
                if mod in FRAMEWORK_IMPORTS:
                    facts.frameworks.add(FRAMEWORK_IMPORTS[mod])
            elif node.type == "variable_declarator":
                name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
                if name is not None and value is not None and name.type == "identifier":
                    v = _string_value(value, consts)
                    if v:
                        consts[text(name)] = v

        for node in walk(root):
            if node.type == "class_declaration":
                self._class(node, ctx, facts)
            elif node.type in ("function_declaration",):
                fname = text(node.child_by_field_name("name"))
                facts.symbols.append(CodeSymbol(kind="function", name=fname, evidence=ctx.evidence(line_of(node))))
            elif node.type == "call_expression":
                self._call(node, ctx, facts, consts)
        return facts

    def _class(self, node: Node, ctx: FileContext, facts: FileFacts) -> None:
        name = text(node.child_by_field_name("name"))
        decs = _preceding_decorators(node)
        dec_names = [_decorator_call(d)[0] for d in decs]
        facts.symbols.append(CodeSymbol(
            kind="class", name=name, annotations=dec_names,
            evidence=ctx.evidence(line_of(node), ctx.line_text(line_of(node))),
        ))
        prefix = ""
        for d in decs:
            fn, args = _decorator_call(d)
            if fn == "Controller":
                facts.frameworks.add("nestjs")
                prefix = unquote(text(args[0])) if args and args[0].type == "string" else ""
        if "Controller" not in dec_names:
            return
        body = node.child_by_field_name("body")
        for member in body.children if body else []:
            if member.type != "method_definition":
                continue
            for d in _preceding_decorators(member):
                fn, args = _decorator_call(d)
                if fn in NEST_METHODS:
                    sub = unquote(text(args[0])) if args and args[0].type == "string" else ""
                    facts.endpoints.append(Endpoint(
                        method=NEST_METHODS[fn], path=join_paths(prefix, sub),
                        handler=f"{name}.{text(member.child_by_field_name('name'))}",
                        evidence=ctx.evidence(line_of(d), text(d)),
                    ))

    def _call(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str]) -> None:
        fn = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        args = [a for a in (args_node.children if args_node else []) if a.is_named]
        if fn is None or not args:
            return
        ev = ctx.evidence(line_of(node), ctx.line_text(line_of(node)))
        obj, prop = "", text(fn)
        if fn.type == "member_expression":
            obj = text(fn.child_by_field_name("object")).split(".")[-1]
            prop = text(fn.child_by_field_name("property"))

        first = _string_value(args[0], consts)
        # Express-style routing: app.get('/path', handler)
        if obj in ROUTERS and prop in HTTP_METHODS | {"all"} and first and first.startswith("/") and len(args) >= 2:
            facts.endpoints.append(Endpoint(method=prop.upper().replace("ALL", "*"), path=first, evidence=ev))
            return
        # HTTP clients: axios.get(url) / fetch(url) / axios({url})
        if prop == "fetch" or (obj in HTTP_CLIENTS and prop in HTTP_METHODS | {"request"}) or prop == "axios":
            url = first
            if url is None and args[0].type == "object":
                pairs = _object_pairs(args[0])
                url = _string_value(pairs["url"], consts) if "url" in pairs else None
            if url and (URL_RE.search(url) or url.startswith("${")):
                method = prop.upper() if prop in HTTP_METHODS else "GET"
                facts.outbound.append(OutboundCall(
                    kind=OutboundKind.HTTP, target=url, detail=method, technology=obj or prop, evidence=ev))
            return
        # kafkajs: producer.send({ topic }) / consumer.subscribe({ topic | topics })
        if prop in ("send", "subscribe") and args[0].type == "object":
            pairs = _object_pairs(args[0])
            topics: list[str] = []
            if "topic" in pairs:
                v = _string_value(pairs["topic"], consts)
                topics += [v] if v else []
            if "topics" in pairs and pairs["topics"].type == "array":
                topics += [v for c in pairs["topics"].named_children if (v := _string_value(c, consts))]
            kind = OutboundKind.MESSAGING_PUBLISH if prop == "send" else OutboundKind.MESSAGING_SUBSCRIBE
            for t in topics:
                facts.outbound.append(OutboundCall(kind=kind, target=t, technology="kafka", evidence=ev))
