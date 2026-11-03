"""Extractor contract and tree-sitter helpers.

Extractors are deterministic: same bytes in, same facts out. They never guess business meaning;
they only record what the syntax tree proves (annotations, decorators, literal URLs, topic names...).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from tree_sitter import Node

from ....model import CodeSymbol, Endpoint, Evidence, OutboundCall, OutboundKind

URL_RE = re.compile(r"https?://[^\s\"'`]+")
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


@dataclass
class FileFacts:
    endpoints: list[Endpoint] = field(default_factory=list)
    outbound: list[OutboundCall] = field(default_factory=list)
    symbols: list[CodeSymbol] = field(default_factory=list)
    packages: set[str] = field(default_factory=set)
    frameworks: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)


@dataclass
class FileContext:
    repo: str
    path: str  # repo-relative, posix
    source: bytes

    def evidence(self, line: int, snippet: str = "") -> Evidence:
        return Evidence(repo=self.repo, path=self.path, line=line, snippet=snippet.strip()[:160])

    def line_text(self, line: int) -> str:
        lines = self.source.decode("utf-8", "replace").splitlines()
        return lines[line - 1] if 0 < line <= len(lines) else ""


class Extractor(Protocol):
    language: str
    extensions: tuple[str, ...]

    def extract(self, ctx: FileContext) -> FileFacts: ...


# --- tree-sitter helpers -------------------------------------------------------------------------


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""


def line_of(node: Node) -> int:
    return node.start_point.row + 1


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'`" and value[-1] == value[0]:
        return value[1:-1]
    return value


def join_paths(*parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined if joined else "/"


def first_child(node: Node, *types: str) -> Node | None:
    for c in node.children:
        if c.type in types:
            return c
    return None


# --- cross-language helpers: AWS SDK resources, gRPC, GraphQL ------------------------------------

# request field (any casing: TableName, tableName, table_name) -> AWS service
AWS_FIELDS = {
    "tablename": "dynamodb", "bucket": "s3", "bucketname": "s3", "queueurl": "sqs", "queuename": "sqs",
    "topicarn": "sns", "targetarn": "sns", "streamname": "kinesis", "streamarn": "kinesis",
}
SUBSCRIBE_HINTS = {
    "sqs": ("receive", "listener", "poll", "consume", "deletemessage"),
    "kinesis": ("getrecords", "getsharditerator", "subscribetoshard", "consumer", "listener", "kcl"),
    "sns": (),
}


def aws_service(field_name: str) -> str | None:
    return AWS_FIELDS.get(field_name.replace("_", "").lower())


def aws_resource_name(value: str) -> str:
    """Physical name from a name, SQS URL, ARN or s3:// URL (placeholders are kept)."""
    v = value.strip()
    if v.startswith("s3://"):
        return v[5:].split("/")[0]
    if v.startswith("arn:"):
        last = v.split(":", 5)[-1]
        return last.split("/")[0] if v.startswith("arn:aws:s3") else last.split("/")[-1]
    if "://" in v and "amazonaws.com" in v:
        return v.rstrip("/").rsplit("/", 1)[-1]
    return v


def aws_call(field_name: str, value: str, operation: str, evidence: Evidence) -> OutboundCall | None:
    """OutboundCall for an AWS SDK request field. `operation` is any text naming the call
    (SendMessageRequest, receive_message, PutItemCommand...) and decides the direction."""
    service = aws_service(field_name)
    if service is None or not value:
        return None
    name = aws_resource_name(value)
    op = operation.replace("_", "").lower()
    # SendMessageRequest / PutItemInput / PublishCommand -> SendMessage / PutItem / Publish
    named = re.search(r"([A-Z]\w+?)(?:Request|Input|Command)\b", operation)
    operation = named.group(1) if named else operation.strip()[:40]
    if service in ("dynamodb", "s3"):
        return OutboundCall(kind=OutboundKind.DATABASE, target=f"{service}://{name}", detail=operation,
                            technology=service, evidence=evidence)
    subscribe = any(h in op for h in SUBSCRIBE_HINTS[service])
    return OutboundCall(kind=OutboundKind.MESSAGING_SUBSCRIBE if subscribe else OutboundKind.MESSAGING_PUBLISH,
                        target=name, detail=operation, technology=service, evidence=evidence)


GRAPHQL_OP_RE = re.compile(r"\b(query|mutation|subscription)\b[^{]*\{", re.I)


def graphql_operation_fields(document: str) -> list[tuple[str, str]]:
    """[(operation, root field)] for every operation in a GraphQL document (client side)."""
    out: set[tuple[str, str]] = set()
    for m in GRAPHQL_OP_RE.finditer(document):
        depth, top = 1, []
        for ch in document[m.end():]:
            if ch in "{(":
                depth += 1
            elif ch in "})":
                depth -= 1
                if depth == 0:
                    break
            elif depth == 1:
                top.append(ch)
            elif ch == "\n":
                top.append(" ")
        # depth-1 text keeps root fields, aliases, directives and fragment spreads only
        for alias, name in re.findall(r"(?<![.@$\w])(\w+)\s*(?::\s*(\w+))?", "".join(top)):
            out.add((m.group(1).upper(), name or alias))
    return sorted(out)


def graphql_calls(document: str, target: str, evidence: Evidence, technology: str = "graphql") -> list[OutboundCall]:
    root = {"QUERY": "Query", "MUTATION": "Mutation", "SUBSCRIPTION": "Subscription"}
    return [OutboundCall(kind=OutboundKind.GRAPHQL, target=target, detail=f"{op} {root[op]}.{field}",
                         technology=technology, evidence=evidence)
            for op, field in graphql_operation_fields(document)]


def looks_like_graphql(value: str) -> bool:
    return bool(re.match(r"\s*(query|mutation|subscription)\b[^{]*\{", value))


def grpc_endpoint(service: str, evidence: Evidence, handler: str = "") -> Endpoint:
    """Placeholder endpoint for an implemented gRPC service; the scanner expands it with the .proto methods."""
    return Endpoint(method="RPC", path=f"/{service}/*", handler=handler, protocol="grpc", evidence=evidence)


def grpc_client(service: str, evidence: Evidence, technology: str = "grpc") -> OutboundCall:
    return OutboundCall(kind=OutboundKind.GRPC, target=service, detail=f"RPC /{service}/*",
                        technology=technology, evidence=evidence)
