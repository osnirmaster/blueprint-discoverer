"""Deterministic cross-repository dependency resolution.

Each rule is named (R-*) and recorded on the link it creates, so every edge in the CALM model can be
traced back to the rule and the source lines that produced it. Confidence values are fixed per rule
(see RULES.md §3) — no heuristics hidden in code paths.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..model import (
    Component,
    DataStore,
    Evidence,
    ExternalSystem,
    Link,
    LinkKind,
    LinkResult,
    OutboundCall,
    OutboundKind,
    RepoScan,
    Topic,
)
from ..workspace import slugify

RULE_CONFIDENCE = {
    "R-HTTP-HOST": 0.95,  # URL host equals a known component alias
    "R-HTTP-CLIENT-NAME": 0.9,  # Feign/named client equals a component alias
    "R-HTTP-ENV-NAME": 0.7,  # ${ACCOUNTS_URL} -> 'accounts' fuzzy alias match
    "R-HTTP-PATH": 0.5,  # host unknown, path matches exactly one component's endpoint template
    "R-HTTP-EXTERNAL": 0.8,  # host does not belong to any scanned component
    "R-ASYNC-TOPIC": 0.95,  # same topic name on both sides
    "R-DATA-DSN": 0.95,  # JDBC/DSN in config or code
    "R-DATA-ENTITY": 0.6,  # ORM entity without explicit datasource
}
PROTOCOL = {"http": "HTTP", "https": "HTTPS", "kafka": "TCP", "rabbitmq": "AMQP", "jms": "TCP"}
DB_PROTOCOL = "JDBC"
PLACEHOLDER_RE = re.compile(r"[{$]\{?(\w+)\}?")


def _host(target: str) -> str:
    if "://" not in target:
        return ""
    return (urlparse(target.replace("jdbc:", "", 1)).hostname or "").lower()


def _path(target: str) -> str:
    return urlparse(target).path if "://" in target else ""


def template_regex(path: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\}|:[A-Za-z_]\w*)", path.rstrip("/"))
    rx = "".join("[^/]+" if (p.startswith(("{", ":"))) else re.escape(p) for p in parts)
    return re.compile("^" + rx + "/?$")


class DependencyResolver:
    def __init__(self, scans: list[RepoScan]):
        self.components: list[Component] = [c for s in scans for c in s.components]
        self.alias_index: dict[str, str] = {}
        for c in self.components:
            for a in {*c.aliases, c.id, c.name}:
                self.alias_index.setdefault(a.lower(), c.id)
        self.result = LinkResult()
        self._links: dict[str, Link] = {}
        self._datastores: dict[str, DataStore] = {}
        self._topics: dict[str, Topic] = {}
        self._externals: dict[str, ExternalSystem] = {}

    # ---------------------------------------------------------------- public
    def resolve(self) -> LinkResult:
        for comp in sorted(self.components, key=lambda c: c.id):
            for call in comp.outbound:
                if call.kind == OutboundKind.HTTP:
                    self._http(comp, call)
                elif call.kind in (OutboundKind.MESSAGING_PUBLISH, OutboundKind.MESSAGING_SUBSCRIBE):
                    self._messaging(comp, call)
                elif call.kind == OutboundKind.DATABASE:
                    self._database(comp, call)
            self._entities_without_dsn(comp)
        self.result.links = sorted(self._links.values(), key=lambda l: l.id)
        self.result.datastores = sorted(self._datastores.values(), key=lambda d: d.id)
        self.result.topics = sorted(self._topics.values(), key=lambda t: t.id)
        self.result.externals = sorted(self._externals.values(), key=lambda e: e.id)
        return self.result

    # ---------------------------------------------------------------- helpers
    def _add(self, kind: LinkKind, source: str, dest: str, rule: str, ev: Evidence, protocol: str = "",
             description: str = "", via: str = "") -> None:
        if source == dest:
            return
        lid = slugify(f"{source}-{kind.value}-{dest}")
        link = self._links.get(lid)
        if link is None:
            link = self._links[lid] = Link(
                id=lid, kind=kind, source=source, destination=dest, protocol=protocol, via=via,
                description=description, confidence=RULE_CONFIDENCE[rule], rule=rule,
            )
        elif RULE_CONFIDENCE[rule] > link.confidence:
            link.confidence, link.rule = RULE_CONFIDENCE[rule], rule
        if description and description not in link.description.split("; "):
            link.description = "; ".join(filter(None, [link.description, description]))
        if ev.ref() not in {e.ref() for e in link.evidence}:
            link.evidence.append(ev)

    def _match_alias(self, name: str) -> str | None:
        name = name.lower()
        if name in self.alias_index:
            return self.alias_index[name]
        slug = slugify(name)
        return self.alias_index.get(slug)

    def _match_env_name(self, var: str) -> str | None:
        stem = re.sub(r"_(SERVICE_)?(URL|URI|HOST|BASE_URL|ENDPOINT|API)$", "", var.upper()).lower()
        stem = slugify(stem)
        hits = {cid for alias, cid in self.alias_index.items()
                if slugify(alias) in (stem, f"{stem}-service", f"{stem}-api")
                or slugify(alias).removesuffix("-service") == stem}
        return hits.pop() if len(hits) == 1 else None

    def _match_path(self, path: str, exclude: str) -> str | None:
        if not path or path == "/":
            return None
        hits = {c.id for c in self.components if c.id != exclude
                for e in c.endpoints if template_regex(e.path).match(path) or
                template_regex(path).match(e.path)}
        return hits.pop() if len(hits) == 1 else None

    # ---------------------------------------------------------------- rules
    def _http(self, comp: Component, call: OutboundCall) -> None:
        method_path = call.detail if " " in call.detail else f"{call.detail} {_path(call.target)}".strip()
        host = _host(call.target)
        if host:
            target = self._match_alias(host)
            if target:
                self._add(LinkKind.HTTP, comp.id, target, "R-HTTP-HOST", call.evidence,
                          PROTOCOL.get(urlparse(call.target).scheme, "HTTP"), method_path)
                return
            target = self._match_path(_path(call.target), comp.id)
            if target:
                self._add(LinkKind.HTTP, comp.id, target, "R-HTTP-PATH", call.evidence, "HTTP", method_path)
                return
            ext_id = "ext-" + slugify(host)
            self._externals.setdefault(ext_id, ExternalSystem(id=ext_id, name=host, target=call.target))
            self._externals[ext_id].evidence.append(call.evidence)
            self._add(LinkKind.EXTERNAL, comp.id, ext_id, "R-HTTP-EXTERNAL", call.evidence,
                      PROTOCOL.get(urlparse(call.target).scheme, "HTTPS"), method_path)
            return
        # named client (Feign) or bare alias
        target = self._match_alias(call.target)
        if target:
            self._add(LinkKind.HTTP, comp.id, target, "R-HTTP-CLIENT-NAME", call.evidence, "HTTP", method_path)
            return
        placeholder = PLACEHOLDER_RE.search(call.target)
        if placeholder and (target := self._match_env_name(placeholder.group(1))):
            self._add(LinkKind.HTTP, comp.id, target, "R-HTTP-ENV-NAME", call.evidence, "HTTP", method_path)
            return
        self.result.unresolved.append(call)

    def _messaging(self, comp: Component, call: OutboundCall) -> None:
        tid = "topic-" + slugify(call.target)
        topic = self._topics.setdefault(tid, Topic(id=tid, name=call.target, technology=call.technology))
        if call.evidence.ref() not in {e.ref() for e in topic.evidence}:
            topic.evidence.append(call.evidence)
        proto = PROTOCOL.get(call.technology, "TCP")
        if call.kind == OutboundKind.MESSAGING_PUBLISH:
            self._add(LinkKind.ASYNC, comp.id, tid, "R-ASYNC-TOPIC", call.evidence, proto,
                      f"publishes to {call.target}", via=tid)
        else:
            self._add(LinkKind.ASYNC, tid, comp.id, "R-ASYNC-TOPIC", call.evidence, proto,
                      f"consumed from {call.target}", via=tid)

    def _datastore_id(self, target: str) -> tuple[str, str]:
        raw = target.replace("jdbc:", "", 1)
        parsed = urlparse(raw.replace("oracle:thin:@", "oracle://"))
        host = parsed.hostname or "unknown"
        db = parsed.path.strip("/").split("/")[0] or host
        return "db-" + slugify(f"{host}-{db}" if db != host else host), f"{db}@{host}"

    def _database(self, comp: Component, call: OutboundCall) -> None:
        if not call.target:
            return  # entities are handled once the component's datasources are known
        did, name = self._datastore_id(call.target)
        ds = self._datastores.setdefault(did, DataStore(id=did, name=name, technology=call.technology))
        ds.evidence.append(call.evidence)
        self._add(LinkKind.DATA, comp.id, did, "R-DATA-DSN", call.evidence, DB_PROTOCOL,
                  f"reads/writes {name}")

    def _entities_without_dsn(self, comp: Component) -> None:
        entities = [c for c in comp.outbound if c.kind == OutboundKind.DATABASE and not c.target]
        if not entities:
            return
        own = [l for l in self._links.values() if l.source == comp.id and l.kind == LinkKind.DATA]
        if own:
            for link in own:
                for e in entities:
                    if e.evidence.ref() not in {x.ref() for x in link.evidence}:
                        link.evidence.append(e.evidence)
                tables = sorted({e.detail for e in entities})
                link.description = f"{link.description} (tables: {', '.join(tables)})"
            return
        did = f"db-{comp.id}"
        self._datastores.setdefault(did, DataStore(id=did, name=f"{comp.name} database",
                                                   technology=entities[0].technology))
        for e in entities:
            self._add(LinkKind.DATA, comp.id, did, "R-DATA-ENTITY", e.evidence, DB_PROTOCOL,
                      f"persists {e.detail}")


def resolve(scans: list[RepoScan]) -> LinkResult:
    return DependencyResolver(scans).resolve()
