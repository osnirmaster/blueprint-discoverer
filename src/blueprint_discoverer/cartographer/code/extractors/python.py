"""Python extractor (CPython `ast`: the reference grammar, no third-party parser needed).

Recognised facts:
  * FastAPI/Flask decorators @app.get('/x'), @router.post, @bp.route(methods=[...]) -> endpoints
  * APIRouter(prefix=...) / Blueprint(url_prefix=...) prefixes
  * requests/httpx/aiohttp calls with resolvable URLs                               -> outbound HTTP
  * kafka-python / confluent-kafka producers and consumers                          -> pub / sub
  * SQLAlchemy models (__tablename__) and create_engine("postgresql://...")         -> persistence
Module constants (including os.getenv("X", "http://default")) are propagated into f-strings.
"""

from __future__ import annotations

import ast

from ....model import CodeSymbol, Endpoint, OutboundCall, OutboundKind
from .base import HTTP_METHODS, URL_RE, FileContext, FileFacts, join_paths

HTTP_CLIENT_MODULES = {"requests", "httpx", "client", "session", "http", "aiohttp"}
FRAMEWORK_IMPORTS = {
    "fastapi": "fastapi", "flask": "flask", "django": "django", "kafka": "kafka-python",
    "confluent_kafka": "confluent-kafka", "sqlalchemy": "sqlalchemy", "httpx": "httpx",
    "requests": "requests", "pika": "rabbitmq", "celery": "celery", "pydantic": "pydantic",
    "boto3": "aws-sdk", "psycopg2": "postgresql", "psycopg": "postgresql", "pymongo": "mongodb",
}
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
        return facts

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
        elif attr == "create_engine" and first and first.split(":")[0].split("+")[0] in DB_SCHEMES:
            facts.outbound.append(OutboundCall(
                kind=OutboundKind.DATABASE, target=first, technology=first.split(":")[0].split("+")[0],
                evidence=ev))
