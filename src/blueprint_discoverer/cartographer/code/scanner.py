"""Cartographer Code: walks a repository, finds deployable components and extracts AST facts."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from ...model import Component, RepoScan
from ...workspace import git_head, slugify
from .config import COMPOSE_FILES, MANIFESTS, ConfigFacts, is_config_file, scan_compose, scan_config_file
from .extractors.base import Extractor, FileContext, FileFacts
from .extractors.java import JavaExtractor
from .extractors.python import PythonExtractor
from .extractors.typescript import TypeScriptExtractor

DEFAULT_EXCLUDES = [
    ".git", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__", ".idea",
    ".gradle", ".next", "coverage", "vendor", ".blueprint", "*.min.js", "*.d.ts",
]
TEST_HINTS = ("/test/", "/tests/", "__tests__", ".spec.", ".test.", "_test.py", "test_")
MAX_FILE_BYTES = 512 * 1024


def default_extractors() -> list[Extractor]:
    return [JavaExtractor(), TypeScriptExtractor(), PythonExtractor()]


class CodeCartographer:
    def __init__(self, extractors: list[Extractor] | None = None, exclude: list[str] | None = None,
                 include_tests: bool = False):
        self.extractors = extractors or default_extractors()
        self.by_ext = {ext: e for e in self.extractors for ext in e.extensions}
        self.exclude = DEFAULT_EXCLUDES + (exclude or [])
        self.include_tests = include_tests

    def _excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        return any(fnmatch.fnmatch(p, pat) for p in parts for pat in self.exclude)

    def _files(self, root: Path) -> list[tuple[Path, str]]:
        out = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if self._excluded(rel):
                continue
            out.append((p, rel))
        return out

    @staticmethod
    def _component_roots(files: list[tuple[Path, str]]) -> list[str]:
        roots = {str(Path(rel).parent) for p, rel in files
                 if any(fnmatch.fnmatch(p.name, m) for m in MANIFESTS)}
        roots = {"." if r in ("", ".") else r for r in roots}
        if not roots:
            return ["."]
        # aggregator manifests (maven parent, npm workspaces root) are dropped when they own no source
        return sorted(roots)

    @staticmethod
    def _owner(rel: str, roots: list[str]) -> str | None:
        """Deepest component root containing `rel` (None for files outside every component)."""
        candidates = [r for r in roots if r == "." or rel == r or rel.startswith(r + "/")]
        return max(candidates, key=lambda r: -1 if r == "." else len(r)) if candidates else None

    def scan(self, repo_name: str, root: Path) -> RepoScan:
        root = root.resolve()
        files = self._files(root)
        roots = self._component_roots(files)
        per_root: dict[str, tuple[ConfigFacts, list[FileFacts], set[str]]] = {
            r: (ConfigFacts(), [], set()) for r in roots
        }
        compose: dict[str, ConfigFacts] = {}
        result = RepoScan(repo=repo_name, location=str(root), commit=git_head(root))

        for path, rel in files:
            if path.name in COMPOSE_FILES:
                compose.update(scan_compose(path, repo_name, rel))
                continue
            owner = self._owner(rel, roots)
            if owner is None:
                continue
            cfg, file_facts, langs = per_root[owner]
            if is_config_file(path, rel):
                try:
                    scan_config_file(path, repo_name, rel, cfg)
                except Exception as exc:  # malformed config must never abort a scan
                    result.warnings.append(f"{rel}: {exc}")
                continue
            ext = path.suffix.lower()
            extractor = self.by_ext.get(ext)
            if extractor is None or path.stat().st_size > MAX_FILE_BYTES:
                continue
            if not self.include_tests and any(h in "/" + rel for h in TEST_HINTS):
                continue
            try:
                facts = extractor.extract(FileContext(repo=repo_name, path=rel, source=path.read_bytes()))
            except Exception as exc:
                result.warnings.append(f"{rel}: extractor {extractor.language} failed: {exc}")
                continue
            result.files_scanned += 1
            langs.add(extractor.language)
            file_facts.append(facts)

        for ctx_path, cfacts in compose.items():
            owner = self._owner(ctx_path, roots)
            if owner is not None:
                cfg = per_root[owner][0]
                cfg.aliases |= cfacts.aliases
                cfg.outbound += cfacts.outbound

        for r in roots:
            cfg, file_facts, langs = per_root[r]
            if not file_facts and not cfg.endpoints and not cfg.outbound and len(roots) > 1:
                continue  # aggregator manifest without code of its own
            result.components.append(self._build_component(repo_name, root, r, cfg, file_facts, langs))
        return result

    @staticmethod
    def _build_component(repo: str, root: Path, rel_root: str, cfg: ConfigFacts,
                         file_facts: list[FileFacts], langs: set[str]) -> Component:
        dir_name = root.name if rel_root == "." else Path(rel_root).name
        name = cfg.name or (repo if rel_root == "." else dir_name)
        comp = Component(
            id=slugify(name), name=name, repo=repo, root_path=rel_root, languages=sorted(langs),
            readme_excerpt=cfg.readme, config=dict(sorted(cfg.config.items())),
        )
        frameworks = set(cfg.frameworks)
        packages: set[str] = set()
        for f in file_facts:
            comp.endpoints += f.endpoints
            comp.outbound += f.outbound
            comp.symbols += f.symbols
            frameworks |= f.frameworks
            packages |= f.packages
        comp.endpoints += cfg.endpoints
        comp.outbound += cfg.outbound
        comp.frameworks = sorted(frameworks)
        comp.packages = sorted(packages)
        comp.aliases = sorted({a for a in {name, slugify(name), dir_name, slugify(dir_name), *cfg.aliases} if a})
        if cfg.is_frontend and not comp.endpoints:
            comp.kind = "webclient"
        # dedupe endpoints (openapi + code often describe the same route)
        seen, unique = set(), []
        for e in sorted(comp.endpoints, key=lambda e: (e.path, e.method, e.evidence.path)):
            if (e.method, e.path) not in seen:
                seen.add((e.method, e.path))
                unique.append(e)
        comp.endpoints = unique
        return comp
