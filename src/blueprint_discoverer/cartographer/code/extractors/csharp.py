"""C# / .NET extractor (tree-sitter).

Recognised facts:
  * ASP.NET Core controllers: [ApiController]/ControllerBase + [Route] + [HttpGet("x")]  -> endpoints
  * Minimal APIs: app.MapGet("/x", ...), MapGroup("/api") prefixes                       -> endpoints
  * gRPC: classes deriving Svc.SvcBase (server); new Svc.SvcClient(channel),
    AddGrpcClient<Svc.SvcClient>, GrpcChannel.ForAddress("http://host") (client)
  * WCF / CoreWCF: [ServiceContract] + [OperationContract] (server); ClientBase<IContract>,
    new EndpointAddress("http://...") (client)                                           -> SOAP
  * HotChocolate: [QueryType]/[MutationType]/[ExtendObjectType("Query")] public methods  -> GraphQL
  * HttpClient GetAsync/PostAsJsonAsync..., new Uri("http://..."), Refit [Get("/x")],
    RestService.For<T>("url"), new RestClient("url")                                    -> outbound HTTP
  * Confluent.Kafka Produce/ProduceAsync/Subscribe                                       -> pub / sub
  * EF Core DbContext DbSet<T>, [Table("x")]                                             -> persistence
  * AWS SDK: request objects { TableName/BucketName/QueueUrl/TopicArn/StreamName = ... },
    [DynamoDBTable("x")], client calls with a literal resource name                     -> cloud resources
String constants (const/static readonly/locals) are propagated, including interpolated strings.
"""

from __future__ import annotations

import re

import tree_sitter_c_sharp as tscs
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

_LANG = Language(tscs.language())

HTTP_ATTRIBUTES = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE",
                   "HttpPatch": "PATCH", "Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE",
                   "Patch": "PATCH"}
MINIMAL_API = {"MapGet": "GET", "MapPost": "POST", "MapPut": "PUT", "MapDelete": "DELETE", "MapPatch": "PATCH",
               "MapMethods": "*"}
HTTP_CLIENT_METHODS = re.compile(r"^(Get|Post|Put|Delete|Patch|Send)(String|Stream|ByteArray|FromJson|AsJson)?Async$")
GRAPHQL_ROOTS = {"QueryType": "Query", "MutationType": "Mutation", "SubscriptionType": "Subscription"}
FRAMEWORK_USINGS = {
    "Microsoft.AspNetCore": "aspnetcore", "Grpc": "grpc", "HotChocolate": "hotchocolate",
    "Amazon": "aws-sdk", "System.ServiceModel": "wcf", "CoreWCF": "corewcf", "Confluent.Kafka": "confluent-kafka",
    "Microsoft.EntityFrameworkCore": "efcore", "Refit": "refit", "RestSharp": "restsharp",
    "MassTransit": "masstransit", "Dapper": "dapper", "MongoDB": "mongodb",
}
AWS_RECEIVER_FIELD = {"sqs": "QueueUrl", "sns": "TopicArn", "kinesis": "StreamName", "dynamo": "TableName",
                      "s3": "BucketName"}


def _string(node: Node | None, consts: dict[str, str]) -> str | None:
    if node is None:
        return None
    if node.type == "string_literal":
        return "".join(text(c) for c in node.named_children if c.type in ("string_literal_content",
                                                                            "escape_sequence"))
    if node.type == "verbatim_string_literal":
        return text(node)[2:-1]
    if node.type == "raw_string_literal":
        return text(node).strip('"').strip()
    if node.type == "interpolated_string_expression":
        out = []
        for c in node.named_children:
            if c.type == "string_content":
                out.append(text(c))
            elif c.type == "interpolation":
                inner = text(c).strip("{}")
                out.append(consts.get(inner, "{" + inner + "}"))
        return "".join(out)
    if node.type == "identifier":
        return consts.get(text(node))
    if node.type == "member_access_expression":
        return consts.get(text(node.child_by_field_name("name")))
    if node.type == "integer_literal":
        return text(node)
    if node.type == "binary_expression":
        parts = [_string(c, consts) for c in node.named_children]
        return "".join(p for p in parts if p) if any(parts) else None
    if node.type in ("argument", "attribute_argument", "parenthesized_expression"):
        return _string(node.named_children[-1] if node.named_children else None, consts)
    return None


def _attributes(node: Node, consts: dict[str, str]) -> list[tuple[str, dict[str, str], Node]]:
    """[(name, {"value"|named arg: string}, node)] for the attribute lists of a declaration."""
    out = []
    for al in (c for c in node.children if c.type == "attribute_list"):
        for a in (c for c in al.named_children if c.type == "attribute"):
            name = text(a.child_by_field_name("name")).split(".")[-1].removesuffix("Attribute")
            args: dict[str, str] = {}
            arglist = next((c for c in a.named_children if c.type == "attribute_argument_list"), None)
            for arg in arglist.named_children if arglist else []:
                key = next((text(c) for c in arg.named_children if c.type == "identifier"), None) \
                    if len(arg.named_children) > 1 else None
                value = _string(arg, consts)
                if value is None and arg.named_children:
                    value = text(arg.named_children[-1]).split(".")[-1]  # OperationTypeNames.Query -> Query
                args.setdefault(key or "value", value or "")
            out.append((name, args, a))
    return out


def _field_name(method: str) -> str:
    """HotChocolate naming: GetCustomerAsync -> customer."""
    base = re.sub(r"Async$", "", re.sub(r"^Get(?=[A-Z])", "", method))
    return base[:1].lower() + base[1:]


def _generic_args(node: Node) -> list[str]:
    return [text(c) for c in walk(node) if c.type == "type_argument_list" for c in c.named_children]


class CSharpExtractor:
    language = "csharp"
    extensions = (".cs",)

    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        root = self._parser.parse(ctx.source).root_node
        consts: dict[str, str] = {}
        groups: dict[str, str] = {}
        for n in walk(root):
            if n.type == "using_directive":
                ns = text(n).removeprefix("global ").removeprefix("using").strip(" ;").split("=")[-1].strip()
                facts.imports.add(ns)
                for prefix, fw in FRAMEWORK_USINGS.items():
                    if ns == prefix or ns.startswith(prefix + "."):
                        facts.frameworks.add(fw)
            elif n.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
                facts.packages.add(text(n.child_by_field_name("name")))
            elif n.type == "variable_declarator":
                name = n.named_children[0]
                value = n.named_children[-1] if len(n.named_children) > 1 else None
                if value is not None and value.type == "invocation_expression" and \
                        text(value.child_by_field_name("function")).endswith("MapGroup"):
                    args = value.child_by_field_name("arguments")
                    groups[text(name)] = _string(args.named_children[0], consts) or "" if args and \
                        args.named_children else ""
                elif value is not None and (v := _string(value, consts)) is not None:
                    consts[text(name)] = v

        for node in walk(root):
            if node.type in ("class_declaration", "interface_declaration", "record_declaration"):
                self._type(node, ctx, facts, consts)
            elif node.type == "invocation_expression":
                self._invocation(node, ctx, facts, consts, groups)
            elif node.type == "object_creation_expression":
                self._creation(node, ctx, facts, consts)
            elif node.type in ("string_literal", "verbatim_string_literal", "raw_string_literal"):
                v = _string(node, consts)
                if v and looks_like_graphql(v):
                    facts.outbound += graphql_calls(v, "", ctx.evidence(line_of(node)), "graphql-client")
        return facts

    # ------------------------------------------------------------------ declarations
    def _type(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str]) -> None:
        name = text(node.child_by_field_name("name"))
        attrs = _attributes(node, consts)
        names = {a[0]: a[1] for a in attrs}
        bases = text(next((c for c in node.named_children if c.type == "base_list"), None))
        line = line_of(node)
        ev = ctx.evidence(line, ctx.line_text(line))
        kind = "interface" if node.type == "interface_declaration" else "class"
        if "Table" in names or "DynamoDBTable" in names:
            kind = "entity"
        facts.symbols.append(CodeSymbol(kind=kind, name=name, annotations=sorted(names), evidence=ev))
        if "Table" in names:
            facts.outbound.append(OutboundCall(kind=OutboundKind.DATABASE, target="", detail=names["Table"].get(
                "value") or name, technology="efcore", evidence=ev))
        if "DynamoDBTable" in names:
            facts.outbound.append(OutboundCall(kind=OutboundKind.DATABASE,
                                               target=f"dynamodb://{names['DynamoDBTable'].get('value', name)}",
                                               detail="DynamoDBContext", technology="dynamodb", evidence=ev))

        is_controller = "ApiController" in names or re.search(r"\bController(Base)?\b", bases) is not None
        prefix = names.get("Route", {}).get("value", "")
        prefix = prefix.replace("[controller]", name.removesuffix("Controller").lower())
        grpc = re.search(r"(\w+)\.(\w+)Base\b", bases)
        if grpc and grpc.group(1) == grpc.group(2):
            facts.frameworks.add("grpc")
            facts.endpoints.append(grpc_endpoint(grpc.group(1), ev, handler=name))
        client_base = re.search(r"ClientBase<\s*I?(\w+)\s*>", bases)
        if client_base:
            facts.frameworks.add("wcf")
            facts.outbound.append(OutboundCall(kind=OutboundKind.SOAP, target=client_base.group(1),
                                               detail=f"SOAP {client_base.group(1)}.*", technology="wcf",
                                               evidence=ev))
        soap = None
        if "ServiceContract" in names and not client_base:
            soap = names["ServiceContract"].get("Name") or (name[1:] if re.match(r"I[A-Z]", name) else name)
        gql_root = next((GRAPHQL_ROOTS[a] for a in names if a in GRAPHQL_ROOTS), None)
        if "ExtendObjectType" in names and names["ExtendObjectType"].get("value") in GRAPHQL_ROOTS.values():
            gql_root = names["ExtendObjectType"]["value"]
        is_dbcontext = re.search(r"\bDbContext\b", bases) is not None
        is_refit = kind == "interface" and not soap

        body = node.child_by_field_name("body")
        for m in body.named_children if body else []:
            m_attrs = _attributes(m, consts)
            m_names = {a[0]: a[1] for a in m_attrs}
            m_name = text(m.child_by_field_name("name"))
            public = "public" in " ".join(text(c) for c in m.children if c.type == "modifier") or kind == "interface"
            if m.type == "property_declaration" and is_dbcontext and text(m.child_by_field_name("type")) \
                    .startswith("DbSet<"):
                entity = text(m.child_by_field_name("type"))[6:-1]
                facts.outbound.append(OutboundCall(kind=OutboundKind.DATABASE, target="", detail=entity,
                                                   technology="efcore", evidence=ctx.evidence(line_of(m))))
                continue
            if m.type != "method_declaration":
                continue
            handler = f"{name}.{m_name}"
            route = m_names.get("Route", {}).get("value")
            for a_name, args, a_node in m_attrs:
                a_ev = ctx.evidence(line_of(a_node), text(a_node))
                if a_name in HTTP_ATTRIBUTES:
                    sub = args.get("value", route or "")
                    if is_controller:
                        # "/x" and "~/x" are absolute routes; anything else is relative to [Route] on the class
                        full = join_paths(sub.lstrip("~")) if sub.startswith(("/", "~/")) else join_paths(prefix, sub)
                        facts.endpoints.append(Endpoint(method=HTTP_ATTRIBUTES[a_name], path=full, handler=handler,
                                                        evidence=a_ev))
                    elif is_refit and a_name in ("Get", "Post", "Put", "Delete", "Patch"):
                        facts.frameworks.add("refit")
                        facts.outbound.append(OutboundCall(kind=OutboundKind.HTTP, target="",
                                                           detail=f"{HTTP_ATTRIBUTES[a_name]} {join_paths(sub)}",
                                                           technology="refit", evidence=a_ev))
                elif a_name == "OperationContract" and soap:
                    op = args.get("Name") or m_name
                    facts.endpoints.append(Endpoint(method="SOAP", path=f"{soap}.{op}", handler=handler,
                                                    protocol="soap", evidence=a_ev))
            if gql_root and public and m_name:
                facts.endpoints.append(Endpoint(method=gql_root.upper(), path=f"{gql_root}.{_field_name(m_name)}",
                                                handler=handler, protocol="graphql",
                                                evidence=ctx.evidence(line_of(m), ctx.line_text(line_of(m)))))

    # ------------------------------------------------------------------ expressions
    def _invocation(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str],
                    groups: dict[str, str]) -> None:
        fn = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        arg_nodes = args_node.named_children if args_node else []
        args = [_string(a, consts) for a in arg_nodes]
        receiver, method = "", text(fn)
        if fn.type == "member_access_expression":
            receiver, method = text(fn.child_by_field_name("expression")), text(fn.child_by_field_name("name"))
        generics = _generic_args(fn)
        method = method.split("<")[0]
        line = line_of(node)
        ev = ctx.evidence(line, ctx.line_text(line))
        recv = receiver.lower()

        def out(kind: OutboundKind, target: str, technology: str, detail: str = "") -> None:
            facts.outbound.append(OutboundCall(kind=kind, target=target, detail=detail, technology=technology,
                                               evidence=ev))

        if method in MINIMAL_API and args and args[0] is not None:
            prefix = groups.get(receiver.split(".")[-1], "")
            facts.endpoints.append(Endpoint(method=MINIMAL_API[method], path=join_paths(prefix, args[0]),
                                            evidence=ev))
            facts.frameworks.add("aspnetcore")
        elif method == "AddGrpcClient" and generics:
            svc = generics[0].split(".")[-1].removesuffix("Client")
            facts.frameworks.add("grpc")
            facts.outbound.append(grpc_client(svc, ev, "grpc-dotnet"))
        elif method == "ForAddress" and receiver.endswith("GrpcChannel") and args and args[0]:
            out(OutboundKind.GRPC, args[0], "grpc-dotnet", "channel")
        elif method == "For" and receiver.endswith("RestService") and args and args[0]:
            out(OutboundKind.HTTP, args[0], "refit", "base-address")
        elif method in ("Produce", "ProduceAsync") and args and args[0]:
            out(OutboundKind.MESSAGING_PUBLISH, args[0], "kafka")
        elif method == "Subscribe" and "consumer" in recv:
            topics = [a for a in args if a] or [v for n in arg_nodes for c in walk(n)
                                                 if c.type == "string_literal" and (v := _string(c, consts))]
            for t in topics:
                out(OutboundKind.MESSAGING_SUBSCRIBE, t, "kafka")
        elif method == "LoadTable" and len(args) > 1 and args[1]:
            out(OutboundKind.DATABASE, f"dynamodb://{args[1]}", "dynamodb", "LoadTable")
        elif HTTP_CLIENT_METHODS.match(method) and args and args[0] and \
                (URL_RE.search(args[0]) or args[0].startswith("/")) and not any(s in recv for s in AWS_RECEIVER_FIELD):
            verb = HTTP_CLIENT_METHODS.match(method).group(1).upper()
            target = URL_RE.search(args[0]).group(0) if URL_RE.search(args[0]) else ""
            out(OutboundKind.HTTP, target, "httpclient",
                f"{verb} {args[0]}" if not target else ("GET" if verb == "SEND" else verb))
        else:
            for key, field in AWS_RECEIVER_FIELD.items():
                if key in recv and args and args[0] and method.endswith("Async"):
                    if c := aws_call(field, args[0], method.removesuffix("Async"), ev):
                        facts.outbound.append(c)
                    break

    def _creation(self, node: Node, ctx: FileContext, facts: FileFacts, consts: dict[str, str]) -> None:
        type_name = text(node.child_by_field_name("type"))
        simple = type_name.split("<")[0].split(".")[-1]
        args_node = node.child_by_field_name("arguments")
        args = [_string(a, consts) for a in (args_node.named_children if args_node else [])]
        line = line_of(node)
        ev = ctx.evidence(line, ctx.line_text(line))
        init = node.child_by_field_name("initializer")
        if init is not None:
            for assign in (c for c in init.named_children if c.type == "assignment_expression"):
                key = text(assign.child_by_field_name("left"))
                if aws_service(key) and (v := _string(assign.child_by_field_name("right"), consts)):
                    if c := aws_call(key, v, type_name, ev):
                        facts.outbound.append(c)
        if simple.endswith("Client") and "." in type_name and type_name.split(".")[-2] + "Client" == simple:
            facts.frameworks.add("grpc")
            facts.outbound.append(grpc_client(type_name.split(".")[-2], ev, "grpc-dotnet"))
        elif simple == "EndpointAddress" and args and args[0]:
            facts.outbound.append(OutboundCall(kind=OutboundKind.SOAP, target=args[0], technology="wcf", evidence=ev))
        elif simple in ("Uri", "RestClient", "HttpRequestMessage") and any(a and URL_RE.search(a) for a in args):
            url = next(URL_RE.search(a).group(0) for a in args if a and URL_RE.search(a))
            statement = node
            while statement.parent is not None and not statement.type.endswith("statement"):
                statement = statement.parent
            if "Grpc" in text(statement):  # AddGrpcClient<T>(o => o.Address = new Uri(...))
                kind, tech = OutboundKind.GRPC, "grpc-dotnet"
            elif url.lower().endswith((".asmx", ".svc")) or "wsdl" in url.lower():
                kind, tech = OutboundKind.SOAP, "wcf"
            else:
                kind, tech = OutboundKind.HTTP, "httpclient"
            facts.outbound.append(OutboundCall(kind=kind, target=url, technology=tech, evidence=ev))
