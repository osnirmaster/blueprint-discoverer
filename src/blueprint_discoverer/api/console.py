"""Web console backend: choose repositories or folders to scan and run the pipeline in the background.

The same rules as the CLI apply (`blueprint discover`, `blueprint repo`, `blueprint run --only`):
blueprint.yaml is the landscape when it exists, `save` appends to it, and an incremental scan reuses the
stored facts of the repositories that were not selected. Folder browsing is limited to `fs_root` and only
lists directories, never file contents.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..discovery import (
    add_repo_to_config,
    discover_repos,
    has_project_files,
    is_git_url,
    repo_spec_for,
    write_config,
)
from ..workspace import ProjectConfig, RepoSpec, Workspace, read_json, slugify

MAX_JOBS = 20


class RepoRequest(BaseModel):
    name: str | None = None
    path: str | None = None
    url: str | None = None
    ref: str | None = None


class ScanRequest(BaseModel):
    repositories: list[RepoRequest] = []  # new or configured repositories to (re)scan
    full: bool = False  # rescan every repository of the landscape
    save: bool = False  # persist new repositories in blueprint.yaml
    use_llm: bool | None = None


class DiscoverRequest(BaseModel):
    path: str
    depth: int = 3


@dataclass
class Job:
    id: str
    repositories: list[str]
    mode: str
    status: str = "queued"  # queued | running | succeeded | failed
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None

    def public(self) -> dict:
        return {"id": self.id, "repositories": self.repositories, "mode": self.mode, "status": self.status,
                "started": self.started, "finished": self.finished, "log": self.log[-200:],
                "result": self.result, "error": self.error}


class _JobLog(logging.Handler):
    def __init__(self, job: Job) -> None:
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        self.job.log.append(f"{time.strftime('%H:%M:%S')} {record.getMessage()}")


class Console:
    def __init__(self, ws: Workspace, config_path: Path | None, fs_root: Path,
                 before_scan: Callable[[], None] | None = None) -> None:
        self.ws = ws
        self.before_scan = before_scan
        self.config_path = config_path.resolve() if config_path else None
        self.base = self.config_path.parent if self.config_path else ws.root.resolve().parent
        self.fs_root = fs_root.resolve()
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _config(self) -> ProjectConfig:
        if self.config_path and self.config_path.exists():
            return ProjectConfig.load(self.config_path)
        cfg = ProjectConfig(name=self.base.name)
        # without a project file the workspace itself is the registry of scanned repositories
        cfg.repos = [RepoSpec(name=read_json(f)["repo"]) for f in sorted(self.ws.facts_dir.glob("*.json"))]
        return cfg

    def _allowed(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved != self.fs_root and self.fs_root not in resolved.parents:
            raise HTTPException(403, f"{resolved} is outside the browsable root {self.fs_root}")
        return resolved

    # ------------------------------------------------------------------ queries
    def sources(self) -> dict:
        cfg = self._config()
        facts = {f.stem: read_json(f) for f in self.ws.facts_dir.glob("*.json")}
        repos = []
        for spec in cfg.repos:
            scan = facts.get(slugify(spec.name))
            repos.append({
                "name": spec.name, "path": spec.path, "url": spec.url, "ref": spec.ref,
                "scanned": scan is not None,
                "commit": (scan or {}).get("commit", "")[:12],
                "components": [c["id"] for c in (scan or {}).get("components", [])],
                "infrastructure": len((scan or {}).get("infrastructure", [])),
                "files_scanned": (scan or {}).get("files_scanned", 0),
                "location": (scan or {}).get("location", ""),
            })
        return {"config": str(self.config_path) if self.config_path else None,
                "config_exists": bool(self.config_path and self.config_path.exists()),
                "base": str(self.base), "workspace": str(self.ws.root), "fs_root": str(self.fs_root),
                "repositories": repos}

    def browse(self, path: str | None = None) -> dict:
        target = self._allowed(Path(path) if path else self.base if self.base.is_relative_to(self.fs_root)
                               else self.fs_root)
        if not target.is_dir():
            raise HTTPException(404, f"{target} is not a directory")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith(".") or child.name in ("node_modules", "__pycache__"):
                continue
            try:
                manifest = has_project_files(child, max_depth=2)
            except PermissionError:
                continue
            entries.append({"name": child.name, "path": str(child), "git": (child / ".git").exists(),
                            "manifest": manifest})
        parent = target.parent if target != self.fs_root else None
        return {"path": str(target), "parent": str(parent) if parent else None, "git": (target / ".git").exists(),
                "entries": entries}

    def discover(self, body: DiscoverRequest) -> list[dict]:
        folder = self._allowed(Path(body.path))
        if not folder.is_dir():
            raise HTTPException(404, f"{folder} is not a directory")
        configured = {r.name for r in self._config().repos}
        return [{"name": s.name if s.path != "." else folder.name, "path": str((folder / s.path).resolve()),
                 "git": ((folder / s.path) / ".git").exists(), "configured": s.name in configured}
                for s in discover_repos(folder, max_depth=max(1, min(body.depth, 6)))]

    def list_jobs(self) -> list[dict]:
        return [j.public() for j in sorted(self.jobs.values(), key=lambda j: -j.started)]

    def job(self, job_id: str) -> dict:
        if job_id not in self.jobs:
            raise HTTPException(404, "unknown job")
        return self.jobs[job_id].public()

    # ------------------------------------------------------------------ scans
    def start(self, body: ScanRequest) -> dict:
        if any(j.status in ("queued", "running") for j in self.jobs.values()):
            raise HTTPException(409, "a scan is already running")
        cfg = self._config()
        specs: list[RepoSpec] = []
        for r in body.repositories:
            target = r.url or r.path or r.name
            if not target:
                raise HTTPException(400, "each repository needs a name, path or url")
            if r.url and not is_git_url(r.url):
                raise HTTPException(400, f"not a git URL: {r.url}")
            if r.path:
                self._allowed(Path(r.path))
            try:
                specs.append(repo_spec_for(target, cfg, self.base, name=r.name, ref=r.ref))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if not specs and not body.full:
            raise HTTPException(400, "select at least one repository, or request a full scan")
        if body.save and specs:
            self._save(cfg, specs)
        names = [s.name for s in specs]
        for spec in specs:
            if spec.name not in {r.name for r in cfg.repos}:
                cfg.repos.append(spec)
        job = Job(id=uuid.uuid4().hex[:8], repositories=names or [r.name for r in cfg.repos],
                  mode="full" if body.full else "incremental")
        self.jobs[job.id] = job
        for old in sorted(self.jobs.values(), key=lambda j: j.started)[:-MAX_JOBS]:
            self.jobs.pop(old.id, None)
        threading.Thread(target=self._run, args=(job, cfg, None if body.full else names, body.use_llm),
                         daemon=True).start()
        return job.public()

    def _save(self, cfg: ProjectConfig, specs: list[RepoSpec]) -> None:
        if self.config_path is None:
            raise HTTPException(400, "no blueprint.yaml configured for this server (use `blueprint serve -c`)")
        try:
            if self.config_path.exists():
                for s in specs:
                    add_repo_to_config(self.config_path, s)
            else:
                write_config(self.config_path, self.base.name, [*cfg.repos, *specs])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def _run(self, job: Job, cfg: ProjectConfig, only: list[str] | None, use_llm: bool | None) -> None:
        from ..pipeline import Pipeline

        logger = logging.getLogger("blueprint_discoverer")
        handler = _JobLog(job)
        with self.lock:
            job.status = "running"
            previous = logger.level
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            try:
                job.log.append(f"{time.strftime('%H:%M:%S')} {job.mode} scan: {', '.join(job.repositories)}")
                if self.before_scan is not None:
                    self.before_scan()
                report = Pipeline(cfg, self.ws, self.base).run(use_llm=use_llm, only=only, refresh=True,
                                                               prune=only is None)
                job.result = {"valid": report.valid, "errors": len(report.errors),
                              "warnings": len(report.warnings), "stats": report.stats}
                job.status = "succeeded"
                job.log.append(f"{time.strftime('%H:%M:%S')} done: {'VALID' if report.valid else 'INVALID'}")
            except Exception as exc:  # surfaced to the UI; the server keeps running
                job.status, job.error = "failed", str(exc)
                job.log.append(f"{time.strftime('%H:%M:%S')} failed: {exc}")
            finally:
                logger.removeHandler(handler)
                logger.setLevel(previous)
                job.finished = time.time()


def console_router(console: Console) -> APIRouter:
    router = APIRouter(prefix="/api")
    router.add_api_route("/sources", console.sources, methods=["GET"])
    router.add_api_route("/fs", console.browse, methods=["GET"])
    router.add_api_route("/sources/discover", console.discover, methods=["POST"])
    router.add_api_route("/scans", console.list_jobs, methods=["GET"])
    router.add_api_route("/scans", console.start, methods=["POST"])
    router.add_api_route("/scans/{job_id}", console.job, methods=["GET"])
    return router
