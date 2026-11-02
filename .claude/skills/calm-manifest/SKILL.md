---
name: calm-manifest
description: Build, extend, debug or validate the FINOS CALM 1.0 architecture-as-code document produced by Blueprint Discoverer (nodes, relationships, interfaces, metadata profile). Use when the CALM output is invalid, when changing the CALM mapping, or when validating an external CALM file.
---

# CALM manifest

`architecture.calm.json` is the source of truth; graph and wiki are projections (RULES §1.6, §5).

## Validate
```bash
blueprint validate .blueprint/architecture.calm.json            # schema + references + profile
blueprint validate .blueprint/architecture.calm.json --calm-cli # + official npx @finos/calm-cli
```
Read issues by `layer`:
- `schema` → structure violates CALM 1.0 (`schemas/calm/1.0/*.json`). Fix the builder, not the output.
- `reference` → dangling node/interface/container id. Usually an id built with a different `slugify` input.
- `profile` → Blueprint conventions: evidence/rule/confidence on edges, `metadata.source`, valid BIAN id.

## Change the mapping (`src/blueprint_discoverer/calm/builder.py`)
1. Keep standard node types (`service`, `webclient`, `database`, `data-asset`, `system`, `ecosystem`, `actor`,
   `network`, `ldap`). Extra semantics go in `metadata`, never in new node types.
2. Protocols must be in the CALM enum (HTTP, HTTPS, JDBC, AMQP, TCP, mTLS, ...).
3. Every `connects` keeps `metadata.{dependency-kind, confidence, rule, evidence}`.
4. Keep output deterministic: sort nodes by (node-type, unique-id), relationships by unique-id, no timestamps.
5. If the validator must learn a new convention, add it to `calm/validator.py` layer 3 **and** RULES §5.
6. Run `pytest -q tests/test_pipeline.py` (`test_pipeline_produces_valid_calm`, `test_calm_is_deterministic`,
   `test_validator_catches_broken_references`).

## Upgrading the CALM release
Download the new meta schemas into `schemas/calm/<version>/`, point `SCHEMA_DIR`/`CORE_ID` and
`CALM_SCHEMA` to it, run the tests, and update RULES §5.
