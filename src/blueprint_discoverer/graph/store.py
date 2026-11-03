"""LadybugDB (embedded property graph, Cypher) projection of the CALM architecture.

The graph is rebuilt from the CALM document on every load, so it can never drift from the canonical
architecture-as-code. It adds what JSON is bad at: traversal (impact analysis, paths) and aggregation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import ladybug as lb

from ..semantic.bian.landscape import Landscape, load_landscape
from ..workspace import slugify

SCHEMA = [
    """CREATE NODE TABLE Application(id STRING, name STRING, node_type STRING, description STRING,
        repo STRING, languages STRING, frameworks STRING, purpose STRING, confidence DOUBLE,
        review_required BOOLEAN, method STRING, service_domain_id STRING, PRIMARY KEY(id))""",
    "CREATE NODE TABLE DataStore(id STRING, name STRING, technology STRING, kind STRING, iac STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE Topic(id STRING, name STRING, technology STRING, kind STRING, iac STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE ExternalSystem(id STRING, name STRING, description STRING, PRIMARY KEY(id))",
    """CREATE NODE TABLE Endpoint(id STRING, method STRING, path STRING, handler STRING, evidence STRING,
        protocol STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE ServiceDomain(id STRING, name STRING, functional_pattern STRING, description STRING,
        PRIMARY KEY(id))""",
    "CREATE NODE TABLE BusinessDomain(id STRING, name STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE BusinessArea(id STRING, name STRING, PRIMARY KEY(id))",
    """CREATE REL TABLE DEPENDS_ON(FROM Application TO Application, FROM Application TO DataStore,
        FROM Application TO Topic, FROM Topic TO Application, FROM Application TO ExternalSystem,
        FROM Topic TO Topic, FROM DataStore TO Application,
        id STRING, kind STRING, protocol STRING, description STRING, confidence DOUBLE, rule STRING,
        evidence STRING)""",
    "CREATE REL TABLE EXPOSES(FROM Application TO Endpoint)",
    "CREATE REL TABLE REALIZES(FROM Application TO ServiceDomain, is_primary BOOLEAN, score DOUBLE)",
    "CREATE REL TABLE IN_BUSINESS_DOMAIN(FROM ServiceDomain TO BusinessDomain)",
    "CREATE REL TABLE IN_BUSINESS_AREA(FROM BusinessDomain TO BusinessArea)",
]
# List-typed properties are stored as JSON strings: ladybug 0.22 spends ~60s of CPU binding the first
# non-empty LIST parameter after a NULL/empty one (see docs/adr/0003-graph-ladybugdb.md).
NODE_TABLE = {"service": "Application", "webclient": "Application", "database": "DataStore",
              "data-asset": "Topic", "system": "ExternalSystem"}


class GraphStore:
    def __init__(self, path: Path, read_only: bool = False):
        self.path = path
        self.db = lb.Database(str(path), read_only=read_only)
        self.conn = lb.Connection(self.db)

    def close(self) -> None:
        self.conn.close()
        self.db.close()

    @classmethod
    def rebuild(cls, path: Path, calm: dict, landscape: Landscape | None = None) -> GraphStore:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            for p in path.parent.glob(path.name + "*"):  # db file + wal/shadow files
                p.unlink()
        store = cls(path)
        for stmt in SCHEMA:
            store.conn.execute(stmt)
        # one write transaction: auto-commit per statement costs a WAL flush each (orders of magnitude slower)
        store.conn.execute("BEGIN TRANSACTION")
        try:
            store._load_bian(landscape or load_landscape())
            store._load_calm(calm)
            store.conn.execute("COMMIT")
        except Exception:
            store.conn.execute("ROLLBACK")
            raise
        return store

    def q(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        res = self.conn.execute(cypher, params or {})
        cols = res.get_column_names()
        return [dict(zip(cols, row, strict=False)) for row in res]

    # ------------------------------------------------------------------ load
    def _load_bian(self, landscape: Landscape) -> None:
        areas, domains = set(), set()
        for sd in landscape.service_domains:
            area_id, dom_id = "ba-" + slugify(sd.business_area), "bd-" + slugify(sd.business_domain)
            if area_id not in areas:
                self.conn.execute("CREATE (:BusinessArea {id:$id, name:$name})", {"id": area_id, "name": sd.business_area})
                areas.add(area_id)
            if dom_id not in domains:
                self.conn.execute("CREATE (:BusinessDomain {id:$id, name:$name})", {"id": dom_id, "name": sd.business_domain})
                self.conn.execute("MATCH (d:BusinessDomain {id:$d}), (a:BusinessArea {id:$a}) "
                                  "CREATE (d)-[:IN_BUSINESS_AREA]->(a)", {"d": dom_id, "a": area_id})
                domains.add(dom_id)
            self.conn.execute(
                "CREATE (:ServiceDomain {id:$id, name:$name, functional_pattern:$fp, description:$dsc})",
                {"id": sd.id, "name": sd.name, "fp": sd.functional_pattern, "dsc": sd.description})
            self.conn.execute("MATCH (s:ServiceDomain {id:$s}), (d:BusinessDomain {id:$d}) "
                              "CREATE (s)-[:IN_BUSINESS_DOMAIN]->(d)", {"s": sd.id, "d": dom_id})

    def _load_calm(self, calm: dict) -> None:
        labels: dict[str, str] = {}
        for n in calm.get("nodes", []):
            meta = n.get("metadata") or {}
            table = NODE_TABLE.get(n["node-type"])
            if meta.get("asset-kind") == "object-storage":
                table = "DataStore"  # S3 buckets are data-assets in CALM but storage in the graph
            if table is None:
                continue
            labels[n["unique-id"]] = table
            if table == "Application":
                sem, bian, tech = meta.get("semantics", {}), meta.get("bian", {}), meta.get("technology", {})
                self.conn.execute(
                    "CREATE (:Application {id:$id, name:$name, node_type:$nt, description:$d, repo:$repo, "
                    "languages:$langs, frameworks:$fw, purpose:$p, confidence:$c, review_required:$rr, "
                    "method:$m, service_domain_id:$sd})",
                    {"id": n["unique-id"], "name": n["name"], "nt": n["node-type"], "d": n["description"],
                     "repo": meta.get("source", {}).get("repository", ""),
                     "langs": json.dumps(tech.get("languages", [])), "fw": json.dumps(tech.get("frameworks", [])),
                     "p": sem.get("purpose", ""), "c": float(sem.get("confidence", 0.0)),
                     "rr": bool(sem.get("review-required", False)), "m": sem.get("method", ""),
                     "sd": bian.get("service-domain-id", "")})
                for iface in n.get("interfaces", []):
                    if "path" not in iface:
                        continue
                    self.conn.execute(
                        "CREATE (:Endpoint {id:$id, method:$m, path:$p, handler:$h, evidence:$e, protocol:$pr})",
                        {"id": iface["unique-id"], "m": iface.get("method", ""), "p": iface["path"],
                         "h": iface.get("handler", ""), "e": iface.get("evidence", ""),
                         "pr": iface.get("protocol", "http")})
                    self.conn.execute("MATCH (a:Application {id:$a}), (e:Endpoint {id:$e}) CREATE (a)-[:EXPOSES]->(e)",
                                      {"a": n["unique-id"], "e": iface["unique-id"]})
                if bian.get("service-domain-id"):
                    self._realizes(n["unique-id"], bian["service-domain-id"], True, 1.0)
                    for s in bian.get("secondary", []):
                        self._realizes(n["unique-id"], s["service-domain-id"], False, float(s.get("score", 0)))
            elif table == "ExternalSystem":
                self.conn.execute("CREATE (:ExternalSystem {id:$id, name:$name, description:$d})",
                                  {"id": n["unique-id"], "name": n["name"], "d": n["description"]})
            else:
                kind = meta.get("asset-kind", "database") if table == "DataStore" else meta.get("asset-kind", "")
                self.conn.execute(f"CREATE (:{table} {{id:$id, name:$name, technology:$t, kind:$k, iac:$iac}})",
                                  {"id": n["unique-id"], "name": n["name"], "t": meta.get("technology", ""),
                                   "k": kind, "iac": json.dumps(meta.get("declared-in", []))})

        for r in calm.get("relationships", []):
            conn = r.get("relationship-type", {}).get("connects")
            if not conn:
                continue
            src, dst = conn["source"]["node"], conn["destination"]["node"]
            if src not in labels or dst not in labels:
                continue
            meta = r.get("metadata") or {}
            self.conn.execute(
                f"MATCH (a:{labels[src]} {{id:$s}}), (b:{labels[dst]} {{id:$d}}) "
                "CREATE (a)-[:DEPENDS_ON {id:$id, kind:$k, protocol:$p, description:$dsc, confidence:$c, "
                "rule:$rl, evidence:$ev}]->(b)",
                {"s": src, "d": dst, "id": r["unique-id"], "k": meta.get("dependency-kind", ""),
                 "p": r.get("protocol", ""), "dsc": r.get("description", ""),
                 "c": float(meta.get("confidence", 1.0)), "rl": meta.get("rule", ""),
                 "ev": json.dumps(meta.get("evidence", []))})

    def _realizes(self, app: str, sd: str, primary: bool, score: float) -> None:
        self.conn.execute("MATCH (a:Application {id:$a}), (s:ServiceDomain {id:$s}) "
                          "CREATE (a)-[:REALIZES {is_primary:$p, score:$sc}]->(s)",
                          {"a": app, "s": sd, "p": primary, "sc": score})

    # ------------------------------------------------------------------ queries used by API/wiki
    def graph_view(self) -> dict:
        nodes = []
        for label in ("Application", "DataStore", "Topic", "ExternalSystem"):
            extra = ", n.service_domain_id AS sd, n.confidence AS confidence, n.review_required AS review" \
                if label == "Application" else ", n.technology AS technology, n.kind AS kind" \
                if label in ("DataStore", "Topic") else ""
            for row in self.q(f"MATCH (n:{label}) RETURN n.id AS id, n.name AS name{extra} ORDER BY n.id"):
                row["type"] = label
                nodes.append(row)
        sds = {r["id"]: r for r in self.q(
            "MATCH (s:ServiceDomain)-[:IN_BUSINESS_DOMAIN]->(d:BusinessDomain)-[:IN_BUSINESS_AREA]->(a:BusinessArea) "
            "RETURN s.id AS id, s.name AS sd, d.name AS bd, a.name AS ba")}
        for n in nodes:
            if n.get("sd") in sds:
                n.update(service_domain=sds[n["sd"]]["sd"], business_domain=sds[n["sd"]]["bd"],
                         business_area=sds[n["sd"]]["ba"])
        edges = self.q("MATCH (a)-[e:DEPENDS_ON]->(b) RETURN e.id AS id, a.id AS source, b.id AS target, "
                       "e.kind AS kind, e.protocol AS protocol, e.description AS description, "
                       "e.confidence AS confidence, e.rule AS rule, e.evidence AS evidence ORDER BY e.id")
        for e in edges:
            e["evidence"] = json.loads(e["evidence"] or "[]")
        return {"nodes": nodes, "edges": edges}

    def impact(self, node_id: str, depth: int = 6) -> list[dict]:
        """Everything that (transitively) depends on `node_id` - who breaks if it changes."""
        return self.q(f"MATCH (a)-[:DEPENDS_ON*1..{int(depth)}]->(b) WHERE b.id = $id AND a.id <> $id "
                      "RETURN DISTINCT a.id AS id, a.name AS name, label(a) AS type ORDER BY id", {"id": node_id})

    def dependencies(self, node_id: str, depth: int = 6) -> list[dict]:
        return self.q(f"MATCH (a)-[:DEPENDS_ON*1..{int(depth)}]->(b) WHERE a.id = $id AND b.id <> $id "
                      "RETURN DISTINCT b.id AS id, b.name AS name, label(b) AS type ORDER BY id", {"id": node_id})

    def bian_coverage(self) -> list[dict]:
        return self.q(
            "MATCH (s:ServiceDomain)-[:IN_BUSINESS_DOMAIN]->(d:BusinessDomain)-[:IN_BUSINESS_AREA]->(a:BusinessArea) "
            "OPTIONAL MATCH (app:Application)-[r:REALIZES]->(s) "
            "RETURN a.name AS business_area, d.name AS business_domain, s.id AS service_domain_id, "
            "s.name AS service_domain, s.functional_pattern AS pattern, "
            "collect(CASE WHEN r.is_primary THEN app.id ELSE NULL END) AS primary_apps, "
            "collect(CASE WHEN NOT r.is_primary THEN app.id ELSE NULL END) AS secondary_apps "
            "ORDER BY business_area, business_domain, service_domain")

    def shared_datastores(self) -> list[dict]:
        return self.q("MATCH (a:Application)-[:DEPENDS_ON]->(d:DataStore) WITH d, collect(a.id) AS apps "
                      "WHERE size(apps) > 1 RETURN d.id AS id, d.name AS name, apps")

    def cypher(self, query: str) -> list[dict]:
        """Read-only ad-hoc Cypher for the explorer (writes are rejected)."""
        upper = f" {query.upper()} "
        if any(f" {kw} " in upper for kw in ("CREATE", "MERGE", "DELETE", "SET", "DROP", "ALTER", "COPY", "LOAD")):
            raise ValueError("only read queries are allowed")
        return json.loads(json.dumps(self.q(query), default=str))
