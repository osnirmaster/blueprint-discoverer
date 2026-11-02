"""Deterministic validation of the CALM architecture document.

Three layers, all must pass for `valid=True`:
  1. JSON Schema   - vendored CALM 1.0 meta-schemas (schemas/calm/1.0), offline.
  2. Referential   - unique ids, every relationship endpoint/interface/container resolves.
  3. Profile       - Blueprint Discoverer conventions (RULES.md §5): evidence on every inferred edge,
                     valid confidence, known BIAN ids, standard node types and protocols.
Optionally (4) delegates to the official `@finos/calm-cli validate` when Node.js is available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field
from referencing import Registry, Resource

from ..semantic.bian.landscape import load_landscape
from ..workspace import resource_path

SCHEMA_DIR = resource_path("schemas/calm/1.0", "_schemas/calm/1.0")
CORE_ID = "https://calm.finos.org/release/1.0/meta/core.json"
NODE_TYPES = {"actor", "ecosystem", "system", "service", "database", "network", "ldap", "webclient", "data-asset"}
PROTOCOLS = {"HTTP", "HTTPS", "FTP", "SFTP", "JDBC", "WebSocket", "SocketIO", "LDAP", "AMQP", "TLS", "mTLS", "TCP"}


class Issue(BaseModel):
    level: str  # error | warning
    layer: str  # schema | reference | profile | calm-cli
    path: str
    message: str


class ValidationReport(BaseModel):
    valid: bool
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    resources = []
    for f in sorted(SCHEMA_DIR.glob("*.json")):
        doc = json.loads(f.read_text())
        resources.append((doc["$id"], Resource.from_contents(doc)))
    registry = Registry().with_resources(resources)
    core = json.loads((SCHEMA_DIR / "core.json").read_text())
    return Draft202012Validator(core, registry=registry)


def validate_architecture(doc: dict, use_calm_cli: bool = False, doc_path: Path | None = None) -> ValidationReport:
    errors: list[Issue] = []
    warnings: list[Issue] = []

    def err(layer, path, msg):
        errors.append(Issue(level="error", layer=layer, path=path, message=msg))

    def warn(layer, path, msg):
        warnings.append(Issue(level="warning", layer=layer, path=path, message=msg))

    # 1. JSON schema
    for e in sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path)):
        err("schema", "/".join(map(str, e.absolute_path)) or "$", e.message)

    nodes = doc.get("nodes", [])
    rels = doc.get("relationships", [])

    # 2. referential integrity
    node_ids: dict[str, dict] = {}
    for i, n in enumerate(nodes):
        nid = n.get("unique-id", "")
        if nid in node_ids:
            err("reference", f"nodes/{i}", f"duplicate node unique-id '{nid}'")
        node_ids[nid] = n
    iface_ids = {nid: {i.get("unique-id") for i in n.get("interfaces", [])} for nid, n in node_ids.items()}
    seen_rel: set[str] = set()
    for i, r in enumerate(rels):
        rid = r.get("unique-id", "")
        path = f"relationships/{i}({rid})"
        if rid in seen_rel or rid in node_ids:
            err("reference", path, f"duplicate unique-id '{rid}'")
        seen_rel.add(rid)
        rt = r.get("relationship-type", {})
        if "connects" in rt:
            for side in ("source", "destination"):
                ni = rt["connects"].get(side, {})
                if ni.get("node") not in node_ids:
                    err("reference", path, f"{side} node '{ni.get('node')}' does not exist")
                    continue
                for iface in ni.get("interfaces", []):
                    if iface not in iface_ids[ni["node"]]:
                        err("reference", path, f"{side} interface '{iface}' not declared on '{ni['node']}'")
        for key in ("composed-of", "deployed-in"):
            if key in rt:
                if rt[key].get("container") not in node_ids:
                    err("reference", path, f"{key} container '{rt[key].get('container')}' does not exist")
                for n in rt[key].get("nodes", []):
                    if n not in node_ids:
                        err("reference", path, f"{key} member '{n}' does not exist")
        if "interacts" in rt:
            for n in [rt["interacts"].get("actor"), *rt["interacts"].get("nodes", [])]:
                if n not in node_ids:
                    err("reference", path, f"interacts member '{n}' does not exist")

    # 3. Blueprint Discoverer profile
    bian_ids = load_landscape().by_id()
    for nid, n in node_ids.items():
        path = f"nodes({nid})"
        if n.get("node-type") not in NODE_TYPES:
            warn("profile", path, f"non-standard node-type '{n.get('node-type')}'")
        meta = n.get("metadata") or {}
        if n.get("node-type") in ("service", "webclient"):
            if "source" not in meta:
                err("profile", path, "service node without metadata.source (repository provenance)")
            bian = meta.get("bian")
            if bian is None:
                warn("profile", path, "service without BIAN classification")
            elif bian.get("service-domain-id") not in bian_ids:
                err("profile", path, f"unknown BIAN service domain '{bian.get('service-domain-id')}'")
            sem = meta.get("semantics", {})
            if sem.get("review-required"):
                warn("profile", path, f"semantic classification needs human review "
                                      f"(confidence={sem.get('confidence')})")
    for r in rels:
        rid = r.get("unique-id")
        if "connects" not in r.get("relationship-type", {}):
            continue
        meta = r.get("metadata") or {}
        if not meta.get("evidence"):
            err("profile", f"relationships({rid})", "inferred relationship without evidence")
        if not meta.get("rule"):
            err("profile", f"relationships({rid})", "inferred relationship without producing rule id")
        conf = meta.get("confidence")
        if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            err("profile", f"relationships({rid})", f"confidence must be in [0,1], got {conf!r}")
        elif conf < 0.6:
            warn("profile", f"relationships({rid})", f"low-confidence dependency ({conf}) - confirm manually")
        if r.get("protocol") and r["protocol"] not in PROTOCOLS:
            err("profile", f"relationships({rid})", f"protocol '{r['protocol']}' not in CALM enum")

    # 4. official CLI (optional)
    if use_calm_cli and doc_path is not None:
        npx = shutil.which("npx")
        if npx is None:
            warn("calm-cli", "$", "npx not found; skipped @finos/calm-cli validation")
        else:
            proc = subprocess.run([npx, "--yes", "@finos/calm-cli", "validate", "-a", str(doc_path), "-f", "json"],
                                  capture_output=True, text=True, timeout=300, check=False)
            if proc.returncode != 0:
                err("calm-cli", "$", (proc.stdout or proc.stderr).strip()[:2000])

    stats = {
        "nodes": len(nodes),
        "relationships": len(rels),
        "services": sum(1 for n in nodes if n.get("node-type") in ("service", "webclient")),
        "databases": sum(1 for n in nodes if n.get("node-type") == "database"),
        "topics": sum(1 for n in nodes if n.get("node-type") == "data-asset"),
        "external-systems": sum(1 for n in nodes if (n.get("metadata") or {}).get("external")),
    }
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings, stats=stats)
