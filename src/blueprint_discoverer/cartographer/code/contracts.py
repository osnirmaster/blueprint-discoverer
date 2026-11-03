"""Interface contracts that live next to the code: Protocol Buffers, GraphQL SDL/operations and WSDL.

They are declarative formats, so a small deterministic parser is enough; every fact keeps its line.
Contracts say *what* an interface looks like; whether a component serves or consumes it is decided by
the code (see scanner._apply_contracts).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ...model import Endpoint, Evidence, OutboundCall
from .extractors.base import graphql_calls

PROTO_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
PROTO_SERVICE_RE = re.compile(r"\bservice\s+(\w+)\s*\{")
PROTO_RPC_RE = re.compile(r"\brpc\s+(\w+)\s*\(")
SDL_ROOT_RE = re.compile(r"\b(?:extend\s+)?type\s+(Query|Mutation|Subscription)\b[^{]*\{")
SDL_FIELD_RE = re.compile(r"^\s*(\w+)\s*[(:]", re.M)
CONTRACT_SUFFIXES = (".proto", ".graphql", ".graphqls", ".gql", ".wsdl")


@dataclass
class ProtoService:
    package: str
    name: str
    methods: list[tuple[str, int]]  # (rpc name, line)
    evidence: Evidence

    @property
    def full_name(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name


@dataclass
class WsdlService:
    name: str
    location: str
    operations: list[str]
    evidence: Evidence


@dataclass
class ContractFacts:
    protos: list[ProtoService] = field(default_factory=list)
    graphql_schema: list[Endpoint] = field(default_factory=list)  # server side (SDL root types)
    graphql_operations: list[OutboundCall] = field(default_factory=list)  # client side (operation documents)
    wsdl: list[WsdlService] = field(default_factory=list)


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _block(content: str, start: int) -> str:
    """Text of the {...} block whose opening brace is at content[start - 1]."""
    depth, i = 1, start
    while i < len(content) and depth:
        depth += {"{": 1, "}": -1}.get(content[i], 0)
        i += 1
    return content[start:i - 1]


def _strip_comments(content: str, line_prefix: str) -> str:
    # keep offsets stable: blank out comments instead of removing them
    content = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), content, flags=re.S)
    return re.sub(rf"{re.escape(line_prefix)}[^\n]*", lambda m: " " * len(m.group(0)), content)


def parse_proto(content: str, repo: str, rel: str) -> list[ProtoService]:
    content = _strip_comments(content, "//")
    package = (m.group(1) if (m := PROTO_PACKAGE_RE.search(content)) else "")
    out = []
    for m in PROTO_SERVICE_RE.finditer(content):
        body = _block(content, m.end())
        methods = [(r.group(1), _line(content, m.end() + r.start())) for r in PROTO_RPC_RE.finditer(body)]
        line = _line(content, m.start())
        out.append(ProtoService(package=package, name=m.group(1), methods=methods,
                                evidence=Evidence(repo=repo, path=rel, line=line, snippet=f"service {m.group(1)}")))
    return out


def parse_graphql(content: str, repo: str, rel: str) -> tuple[list[Endpoint], list[OutboundCall]]:
    content = _strip_comments(content, "#")
    endpoints: list[Endpoint] = []
    for m in SDL_ROOT_RE.finditer(content):
        root, body = m.group(1), _block(content, m.end())
        body_flat = re.sub(r"\([^)]*\)", lambda x: re.sub(r"[^\n]", " ", x.group(0)), body)  # drop arguments
        for f in SDL_FIELD_RE.finditer(body_flat):
            line = _line(content, m.end() + f.start(1))
            endpoints.append(Endpoint(
                method=root.upper(), path=f"{root}.{f.group(1)}", protocol="graphql",
                evidence=Evidence(repo=repo, path=rel, line=line, snippet=f"{root}.{f.group(1)}")))
    if endpoints:
        return endpoints, []
    ev = Evidence(repo=repo, path=rel, line=1, snippet="GraphQL operations")
    return [], graphql_calls(content, "", ev)


def parse_wsdl(content: str, repo: str, rel: str) -> list[WsdlService]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    operations = sorted({op.get("name", "") for pt in root.iter() if local(pt.tag) == "portType"
                         for op in pt if local(op.tag) == "operation" and op.get("name")})
    out = []
    for svc in (e for e in root.iter() if local(e.tag) == "service"):
        name = svc.get("name", "")
        location = next((a.get("location", "") for a in svc.iter() if local(a.tag) == "address"), "")
        line = next((i for i, text in enumerate(content.splitlines(), 1) if f'name="{name}"' in text
                     and "service" in text), 1)
        out.append(WsdlService(name=name, location=location, operations=operations,
                               evidence=Evidence(repo=repo, path=rel, line=line, snippet=f"service {name}")))
    return out


def scan_contract_file(content: str, repo: str, rel: str, facts: ContractFacts) -> None:
    lower = rel.lower()
    if lower.endswith(".proto"):
        facts.protos += parse_proto(content, repo, rel)
    elif lower.endswith((".graphql", ".graphqls", ".gql")):
        endpoints, calls = parse_graphql(content, repo, rel)
        facts.graphql_schema += endpoints
        facts.graphql_operations += calls
    elif lower.endswith(".wsdl"):
        facts.wsdl += parse_wsdl(content, repo, rel)
