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
    InfraResource,
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
    "R-GRPC-HOST": 0.95,  # gRPC channel address host equals a component alias
    "R-GRPC-SERVICE": 0.9,  # client stub service == service implemented by exactly one component
    "R-GRPC-EXTERNAL": 0.8,  # gRPC address host does not belong to any scanned component
    "R-GRAPHQL-FIELD": 0.8,  # operation root field exposed by exactly one component
    "R-SOAP-SERVICE": 0.85,  # SOAP client service/contract name == service exposed by one component
    "R-DATA-CLOUD": 0.9,  # AWS SDK call naming a DynamoDB table / S3 bucket
    "R-CLOUD-IAC-SUFFIX": 0.75,  # SDK name matches an IaC resource only up to an environment prefix/suffix
    "R-IAC-SUBSCRIPTION": 0.95,  # SNS -> SQS/Lambda subscription declared in IaC
    "R-IAC-EVENT-SOURCE": 0.85,  # SQS/Kinesis/DynamoDB/SNS/S3 event source feeding a component's function
    "R-IAC-ENV-REF": 0.7,  # resource referenced by a component function's environment (direction unknown)
}
PROTOCOL = {"http": "HTTP", "https": "HTTPS", "kafka": "TCP", "rabbitmq": "AMQP", "jms": "TCP", "grpc": "HTTP",
            "sqs": "HTTPS", "sns": "HTTPS", "kinesis": "HTTPS", "dynamodb": "HTTPS", "s3": "HTTPS"}
CLOUD_TECH = {"dynamodb-table": "dynamodb", "s3-bucket": "s3", "sqs-queue": "sqs", "sns-topic": "sns",
              "kinesis-stream": "kinesis"}
TOPIC_KIND = {"sqs": "queue", "rabbitmq": "queue", "jms": "queue", "kinesis": "stream", "sns": "topic"}
REMOTE_KIND = {OutboundKind.HTTP: LinkKind.HTTP, OutboundKind.GRAPHQL: LinkKind.GRAPHQL,
               OutboundKind.SOAP: LinkKind.SOAP, OutboundKind.GRPC: LinkKind.GRPC}
DB_PROTOCOL = "JDBC"
PLACEHOLDER_RE = re.compile(r"[{$]\{?(\w+)\}?")


def _host(target: str) -> str:
    if "://" not in target:
        return ""
    return (urlparse(target.replace("jdbc:", "", 1)).hostname or "").lower()


def _path(target: str) -> str:
    return urlparse(target).path if "://" in target else ""


def cloud_key(name: str) -> str:
    """Environment-independent key of a cloud resource name: placeholders are dropped."""
    stripped = re.sub(r"\$\{[^}]*\}|\{[^}]*\}|%[sd]", "", name)
    key = slugify(stripped)
    return key if key != "unnamed" else slugify(name)


def template_regex(path: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\}|:[A-Za-z_]\w*)", path.rstrip("/"))
    rx = "".join("[^/]+" if (p.startswith(("{", ":"))) else re.escape(p) for p in parts)
    return re.compile("^" + rx + "/?$")


class DependencyResolver:
    def __init__(self, scans: list[RepoScan]):
        self.scans = scans
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
        self._iac: dict[tuple[str, str], InfraResource] = {}  # (technology, cloud_key) -> declaration
        self._iac_nodes: dict[tuple[str, str], str] = {}  # (repo, IaC ref) -> node id | component id

    # ---------------------------------------------------------------- public
    def resolve(self) -> LinkResult:
        self._infrastructure()
        for comp in sorted(self.components, key=lambda c: c.id):
            for call in comp.outbound:
                if call.kind in REMOTE_KIND:
                    self._remote(comp, call)
                elif call.kind in (OutboundKind.MESSAGING_PUBLISH, OutboundKind.MESSAGING_SUBSCRIBE):
                    self._messaging(comp, call)
                elif call.kind == OutboundKind.DATABASE:
                    self._database(comp, call)
            self._entities_without_dsn(comp)
        self._infra_relations()
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
    def _external(self, comp: Component, host: str, call: OutboundCall, rule: str, protocol: str,
                  description: str) -> None:
        ext_id = "ext-" + slugify(host)
        self._externals.setdefault(ext_id, ExternalSystem(id=ext_id, name=host, target=call.target))
        self._externals[ext_id].evidence.append(call.evidence)
        self._add(LinkKind.EXTERNAL, comp.id, ext_id, rule, call.evidence, protocol, description)

    def _serving(self, comp: Component, protocol: str, match) -> str | None:
        """The single other component exposing an interface of `protocol` that satisfies `match`."""
        hits = {c.id for c in self.components if c.id != comp.id
                for e in c.endpoints if e.protocol == protocol and match(e.path)}
        return hits.pop() if len(hits) == 1 else None

    def _remote(self, comp: Component, call: OutboundCall) -> None:
        """Synchronous request/response dependencies (downstream caller -> upstream provider)."""
        kind = REMOTE_KIND[call.kind]
        if call.kind == OutboundKind.GRPC:
            return self._grpc(comp, call)
        method_path = call.detail if " " in call.detail else f"{call.detail} {_path(call.target)}".strip()
        path = _path(call.target) or (call.detail.split(" ", 1)[1] if " " in call.detail else "")
        if kind == LinkKind.HTTP and path.rstrip("/").endswith("/graphql"):
            kind = LinkKind.GRAPHQL
        elif kind == LinkKind.HTTP and (path.lower().endswith((".asmx", ".svc")) or "wsdl" in call.target.lower()):
            kind = LinkKind.SOAP
        host = _host(call.target)
        protocol = PROTOCOL.get(urlparse(call.target).scheme, "HTTP") if host else "HTTP"
        if host:
            target = self._match_alias(host)
            if target:
                if kind == LinkKind.SOAP:  # name the operations of the single SOAP service the upstream exposes
                    services = {e.path.split(".")[0] for c in self.components if c.id == target
                                for e in c.endpoints if e.protocol == "soap"}
                    method_path = f"SOAP {services.pop()}.*" if len(services) == 1 else method_path
                self._add(kind, comp.id, target, "R-HTTP-HOST", call.evidence, protocol, method_path)
                return
            target = self._match_path(path, comp.id) if kind == LinkKind.HTTP else None
            if target:
                self._add(kind, comp.id, target, "R-HTTP-PATH", call.evidence, "HTTP", method_path)
                return
            self._external(comp, host, call, "R-HTTP-EXTERNAL", PROTOCOL.get(urlparse(call.target).scheme, "HTTPS"),
                           method_path)
            return
        if kind == LinkKind.GRAPHQL and " " in call.detail:  # QUERY Query.field
            field = call.detail.split(" ", 1)[1]
            if target := self._serving(comp, "graphql", lambda p: p == field):
                self._add(kind, comp.id, target, "R-GRAPHQL-FIELD", call.evidence, "HTTP", call.detail)
                return
            self.result.unresolved.append(call)
            return
        if kind == LinkKind.SOAP and call.target:
            name = call.target
            if target := self._serving(comp, "soap", lambda p: p.split(".")[0] in (name, name.removesuffix("Service"),
                                                                                   f"{name}Service")):
                self._add(kind, comp.id, target, "R-SOAP-SERVICE", call.evidence, "HTTP", call.detail)
                return
        if not call.target:  # declarative client without base URL (Retrofit, Refit): match by path only
            target = self._match_path(path, comp.id)
            if target:
                self._add(kind, comp.id, target, "R-HTTP-PATH", call.evidence, "HTTP", method_path)
                return
            self.result.unresolved.append(call)
            return
        # named client (Feign) or bare alias
        target = self._match_alias(call.target)
        if target:
            self._add(kind, comp.id, target, "R-HTTP-CLIENT-NAME", call.evidence, "HTTP", method_path)
            return
        placeholder = PLACEHOLDER_RE.search(call.target)
        if placeholder and (target := self._match_env_name(placeholder.group(1))):
            self._add(kind, comp.id, target, "R-HTTP-ENV-NAME", call.evidence, "HTTP", method_path)
            return
        self.result.unresolved.append(call)

    def _grpc(self, comp: Component, call: OutboundCall) -> None:
        if "://" in call.target:
            host = _host(call.target.replace("grpc://", "http://", 1))
            if target := self._match_alias(host):
                self._add(LinkKind.GRPC, comp.id, target, "R-GRPC-HOST", call.evidence, "HTTP", "gRPC channel")
            elif host:
                self._external(comp, host, call, "R-GRPC-EXTERNAL", "HTTP", "gRPC channel")
            return
        short = call.target.split(".")[-1]
        upstream = {c.id: e.path for c in self.components if c.id != comp.id for e in c.endpoints
                    if e.protocol == "grpc" and e.path.strip("/").split("/")[0].split(".")[-1] == short}
        if len(upstream) == 1:
            (target, path), = upstream.items()
            service = path.strip("/").split("/")[0]
            self._add(LinkKind.GRPC, comp.id, target, "R-GRPC-SERVICE", call.evidence, "HTTP", f"RPC /{service}/*")
            return
        target = self._match_alias(call.target)  # @GrpcClient("accounts") channel names
        if target:
            self._add(LinkKind.GRPC, comp.id, target, "R-HTTP-CLIENT-NAME", call.evidence, "HTTP", call.detail)
            return
        self.result.unresolved.append(call)

    # ---------------------------------------------------------------- cloud resources (AWS)
    def _cloud_id(self, technology: str, name: str) -> tuple[str, str]:
        """(node id, rule) for a resource named in code, preferring the IaC declaration."""
        key = cloud_key(name)
        if (technology, key) in self._iac:
            return f"{technology}-{key}", ""
        near = [k for (t, k) in self._iac if t == technology and (k.endswith(f"-{key}") or key.endswith(f"-{k}"))]
        if len(near) == 1:
            return f"{technology}-{near[0]}", "R-CLOUD-IAC-SUFFIX"
        return f"{technology}-{key}", ""

    def _cloud_node(self, node_id: str, technology: str, name: str, ev: Evidence | None, iac: str = "") -> None:
        if technology in ("dynamodb", "s3"):
            node = self._datastores.setdefault(node_id, DataStore(
                id=node_id, name=name, technology=technology,
                kind="object-storage" if technology == "s3" else "database"))
        else:
            node = self._topics.setdefault(node_id, Topic(id=node_id, name=name, technology=technology,
                                                          kind=TOPIC_KIND.get(technology, "topic")))
        if ev is not None and ev.ref() not in {e.ref() for e in node.evidence}:
            node.evidence.append(ev)
        if iac and iac not in node.iac:
            node.iac.append(iac)

    def _infrastructure(self) -> None:
        for scan in self.scans:
            for res in scan.infrastructure:
                if res.type == "lambda-function":
                    if res.owner:
                        self._iac_nodes[(scan.repo, res.ref)] = res.owner
                    continue
                tech = CLOUD_TECH.get(res.type)
                if tech is None:
                    continue
                key = cloud_key(res.name)
                self._iac.setdefault((tech, key), res)
                node_id = f"{tech}-{key}"
                self._iac_nodes[(scan.repo, res.ref)] = node_id
                self._cloud_node(node_id, tech, res.name, None, res.evidence.ref())

    def _infra_relations(self) -> None:
        components = {c.id for c in self.components}
        functions = {(s.repo, r.ref): r for s in self.scans for r in s.infrastructure if r.type == "lambda-function"}
        for scan in self.scans:
            for rel in scan.infra_relations:
                src = self._iac_nodes.get((scan.repo, rel.source))
                dst = self._iac_nodes.get((scan.repo, rel.destination))
                if src is None or dst is None:
                    continue
                rule = "R-IAC-SUBSCRIPTION" if rel.kind == "subscription" else "R-IAC-EVENT-SOURCE"
                desc = "subscribes to" if rel.kind == "subscription" else "triggers"
                self._add(LinkKind.ASYNC, src, dst, rule, rel.evidence, "HTTPS", f"{desc} {dst}",
                          via=src if src not in components else "")
            for (repo, _ref), fn in functions.items():
                if repo != scan.repo or not fn.owner:
                    continue
                for var, ref in sorted(fn.environment.items()):
                    node = self._iac_nodes.get((repo, ref))
                    if node is None or node in components:
                        continue
                    kind = LinkKind.DATA if node in self._datastores else LinkKind.ASYNC
                    self._add(kind, fn.owner, node, "R-IAC-ENV-REF", fn.evidence, "HTTPS",
                              f"uses {node} (env {var})", via=node if kind == LinkKind.ASYNC else "")

    def _messaging(self, comp: Component, call: OutboundCall) -> None:
        rule = "R-ASYNC-TOPIC"
        if call.technology in ("sqs", "sns", "kinesis"):
            tid, suffix_rule = self._cloud_id(call.technology, call.target)
            rule = suffix_rule or rule
            self._cloud_node(tid, call.technology, call.target, call.evidence)
        else:
            tid = "topic-" + slugify(call.target)
            topic = self._topics.setdefault(tid, Topic(id=tid, name=call.target, technology=call.technology,
                                                       kind=TOPIC_KIND.get(call.technology, "topic")))
            if call.evidence.ref() not in {e.ref() for e in topic.evidence}:
                topic.evidence.append(call.evidence)
        proto = PROTOCOL.get(call.technology, "TCP")
        if call.kind == OutboundKind.MESSAGING_PUBLISH:
            self._add(LinkKind.ASYNC, comp.id, tid, rule, call.evidence, proto,
                      f"publishes to {call.target}", via=tid)
        else:
            self._add(LinkKind.ASYNC, tid, comp.id, rule, call.evidence, proto,
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
        if call.technology in ("dynamodb", "s3") and "://" in call.target:
            name = call.target.split("://", 1)[1]
            did, suffix_rule = self._cloud_id(call.technology, name)
            self._cloud_node(did, call.technology, name, call.evidence)
            self._add(LinkKind.DATA, comp.id, did, suffix_rule or "R-DATA-CLOUD", call.evidence, "HTTPS",
                      f"{call.detail or 'uses'} {name}".strip())
            return
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
