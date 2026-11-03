"""Builds the canonical FINOS CALM 1.0 architecture document from facts, links and semantics.

The CALM document is the source of truth of the as-is landscape: the graph database and the wiki are
projections of it. Output is deterministic (sorted ids, stable slugs) so it diffs cleanly in git.

Mapping (see RULES.md §5):
  component (service)      -> node-type "service"      | webclient -> "webclient"
  datastore / DynamoDB     -> node-type "database"
  S3 bucket                -> node-type "data-asset"   (metadata.asset-kind = object-storage)
  topic / queue / stream   -> node-type "data-asset"   (asset-kind messaging-topic | messaging-queue | event-stream)
  external host            -> node-type "system"       (metadata.external = true)
  BIAN business domain     -> node-type "ecosystem" + composed-of relationship over its services
  HTTP/gRPC/GraphQL/SOAP   -> connects downstream -> upstream, destination interfaces = matched operations
  async link               -> connects (TCP/AMQP/HTTPS) through the topic/queue/stream node
  data link                -> connects (JDBC/HTTPS)
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..linker.resolver import template_regex
from ..model import Component, LinkKind, LinkResult, RepoScan, SemanticProfile
from ..workspace import slugify

CALM_SCHEMA = "https://calm.finos.org/release/1.0/meta/core.json"
GENERATOR = "blueprint-discoverer"


def _evidence(evs, limit: int = 10) -> list[str]:
    return sorted({e.ref() if hasattr(e, "ref") else str(e) for e in evs})[:limit]


def _component_node(comp: Component, scan: RepoScan, profile: SemanticProfile | None) -> dict:
    interfaces = []
    for alias in comp.aliases[:1]:
        interfaces.append({"unique-id": f"{comp.id}-host", "host": alias})
    for e in comp.endpoints:
        iface = {
            "unique-id": slugify(f"{comp.id}-{e.method}-{e.path}"),
            "method": e.method, "path": e.path, "handler": e.handler, "evidence": e.evidence.ref(),
        }
        if e.protocol != "http":
            iface["protocol"] = e.protocol
        interfaces.append(iface)
    meta: dict = {
        "source": {"repository": comp.repo, "location": scan.location, "path": comp.root_path,
                   "commit": scan.commit},
        "technology": {"languages": comp.languages, "frameworks": comp.frameworks},
        "aliases": comp.aliases,
    }
    description = comp.description or (profile.purpose if profile and profile.purpose else comp.name)
    if profile:
        meta["semantics"] = {
            "purpose": profile.purpose,
            "objectives": profile.objectives,
            "business-capabilities": profile.business_capabilities,
            "business-functions": profile.business_functions,
            "confidence": profile.confidence,
            "method": profile.method,
            "review-required": profile.review_required,
            "rationale": profile.rationale,
        }
        if profile.primary_domain:
            p = profile.primary_domain
            meta["bian"] = {
                "service-domain-id": p.service_domain_id,
                "service-domain": p.service_domain,
                "business-domain": p.business_domain,
                "business-area": p.business_area,
                "evidence": p.evidence,
                "secondary": [{"service-domain-id": s.service_domain_id, "service-domain": s.service_domain,
                               "score": s.score} for s in profile.secondary_domains],
            }
    return {
        "unique-id": comp.id,
        "node-type": comp.kind if comp.kind in ("service", "webclient", "system") else "service",
        "name": comp.name,
        "description": description,
        "interfaces": interfaces,
        "metadata": meta,
    }


SYNC_KINDS = {LinkKind.HTTP, LinkKind.GRPC, LinkKind.GRAPHQL, LinkKind.SOAP}
ASSET_KIND = {"topic": "messaging-topic", "queue": "messaging-queue", "stream": "event-stream"}


def _endpoint_interfaces(link_desc: str, dest: Component | None, protocol: str = "http") -> list[str]:
    """Pick destination interface ids whose operation appears in the link description.

    http: "GET /accounts/{id}"; grpc: "RPC /pkg.Svc/*" or "RPC /pkg.Svc/Method";
    graphql: "QUERY Query.field"; soap: "SOAP Svc.Operation" or "SOAP Svc.*"."""
    if dest is None:
        return []
    out = []
    for call in link_desc.split("; "):
        parts = call.split(" ", 1)
        if len(parts) != 2:
            continue
        method, target = parts[0].upper(), parts[1].split("?")[0]
        for e in dest.endpoints:
            if e.protocol != protocol:
                continue
            if protocol == "http":
                ok = e.method in (method, "*") and template_regex(e.path).match(re.sub(r"\{[^}]*\}", "x", target))
            elif target.endswith("*"):
                ok = e.path.startswith(target[:-1])
            else:
                ok = e.path == target
            if ok:
                out.append(slugify(f"{dest.id}-{e.method}-{e.path}"))
    return sorted(set(out))


def build_architecture(name: str, scans: list[RepoScan], links: LinkResult,
                       profiles: list[SemanticProfile], landscape_version: str = "") -> dict:
    comps = {c.id: (c, s) for s in scans for c in s.components}
    prof = {p.component_id: p for p in profiles}
    nodes: list[dict] = []
    relationships: list[dict] = []

    for cid in sorted(comps):
        comp, scan = comps[cid]
        nodes.append(_component_node(comp, scan, prof.get(cid)))
    for ds in links.datastores:
        meta = {"technology": ds.technology, "evidence": _evidence(ds.evidence)}
        if ds.iac:
            meta["declared-in"] = sorted(ds.iac)
        if ds.kind == "object-storage":
            meta["asset-kind"] = "object-storage"
            nodes.append({"unique-id": ds.id, "node-type": "data-asset", "name": ds.name,
                          "description": f"{ds.technology} bucket '{ds.name}'", "metadata": meta})
            continue
        source = "declared in IaC" if ds.iac else "inferred from code/config"
        nodes.append({
            "unique-id": ds.id, "node-type": "database", "name": ds.name,
            "description": f"{ds.technology or 'Database'} {ds.name} ({source})", "metadata": meta,
        })
    for t in links.topics:
        meta = {"asset-kind": ASSET_KIND.get(t.kind, "messaging-topic"), "technology": t.technology,
                "evidence": _evidence(t.evidence)}
        if t.iac:
            meta["declared-in"] = sorted(t.iac)
        nodes.append({
            "unique-id": t.id, "node-type": "data-asset", "name": t.name,
            "description": f"{t.technology} {t.kind} '{t.name}'", "metadata": meta,
        })
    for ext in links.externals:
        nodes.append({
            "unique-id": ext.id, "node-type": "system", "name": ext.name,
            "description": f"External system reached at {ext.target}",
            "metadata": {"external": True, "evidence": _evidence(ext.evidence)},
        })

    for link in links.links:
        dest_comp = comps.get(link.destination, (None, None))[0]
        destination = {"node": link.destination}
        if link.kind in SYNC_KINDS:
            ifaces = _endpoint_interfaces(link.description, dest_comp, link.kind.value)
            if ifaces:
                destination["interfaces"] = ifaces
        rel = {
            "unique-id": link.id,
            "description": link.description or f"{link.kind.value} dependency",
            "relationship-type": {"connects": {"source": {"node": link.source}, "destination": destination}},
            "metadata": {
                "dependency-kind": link.kind.value,
                "confidence": link.confidence,
                "rule": link.rule,
                "evidence": _evidence(link.evidence),
            },
        }
        if link.protocol:
            rel["protocol"] = link.protocol
        if link.via:
            rel["metadata"]["via"] = link.via
        if link.kind in SYNC_KINDS or link.kind in (LinkKind.EXTERNAL, LinkKind.DATA):
            # source calls destination: the caller is downstream, the provider upstream
            rel["metadata"]["direction"] = {"downstream": link.source, "upstream": link.destination}
        relationships.append(rel)

    # BIAN business domains as ecosystems composed of the services that realise them
    by_domain: dict[str, list[str]] = defaultdict(list)
    domain_area: dict[str, str] = {}
    for p in profiles:
        if p.primary_domain:
            by_domain[p.primary_domain.business_domain].append(p.component_id)
            domain_area[p.primary_domain.business_domain] = p.primary_domain.business_area
    for dom in sorted(by_domain):
        eco_id = "bian-bd-" + slugify(dom)
        nodes.append({
            "unique-id": eco_id, "node-type": "ecosystem", "name": f"BIAN {dom}",
            "description": f"BIAN business domain '{dom}' ({domain_area[dom]})",
            "metadata": {"bian": {"business-domain": dom, "business-area": domain_area[dom]}},
        })
        relationships.append({
            "unique-id": f"{eco_id}-composition",
            "description": f"Applications realising service domains of BIAN '{dom}'",
            "relationship-type": {"composed-of": {"container": eco_id, "nodes": sorted(by_domain[dom])}},
        })

    nodes.sort(key=lambda n: (n["node-type"], n["unique-id"]))
    relationships.sort(key=lambda r: r["unique-id"])
    return {
        "$schema": CALM_SCHEMA,
        "unique-id": slugify(name),
        "name": name,
        "description": f"As-is architecture of '{name}' discovered from {len(scans)} repositories.",
        "nodes": nodes,
        "relationships": relationships,
        "metadata": [
            {"generator": GENERATOR, "generator-version": "0.1.0"},
            {"bian-landscape": landscape_version},
            {"repositories": sorted({f"{s.repo}@{s.commit[:12]}" if s.commit else s.repo for s in scans})},
        ],
    }
