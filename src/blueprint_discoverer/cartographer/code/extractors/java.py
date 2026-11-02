"""Java / Spring extractor (tree-sitter).

Recognised facts:
  * @RestController/@Controller + @RequestMapping/@GetMapping/... -> inbound endpoints
  * @FeignClient(name|value|url) + its mapping annotations          -> outbound HTTP calls
  * RestTemplate / WebClient calls with literal http(s) URLs          -> outbound HTTP calls
  * @KafkaListener(topics) / @RabbitListener(queues)                   -> subscriptions
  * kafkaTemplate.send("topic", ...) / rabbitTemplate.convertAndSend  -> publications
  * @Entity/@Table, Spring Data repositories                          -> persistence symbols
"""

from __future__ import annotations

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

from ....model import CodeSymbol, Endpoint, OutboundCall, OutboundKind
from .base import URL_RE, FileContext, FileFacts, join_paths, line_of, text, unquote, walk

_LANG = Language(tsjava.language())

MAPPINGS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "*",
}
REST_CLIENT_METHODS = {
    "getForObject", "getForEntity", "postForObject", "postForEntity", "put", "delete", "exchange",
    "patchForObject", "uri", "retrieve",
}
STEREOTYPES = {
    "RestController": "spring-web", "Controller": "spring-web", "Service": "spring",
    "Repository": "spring-data", "Entity": "jpa", "FeignClient": "spring-cloud-openfeign",
    "KafkaListener": "spring-kafka", "RabbitListener": "spring-amqp", "SpringBootApplication": "spring-boot",
}


def _annotations(node: Node) -> list[Node]:
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    if mods is None:
        return []
    return [c for c in mods.children if c.type in ("annotation", "marker_annotation")]


def _ann_name(ann: Node) -> str:
    name = ann.child_by_field_name("name")
    return text(name).split(".")[-1]


def _values(node: Node) -> list[str]:
    if node.type == "string_literal":
        return [unquote(text(node))]
    if node.type == "element_value_array_initializer":
        return [v for c in node.children for v in _values(c)]
    if node.type in ("field_access", "identifier", "scoped_identifier"):
        return [text(node)]
    return []


def _ann_args(ann: Node) -> dict[str, list[str]]:
    """{"value": [...], "path": [...], "method": [...]} from an annotation argument list."""
    args: dict[str, list[str]] = {}
    arglist = ann.child_by_field_name("arguments")
    if arglist is None:
        return args
    for c in arglist.children:
        if c.type == "element_value_pair":
            key = text(c.child_by_field_name("key"))
            args.setdefault(key, []).extend(_values(c.child_by_field_name("value")))
        else:
            vals = _values(c)
            if vals:
                args.setdefault("value", []).extend(vals)
    return args


def _mapping(ann: Node) -> tuple[str, list[str]] | None:
    name = _ann_name(ann)
    if name not in MAPPINGS:
        return None
    args = _ann_args(ann)
    paths = args.get("value", []) + args.get("path", []) or [""]
    method = MAPPINGS[name]
    if method == "*" and args.get("method"):
        method = args["method"][0].split(".")[-1].upper()
    return method, paths


class JavaExtractor:
    language = "java"
    extensions = (".java",)

    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        tree = self._parser.parse(ctx.source)
        root = tree.root_node
        package = ""
        for n in root.children:
            if n.type == "package_declaration":
                package = text(n).removeprefix("package").strip(" ;")
                facts.packages.add(package)
            elif n.type == "import_declaration":
                facts.imports.add(text(n).removeprefix("import").strip(" ;").removeprefix("static "))

        for node in walk(root):
            if node.type in ("class_declaration", "interface_declaration", "record_declaration"):
                self._type_decl(node, ctx, facts, package)
            elif node.type == "method_invocation":
                self._invocation(node, ctx, facts)
        return facts

    def _type_decl(self, node: Node, ctx: FileContext, facts: FileFacts, package: str) -> None:
        name = text(node.child_by_field_name("name"))
        anns = _annotations(node)
        ann_names = [_ann_name(a) for a in anns]
        for a in ann_names:
            if a in STEREOTYPES:
                facts.frameworks.add(STEREOTYPES[a])

        kind = "interface" if node.type == "interface_declaration" else "class"
        if "Entity" in ann_names or "Table" in ann_names:
            kind = "entity"
        supers = text(node.child_by_field_name("interfaces")) + text(node.child_by_field_name("superclass"))
        if any(r in supers for r in ("JpaRepository", "CrudRepository", "MongoRepository", "ReactiveCrudRepository")):
            kind = "repository"
            facts.frameworks.add("spring-data")
        facts.symbols.append(CodeSymbol(
            kind=kind, name=name, qualified_name=f"{package}.{name}" if package else name,
            annotations=ann_names, evidence=ctx.evidence(line_of(node), ctx.line_text(line_of(node))),
        ))
        if kind == "entity":
            table = next((_ann_args(a).get("name", [""])[0] for a in anns if _ann_name(a) == "Table"), "")
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.DATABASE, target="", detail=table or name, technology="jpa",
                evidence=ctx.evidence(line_of(node), f"@Entity {name}"),
            ))

        feign = next((a for a in anns if _ann_name(a) == "FeignClient"), None)
        feign_target = ""
        if feign is not None:
            args = _ann_args(feign)
            feign_target = (args.get("url") or args.get("name") or args.get("value") or [name])[0]
        prefix_paths = [""]
        for a in anns:
            m = _mapping(a)
            if m:
                prefix_paths = m[1]
        is_controller = any(a in ("RestController", "Controller") for a in ann_names)

        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.children:
            if member.type != "method_declaration":
                continue
            for a in _annotations(member):
                if _ann_name(a) in ("KafkaListener", "RabbitListener", "JmsListener"):
                    args = _ann_args(a)
                    tech = {"KafkaListener": "kafka", "RabbitListener": "rabbitmq", "JmsListener": "jms"}[_ann_name(a)]
                    for topic in args.get("topics", []) + args.get("queues", []) + args.get("destination", []) + args.get("value", []):
                        facts.outbound.append(OutboundCall(
                            kind=OutboundKind.MESSAGING_SUBSCRIBE, target=topic, technology=tech,
                            evidence=ctx.evidence(line_of(a), text(a)),
                        ))
                m = _mapping(a)
                if not m:
                    continue
                method, paths = m
                handler = f"{name}.{text(member.child_by_field_name('name'))}"
                for prefix in prefix_paths:
                    for p in paths:
                        full = join_paths(prefix, p)
                        ev = ctx.evidence(line_of(a), text(a))
                        if feign is not None:
                            facts.outbound.append(OutboundCall(
                                kind=OutboundKind.HTTP, target=feign_target, detail=f"{method} {full}",
                                technology="feign", evidence=ev,
                            ))
                        elif is_controller:
                            facts.endpoints.append(Endpoint(method=method, path=full, handler=handler, evidence=ev))

    def _invocation(self, node: Node, ctx: FileContext, facts: FileFacts) -> None:
        method = text(node.child_by_field_name("name"))
        obj = text(node.child_by_field_name("object")).lower()
        args = node.child_by_field_name("arguments")
        literals = [unquote(text(c)) for c in (args.children if args else []) if c.type == "string_literal"]
        ev = ctx.evidence(line_of(node), ctx.line_text(line_of(node)))
        if method == "send" and "kafka" in obj and literals:
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.MESSAGING_PUBLISH, target=literals[0], technology="kafka", evidence=ev))
        elif method == "convertAndSend" and "rabbit" in obj and literals:
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.MESSAGING_PUBLISH, target=literals[0], technology="rabbitmq", evidence=ev))
        elif method in REST_CLIENT_METHODS:
            for lit in literals:
                url = URL_RE.search(lit)
                if url:
                    facts.outbound.append(OutboundCall(
                        kind=OutboundKind.HTTP, target=url.group(0), detail=method, technology="rest-client",
                        evidence=ev))
