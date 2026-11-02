"""REST API consumed by the Cloudscape web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..graph.store import GraphStore
from ..workspace import REPO_ROOT, Workspace, read_json


class CypherQuery(BaseModel):
    query: str


def create_app(ws: Workspace, web_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="Blueprint Discoverer API", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    state: dict = {"graph": None, "mtime": None}

    def calm() -> dict:
        if not ws.calm_file.exists():
            raise HTTPException(404, "no CALM document yet - run `blueprint run` first")
        return read_json(ws.calm_file)

    def graph() -> GraphStore:
        if not ws.graph_path.exists():
            raise HTTPException(404, "graph not built yet - run `blueprint run` first")
        mtime = ws.graph_path.stat().st_mtime
        if state["graph"] is None or state["mtime"] != mtime:
            state["graph"] = GraphStore(ws.graph_path, read_only=True)
            state["mtime"] = mtime
        return state["graph"]

    def nodes_by_id() -> dict[str, dict]:
        return {n["unique-id"]: n for n in calm()["nodes"]}

    @app.get("/api/summary")
    def summary():
        doc = calm()
        validation = read_json(ws.validation_file) if ws.validation_file.exists() else {}
        apps = [n for n in doc["nodes"] if n["node-type"] in ("service", "webclient")]
        review = [n["unique-id"] for n in apps if (n.get("metadata") or {}).get("semantics", {}).get("review-required")]
        domains = sorted({(n.get("metadata") or {}).get("bian", {}).get("business-domain") for n in apps} - {None})
        return {"name": doc.get("name"), "description": doc.get("description"), "metadata": doc.get("metadata"),
                "validation": validation, "applications": len(apps), "review_required": review,
                "business_domains": domains}

    @app.get("/api/architecture")
    def architecture():
        return calm()

    @app.get("/api/validation")
    def validation():
        return read_json(ws.validation_file) if ws.validation_file.exists() else {}

    @app.get("/api/graph")
    def graph_view():
        return graph().graph_view()

    @app.get("/api/applications")
    def applications():
        out = []
        for n in calm()["nodes"]:
            if n["node-type"] not in ("service", "webclient"):
                continue
            meta = n.get("metadata") or {}
            out.append({"id": n["unique-id"], "name": n["name"], "type": n["node-type"],
                        "description": n["description"], "repository": meta.get("source", {}).get("repository"),
                        "languages": meta.get("technology", {}).get("languages", []),
                        "frameworks": meta.get("technology", {}).get("frameworks", []),
                        "bian": meta.get("bian", {}), "semantics": meta.get("semantics", {}),
                        "endpoints": sum(1 for i in n.get("interfaces", []) if "path" in i)})
        return sorted(out, key=lambda a: a["name"])

    @app.get("/api/applications/{app_id}")
    def application(app_id: str):
        nodes = nodes_by_id()
        if app_id not in nodes:
            raise HTTPException(404, f"unknown application {app_id}")
        rels_out, rels_in = [], []
        for r in calm()["relationships"]:
            c = r["relationship-type"].get("connects")
            if not c:
                continue
            if c["source"]["node"] == app_id:
                rels_out.append({**r, "other": nodes.get(c["destination"]["node"])})
            elif c["destination"]["node"] == app_id:
                rels_in.append({**r, "other": nodes.get(c["source"]["node"])})
        g = graph()
        return {"node": nodes[app_id], "outbound": rels_out, "inbound": rels_in,
                "impact": g.impact(app_id), "dependencies": g.dependencies(app_id)}

    @app.get("/api/bian/coverage")
    def bian_coverage():
        return graph().bian_coverage()

    @app.post("/api/cypher")
    def cypher(body: CypherQuery):
        try:
            return graph().cypher(body.query)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(400, f"cypher error: {exc}") from exc

    @app.get("/api/wiki/tree")
    def wiki_tree():
        if not ws.wiki_dir.exists():
            return []
        return sorted(p.relative_to(ws.wiki_dir).as_posix() for p in ws.wiki_dir.rglob("*.md"))

    @app.get("/api/wiki/page")
    def wiki_page(path: str = "index.md"):
        target = (ws.wiki_dir / path).resolve()
        if ws.wiki_dir.resolve() not in target.parents or not target.exists():
            raise HTTPException(404, "page not found")
        return {"path": path, "markdown": target.read_text()}

    dist = web_dist or REPO_ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app
