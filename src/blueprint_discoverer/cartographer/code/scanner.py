"""Cartographer Code: walks a repository, finds deployable components and extracts AST facts."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from ...model import Component, Endpoint, OutboundCall, OutboundKind, RepoScan
from ...workspace import git_head, slugify
from ..infra import TerraformScanner, is_cloudformation, scan_cloudformation
from .config import (
    COMPOSE_FILES,
    CONFIG_PATTERNS,
    MANIFESTS,
    ConfigFacts,
    is_config_file,
    scan_compose,
    scan_config_file,
)
from .contracts import CONTRACT_SUFFIXES, ContractFacts, ProtoService, scan_contract_file
from .extractors.base import Extractor, FileContext, FileFacts
from .extractors.csharp import CSharpExtractor
from .extractors.go import GoExtractor
from .extractors.java import JavaExtractor
from .extractors.kotlin import KotlinExtractor
from .extractors.python import PythonExtractor
from .extractors.typescript import TypeScriptExtractor

DEFAULT_EXCLUDES = [
    ".git", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__", ".idea",
    ".gradle", ".next", "coverage", "vendor", ".blueprint", "*.min.js", "*.d.ts",
    "bin", "obj", ".terraform", "cdk.out", "*.pb.go", "*_pb2.py", "*_pb2_grpc.py",
]
TEST_HINTS = ("/test/", "/tests/", "__tests__", ".spec.", ".test.", "_test.py", "test_", "_test.go", ".Tests/",
              ".Test/", "/Tests/")
MAX_FILE_BYTES = 512 * 1024
IAC_SUFFIXES = (".yaml", ".yml", ".json", ".template")
# a GraphQL schema file only makes a component a GraphQL *server* when it runs a server library
GRAPHQL_SERVERS = {"spring-graphql", "netflix-dgs", "nestjs-graphql", "apollo-server", "gqlgen", "strawberry",
                   "graphene", "ariadne", "hotchocolate", "graphql-java", "graphql-yoga", "mercurius"}
PLACEHOLDER_RE = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def default_extractors() -> list[Extractor]:
    return [JavaExtractor(), KotlinExtractor(), TypeScriptExtractor(), PythonExtractor(), CSharpExtractor(),
            GoExtractor()]


def _is_library_csproj(path: Path) -> bool:
    """Class-library and test projects are not deployable components (their code belongs to the app)."""
    if path.suffix != ".csproj":
        return False
    content = path.read_text(errors="replace")
    deployable = "Microsoft.NET.Sdk.Web" in content or "Microsoft.NET.Sdk.Worker" in content or \
        re.search(r"<OutputType>\s*(Win)?Exe\s*</OutputType>", content) or "AWSProjectType" in content
    return not deployable or "Microsoft.NET.Test.Sdk" in content


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
                 if any(fnmatch.fnmatch(p.name, m) for m in MANIFESTS) and not _is_library_csproj(p)}
        roots = {"." if r in ("", ".") else r for r in roots}
        if not roots:
            return ["."]
        # aggregator manifests (maven parent, npm workspaces root) are dropped when they own no source
        return sorted(roots)

    @staticmethod
    def _owner(rel: str, roots: list[str]) -> str | None:
        """Deepest component root containing `rel` (None for files outside every component)."""
        candidates = [r for r in roots if r == "." or rel == r or rel.startswith(r + "/")]
        if not candidates and len(roots) == 1:
            return roots[0]  # shared code (e.g. .NET class libraries) of a single-component repository
        return max(candidates, key=lambda r: -1 if r == "." else len(r)) if candidates else None

    def scan(self, repo_name: str, root: Path) -> RepoScan:
        root = root.resolve()
        files = self._files(root)
        roots = self._component_roots(files)
        per_root: dict[str, tuple[ConfigFacts, list[FileFacts], set[str]]] = {
            r: (ConfigFacts(), [], set()) for r in roots
        }
        compose: dict[str, ConfigFacts] = {}
        contracts: dict[str, ContractFacts] = {r: ContractFacts() for r in roots}
        terraform = TerraformScanner(repo_name)
        result = RepoScan(repo=repo_name, location=str(root), commit=git_head(root))

        for path, rel in files:
            if path.name in COMPOSE_FILES:
                compose.update(scan_compose(path, repo_name, rel))
                continue
            if path.suffix == ".tf":
                terraform.add(rel, path.read_bytes())
                continue
            if path.suffix.lower() in IAC_SUFFIXES and not any(fnmatch.fnmatch(path.name, p) for p in CONFIG_PATTERNS) \
                    and path.stat().st_size <= MAX_FILE_BYTES:
                content = path.read_text(errors="replace")
                if is_cloudformation(content):
                    resources, relations = scan_cloudformation(content, repo_name, rel)
                    result.infrastructure += resources
                    result.infra_relations += relations
                    continue
            owner = self._owner(rel, roots)
            if owner is None:
                continue
            cfg, file_facts, langs = per_root[owner]
            if path.suffix.lower() in CONTRACT_SUFFIXES:
                try:
                    scan_contract_file(path.read_text(errors="replace"), repo_name, rel, contracts[owner])
                except Exception as exc:  # a malformed contract must never abort a scan
                    result.warnings.append(f"{rel}: {exc}")
                continue
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

        protos = [p for c in contracts.values() for p in c.protos]
        by_root: dict[str, str] = {}
        has_manifest = any(any(fnmatch.fnmatch(p.name, m) for m in MANIFESTS) for p, _ in files)
        for r in roots:
            cfg, file_facts, langs = per_root[r]
            if not file_facts and not cfg.endpoints and not cfg.outbound and (len(roots) > 1 or not has_manifest):
                continue  # aggregator manifest, or infrastructure-only repository, without code of its own
            comp = self._build_component(repo_name, root, r, cfg, file_facts, langs)
            self._apply_contracts(comp, contracts[r], protos)
            self._resolve_placeholders(comp)
            result.components.append(comp)
            by_root[r] = comp.id

        tf_resources, tf_relations = terraform.scan()
        result.infrastructure += tf_resources
        result.infra_relations += tf_relations
        for res in result.infrastructure:
            owner = self._owner(res.evidence.path, roots)
            if owner in by_root and (owner != "." or len(roots) == 1):
                res.owner = by_root[owner]
        result.infrastructure.sort(key=lambda r: r.ref)
        result.infra_relations.sort(key=lambda r: (r.source, r.destination, r.kind))
        return result

    @staticmethod
    def _apply_contracts(comp: Component, contracts: ContractFacts, protos: list[ProtoService]) -> None:
        """Bind interface contracts to the component: the code decides who serves and who consumes."""
        by_name: dict[str, ProtoService] = {}
        for p in protos:
            by_name.setdefault(p.name, p)
        # gRPC servers: expand "/Svc/*" placeholders with the methods declared in the .proto
        endpoints: list[Endpoint] = []
        for e in comp.endpoints:
            svc = e.path.strip("/").split("/")[0] if e.protocol == "grpc" else ""
            proto = by_name.get(svc.split(".")[-1]) if svc else None
            if proto is None:
                endpoints.append(e)
            elif e.path.endswith("/*"):
                endpoints += [Endpoint(method="RPC", path=f"/{proto.full_name}/{m}", handler=e.handler,
                                       protocol="grpc", evidence=e.evidence) for m, _ in proto.methods]
            else:
                endpoints.append(e.model_copy(update={"path": f"/{proto.full_name}/{e.path.rsplit('/', 1)[-1]}"}))
        # gRPC clients: short service names become fully qualified when the .proto is in the repository
        for call in comp.outbound:
            if call.kind == OutboundKind.GRPC and "://" not in call.target and call.target in by_name:
                full = by_name[call.target].full_name
                call.target, call.detail = full, f"RPC /{full}/*"
        # GraphQL: SDL is a server contract only for components running a GraphQL server library
        if contracts.graphql_schema and (set(comp.frameworks) & GRAPHQL_SERVERS or
                                         any(e.protocol == "graphql" for e in endpoints)):
            endpoints += contracts.graphql_schema
        comp.outbound += contracts.graphql_operations
        # WSDL: served when the code implements the service, otherwise it describes a dependency
        served = {e.path.split(".")[0] for e in endpoints if e.protocol == "soap"}
        for w in contracts.wsdl:
            if w.name in served or w.name.removesuffix("Service") in served:
                continue
            comp.outbound.append(OutboundCall(kind=OutboundKind.SOAP, target=w.location or w.name,
                                              detail=f"SOAP {w.name}.*", technology="wsdl", evidence=w.evidence))
        seen, unique = set(), []
        for e in sorted(endpoints, key=lambda e: (e.protocol, e.path, e.method, e.evidence.path)):
            if (e.protocol, e.method, e.path) not in seen:
                seen.add((e.protocol, e.method, e.path))
                unique.append(e)
        comp.endpoints = unique

    @staticmethod
    def _resolve_placeholders(comp: Component) -> None:
        """${clients.accounts.url} / ${ACCOUNTS_URL:http://...} in targets -> value from the component config."""
        config = {k.lower(): v for k, v in comp.config.items()}

        def sub(m: re.Match) -> str:
            key = m.group(1).strip()
            for candidate in (key, key.replace("_", ".").lower(), key.lower()):
                if candidate.lower() in config:
                    return config[candidate.lower()]
            return m.group(2) if m.group(2) is not None else m.group(0)

        for call in comp.outbound:
            if "${" in call.target:
                call.target = PLACEHOLDER_RE.sub(sub, call.target)

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
            if (e.protocol, e.method, e.path) not in seen:
                seen.add((e.protocol, e.method, e.path))
                unique.append(e)
        comp.endpoints = unique
        return comp
