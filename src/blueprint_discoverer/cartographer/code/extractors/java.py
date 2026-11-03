"""Java extractor (tree-sitter).

Translates the Java syntax tree into the neutral records of `jvm.py`, where the framework rules
(Spring, JAX-RS, Feign, Retrofit, gRPC, GraphQL, SOAP, messaging, AWS SDK) live. String constants
(`static final String X = "..."` and local variables) are propagated into annotation and call arguments.
"""

from __future__ import annotations

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

from .base import FileContext, FileFacts, line_of, text, walk
from .jvm import Ann, Call, Member, TypeDecl, call_facts, type_facts

_LANG = Language(tsjava.language())
STATEMENTS = ("expression_statement", "local_variable_declaration", "return_statement", "field_declaration")


def _string(node: Node, consts: dict[str, str]) -> str | None:
    if node.type == "string_literal":
        raw = text(node)
        return raw[3:-3].strip() if raw.startswith('"""') else raw[1:-1]
    if node.type == "identifier":
        return consts.get(text(node))
    if node.type == "decimal_integer_literal":
        return text(node)
    if node.type == "field_access":  # Constants.TOPIC
        return consts.get(text(node.child_by_field_name("field")))
    if node.type == "binary_expression":
        parts = [_string(c, consts) for c in node.named_children]
        return "".join(p for p in parts if p) if any(parts) else None
    return None


def _values(node: Node, consts: dict[str, str]) -> list[str]:
    if node.type == "element_value_array_initializer":
        return [v for c in node.named_children for v in _values(c, consts)]
    v = _string(node, consts)
    if v is not None:
        return [v]
    if node.type in ("field_access", "identifier", "scoped_identifier"):
        return [text(node)]
    return []


def _ann(ann: Node, consts: dict[str, str]) -> Ann:
    args: dict[str, list[str]] = {}
    arglist = ann.child_by_field_name("arguments")
    for c in arglist.named_children if arglist is not None else []:
        if c.type == "element_value_pair":
            key = text(c.child_by_field_name("key"))
            args.setdefault(key, []).extend(_values(c.child_by_field_name("value"), consts))
        else:
            args.setdefault("value", []).extend(_values(c, consts))
    return Ann(name=text(ann.child_by_field_name("name")).split(".")[-1], args=args, line=line_of(ann),
               text=text(ann))


def _modifiers(node: Node) -> Node | None:
    return next((c for c in node.children if c.type == "modifiers"), None)


def _anns(node: Node, consts: dict[str, str]) -> list[Ann]:
    mods = _modifiers(node)
    return [_ann(c, consts) for c in (mods.children if mods else []) if c.type in ("annotation", "marker_annotation")]


def _statement(node: Node) -> str:
    n = node
    while n.parent is not None and n.type not in STATEMENTS:
        n = n.parent
    return text(n)


class JavaExtractor:
    language = "java"
    extensions = (".java",)

    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        root = self._parser.parse(ctx.source).root_node
        package = ""
        consts: dict[str, str] = {}
        for n in root.children:
            if n.type == "package_declaration":
                package = text(n).removeprefix("package").strip(" ;")
                facts.packages.add(package)
            elif n.type == "import_declaration":
                facts.imports.add(text(n).removeprefix("import").strip(" ;").removeprefix("static "))
        for n in walk(root):
            if n.type == "variable_declarator":
                name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
                if value is not None and (v := _string(value, consts)) is not None:
                    consts[text(name)] = v

        for node in walk(root):
            if node.type in ("class_declaration", "interface_declaration", "record_declaration"):
                type_facts(self._type(node, consts, package, ctx), ctx, facts)
            elif node.type == "method_invocation":
                args = node.child_by_field_name("arguments")
                call_facts(Call(
                    receiver=text(node.child_by_field_name("object")), method=text(node.child_by_field_name("name")),
                    args=[_string(a, consts) for a in (args.named_children if args else [])],
                    line=line_of(node), statement=_statement(node)), ctx, facts)
            elif node.type == "object_creation_expression":
                args = node.child_by_field_name("arguments")
                call_facts(Call(
                    receiver="", method=text(node.child_by_field_name("type")).split("<")[0].split(".")[-1],
                    args=[_string(a, consts) for a in (args.named_children if args else [])],
                    line=line_of(node), statement=_statement(node), is_new=True), ctx, facts)
        return facts

    @staticmethod
    def _type(node: Node, consts: dict[str, str], package: str, ctx: FileContext) -> TypeDecl:
        is_interface = node.type == "interface_declaration"
        members = []
        body = node.child_by_field_name("body")
        for m in body.named_children if body else []:
            mods = text(_modifiers(m)) if _modifiers(m) else ""
            if m.type == "method_declaration":
                members.append(Member(name=text(m.child_by_field_name("name")), kind="method", anns=_anns(m, consts),
                                      line=line_of(m), public=is_interface or "public" in mods))
            elif m.type == "field_declaration":
                declarator = m.child_by_field_name("declarator")
                members.append(Member(name=text(declarator.child_by_field_name("name")) if declarator else "",
                                      kind="field", anns=_anns(m, consts), line=line_of(m),
                                      type_text=text(m.child_by_field_name("type"))))
        supers = " ".join(text(node.child_by_field_name(f)) for f in ("superclass", "interfaces"))
        supers += " " + " ".join(text(c) for c in node.children if c.type == "extends_interfaces")
        return TypeDecl(name=text(node.child_by_field_name("name")), kind="interface" if is_interface else "class",
                        anns=_anns(node, consts), supers=supers, members=members, line=line_of(node),
                        package=package, line_text=ctx.line_text(line_of(node)))
