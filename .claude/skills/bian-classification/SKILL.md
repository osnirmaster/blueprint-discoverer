---
name: bian-classification
description: Classify or review the BIAN Service Landscape mapping (business area, business domain, service domain, capabilities) of a component discovered by Blueprint Discoverer. Use when a classification looks wrong, is flagged review-required, when tuning BIAN keywords, or when adding service domains.
---

# BIAN classification

Goal: every application maps to the BIAN service domain whose **control record** it maintains,
backed by evidence, with predictable confidence. The contract is RULES.md §4 — re-read it first.

## Inputs to gather
1. `.blueprint/facts/<repo>.json` → the component (endpoints, symbols, outbound).
2. `.blueprint/semantic/<component>.json` → current profile (method, confidence, rationale, candidates).
3. `blueprint bian list` → the closed taxonomy (ids are the only valid answers).
4. Deterministic ranking: `python -c` with `DeterministicClassifier(load_landscape()).rank(comp)`.

## Procedure
1. List the control-record evidence: entities/tables + write endpoints (POST/PUT/PATCH/DELETE) first.
2. Name the business object they manage; find the service domain whose description owns that object.
3. Compare with `deterministic_candidates`:
   - same answer → done; if confidence is low, the vocabulary is missing (step 5).
   - different answer → decide using RULES §4.3 disambiguation; document why.
4. Choose the fix with the **narrowest blast radius**:
   - one component is special → `blueprint.yaml › overrides.classifications.<component-id>: <sd-id>`;
   - a term is systematically missed (e.g. "pix", "boleto", "consignado") → add it to the service domain
     `keywords` in `semantic/bian/service_landscape.yaml` (EN + PT-BR, singular form, phrases > single words);
   - a domain is missing → add it to the landscape (name, business_area, business_domain, functional_pattern,
     description, keywords) or import the official list with `blueprint bian import <csv>`.
5. Re-run `blueprint infer` then `blueprint calm`; check the wiki page and the review queue.
6. Add/adjust an assertion in `tests/test_pipeline.py::test_bian_classification` when the change covers the example.

## Never
- Invent BIAN names or ids; use only the loaded landscape.
- Raise confidence by hand; fix evidence or vocabulary instead.
- Put generic tokens (`service`, `api`, `data`) in keywords — they are stopwords for a reason.

## Output when asked to classify manually
```json
{"component": "<id>", "primary": "<sd-id>", "secondary": ["<sd-id>"], "confidence": 0.0,
 "evidence": ["repo:path:line"], "rationale": "<pt-BR, cite decisive terms>"}
```
