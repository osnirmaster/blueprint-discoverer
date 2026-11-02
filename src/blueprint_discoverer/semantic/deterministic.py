"""Deterministic BIAN classifier: weighted keyword evidence -> ranked service-domain candidates.

Same input always yields the same ranking. This is the floor the LLM layer must respect:
the LLM may re-rank among candidates and add purpose/capabilities, but every domain it proposes must
exist in the closed taxonomy and carry evidence (see RULES.md §4).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from ..model import Component, DomainCandidate, LinkResult, SemanticProfile
from .bian.landscape import Landscape, ServiceDomain

# Where a term was seen says how much business meaning it carries (RULES.md §4.2)
SOURCE_WEIGHTS = {
    "endpoint": 3.0,
    "entity": 3.0,
    "component": 2.5,
    "topic": 2.0,
    "class": 1.5,
    "readme": 1.0,
    "function": 1.0,
    "package": 0.8,
}
STOPWORDS = {
    "com", "br", "org", "api", "app", "src", "main", "java", "service", "services", "controller", "impl",
    "get", "set", "post", "put", "delete", "id", "v1", "v2", "the", "and", "of", "to", "for", "a", "an",
    "dto", "request", "response", "handler", "repository", "client", "config", "util", "utils", "model",
    "de", "da", "do", "e", "o", "para", "bank", "banco", "internal", "core", "domain", "events", "event",
}
SPLIT_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.split(r"[^A-Za-z0-9À-ÿ]+", text):
        for t in SPLIT_RE.findall(raw) or [raw]:
            t = t.lower()
            if t and t not in STOPWORDS and not t.isdigit():
                out.append(_stem(t))
    return out


def _stem(t: str) -> str:
    # deliberately tiny, language-agnostic stemming: plural endings only
    if len(t) <= 4 or t.endswith("ss"):
        return t
    if t.endswith("ies"):
        return t[:-3] + "y"
    if t.endswith(("ches", "shes", "xes", "zes", "sses")):
        return t[:-2]
    return t[:-1] if t.endswith("s") else t


@dataclass
class Term:
    tokens: tuple[str, ...]
    source: str
    ref: str  # Evidence.ref()


def component_terms(comp: Component, links: LinkResult | None = None) -> list[Term]:
    terms: list[Term] = []
    first_ref = ""  # names/packages are self-evident; only line-addressable facts become evidence
    root = "" if comp.root_path in (".", "") else comp.root_path.rstrip("/") + "/"
    for name in {comp.name, *comp.aliases}:
        terms.append(Term(tuple(tokens(name)), "component", first_ref))
    for e in comp.endpoints:
        path = re.sub(r"\{[^}]+\}|:[A-Za-z_]\w*", " ", e.path)
        terms.append(Term(tuple(tokens(path)), "endpoint", e.evidence.ref()))
        if e.handler:
            terms.append(Term(tuple(tokens(e.handler.split(".")[-1])), "function", e.evidence.ref()))
    for s in comp.symbols:
        src = "entity" if s.kind == "entity" else ("class" if s.kind in ("class", "interface") else "function")
        terms.append(Term(tuple(tokens(s.name)), src, s.evidence.ref()))
    for o in comp.outbound:
        if o.kind.value in ("publish", "subscribe"):
            terms.append(Term(tuple(tokens(o.target)), "topic", o.evidence.ref()))
        elif o.kind.value == "database" and o.detail:
            terms.append(Term(tuple(tokens(o.detail)), "entity", o.evidence.ref()))
    for p in comp.packages:
        terms.append(Term(tuple(tokens(p)), "package", first_ref))
    if comp.readme_excerpt:
        for sentence in re.split(r"[.\n]", comp.readme_excerpt):
            if sentence.strip():
                terms.append(Term(tuple(tokens(sentence)), "readme", f"{comp.repo}:{root}README.md:1"))
    return [t for t in terms if t.tokens]


class DeterministicClassifier:
    def __init__(self, landscape: Landscape):
        self.landscape = landscape
        self.keywords: dict[str, list[tuple[str, ...]]] = {
            sd.id: sorted({tuple(tokens(k)) for k in sd.keywords + [sd.name] if tokens(k)})
            for sd in landscape.service_domains
        }
        # IDF over single tokens: 'account' appears in many domains, 'overdraft' in one
        df: dict[str, int] = defaultdict(int)
        for kws in self.keywords.values():
            for tok in {t for kw in kws for t in kw}:
                df[tok] += 1
        n = len(self.keywords)
        self.idf = {tok: math.log(1 + n / c) for tok, c in df.items()}

    def _match(self, kw: tuple[str, ...], term: Term) -> bool:
        if len(kw) == 1:
            return kw[0] in term.tokens
        # phrase: contiguous match inside the term's token sequence
        k = len(kw)
        return any(term.tokens[i : i + k] == kw for i in range(len(term.tokens) - k + 1))

    def rank(self, comp: Component, links: LinkResult | None = None, top: int = 5) -> list[DomainCandidate]:
        return self.rank_with_strength(comp, links, top)[0]

    def rank_with_strength(self, comp: Component, links: LinkResult | None = None,
                           top: int = 5) -> tuple[list[DomainCandidate], float]:
        """Ranked candidates (scores relative to the winner) + absolute evidence strength in [0, 1)."""
        terms = component_terms(comp, links)
        sds = self.landscape.by_id()
        raw: dict[str, float] = {}
        matched: dict[str, set[str]] = defaultdict(set)
        evidence: dict[str, set[str]] = defaultdict(set)
        for sd_id, kws in self.keywords.items():
            score = 0.0
            for term in terms:
                best = 0.0
                for kw in kws:
                    if self._match(kw, term):
                        # phrases are worth more than the sum of their words (specificity bonus)
                        val = sum(self.idf.get(t, 1.0) for t in kw) * (1.5 if len(kw) > 1 else 1.0)
                        if val > best:
                            best = val
                            matched[sd_id].add(" ".join(kw))
                if best:
                    score += best * SOURCE_WEIGHTS[term.source]
                    if term.ref:
                        evidence[sd_id].add(term.ref)
            if score:
                raw[sd_id] = score
        if not raw:
            return [], 0.0
        top_raw = max(raw.values())
        ranked = sorted(raw.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
        out = []
        for sd_id, score in ranked:
            sd: ServiceDomain = sds[sd_id]
            out.append(DomainCandidate(
                service_domain_id=sd_id, service_domain=sd.name, business_domain=sd.business_domain,
                business_area=sd.business_area, score=round(score / top_raw, 4), source="deterministic",
                matched_terms=sorted(matched[sd_id]), evidence=sorted(evidence[sd_id])[:12],
            ))
        # absolute strength of the winner, saturating: 30 weighted points -> 0.75
        return out, top_raw / (top_raw + 10.0)

    def profile(self, comp: Component, links: LinkResult | None = None, min_score: float = 0.15) -> SemanticProfile:
        ranked, strength = self.rank_with_strength(comp, links)
        cands = [c for c in ranked if c.score >= min_score]
        if not cands:
            return SemanticProfile(component_id=comp.id, method="deterministic", confidence=0.0,
                                   review_required=True, rationale="No BIAN keyword evidence found.")
        # margin between #1 and #2 lowers confidence when the call is close
        margin = 1.0 - (cands[1].score if len(cands) > 1 else 0.0)
        confidence = round(min(0.9, strength * (0.6 + 0.4 * margin)), 3)
        primary = cands[0]
        functions = sorted({e.handler.split(".")[-1] for e in comp.endpoints if e.handler})
        return SemanticProfile(
            component_id=comp.id,
            purpose=_template_purpose(comp, primary),
            business_capabilities=[c.service_domain for c in cands[:3]],
            business_functions=[_humanize(f) for f in functions][:15],
            primary_domain=primary,
            secondary_domains=cands[1:],
            confidence=confidence,
            method="deterministic",
            rationale=f"Top terms for {primary.service_domain}: {', '.join(primary.matched_terms[:6])}.",
            review_required=confidence < 0.5,
        )


def _humanize(identifier: str) -> str:
    words = SPLIT_RE.findall(identifier.replace("_", " ")) or [identifier]
    return " ".join(w.lower() for w in words).capitalize()


def _template_purpose(comp: Component, primary: DomainCandidate) -> str:
    kind = {"webclient": "Front-end", "service": "Service"}.get(comp.kind, comp.kind.capitalize())
    return (f"{kind} '{comp.name}' realising the BIAN service domain '{primary.service_domain}' "
            f"({primary.business_domain} / {primary.business_area}).")
