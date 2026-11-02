"""Workspace layout, project config (blueprint.yaml) and small shared helpers."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent  # valid in editable/dev installs


def resource_path(dev_relative: str, packaged_relative: str) -> Path:
    """Locate a bundled resource both from a source checkout and from an installed wheel."""
    dev = REPO_ROOT / dev_relative
    return dev if dev.exists() else PACKAGE_DIR / packaged_relative


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "unnamed"


@dataclass
class RepoSpec:
    name: str
    path: str | None = None
    url: str | None = None
    ref: str | None = None


@dataclass
class ProjectConfig:
    name: str = "as-is-landscape"
    repos: list[RepoSpec] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    llm_enabled: bool = False
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    min_domain_score: float = 0.15
    # human decisions win over every inference layer: {component_id: service_domain_id}
    classification_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ProjectConfig:
        raw = yaml.safe_load(path.read_text()) or {}
        repos = [RepoSpec(**r) for r in raw.get("repositories", [])]
        llm = raw.get("inference", {}).get("llm", {})
        return cls(
            name=raw.get("name", cls.name),
            repos=repos,
            exclude=raw.get("exclude", []),
            llm_enabled=bool(llm.get("enabled", False)),
            llm_model=llm.get("model", cls.llm_model),
            llm_effort=llm.get("effort", cls.llm_effort),
            min_domain_score=float(raw.get("inference", {}).get("min_domain_score", cls.min_domain_score)),
            classification_overrides=dict((raw.get("overrides") or {}).get("classifications") or {}),
        )


class Workspace:
    """Every artefact of a run lives under one directory (default `.blueprint/`)."""

    def __init__(self, root: Path):
        self.root = root
        for d in (self.facts_dir, self.semantic_dir, self.llm_cache_dir, self.repos_dir):
            d.mkdir(parents=True, exist_ok=True)

    facts_dir = property(lambda self: self.root / "facts")
    semantic_dir = property(lambda self: self.root / "semantic")
    llm_cache_dir = property(lambda self: self.root / "cache" / "llm")
    repos_dir = property(lambda self: self.root / "repos")
    links_file = property(lambda self: self.root / "links.json")
    calm_file = property(lambda self: self.root / "architecture.calm.json")
    validation_file = property(lambda self: self.root / "validation.json")
    graph_path = property(lambda self: self.root / "graph.lbdb")
    wiki_dir = property(lambda self: self.root / "wiki")


def write_json(path: Path, data: Any) -> None:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + fixed indent keep every artefact byte-stable between identical runs
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def git_head(path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def materialize_repo(spec: RepoSpec, ws: Workspace, base: Path) -> Path:
    """Return a local path for the repo, shallow-cloning remote URLs into the workspace."""
    if spec.path:
        return (base / spec.path).resolve()
    if not spec.url:
        raise ValueError(f"repository '{spec.name}' needs either path or url")
    dest = ws.repos_dir / slugify(spec.name)
    if not dest.exists():
        cmd = ["git", "clone", "--depth", "1"]
        if spec.ref:
            cmd += ["--branch", spec.ref]
        subprocess.run([*cmd, spec.url, str(dest)], check=True)
    return dest
