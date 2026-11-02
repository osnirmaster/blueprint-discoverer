"""Semantic engine: deterministic classification first, optional Claude enrichment second."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..model import Component, DomainCandidate, LinkResult, SemanticProfile
from .bian.landscape import Landscape, load_landscape
from .deterministic import DeterministicClassifier
from .llm import LlmInferencer

log = logging.getLogger(__name__)


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE"))


class SemanticEngine:
    def __init__(self, cache_dir: Path, use_llm: bool = False, model: str = "claude-opus-5-5",
                 effort: str = "medium", min_score: float = 0.15, landscape: Landscape | None = None,
                 overrides: dict[str, str] | None = None):
        self.landscape = landscape or load_landscape()
        self.overrides = overrides or {}
        unknown = {v for v in self.overrides.values() if v not in self.landscape.by_id()}
        if unknown:
            raise ValueError(f"classification overrides reference unknown BIAN ids: {sorted(unknown)}")
        self.det = DeterministicClassifier(self.landscape)
        self.min_score = min_score
        self.llm: LlmInferencer | None = None
        if use_llm:
            if llm_available():
                self.llm = LlmInferencer(self.landscape, cache_dir, model=model, effort=effort)
            else:
                log.warning("LLM enrichment requested but no Anthropic credentials found; "
                            "running deterministic-only.")

    def infer(self, comp: Component, links: LinkResult) -> SemanticProfile:
        candidates = self.det.rank(comp, links)
        profile = self.det.profile(comp, links, self.min_score)
        if self.llm is not None:
            profile = self.llm.classify(comp, links, profile, candidates)
        if comp.id in self.overrides:
            profile = self._apply_override(profile, self.overrides[comp.id])
        return profile

    def _apply_override(self, profile: SemanticProfile, sd_id: str) -> SemanticProfile:
        sd = self.landscape.by_id()[sd_id]
        primary = DomainCandidate(service_domain_id=sd.id, service_domain=sd.name, business_domain=sd.business_domain,
                                  business_area=sd.business_area, score=1.0, source="human",
                                  evidence=profile.primary_domain.evidence if profile.primary_domain else [])
        secondary = [c for c in ([profile.primary_domain] if profile.primary_domain else []) + profile.secondary_domains
                     if c.service_domain_id != sd_id]
        return profile.model_copy(update={
            "primary_domain": primary, "secondary_domains": secondary, "confidence": 1.0, "method": "human",
            "review_required": False, "rationale": f"Human override (blueprint.yaml). Inferred: {profile.rationale}",
        })

    def infer_all(self, components: list[Component], links: LinkResult) -> list[SemanticProfile]:
        return [self.infer(c, links) for c in sorted(components, key=lambda c: c.id)]
