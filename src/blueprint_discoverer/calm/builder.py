"""Builds the canonical FINOS CALM 1.0 architecture document from facts, links and semantics.

The CALM document is the source of truth of the as-is landscape: the graph database and the wiki are
projections of it. Output is deterministic (sorted ids, stable slugs) so it diffs cleanly in git.

Mapping (see RULES.md §5):
  component (service)      -> node-type "service"      | webclient -> "webclient"
  datastore                -> node-type "database"
  messaging topic          -> node-type "data-asset"   (metadata.asset-kind = messaging-topic)
  external host            -> node-type "system"       (metadata.external = true)
  BIAN business domain     -> node-type "ecosystem" + composed-of relationship over its services
  HTTP link                -> connects (HTTP/HTTPS), destination interface = matched endpoint group
  async link               -> connects (TCP/AMQP) through the topic node
  data link                -> connects (JDBC)
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
        interfaces.append({
            "unique-id": slugify(f"{comp.id}-{e.method}-{e.path}"),
            "method": e.method, "path": e.path, "handler": e.handler, "evidence": e.evidence.ref(),
        })
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


def _endpoint_interfaces(link_desc: str, dest: Component | None) -> list[str]:
    """Pick destination interface ids whose method+path appear in the link description."""
    if dest is None:
        return []
    out = []
    for call in link_desc.split("; "):
        parts = call.split(" ", 1)
        if len(parts) != 2:
            continue
        method, path = parts[0].upper(), re.sub(r"\{[^}]*\}", "x", parts[1].split("?")[0])
        for e in dest.endpoints:
            if (e.method in (method, "*")) and template_regex(e.path).match(path):
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
        nodes.append({
            "unique-id": ds.id, "node-type": "database", "name": ds.name,
            "description": f"{ds.technology or 'Database'} {ds.name} (inferred from code/config)",
            "metadata": {"technology": ds.technology, "evidence": _evidence(ds.evidence)},
        })
    for t in links.topics:
        nodes.append({
            "unique-id": t.id, "node-type": "data-asset", "name": t.name,
            "description": f"{t.technology} topic/queue '{t.name}'",
            "metadata": {"asset-kind": "messaging-topic", "technology": t.technology,
                         "evidence": _evidence(t.evidence)},
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
        if link.kind == LinkKind.HTTP:
            ifaces = _endpoint_interfaces(link.description, dest_comp)
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
