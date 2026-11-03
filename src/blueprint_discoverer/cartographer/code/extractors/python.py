"""Python extractor (CPython `ast`: the reference grammar, no third-party parser needed).

Recognised facts:
  * FastAPI/Flask decorators @app.get('/x'), @router.post, @bp.route(methods=[...]) -> endpoints
  * APIRouter(prefix=...) / Blueprint(url_prefix=...) prefixes
  * requests/httpx/aiohttp calls with resolvable URLs                               -> outbound HTTP
  * kafka-python / confluent-kafka producers and consumers                          -> pub / sub
  * SQLAlchemy models (__tablename__) and create_engine("postgresql://...")         -> persistence
  * gRPC: add_XServicer_to_server / XServicer subclasses (server); x_pb2_grpc.XStub(channel),
    grpc.insecure_channel("host:port") (client)
  * GraphQL: strawberry @strawberry.type Query/Mutation, graphene ObjectType roots, SDL strings
    (ariadne) (server); operation documents in strings, e.g. gql("query {...}") (client)
  * SOAP: spyne ServiceBase + @rpc (server); zeep Client("...?wsdl") (client)
  * boto3: TableName/Bucket/QueueUrl/TopicArn/StreamName arguments, resource.Table("x")  -> cloud resources
Module constants (including os.getenv("X", "http://default")) are propagated into f-strings.
"""

from __future__ import annotations

import ast
import re

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
    looks_like_graphql,
)

HTTP_CLIENT_MODULES = {"requests", "httpx", "client", "session", "http", "aiohttp"}
FRAMEWORK_IMPORTS = {
    "fastapi": "fastapi", "flask": "flask", "django": "django", "kafka": "kafka-python",
    "confluent_kafka": "confluent-kafka", "sqlalchemy": "sqlalchemy", "httpx": "httpx",
    "requests": "requests", "pika": "rabbitmq", "celery": "celery", "pydantic": "pydantic",
    "boto3": "aws-sdk", "psycopg2": "postgresql", "psycopg": "postgresql", "pymongo": "mongodb",
    "grpc": "grpc", "strawberry": "strawberry", "graphene": "graphene", "ariadne": "ariadne", "gql": "gql",
    "zeep": "zeep", "spyne": "spyne",
}
GRAPHQL_ROOTS = ("Query", "Mutation", "Subscription")
DB_SCHEMES = ("postgresql", "postgres", "mysql", "oracle", "mssql", "sqlite", "mongodb", "redis", "db2")


def _const_str(node: ast.AST, consts: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.Attribute):
        return consts.get(ast.unparse(node))
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                inner = _const_str(v.value, consts)
                out.append(inner if inner is not None else "{" + ast.unparse(v.value) + "}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _const_str(node.left, consts), _const_str(node.right, consts)
        return (left or "") + (right or "") if (left or right) else None
    if isinstance(node, ast.Call):  # os.getenv("X", "default") / os.environ.get("X", "default")
        fn = ast.unparse(node.func)
        if fn in ("os.getenv", "os.environ.get", "getenv", "environ.get") and len(node.args) >= 2:
            return _const_str(node.args[1], consts)
    if isinstance(node, ast.BoolOp):  # os.getenv("X") or "default"
        vals = [_const_str(v, consts) for v in node.values]
        vals = [v for v in vals if v]
        return vals[-1] if vals else None
    return None


class PythonExtractor:
    language = "python"
    extensions = (".py",)

    def extract(self, ctx: FileContext) -> FileFacts:
        facts = FileFacts()
        try:
            tree = ast.parse(ctx.source.decode("utf-8", "replace"), filename=ctx.path)
        except SyntaxError:
            return facts
        consts: dict[str, str] = {}
        prefixes: dict[str, str] = {}
        self._producers: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self._import(a.name, facts)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self._import(node.module, facts)

        # constants and router prefixes, in source order so later definitions can reuse earlier ones
        for node in [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))]:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is None:
                continue
            for t in targets:
                key = ast.unparse(t)
                v = _const_str(node.value, consts)
                if v is not None:
                    consts[key] = v
                if isinstance(node.value, ast.Call):
                    fn = ast.unparse(node.value.func).split(".")[-1]
                    if fn in ("KafkaProducer", "Producer", "AIOKafkaProducer"):
                        self._producers.add(key)
                    if fn in ("APIRouter", "Blueprint"):
                        for kw in node.value.keywords:
                            if kw.arg in ("prefix", "url_prefix"):
                                prefixes[key] = _const_str(kw.value, consts) or ""

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._class(node, ctx, facts)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._function(node, ctx, facts, consts, prefixes)
            elif isinstance(node, ast.Call):
                self._call(node, ctx, facts, consts)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "{" in node.value:
                self._graphql_document(node, ctx, facts)
        return facts

    @staticmethod
    def _graphql_document(node: ast.Constant, ctx: FileContext, facts: FileFacts) -> None:
        from ..contracts import parse_graphql  # SDL strings (ariadne/gql type_defs)

        value = node.value
        if re.search(r"\btype\s+(Query|Mutation|Subscription)\b", value):
            endpoints, _ = parse_graphql(value, ctx.repo, ctx.path)
            for e in endpoints:
                e.evidence = ctx.evidence(node.lineno + e.evidence.line - 1, e.path)
            facts.endpoints += endpoints
        elif looks_like_graphql(value):
            facts.outbound += graphql_calls(value, "", ctx.evidence(node.lineno), "graphql-client")

    @staticmethod
    def _import(module: str, facts: FileFacts) -> None:
        facts.imports.add(module)
        top = module.split(".")[0]
        if top in FRAMEWORK_IMPORTS:
            facts.frameworks.add(FRAMEWORK_IMPORTS[top])

    def _class(self, node: ast.ClassDef, ctx: FileContext, facts: FileFacts) -> None:
        bases = [ast.unparse(b) for b in node.bases]
        kind = "class"
        table = ""
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and any(ast.unparse(t) == "__tablename__" for t in stmt.targets):
                kind, table = "entity", _const_str(stmt.value, {}) or ""
        facts.symbols.append(CodeSymbol(
            kind=kind, name=node.name, annotations=[ast.unparse(d) for d in node.decorator_list] + bases,
            evidence=ctx.evidence(node.lineno, ctx.line_text(node.lineno)),
        ))
        if kind == "entity":
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.DATABASE, target="", detail=table or node.name, technology="sqlalchemy",
                evidence=ctx.evidence(node.lineno, f"class {node.name} (__tablename__={table})"),
            ))
        ev = ctx.evidence(node.lineno, ctx.line_text(node.lineno))
        for b in bases:
            if b.endswith("Servicer") and "pb2_grpc" in b:
                facts.endpoints.append(grpc_endpoint(b.split(".")[-1].removesuffix("Servicer"), ev, node.name))
        decorators = [ast.unparse(d) for d in node.decorator_list]
        is_graphql_root = node.name in GRAPHQL_ROOTS and (
            any(d.startswith("strawberry.type") for d in decorators) or any("ObjectType" in b for b in bases))
        is_spyne = any(b.endswith("ServiceBase") for b in bases)
        for stmt in node.body:
            if is_graphql_root and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.AnnAssign, ast.Assign)):
                names = [stmt.name] if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) else \
                    [ast.unparse(t) for t in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target])]
                for n in names:
                    if n.startswith(("_", "resolve_")):
                        continue
                    field = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), n)  # snake_case -> camelCase
                    facts.endpoints.append(Endpoint(
                        method=node.name.upper(), path=f"{node.name}.{field}", handler=f"{node.name}.{n}",
                        protocol="graphql", evidence=ctx.evidence(stmt.lineno, ctx.line_text(stmt.lineno))))
            if is_spyne and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    any(ast.unparse(d).split("(")[0].endswith("rpc") for d in stmt.decorator_list):
                facts.endpoints.append(Endpoint(
                    method="SOAP", path=f"{node.name}.{stmt.name}", handler=f"{node.name}.{stmt.name}",
                    protocol="soap", evidence=ctx.evidence(stmt.lineno, ctx.line_text(stmt.lineno))))

    def _function(self, node, ctx: FileContext, facts: FileFacts, consts, prefixes) -> None:
        facts.symbols.append(CodeSymbol(kind="function", name=node.name, evidence=ctx.evidence(node.lineno)))
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            owner, attr = ast.unparse(dec.func.value), dec.func.attr
            path = _const_str(dec.args[0], consts) if dec.args else None
            if path is None or not (path.startswith("/") or (path == "" and owner in prefixes)):
                continue
            if attr in HTTP_METHODS:
                methods = [attr.upper()]
            elif attr in ("route", "api_route"):
                methods = ["GET"]
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [str(_const_str(e, consts)).upper() for e in kw.value.elts]
            else:
                continue
            for m in methods:
                facts.endpoints.append(Endpoint(
                    method=m, path=join_paths(prefixes.get(owner, ""), path), handler=node.name,
                    evidence=ctx.evidence(dec.lineno, ctx.line_text(dec.lineno)),
                ))

    def _call(self, node: ast.Call, ctx: FileContext, facts: FileFacts, consts: dict[str, str]) -> None:
        fn = ast.unparse(node.func)
        parts = fn.split(".")
        owner, attr = (parts[-2] if len(parts) > 1 else ""), parts[-1]
        ev = ctx.evidence(node.lineno, ctx.line_text(node.lineno))
        for kw in node.keywords:  # boto3: client.send_message(QueueUrl=...), table args, etc.
            if kw.arg and aws_service(kw.arg) and (v := _const_str(kw.value, consts)):
                if c := aws_call(kw.arg, v, attr, ev):
                    facts.outbound.append(c)
        first = _const_str(node.args[0], consts) if node.args else None
        if first is None:
            for kw in node.keywords:
                if kw.arg in ("url", "topic"):
                    first = _const_str(kw.value, consts)

        if attr in HTTP_METHODS | {"request"} and (owner in HTTP_CLIENT_MODULES or owner.endswith("client")):
            if attr == "request" and len(node.args) >= 2:
                first = _const_str(node.args[1], consts)
            if first and (URL_RE.search(first) or first.startswith("{")):
                facts.outbound.append(OutboundCall(
                    kind=OutboundKind.HTTP, target=first, detail=attr.upper(), technology=owner or "http",
                    evidence=ev))
        elif attr in ("send", "produce") and first and (
                owner in self._producers or "producer" in owner.lower() or "kafka" in owner.lower()):
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.MESSAGING_PUBLISH, target=first, technology="kafka", evidence=ev))
        elif attr in ("KafkaConsumer", "Consumer") and node.args:
            for a in node.args:
                topic = _const_str(a, consts)
                if topic:
                    facts.outbound.append(OutboundCall(
                        kind=OutboundKind.MESSAGING_SUBSCRIBE, target=topic, technology="kafka", evidence=ev))
        elif attr == "subscribe" and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
            for e in node.args[0].elts:
                topic = _const_str(e, consts)
                if topic:
                    facts.outbound.append(OutboundCall(
                        kind=OutboundKind.MESSAGING_SUBSCRIBE, target=topic, technology="kafka", evidence=ev))
        elif attr.startswith("add_") and attr.endswith("Servicer_to_server"):
            facts.endpoints.append(grpc_endpoint(attr[4:].removesuffix("Servicer_to_server"), ev))
        elif attr.endswith("Stub") and owner.endswith("pb2_grpc"):
            facts.outbound.append(grpc_client(attr.removesuffix("Stub"), ev))
        elif attr in ("insecure_channel", "secure_channel") and first:
            facts.outbound.append(OutboundCall(kind=OutboundKind.GRPC, target=f"grpc://{first}", detail="channel",
                                               technology="grpc", evidence=ev))
        elif attr == "Client" and owner in ("zeep", "") and (first or any(k.arg == "wsdl" for k in node.keywords)):
            wsdl = first or next(_const_str(k.value, consts) for k in node.keywords if k.arg == "wsdl")
            if wsdl and URL_RE.search(wsdl):
                facts.outbound.append(OutboundCall(kind=OutboundKind.SOAP, target=wsdl, technology="zeep",
                                                   evidence=ev))
        elif attr == "urlopen" and first and URL_RE.search(first):
            facts.outbound.append(OutboundCall(kind=OutboundKind.HTTP, target=first, detail="GET",
                                               technology="urllib", evidence=ev))
        elif attr == "Table" and node.args and first and not owner[:1].isupper():  # boto3 resource("dynamodb").Table
            facts.outbound.append(OutboundCall(kind=OutboundKind.DATABASE, target=f"dynamodb://{first}",
                                               detail="Table", technology="dynamodb", evidence=ev))
        elif attr == "Bucket" and node.args and first:
            facts.outbound.append(OutboundCall(kind=OutboundKind.DATABASE, target=f"s3://{first}",
                                               detail="Bucket", technology="s3", evidence=ev))
        elif attr == "create_engine" and first and first.split(":")[0].split("+")[0] in DB_SCHEMES:
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.DATABASE, target=first, technology=first.split(":")[0].split("+")[0],
                evidence=ev))
