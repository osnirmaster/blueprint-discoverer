"""TypeScript / JavaScript extractor (tree-sitter, tsx grammar parses plain JS too).

Recognised facts:
  * NestJS @Controller + @Get/@Post/...                   -> inbound endpoints
  * Express/Fastify app.get('/x') / router.post('/x')     -> inbound endpoints
  * axios/fetch/got/ky calls with resolvable URLs          -> outbound HTTP calls
  * kafkajs producer.send({topic}) / consumer.subscribe()  -> publications / subscriptions
  * gRPC: NestJS @GrpcMethod('Svc', 'M'), server.addService(proto.Svc.service) (server);
    new SvcClient("host:port", creds), client.getService<T>('Svc'), Transport.GRPC options (client)
  * GraphQL: NestJS @Resolver + @Query/@Mutation, gql`type Query {...}` SDL (server);
    gql`query {...}` documents, ApolloClient/HttpLink { uri } (client)
  * SOAP: soap.listen(server, '/path', ...) (server); soap.createClient(Async)(url) (client)
  * AWS SDK v3: new XCommand({ TableName/Bucket/QueueUrl/TopicArn/StreamName }), DocumentClient
    calls with those fields                                -> cloud resources
Module-level constants such as `const BASE = process.env.X ?? "http://svc:8080"` are propagated
into template strings so that `${BASE}/accounts` resolves deterministically.
"""

from __future__ import annotations

import re

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from ....model import CodeSymbol, Endpoint, OutboundCall, OutboundKind
from .base import (
    HTTP_METHODS,
    URL_RE,
    FileContext,
    FileFacts,
    aws_call,
    aws_service,
    graphql_calls,
    grpc_client,
    grpc_endpoint,
    join_paths,
    line_of,
    looks_like_graphql,
    text,
    unquote,
    walk,
)

_TS = Language(tsts.language_typescript())
_TSX = Language(tsts.language_tsx())

NEST_METHODS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "All": "*"}
HTTP_CLIENTS = {"axios", "http", "got", "ky", "superagent", "request", "client", "httpClient", "api", "httpService",
                "instance", "$http"}
GRAPHQL_DECORATORS = {"Query": "Query", "Mutation": "Mutation", "Subscription": "Subscription"}
ROUTERS = {"app", "router", "server", "api", "fastify", "routes"}
FRAMEWORK_IMPORTS = {
    "express": "express", "@nestjs/common": "nestjs", "fastify": "fastify", "kafkajs": "kafkajs",
    "axios": "axios", "react": "react", "@angular/core": "angular", "vue": "vue", "next": "nextjs",
    "amqplib": "rabbitmq", "pg": "postgresql", "mongoose": "mongodb", "typeorm": "typeorm", "prisma": "prisma",
    "@grpc/grpc-js": "grpc", "@nestjs/microservices": "nestjs-microservices", "@nestjs/graphql": "nestjs-graphql",
    "@apollo/client": "apollo-client", "@apollo/server": "apollo-server", "graphql-request": "graphql-request",
    "soap": "node-soap", "@aws-sdk": "aws-sdk", "aws-sdk": "aws-sdk",
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
            elif node.type == "new_expression":
                self._new(node, ctx, facts, consts)
            elif node.type == "template_string":
                self._graphql_document(node, ctx, facts)
            elif node.type in ("function_declaration",):
                fname = text(node.child_by_field_name("name"))
                facts.symbols.append(CodeSymbol(kind="function", name=fname, evidence=ctx.evidence(line_of(node))))
            elif node.type == "call_expression":
                self._call(node, ctx, facts, consts)
        return facts

    @staticmethod
    def _graphql_document(node: Node, ctx: FileContext, facts: FileFacts) -> None:
        from ..contracts import parse_graphql

        value = text(node)[1:-1]
        if "{" not in value:
            return
        if re.search(r"\btype\s+(Query|Mutation|Subscription)\b", value):
            endpoints, _ = parse_graphql(value, ctx.repo, ctx.path)
            for e in endpoints:
                e.evidence = ctx.evidence(line_of(node) + e.evidence.line - 1, e.path)
            facts.endpoints += endpoints
        elif looks_like_graphql(value):
            facts.outbound += graphql_calls(value, "", ctx.evidence(line_of(node)), "graphql-client")

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
        if "Resolver" in dec_names:
            self._resolver(node, name, ctx, facts)
        body = node.child_by_field_name("body")
        for member in body.children if body else []:
            for d in _preceding_decorators(member) if member.type == "method_definition" else []:
                fn, args = _decorator_call(d)
                if fn == "GrpcMethod" and args:
                    svc = unquote(text(args[0]))
                    method = unquote(text(args[1])) if len(args) > 1 else text(member.child_by_field_name("name"))
                    facts.frameworks.add("grpc")
                    facts.endpoints.append(Endpoint(method="RPC", path=f"/{svc}/{method[:1].upper()}{method[1:]}",
                                                    handler=f"{name}.{text(member.child_by_field_name('name'))}",
                                                    protocol="grpc", evidence=ctx.evidence(line_of(d), text(d))))
        if "Controller" not in dec_names:
            return
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

    @staticmethod
    def _resolver(node: Node, name: str, ctx: FileContext, facts: FileFacts) -> None:
        """NestJS code-first/schema-first resolvers: @Query()/@Mutation() methods are root fields."""
        facts.frameworks.add("nestjs-graphql")
        body = node.child_by_field_name("body")
        for member in body.children if body else []:
            if member.type != "method_definition":
                continue
            for d in _preceding_decorators(member):
                fn, args = _decorator_call(d)
                if fn not in GRAPHQL_DECORATORS:
                    continue
                field = text(member.child_by_field_name("name"))
                for a in args:
                    if a.type == "object" and "name" in _object_pairs(a):
                        field = unquote(text(_object_pairs(a)["name"]))
                    elif a.type == "string":
                        field = unquote(text(a))
                root = GRAPHQL_DECORATORS[fn]
                facts.endpoints.append(Endpoint(method=root.upper(), path=f"{root}.{field}",
                                                handler=f"{name}.{text(member.child_by_field_name('name'))}",
                                                protocol="graphql", evidence=ctx.evidence(line_of(d), text(d))))

    def _aws_objects(self, node: Node, operation: str, consts: dict[str, str], ev, facts: FileFacts) -> None:
        for obj in (a for a in node.named_children if a.type == "object"):
            for key, value in _object_pairs(obj).items():
                if aws_service(key) and (v := _string_value(value, consts)):
                    if c := aws_call(key, v, operation, ev):
                        facts.outbound.append(c)

    def _new(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str]) -> None:
        ctor = text(node.child_by_field_name("constructor"))
        args_node = node.child_by_field_name("arguments")
        args = [a for a in (args_node.children if args_node else []) if a.is_named]
        ev = ctx.evidence(line_of(node), ctx.line_text(line_of(node)))
        simple = ctor.split(".")[-1]
        if args_node is not None:
            self._aws_objects(args_node, simple, consts, ev, facts)
        first = _string_value(args[0], consts) if args else None
        if simple.endswith("Client") and first and re.match(r"^[\w.-]+:\d+$", first):
            facts.frameworks.add("grpc")
            facts.outbound.append(grpc_client(simple.removesuffix("Client"), ev, "grpc-js"))
            facts.outbound.append(OutboundCall(kind=OutboundKind.GRPC, target=f"grpc://{first}", detail="channel",
                                               technology="grpc-js", evidence=ev))
        elif simple in ("ApolloClient", "HttpLink", "GraphQLClient") or ctor.endswith("createHttpLink"):
            uri = first if first and URL_RE.search(first) else None
            if uri is None and args and args[0].type == "object" and "uri" in _object_pairs(args[0]):
                uri = _string_value(_object_pairs(args[0])["uri"], consts)
            if uri:
                facts.outbound.append(OutboundCall(kind=OutboundKind.GRAPHQL, target=uri, detail="",
                                                   technology="graphql-client", evidence=ev))

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
        operation = prop if prop[:1].islower() else text(fn)
        if args_node is not None:
            self._aws_objects(args_node, operation, consts, ev, facts)
        # SOAP (node-soap)
        if obj == "soap" and prop in ("createClient", "createClientAsync") and first:
            facts.outbound.append(OutboundCall(kind=OutboundKind.SOAP, target=first, technology="node-soap",
                                               evidence=ev))
            return
        if obj == "soap" and prop == "listen" and len(args) > 1:
            path = _string_value(args[1], consts)
            if path:
                facts.endpoints.append(Endpoint(method="SOAP", path=path, protocol="soap", evidence=ev))
            return
        # gRPC: grpc-js server.addService(proto.pkg.Svc.service, impl) / Nest client.getService<T>('Svc')
        if prop == "addService" and args and text(args[0]).endswith(".service"):
            facts.frameworks.add("grpc")
            facts.endpoints.append(grpc_endpoint(text(args[0]).split(".")[-2], ev))
            return
        if prop == "getService" and first:
            facts.outbound.append(grpc_client(first, ev, "nestjs-grpc"))
            return
        # Nest ClientsModule.register / ClientProxyFactory.create({ transport: Transport.GRPC, options: { url } })
        if args[0].type in ("object", "array") and "Transport.GRPC" in text(args[0]):
            for url in re.findall(r"url:\s*['\"`]([^'\"`]+)", text(args[0])):
                facts.outbound.append(OutboundCall(kind=OutboundKind.GRPC, target=f"grpc://{url}", detail="channel",
                                                   technology="nestjs-grpc", evidence=ev))
            return
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
