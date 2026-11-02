"""BIAN Service Landscape metamodel: business areas -> business domains -> service domains."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ...workspace import slugify

DEFAULT_LANDSCAPE = Path(__file__).with_name("service_landscape.yaml")


class ServiceDomain(BaseModel):
    id: str
    name: str
    business_area: str
    business_domain: str
    functional_pattern: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)


class Landscape(BaseModel):
    version: str
    service_domains: list[ServiceDomain]

    def by_id(self) -> dict[str, ServiceDomain]:
        return {sd.id: sd for sd in self.service_domains}

    def tree(self) -> dict[str, dict[str, list[ServiceDomain]]]:
        out: dict[str, dict[str, list[ServiceDomain]]] = {}
        for sd in sorted(self.service_domains, key=lambda s: (s.business_area, s.business_domain, s.name)):
            out.setdefault(sd.business_area, {}).setdefault(sd.business_domain, []).append(sd)
        return out

    def catalog_for_prompt(self) -> str:
        """Compact, stable text form of the closed taxonomy handed to the LLM."""
        lines = [f"BIAN Service Landscape {self.version} (closed list - use ids exactly):"]
        for area, domains in self.tree().items():
            lines.append(f"# {area}")
            for dom, sds in domains.items():
                lines.append(f"## {dom}")
                lines += [f"- {sd.id}: {sd.name} [{sd.functional_pattern}] - {sd.description}" for sd in sds]
        return "\n".join(lines)


def _normalise(raw: dict) -> ServiceDomain:
    raw = dict(raw)
    raw.setdefault("id", slugify(raw["name"]))
    kw = raw.get("keywords") or []
    raw["keywords"] = [k.strip() for k in (kw.split(";") if isinstance(kw, str) else kw) if k.strip()]
    return ServiceDomain(**raw)


@lru_cache(maxsize=4)
def load_landscape(path: str | None = None) -> Landscape:
    p = Path(path) if path else DEFAULT_LANDSCAPE
    data = yaml.safe_load(p.read_text())
    return Landscape(version=str(data.get("version", "unknown")),
                     service_domains=[_normalise(sd) for sd in data["service_domains"]])


def import_landscape(source: Path, dest: Path = DEFAULT_LANDSCAPE, merge: bool = True) -> int:
    """Import an official/extended landscape from CSV (name,business_area,business_domain,...) or YAML.

    Existing keywords are preserved on merge so curated vocabulary is not lost when names are refreshed.
    """
    if source.suffix.lower() == ".csv":
        with source.open(newline="") as fh:
            incoming = [_normalise(r) for r in csv.DictReader(fh)]
    else:
        incoming = [_normalise(r) for r in yaml.safe_load(source.read_text())["service_domains"]]
    current = load_landscape(str(dest)).by_id() if (merge and dest.exists()) else {}
    for sd in incoming:
        if sd.id in current and not sd.keywords:
            sd.keywords = current[sd.id].keywords
        current[sd.id] = sd
    doc = {"version": load_landscape(str(dest)).version if dest.exists() else "imported",
           "service_domains": [sd.model_dump() for sd in sorted(current.values(), key=lambda s: s.id)]}
    dest.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    load_landscape.cache_clear()
    return len(incoming)
