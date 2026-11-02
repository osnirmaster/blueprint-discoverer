"""End-to-end pipeline. Each stage reads the previous stage's artefact from the workspace, so any stage
can be re-run in isolation (e.g. re-infer semantics without rescanning code)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Collection
from pathlib import Path

from .calm.builder import build_architecture
from .calm.validator import ValidationReport, validate_architecture
from .cartographer.code.scanner import CodeCartographer
from .graph.store import GraphStore
from .linker.resolver import resolve
from .model import LinkResult, RepoScan, SemanticProfile
from .semantic.bian.landscape import load_landscape
from .semantic.inference import SemanticEngine
from .wiki.generator import WikiGenerator
from .workspace import ProjectConfig, Workspace, materialize_repo, read_json, slugify, write_json

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: ProjectConfig, workspace: Workspace, base_dir: Path):
        self.config = config
        self.ws = workspace
        self.base_dir = base_dir

    # 1 ---------------------------------------------------------------------------------------
    def scan(self, only: Collection[str] | None = None, refresh: bool = False,
             prune: bool = True) -> list[RepoScan]:
        """Scan the configured repositories and return the facts of the whole landscape.

        `only` rescans just those repositories (by name) and reuses the stored facts of the others; a
        repository without stored facts is scanned anyway. `prune` drops facts of repositories that are
        no longer configured. `refresh` updates existing clones of remote repositories.
        """
        carto = CodeCartographer(exclude=self.config.exclude)
        unknown = set(only or ()) - {s.name for s in self.config.repos}
        if unknown:
            raise ValueError(f"unknown repositories: {', '.join(sorted(unknown))}")
        scans = []
        for spec in self.config.repos:
            facts = self.ws.facts_dir / f"{slugify(spec.name)}.json"
            if only is not None and spec.name not in only and facts.exists():
                scans.append(RepoScan.model_validate(read_json(facts)))
                continue
            path = materialize_repo(spec, self.ws, self.base_dir, refresh=refresh)
            log.info("scanning %s (%s)", spec.name, path)
            scan = carto.scan(spec.name, path)
            write_json(facts, scan)
            scans.append(scan)
        if prune:
            keep = {f"{slugify(s.name)}.json" for s in self.config.repos}
            for stale in self.ws.facts_dir.glob("*.json"):
                if stale.name not in keep:
                    log.info("dropping facts of unlisted repository %s", stale.stem)
                    stale.unlink()
        return scans

    def load_scans(self) -> list[RepoScan]:
        return [RepoScan.model_validate(read_json(f)) for f in sorted(self.ws.facts_dir.glob("*.json"))]

    # 2 ---------------------------------------------------------------------------------------
    def link(self, scans: list[RepoScan] | None = None) -> LinkResult:
        result = resolve(scans or self.load_scans())
        write_json(self.ws.links_file, result)
        return result

    def load_links(self) -> LinkResult:
        return LinkResult.model_validate(read_json(self.ws.links_file))

    # 3 ---------------------------------------------------------------------------------------
    def infer(self, scans: list[RepoScan] | None = None, links: LinkResult | None = None,
              use_llm: bool | None = None) -> list[SemanticProfile]:
        scans = scans or self.load_scans()
        links = links or self.load_links()
        engine = SemanticEngine(
            cache_dir=self.ws.llm_cache_dir,
            use_llm=self.config.llm_enabled if use_llm is None else use_llm,
            model=self.config.llm_model, effort=self.config.llm_effort,
            min_score=self.config.min_domain_score,
            overrides=self.config.classification_overrides,
        )
        profiles = engine.infer_all([c for s in scans for c in s.components], links)
        for p in profiles:
            write_json(self.ws.semantic_dir / f"{p.component_id}.json", p)
        keep = {f"{p.component_id}.json" for p in profiles}
        for stale in self.ws.semantic_dir.glob("*.json"):
            if stale.name not in keep:
                stale.unlink()
        return profiles

    def load_profiles(self) -> list[SemanticProfile]:
        return [SemanticProfile.model_validate(read_json(f)) for f in sorted(self.ws.semantic_dir.glob("*.json"))]

    # 4 ---------------------------------------------------------------------------------------
    def build_calm(self, scans=None, links=None, profiles=None) -> dict:
        doc = build_architecture(self.config.name, scans or self.load_scans(), links or self.load_links(),
                                 profiles or self.load_profiles(), load_landscape().version)
        write_json(self.ws.calm_file, doc)
        return doc

    def validate(self, doc: dict | None = None, use_calm_cli: bool = False) -> ValidationReport:
        doc = doc or read_json(self.ws.calm_file)
        report = validate_architecture(doc, use_calm_cli=use_calm_cli, doc_path=self.ws.calm_file)
        write_json(self.ws.validation_file, report)
        return report

    # 5 ---------------------------------------------------------------------------------------
    def load_graph(self, doc: dict | None = None, force: bool = False) -> GraphStore:
        """Rebuild the graph projection only when the CALM document changed (content hash)."""
        doc = doc or read_json(self.ws.calm_file)
        digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
        marker = self.ws.root / "graph.sha256"
        if not force and self.ws.graph_path.exists() and marker.exists() and marker.read_text() == digest:
            return GraphStore(self.ws.graph_path)
        store = GraphStore.rebuild(self.ws.graph_path, doc)
        marker.write_text(digest)
        return store

    def wiki(self, doc: dict | None = None, graph: GraphStore | None = None) -> list[Path]:
        doc = doc or read_json(self.ws.calm_file)
        validation = read_json(self.ws.validation_file) if self.ws.validation_file.exists() else {}
        links = self.load_links() if self.ws.links_file.exists() else LinkResult()
        return WikiGenerator(doc, self.ws.wiki_dir, graph=graph, validation=validation,
                             unresolved=links.unresolved, overrides_dir=self.ws.root / "overrides").generate()

    # all -------------------------------------------------------------------------------------
    def run(self, use_llm: bool | None = None, use_calm_cli: bool = False,
            only: Collection[str] | None = None, refresh: bool = False, prune: bool = True) -> ValidationReport:
        scans = self.scan(only=only, refresh=refresh, prune=prune)
        links = self.link(scans)
        profiles = self.infer(scans, links, use_llm)
        doc = self.build_calm(scans, links, profiles)
        report = self.validate(doc, use_calm_cli)
        graph = self.load_graph(doc)
        self.wiki(doc, graph)
        return report
