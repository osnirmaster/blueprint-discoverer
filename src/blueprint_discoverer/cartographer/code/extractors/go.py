"""Go extractor (tree-sitter).

Recognised facts:
  * net/http HandleFunc("/x") (incl. Go 1.22 "GET /x" patterns), gin/echo r.GET("/x"),
    chi/fiber r.Get("/x"), gorilla/mux HandleFunc, route groups r.Group("/api")          -> endpoints
  * http.Get/Post, http.NewRequest(method, url), resty/any client .Get(url) with literal URLs -> outbound HTTP
  * gRPC: pb.RegisterXServer(s, impl) (server); pb.NewXClient(conn), grpc.Dial/NewClient("host:port")
  * segmentio/kafka-go Writer/ReaderConfig{Topic}, sarama ProducerMessage{Topic}, SubscribeTopics   -> pub / sub
  * AWS SDK v2 inputs: &dynamodb.PutItemInput{TableName: aws.String("x")}, &sqs.SendMessageInput{QueueUrl},
    &sns.PublishInput{TopicArn}, &kinesis.PutRecordInput{StreamName}, &s3.PutObjectInput{Bucket}
  * sql.Open/gorm postgres.Open(dsn)                                                       -> persistence
  * soap.NewClient("url") (gowsdl)                                                          -> outbound SOAP
  * GraphQL operation documents in string literals                                         -> outbound GraphQL
String constants (const/var/:=) are propagated into concatenations.
"""

from __future__ import annotations

import re

import tree_sitter_go as tsgo
from tree_sitter import Language, Node, Parser

from ....model import CodeSymbol, Endpoint, OutboundCall, OutboundKind
from .base import (
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
    walk,
)

_LANG = Language(tsgo.language())

ROUTE_METHODS = {"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE", "PATCH": "PATCH", "Any": "*",
                 "Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH",
                 "HandleFunc": "*", "Handle": "*", "Method": "*"}
CLIENT_METHODS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Head": "HEAD",
                  "PostForm": "POST", "SetBaseURL": "", "NewRequest": "", "NewRequestWithContext": ""}
FRAMEWORK_IMPORTS = {
    "net/http": "net-http", "github.com/gin-gonic/gin": "gin", "github.com/labstack/echo": "echo",
    "github.com/go-chi/chi": "chi", "github.com/gofiber/fiber": "fiber", "github.com/gorilla/mux": "gorilla-mux",
    "google.golang.org/grpc": "grpc", "github.com/aws/aws-sdk-go-v2": "aws-sdk", "github.com/aws/aws-sdk-go": "aws-sdk",
    "github.com/segmentio/kafka-go": "kafka-go", "github.com/IBM/sarama": "sarama", "github.com/Shopify/sarama": "sarama",
    "github.com/confluentinc/confluent-kafka-go": "confluent-kafka", "database/sql": "database-sql",
    "gorm.io/gorm": "gorm", "github.com/99designs/gqlgen": "gqlgen", "github.com/go-resty/resty": "resty",
    "github.com/hooklift/gowsdl": "gowsdl",
}
DB_SCHEMES = ("postgres", "postgresql", "mysql", "sqlserver", "oracle", "mongodb", "redis")


def _string(node: Node | None, consts: dict[str, str]) -> str | None:
    if node is None:
        return None
    if node.type in ("interpreted_string_literal", "raw_string_literal"):
        return text(node)[1:-1]
    if node.type == "identifier":
        return consts.get(text(node))
    if node.type == "selector_expression":
        return consts.get(text(node.child_by_field_name("field")))
    if node.type == "int_literal":
        return text(node)
    if node.type == "binary_expression":
        parts = [_string(c, consts) for c in node.named_children]
        return "".join(p for p in parts if p) if any(parts) else None
    if node.type == "call_expression":  # aws.String("x"), fmt.Sprintf("%s/x", base)
        fn = text(node.child_by_field_name("function"))
        args = node.child_by_field_name("arguments")
        vals = [_string(a, consts) for a in (args.named_children if args else [])]
        if fn.endswith(("String", "StringPtr")) and vals:
            return vals[0]
        if fn == "fmt.Sprintf" and vals and vals[0]:
            out = vals[0]
            for v in vals[1:]:
                out = out.replace("%s", v or "{}", 1)
            return out
    if node.type == "unary_expression":  # &topic
        return _string(node.named_children[-1] if node.named_children else None, consts)
    return None


class GoExtractor:
    language = "go"
    extensions = (".go",)

    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        root = self._parser.parse(ctx.source).root_node
        consts: dict[str, str] = {}
        groups: dict[str, str] = {}
        for n in walk(root):
            if n.type == "package_clause":
                facts.packages.add(text(n.named_children[0]))
            elif n.type == "import_spec":
                path = text(n.child_by_field_name("path")).strip('"')
                facts.imports.add(path)
                for prefix, fw in FRAMEWORK_IMPORTS.items():
                    if path == prefix or path.startswith(prefix + "/"):
                        facts.frameworks.add(fw)
            elif n.type in ("const_spec", "var_spec"):
                names = [c for c in n.named_children if c.type == "identifier"]
                values = n.child_by_field_name("value")
                for name, value in zip(names, values.named_children if values else [], strict=False):
                    if (v := _string(value, consts)) is not None:
                        consts[text(name)] = v
            elif n.type in ("short_var_declaration", "assignment_statement"):
                left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                for name, value in zip(left.named_children, right.named_children, strict=False):
                    if value.type == "call_expression" and \
                            text(value.child_by_field_name("function")).endswith((".Group", ".Route", ".PathPrefix")):
                        owner = text(value.child_by_field_name("function")).rsplit(".", 1)[0]
                        args = value.child_by_field_name("arguments")
                        prefix = _string(args.named_children[0], consts) if args and args.named_children else ""
                        groups[text(name)] = join_paths(groups.get(owner, ""), prefix or "")
                    elif (v := _string(value, consts)) is not None:
                        consts[text(name)] = v

        for node in walk(root):
            if node.type in ("function_declaration", "method_declaration"):
                facts.symbols.append(CodeSymbol(kind="function", name=text(node.child_by_field_name("name")),
                                                evidence=ctx.evidence(line_of(node))))
            elif node.type == "type_spec":
                facts.symbols.append(CodeSymbol(kind="class", name=text(node.child_by_field_name("name")),
                                                evidence=ctx.evidence(line_of(node), ctx.line_text(line_of(node)))))
            elif node.type == "call_expression":
                self._call(node, ctx, facts, consts, groups)
            elif node.type == "composite_literal":
                self._composite(node, ctx, facts, consts)
            elif node.type == "raw_string_literal":
                v = _string(node, consts)
                if v and looks_like_graphql(v):
                    facts.outbound += graphql_calls(v, "", ctx.evidence(line_of(node)), "graphql-client")
        return facts

    def _call(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str],
              groups: dict[str, str]) -> None:
        fn = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        arg_nodes = args_node.named_children if args_node else []
        args = [_string(a, consts) for a in arg_nodes]
        operand, method = ("", text(fn))
        if fn.type == "selector_expression":
            operand, method = text(fn.child_by_field_name("operand")), text(fn.child_by_field_name("field"))
        line = line_of(node)
        ev = ctx.evidence(line, ctx.line_text(line))

        def out(kind: OutboundKind, target: str, technology: str, detail: str = "") -> None:
            facts.outbound.append(OutboundCall(kind=kind, target=target, detail=detail, technology=technology,
                                               evidence=ev))

        first = args[0] if args else None
        # routes: first argument is a path and a handler follows
        if method in ROUTE_METHODS and first is not None and len(args) >= 2 and \
                (first.startswith("/") or re.match(r"^[A-Z]+ /", first)):
            verb, path = ROUTE_METHODS[method], first
            if " /" in first:  # Go 1.22 ServeMux pattern "GET /accounts/{id}"
                verb, path = first.split(" ", 1)
            prefix = groups.get(operand.split(".")[-1], "")
            facts.endpoints.append(Endpoint(method=verb, path=join_paths(prefix, path), evidence=ev))
            return
        if (m := re.fullmatch(r"Register(\w+)Server", method)) and operand:
            facts.frameworks.add("grpc")
            facts.endpoints.append(grpc_endpoint(m.group(1), ev))
            return
        if (m := re.fullmatch(r"New(\w+)Client", method)) and operand and len(args) == 1 and \
                operand not in ("http", "resty", "redis", "s3", "sqs", "sns", "kinesis", "dynamodb"):
            facts.outbound.append(grpc_client(m.group(1), ev, "grpc-go"))
            return
        if operand == "grpc" and method in ("Dial", "DialContext", "NewClient"):
            address = next((a for a in args if a and ":" in a), None)
            if address:
                out(OutboundKind.GRPC, address if "://" in address else f"grpc://{address}", "grpc-go", "channel")
            return
        if operand == "soap" and method == "NewClient" and first:
            out(OutboundKind.SOAP, first, "gowsdl")
            return
        if method in ("SubscribeTopics", "ConsumePartition", "Consume"):
            topics = [first] if first and method == "ConsumePartition" else \
                [v for n in arg_nodes for c in walk(n) if c.type == "interpreted_string_literal"
                 and (v := _string(c, consts))]
            for t in topics:
                out(OutboundKind.MESSAGING_SUBSCRIBE, t, "kafka")
            return
        if method == "Open" and len(args) >= 2 and args[-1] and args[-1].split(":")[0] in DB_SCHEMES:
            out(OutboundKind.DATABASE, args[-1], args[-1].split(":")[0])
            return
        if method == "Open" and operand in ("postgres", "mysql", "sqlserver") and first and "://" in first:
            out(OutboundKind.DATABASE, first, operand)
            return
        if method in CLIENT_METHODS:
            url = next((a for a in args if a and URL_RE.search(a)), None)
            if url:
                verb = CLIENT_METHODS[method] or next((a for a in args if a and a.isupper()), "GET")
                out(OutboundKind.HTTP, URL_RE.search(url).group(0), operand or "net-http", verb)

    def _composite(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str]) -> None:
        type_name = text(node.child_by_field_name("type"))
        body = node.child_by_field_name("body")
        fields: dict[str, str] = {}
        for el in body.named_children if body else []:
            if el.type == "keyed_element" and len(el.named_children) == 2:
                key = text(el.named_children[0])
                value = el.named_children[1]
                value = value.named_children[0] if value.type == "literal_element" and value.named_children else value
                if (v := _string(value, consts)) is not None:
                    fields[key] = v
        ev = ctx.evidence(line_of(node), ctx.line_text(line_of(node)))
        for key, value in fields.items():
            if aws_service(key) and (c := aws_call(key, value, type_name, ev)):
                facts.outbound.append(c)
        if "Topic" in fields and fields["Topic"]:
            subscribe = "Reader" in type_name or "Consumer" in type_name
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.MESSAGING_SUBSCRIBE if subscribe else OutboundKind.MESSAGING_PUBLISH,
                target=fields["Topic"], technology="kafka", evidence=ev))
