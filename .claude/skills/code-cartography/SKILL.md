---
name: code-cartography
description: Add or improve AST extractors in Blueprint Discoverer's Cartographer Code (new language, framework, HTTP client, messaging or persistence pattern). Use when endpoints or dependencies of a scanned repo are missing, wrong, or land in the unresolved list.
---

# Code cartography

Extractors turn syntax into facts — never business meaning (RULES §2). Facts: `Endpoint` (protocol http |
grpc | graphql | soap), `OutboundCall` (http | grpc | graphql | soap | publish | subscribe | database),
`CodeSymbol`, each with `Evidence(repo, path, line)`. IaC produces `InfraResource`/`InfraRelation`.

## Diagnose first
1. `blueprint scan` then inspect `.blueprint/facts/<repo>.json` for the component.
2. Check `.blueprint/links.json › unresolved` and the wiki "Fila de revisão".
3. Reproduce with the smallest snippet in a unit test (`tests/test_extractors.py` style) before changing code.

## Add a pattern to an existing language
- Java/Kotlin/C#/Go/TS: tree-sitter; inspect the node shape with
  `Parser(Language(tsjava.language())).parse(src)` and walk `node.children` (see `extractors/base.py` helpers).
- Python: stdlib `ast`.
- JVM framework rules (Spring, JAX-RS, Feign, Retrofit, gRPC, GraphQL, SOAP, AWS SDK) live once in
  `extractors/jvm.py`; Java and Kotlin only translate their AST into `TypeDecl`/`Call`. Add JVM patterns there.
- AWS SDK fields (`TableName`, `QueueUrl`, `TopicArn`, `StreamName`, `Bucket`) go through `base.aws_call`, which
  normalises URLs/ARNs and decides publish vs subscribe from the operation name.
- gRPC servers emit `grpc_endpoint("Svc")` (`/Svc/*`); the scanner expands it with the `.proto` methods. gRPC
  clients emit `grpc_client("Svc")` or a `grpc://host:port` channel.
- Contracts (`contracts.py`) and IaC (`cartographer/infra.py`) are parsed separately; the code decides who
  serves a contract.
- Resolve literals only: string constants, `getenv(..., default)`, `process.env.X ?? default`, f-strings/
  template strings built from those. Unresolvable parts stay as `{expr}` placeholders.
- Record the line of the *call/annotation*, not the enclosing function.

## Add a new language
1. Create `extractors/<lang>.py` with a class exposing `language`, `extensions`, `extract(ctx) -> FileFacts`.
2. Register it in `scanner.default_extractors()`; add the grammar wheel to `pyproject.toml`.
3. Add manifest names to `config.MANIFESTS` so components are detected (and name rules to `config.py`).
4. Tests: one per recognised pattern + an example repo under `examples/` if it changes linking.

## Config is code
Base URLs, JDBC/DSN, broker addresses and service names usually live in `application.yml`, `.env`,
`docker-compose.yml` or k8s manifests (`cartographer/code/config.py`). Prefer adding a config rule over a
code heuristic when the wiring is declared there.

## Done when
`pytest -q` passes, the new facts appear with correct evidence, and `blueprint run` stays VALID.
