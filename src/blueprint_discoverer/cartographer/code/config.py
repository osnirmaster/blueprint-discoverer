"""Deterministic scanners for build manifests and runtime configuration.

Configuration is where most cross-application wiring hides (base URLs, JDBC URLs, broker addresses),
so it is read with the same rigour as code: every fact carries file:line evidence.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ...model import Endpoint, Evidence, OutboundCall, OutboundKind

MANIFESTS = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml",
             "requirements.txt", "setup.py", "go.mod", "*.csproj")
URL_VALUE_RE = re.compile(r"^(https?|jdbc:[a-z0-9]+|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://", re.I)
JDBC_RE = re.compile(r"^jdbc:(?P<tech>[a-z0-9]+):(?:thin:@)?//?(?P<host>[^:/;]+)(?::\d+)?[/;:]?(?P<db>[\w.-]*)", re.I)
DSN_RE = re.compile(r"^(?P<tech>[a-z0-9+]+)://(?:[^@/]+@)?(?P<host>[^:/]+)(?::\d+)?/?(?P<db>[\w.-]*)", re.I)
FRONTEND_DEPS = {"react", "@angular/core", "vue", "next", "svelte"}
BACKEND_DEPS = {"express", "@nestjs/core", "fastify", "koa", "hapi"}


@dataclass
class ConfigFacts:
    name: str = ""
    aliases: set[str] = field(default_factory=set)
    frameworks: set[str] = field(default_factory=set)
    outbound: list[OutboundCall] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    config: dict[str, str] = field(default_factory=dict)
    is_frontend: bool = False
    readme: str = ""


def _line_of(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines, 1):
        if needle and needle in line:
            return i
    return 0


def _flatten(prefix: str, value, out: dict[str, str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif value is not None:
        out[prefix] = str(value)


def classify_url(value: str) -> tuple[OutboundKind, str] | None:
    """Return (kind, technology) for URL-looking config values."""
    v = value.strip()
    if m := JDBC_RE.match(v):
        return OutboundKind.DATABASE, m.group("tech").lower()
    if v.lower().startswith(("http://", "https://")):
        return OutboundKind.HTTP, "http"
    if m := DSN_RE.match(v):
        tech = m.group("tech").lower().split("+")[0]
        if tech in ("amqp", "amqps"):
            return OutboundKind.MESSAGING_PUBLISH, "rabbitmq"
        return OutboundKind.DATABASE, tech
    return None


def _scan_properties(flat: dict[str, str], repo: str, rel: str, lines: list[str], facts: ConfigFacts) -> None:
    for key, raw in flat.items():
        value = re.sub(r"\$\{[^:}]+:([^}]+)\}", r"\1", raw)  # Spring ${ENV:default} -> default
        facts.config[key] = value
        lk = key.lower()
        if lk in ("spring.application.name", "name") and not facts.name and rel.split("/")[-1].startswith("application"):
            facts.name = value
        if lk.endswith(("bootstrap-servers", "bootstrap.servers")):
            facts.frameworks.add("kafka")
            continue
        if not URL_VALUE_RE.match(value):
            continue
        cls = classify_url(value)
        if cls is None:
            continue
        kind, tech = cls
        ev = Evidence(repo=repo, path=rel, line=_line_of(lines, key.split(".")[-1]), snippet=f"{key}={value}")
        facts.outbound.append(OutboundCall(kind=kind, target=value, detail=key, technology=tech, evidence=ev))


def scan_config_file(path: Path, repo: str, rel: str, facts: ConfigFacts) -> None:
    name = path.name
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return
    lines = content.splitlines()
    lower = name.lower()

    if lower.startswith(("application", "bootstrap")) and lower.endswith((".yml", ".yaml")):
        flat: dict[str, str] = {}
        for doc in yaml.safe_load_all(content):
            _flatten("", doc or {}, flat)
        _scan_properties(flat, repo, rel, lines, facts)
    elif lower.startswith("application") and lower.endswith(".properties"):
        flat = {}
        for line in lines:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                flat[k.strip()] = v.strip()
        _scan_properties(flat, repo, rel, lines, facts)
    elif lower.startswith(".env") or lower == "config.env":
        flat = {}
        for line in lines:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                flat[k.strip()] = v.strip().strip("\"'")
        _scan_properties(flat, repo, rel, lines, facts)
    elif lower == "package.json":
        data = json.loads(content or "{}")
        facts.name = facts.name or data.get("name", "")
        deps = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
        facts.frameworks |= {d for d in deps if d in FRONTEND_DEPS | BACKEND_DEPS | {"kafkajs", "axios"}}
        facts.is_frontend = bool(deps & FRONTEND_DEPS) and not (deps & BACKEND_DEPS)
    elif lower == "pyproject.toml":
        data = tomllib.loads(content)
        facts.name = facts.name or data.get("project", {}).get("name", "") or \
            data.get("tool", {}).get("poetry", {}).get("name", "")
    elif lower == "pom.xml":
        # artifactId of the project itself (first one outside <parent>)
        body = re.sub(r"<parent>.*?</parent>", "", content, flags=re.S)
        if m := re.search(r"<artifactId>([^<]+)</artifactId>", body):
            facts.name = facts.name or m.group(1)
        if "spring-boot" in content:
            facts.frameworks.add("spring-boot")
    elif lower in ("openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.json"):
        data = yaml.safe_load(content) or {}
        for p, ops in (data.get("paths") or {}).items():
            for method in ops or {}:
                if method.lower() in ("get", "post", "put", "delete", "patch"):
                    facts.endpoints.append(Endpoint(
                        method=method.upper(), path=p, handler=(ops[method] or {}).get("operationId", ""),
                        evidence=Evidence(repo=repo, path=rel, line=_line_of(lines, p), snippet=f"{method} {p}")))
    elif lower.endswith((".yaml", ".yml")) and ("k8s" in rel or "kubernetes" in rel or "deploy" in rel):
        for doc in yaml.safe_load_all(content):
            if isinstance(doc, dict) and doc.get("kind") == "Service":
                facts.aliases.add(doc.get("metadata", {}).get("name", ""))
    elif lower.startswith("readme"):
        facts.readme = content[:1200]


COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def scan_compose(path: Path, repo: str, rel: str) -> dict[str, ConfigFacts]:
    """docker-compose: map each *built* service (build context) to its alias and environment wiring.

    Returns {build_context_relative_to_repo: ConfigFacts}. Image-only services (postgres, kafka...)
    are infrastructure and are recognised later by the linker from the URLs that point at them.
    """
    content = path.read_text(errors="replace")
    lines = content.splitlines()
    data = yaml.safe_load(content) or {}
    base = Path(rel).parent
    out: dict[str, ConfigFacts] = {}
    for svc, spec in (data.get("services") or {}).items():
        spec = spec or {}
        build = spec.get("build")
        if build is None:
            continue
        context = build if isinstance(build, str) else build.get("context", ".")
        key = (base / context).as_posix()
        key = "." if key in ("", ".") else str(Path(key)).removeprefix("./")
        facts = out.setdefault(key, ConfigFacts())
        facts.aliases.add(str(svc))
        if spec.get("container_name"):
            facts.aliases.add(str(spec["container_name"]))
        env = spec.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        _scan_properties({f"services.{svc}.environment.{k}": str(v) for k, v in env.items()},
                         repo, rel, lines, facts)
    return out


CONFIG_PATTERNS = (
    "application*.yml", "application*.yaml", "application*.properties", "bootstrap*.yml",
    ".env", ".env.*", "config.env", "package.json", "pyproject.toml", "pom.xml",
    "openapi.*", "swagger.*", "README*", "readme*",
)


def is_config_file(path: Path, rel: str) -> bool:
    from fnmatch import fnmatch

    if any(fnmatch(path.name, p) for p in CONFIG_PATTERNS):
        return True
    return path.suffix in (".yaml", ".yml") and any(k in rel for k in ("k8s", "kubernetes", "deploy"))
