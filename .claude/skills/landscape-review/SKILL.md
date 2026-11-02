---
name: landscape-review
description: Work through Blueprint Discoverer's review queue — low-confidence dependencies, review-required BIAN classifications, unresolved calls and validation warnings — and record human decisions so the next run is predictable. Use after a discovery run or when asked to curate the as-is landscape.
---

# Landscape review

Source of the queue: `.blueprint/wiki/fila-de-revisao.md` (also `validation.json` warnings and
`links.json › unresolved`). Every decision must be persisted, otherwise the next run repeats the question.

## For each item
| Item | Investigate | Persist the decision |
|---|---|---|
| Classification `review-required` | evidence on the app page; RULES §4.3 | `blueprint.yaml › overrides.classifications` or keyword change (skill `bian-classification`) |
| Dependency confidence < 0.8 | open the evidence lines; confirm the target | improve the rule/alias (skill `code-cartography`); never edit links.json |
| Unresolved call (`{VAR}/path`) | find where `VAR` is defined (env, helm, compose) | add the config file to the scan, or an alias in docker-compose/k8s |
| External system | is it really external (SaaS, regulator, bureau)? | if internal, add its repository to `blueprint.yaml` |
| Shared database | confirm with the owning teams | document in `.blueprint/overrides/<db-id>.md` |

## Close the loop
1. `blueprint run` (graph rebuild only when CALM changed).
2. Confirm the item left the queue and `validation: VALID`.
3. Summarise decisions (item → decision → where persisted) for the architecture team.
