"""Kotlin extractor (tree-sitter).

Translates Kotlin into the neutral records of `jvm.py`, so Spring, JAX-RS, Feign, Retrofit, gRPC
(including grpc-kotlin coroutine stubs), GraphQL, SOAP, messaging and AWS SDK rules are shared with Java.
Kotlin-only facts:
  * Ktor server routing: routing { route("/api") { get("/x") { } } }            -> endpoints
  * Ktor client: client.get("http://...")                                        -> outbound HTTP
String templates keep Spring placeholders ("\\${accounts.url}") and resolve `const val` constants.

Grammar quirks handled here:
  * an annotated class right after top-level statements can be parsed as a chain of `annotated_expression`
    siblings before the `class_declaration`; both shapes are read;
  * an annotated declaration that follows a class with members can turn the rest of the file into one
    top-level ERROR node; such regions are parsed again on their own (line numbers are preserved).
"""

from __future__ import annotations

import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Node, Parser

from ....model import Endpoint
from .base import HTTP_METHODS, FileContext, FileFacts, join_paths, line_of, text, walk
from .jvm import Ann, Call, Member, TypeDecl, call_facts, type_facts

_LANG = Language(tskotlin.language())
STATEMENTS = ("property_declaration", "assignment", "jump_expression", "return_expression")
HIDDEN = ("private", "protected", "internal")


def _string(node: Node | None, consts: dict[str, str]) -> str | None:
    if node is None:
        return None
    if node.type == "string_literal":
        out = []
        for c in node.children:
            if c.type == "string_content":
                out.append(text(c))
            elif c.type == "escape_sequence":
                out.append(text(c)[1:])  # "\$" -> "$"
            elif c.type == "interpolation":
                inner = text(c).lstrip("$").strip("{}")
                out.append(consts.get(inner, "${" + inner + "}"))
        return "".join(out)
    if node.type in ("identifier", "simple_identifier"):
        return consts.get(text(node))
    if node.type == "navigation_expression":
        return consts.get(text(node).split(".")[-1])
    if node.type in ("integer_literal", "long_literal"):
        return text(node)
    if node.type == "additive_expression":
        parts = [_string(c, consts) for c in node.named_children]
        return "".join(p for p in parts if p) if any(parts) else None
    if node.type in ("value_argument", "parenthesized_expression"):
        return _string(node.named_children[-1] if node.named_children else None, consts)
    return None


def _values(node: Node, consts: dict[str, str]) -> list[str]:
    if node.type == "collection_literal":
        return [v for c in node.named_children for v in _values(c, consts)]
    if node.type == "call_expression" and text(node.named_children[0]) in ("arrayOf", "listOf"):
        args = next((c for c in node.named_children if c.type == "value_arguments"), None)
        return [v for a in (args.named_children if args else []) for v in _values(a.named_children[-1], consts)]
    v = _string(node, consts)
    if v is not None:
        return [v]
    return [text(node)] if node.type in ("navigation_expression", "identifier") else []


def _ann_from(name_node: Node, args_node: Node | None, line: int, raw: str, consts: dict[str, str]) -> Ann:
    name = text(name_node).split(".")[-1]
    args: dict[str, list[str]] = {}
    if args_node is not None and args_node.type == "value_arguments":
        for va in args_node.named_children:
            kids = va.named_children
            if len(kids) >= 2 and kids[0].type == "identifier":
                args.setdefault(text(kids[0]), []).extend(_values(kids[-1], consts))
            elif kids:
                args.setdefault("value", []).extend(_values(kids[-1], consts))
    elif args_node is not None:  # parenthesized_expression of the top-level quirk
        args["value"] = _values(args_node.named_children[0], consts) if args_node.named_children else []
    return Ann(name=name, args=args, line=line, text=raw)


def _annotation(node: Node, consts: dict[str, str]) -> Ann:
    inv = next((c for c in node.named_children if c.type == "constructor_invocation"), None)
    if inv is not None:
        user_type = next(c for c in inv.named_children if c.type == "user_type")
        args = next((c for c in inv.named_children if c.type == "value_arguments"), None)
        return _ann_from(user_type, args, line_of(node), text(node), consts)
    user_type = next((c for c in node.named_children if c.type == "user_type"), node)
    return _ann_from(user_type, None, line_of(node), text(node), consts)


def _modifier_anns(node: Node, consts: dict[str, str]) -> list[Ann]:
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    return [_annotation(c, consts) for c in (mods.named_children if mods else []) if c.type == "annotation"]


def _quirk_anns(node: Node, consts: dict[str, str]) -> list[Ann]:
    """Annotations parsed as `annotated_expression` siblings right before a declaration."""
    sib = node.prev_named_sibling
    if sib is None or sib.type != "annotated_expression":
        return []
    out: list[Ann] = []
    n: Node | None = sib
    while n is not None and n.type == "annotated_expression":
        ann_node = n.named_children[0]
        rest = n.named_children[1] if len(n.named_children) > 1 else None
        args = rest if rest is not None and rest.type == "parenthesized_expression" else None
        out.append(_annotation(ann_node, consts) if args is None else
                   _ann_from(next(c for c in ann_node.named_children if c.type == "user_type"), args,
                             line_of(ann_node), text(ann_node) + text(args), consts))
        n = rest if rest is not None and rest.type == "annotated_expression" else None
    return out


def _statement(node: Node) -> str:
    n = node
    while n.parent is not None and n.parent.type not in ("block", "source_file", "class_body", "statements"):
        n = n.parent
    return text(n)


def _call_parts(node: Node) -> tuple[str, str]:
    callee = node.named_children[0]
    if callee.type == "navigation_expression":
        ids = [c for c in callee.named_children]
        return text(ids[0]) if len(ids) > 1 else "", text(ids[-1])
    return "", text(callee)


def _dsl_call(node: Node, consts: dict[str, str]) -> tuple[str, str | None] | None:
    """(name, first argument) of a trailing-lambda DSL call such as get("/x") { } or route("/api") { }.

    The grammar nests it as call_expression(call_expression(name, args), annotated_lambda)."""
    if not any(c.type == "annotated_lambda" for c in node.named_children):
        return None
    inner = node.named_children[0]
    target = inner if inner.type == "call_expression" else node
    if target.named_children[0].type != "identifier":
        return None
    args = next((c for c in target.named_children if c.type == "value_arguments"), None)
    first = _string(args.named_children[0], consts) if args is not None and args.named_children else None
    return text(target.named_children[0]), first


class KotlinExtractor:
    language = "kotlin"
    extensions = (".kt",)

    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    def _parse(self, source: bytes) -> tuple[list[Node], list[Node]]:
        """(roots, nodes): top-level ERROR regions that parse cleanly on their own replace the error subtree;
        otherwise the error subtree is still walked (tree-sitter keeps most of the structure inside it)."""
        root = self._parser.parse(source).root_node
        roots, nodes = [root], [root]
        for child in root.named_children:
            if child.type == "ERROR":
                # padding with newlines keeps the original line numbers
                sub = self._parser.parse(b"\n" * child.start_point.row + source[child.start_byte:child.end_byte])
                if not any(c.type == "ERROR" for c in sub.root_node.named_children):
                    roots.append(sub.root_node)
                    nodes += list(walk(sub.root_node))
                    continue
            nodes += list(walk(child))
        return roots, nodes

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        roots, nodes = self._parse(ctx.source)
        package = ""
        consts: dict[str, str] = {}
        for n in (c for r in roots for c in r.named_children):
            if n.type == "package_header":
                package = text(n).removeprefix("package").strip()
                facts.packages.add(package)
            elif n.type == "import":
                facts.imports.add(text(n).removeprefix("import").strip())
        for n in nodes:
            if n.type == "property_declaration":
                var = next((c for c in n.named_children if c.type == "variable_declaration"), None)
                value = n.named_children[-1]
                if var is not None and value is not var and (v := _string(value, consts)) is not None:
                    consts[text(var.named_children[0])] = v

        for node in nodes:
            if node.type in ("class_declaration", "object_declaration"):
                type_facts(self._type(node, consts, package, ctx), ctx, facts)
            elif node.type == "call_expression":
                self._call(node, consts, ctx, facts)
        return facts

    def _type(self, node: Node, consts: dict[str, str], package: str, ctx: FileContext) -> TypeDecl:
        name_node = next((c for c in node.named_children if c.type in ("identifier", "type_identifier")), None)
        is_interface = any(c.type == "interface" for c in node.children)
        members: list[Member] = []
        for ctor in (c for c in node.named_children if c.type == "primary_constructor"):
            for p in walk(ctor):
                if p.type == "class_parameter":
                    ident = next((c for c in p.named_children if c.type == "identifier"), None)
                    members.append(Member(name=text(ident), kind="field", anns=_modifier_anns(p, consts),
                                          line=line_of(p), type_text=text(p.named_children[-1])))
        body = next((c for c in node.named_children if c.type == "class_body"), None)
        for m in body.named_children if body else []:
            mods = next((c for c in m.children if c.type == "modifiers"), None)
            hidden = any(h in text(mods) for h in HIDDEN) if mods else False
            if m.type == "function_declaration":
                ident = next((c for c in m.named_children if c.type in ("identifier", "simple_identifier")), None)
                members.append(Member(name=text(ident), kind="method", anns=_modifier_anns(m, consts),
                                      line=line_of(m), public=not hidden))
            elif m.type == "property_declaration":
                var = next((c for c in m.named_children if c.type == "variable_declaration"), None)
                members.append(Member(name=text(var.named_children[0]) if var else "", kind="field",
                                      anns=_modifier_anns(m, consts), line=line_of(m),
                                      type_text=text(var.named_children[-1]) if var else ""))
        supers = " ".join(text(c) for c in node.named_children if c.type == "delegation_specifiers")
        return TypeDecl(name=text(name_node), kind="interface" if is_interface else "class",
                        anns=_quirk_anns(node, consts) + _modifier_anns(node, consts), supers=supers,
                        members=members, line=line_of(node), package=package,
                        line_text=ctx.line_text(line_of(node)))

    def _call(self, node: Node, consts: dict[str, str], ctx: FileContext, facts: FileFacts) -> None:
        if node.named_children[0].type == "call_expression":
            # outer half of a trailing-lambda call: the inner call_expression is visited on its own
            dsl = _dsl_call(node, consts)
            if dsl and dsl[0] in HTTP_METHODS and dsl[1] and dsl[1].startswith("/"):
                self._ktor_route(node, dsl[0], dsl[1], consts, ctx, facts)
            return
        receiver, method = _call_parts(node)
        args_node = next((c for c in node.named_children if c.type == "value_arguments"), None)
        args = [_string(a, consts) for a in (args_node.named_children if args_node else [])]
        if (dsl := _dsl_call(node, consts)) and dsl[0] in HTTP_METHODS and dsl[1] and dsl[1].startswith("/"):
            self._ktor_route(node, dsl[0], dsl[1], consts, ctx, facts)
            return
        call_facts(Call(receiver=receiver, method=method, args=args, line=line_of(node),
                        statement=_statement(node)), ctx, facts)

    @staticmethod
    def _ktor_route(node: Node, method: str, path: str, consts: dict[str, str], ctx: FileContext,
                    facts: FileFacts) -> None:
        """Ktor routing DSL: get("/x") { } nested in route("/prefix") { } blocks."""
        facts.frameworks.add("ktor")
        prefix, parent = [], node.parent
        while parent is not None:
            if parent.type == "call_expression" and (dsl := _dsl_call(parent, consts)) and dsl[0] == "route":
                prefix.insert(0, dsl[1] or "")
            parent = parent.parent
        facts.endpoints.append(Endpoint(method=method.upper(), path=join_paths(*prefix, path),
                                        evidence=ctx.evidence(line_of(node), ctx.line_text(line_of(node)))))
