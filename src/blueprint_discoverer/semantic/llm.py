"""LLM enrichment (Claude) on top of the deterministic classifier.

Predictability levers, in order of strength:
  1. Closed taxonomy - the model may only answer with BIAN ids from the landscape; anything else is dropped.
  2. Structured output - the response is parsed into `LlmClassification` (schema-validated by the API).
  3. Evidence pack - the model sees only facts extracted by the AST layer, each with a citable ref.
  4. Guards - post-validation against the deterministic ranking; disagreement => review_required.
  5. Content-addressed cache - identical evidence + rules + model => identical answer, no new call.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import anthropic
from pydantic import BaseModel

from ..model import Component, DomainCandidate, LinkResult, SemanticProfile
from ..workspace import read_json, resource_path, write_json
from .bian.landscape import Landscape

log = logging.getLogger(__name__)

PROMPT_VERSION = "2026-11-02.2"
MAX_ITEMS = {"endpoints": 60, "symbols": 80, "outbound": 40}


class LlmClassification(BaseModel):
    purpose: str
    objectives: list[str]
    business_capabilities: list[str]
    business_functions: list[str]
    primary_service_domain_id: str
    secondary_service_domain_ids: list[str]
    confidence: float
    rationale: str
    evidence_refs: list[str]


def load_rules() -> str:
    return resource_path("RULES.md", "_rules/RULES.md").read_text()


def evidence_pack(comp: Component, links: LinkResult, candidates: list[DomainCandidate]) -> dict:
    """Compact, deterministic (sorted) view of what the code proves about a component."""
    out_links = [
        {"to": l.destination, "kind": l.kind.value, "what": l.description, "rule": l.rule}
        for l in links.links if l.source == comp.id
    ]
    in_links = [{"from": l.source, "kind": l.kind.value, "what": l.description}
                for l in links.links if l.destination == comp.id]
    symbols = sorted(comp.symbols, key=lambda s: (s.kind != "entity", s.kind, s.name))
    return {
        "component": {"id": comp.id, "name": comp.name, "repo": comp.repo, "kind": comp.kind,
                      "languages": comp.languages, "frameworks": comp.frameworks, "packages": comp.packages},
        "readme_excerpt": comp.readme_excerpt[:1200],
        "endpoints": [{"method": e.method, "path": e.path, "handler": e.handler, "ref": e.evidence.ref()}
                      for e in comp.endpoints[: MAX_ITEMS["endpoints"]]],
        "symbols": [{"kind": s.kind, "name": s.name, "ref": s.evidence.ref()}
                    for s in symbols[: MAX_ITEMS["symbols"]]],
        "messaging_and_data": [{"kind": o.kind.value, "target": o.target or o.detail, "ref": o.evidence.ref()}
                               for o in comp.outbound if o.kind.value != "http"][: MAX_ITEMS["outbound"]],
        "depends_on": sorted(out_links, key=lambda x: x["to"]),
        "used_by": sorted(in_links, key=lambda x: x["from"]),
        "deterministic_candidates": [
            {"id": c.service_domain_id, "name": c.service_domain, "score": c.score, "matched": c.matched_terms}
            for c in candidates
        ],
        "truncation": {k: v for k, v in MAX_ITEMS.items()},
    }


class LlmInferencer:
    def __init__(self, landscape: Landscape, cache_dir: Path, model: str = "claude-opus-5-5",
                 effort: str = "medium", client: anthropic.Anthropic | None = None):
        self.landscape = landscape
        self.cache_dir = cache_dir
        self.model = model
        self.effort = effort
        self.rules = load_rules()
        self._client = client
        self.system_text = (
            "You are the semantic analyst of Blueprint Discoverer, an as-is architecture mapper for banks.\n"
            "Classify ONE software component using only the evidence pack the user sends.\n"
            "Follow the project rules below exactly; they override any instinct to speculate.\n\n"
            f"<rules>\n{self.rules}\n</rules>\n\n<taxonomy>\n{landscape.catalog_for_prompt()}\n</taxonomy>"
        )

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def _cache_key(self, pack: dict) -> str:
        h = hashlib.sha256()
        for part in (PROMPT_VERSION, self.model, self.effort, self.system_text,
                     json.dumps(pack, sort_keys=True, ensure_ascii=False)):
            h.update(part.encode())
        return h.hexdigest()[:32]

    def classify(self, comp: Component, links: LinkResult, det: SemanticProfile,
                 candidates: list[DomainCandidate]) -> SemanticProfile:
        pack = evidence_pack(comp, links, candidates)
        key = self._cache_key(pack)
        cache_file = self.cache_dir / f"{comp.id}-{key}.json"
        method = "llm-cached"
        if cache_file.exists():
            result = LlmClassification.model_validate(read_json(cache_file))
        else:
            result = self._call(pack)
            if result is None:
                det.rationale += " (LLM unavailable or declined; deterministic result kept.)"
                return det
            write_json(cache_file, result)
            method = "llm"
        return self._guard(comp, det, candidates, result, pack, method)

    def _call(self, pack: dict) -> LlmClassification | None:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.effort},
                system=[{"type": "text", "text": self.system_text, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": (
                    "Classify this component. Evidence pack (JSON):\n"
                    + json.dumps(pack, indent=1, sort_keys=True, ensure_ascii=False))}],
                output_format=LlmClassification,
            )
        except anthropic.RateLimitError as exc:
            log.warning("rate limited by the Claude API: %s", exc)
            return None
        except anthropic.APIStatusError as exc:
            log.warning("Claude API error %s: %s", exc.status_code, exc.message)
            return None
        except anthropic.APIConnectionError as exc:
            log.warning("cannot reach the Claude API: %s", exc)
            return None
        if response.stop_reason == "refusal":
            log.warning("model declined to classify (category=%s)",
                        getattr(response.stop_details, "category", None))
            return None
        return response.parsed_output

    def _guard(self, comp: Component, det: SemanticProfile, candidates: list[DomainCandidate],
               r: LlmClassification, pack: dict, method: str) -> SemanticProfile:
        sds = self.landscape.by_id()
        allowed_refs = {item["ref"] for section in ("endpoints", "symbols", "messaging_and_data")
                        for item in pack[section]}
        notes = []

        def cand(sd_id: str, score: float) -> DomainCandidate:
            sd = sds[sd_id]
            det_c = next((c for c in candidates if c.service_domain_id == sd_id), None)
            return DomainCandidate(
                service_domain_id=sd_id, service_domain=sd.name, business_domain=sd.business_domain,
                business_area=sd.business_area, score=round(score, 4),
                source="merged" if det_c else "llm",
                matched_terms=det_c.matched_terms if det_c else [],
                evidence=sorted({ref for ref in r.evidence_refs if ref in allowed_refs}
                                | set(det_c.evidence if det_c else []))[:12],
            )

        if r.primary_service_domain_id not in sds:
            notes.append(f"LLM proposed unknown domain '{r.primary_service_domain_id}' (discarded)")
            return det.model_copy(update={"review_required": True,
                                          "rationale": det.rationale + " " + "; ".join(notes)})
        llm_conf = max(0.0, min(1.0, r.confidence))
        det_ids = [c.service_domain_id for c in candidates]
        agrees = det.primary_domain is not None and det.primary_domain.service_domain_id == r.primary_service_domain_id
        if agrees:
            confidence = round(min(0.97, 0.5 * llm_conf + 0.5 * max(det.confidence, 0.5) + 0.1), 3)
        elif r.primary_service_domain_id in det_ids:
            confidence = round(min(llm_conf, 0.75), 3)
            notes.append("LLM re-ranked deterministic candidates")
        else:
            confidence = round(min(llm_conf, 0.6), 3)
            notes.append("LLM primary domain has no deterministic keyword support")
        dropped = [x for x in r.secondary_service_domain_ids if x not in sds]
        if dropped:
            notes.append(f"dropped unknown secondary ids {dropped}")
        secondaries = [cand(x, 0.5) for x in r.secondary_service_domain_ids
                       if x in sds and x != r.primary_service_domain_id][:4]
        return SemanticProfile(
            component_id=comp.id,
            purpose=r.purpose.strip(),
            objectives=[o.strip() for o in r.objectives][:6],
            business_capabilities=[c.strip() for c in r.business_capabilities][:8],
            business_functions=[f.strip() for f in r.business_functions][:15],
            primary_domain=cand(r.primary_service_domain_id, 1.0),
            secondary_domains=secondaries,
            confidence=confidence,
            method=method,
            rationale=(r.rationale.strip() + (" | guards: " + "; ".join(notes) if notes else "")),
            review_required=confidence < 0.5 or not agrees and r.primary_service_domain_id not in det_ids,
        )
