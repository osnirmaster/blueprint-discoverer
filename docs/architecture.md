# Arquitetura do Blueprint Discoverer

## Camadas

| Camada | Tipo | Saída | Determinística? |
|---|---|---|---|
| Cartographer Code | AST (tree-sitter Java/TS, `ast` Python) + configuração | `facts/<repo>.json` | sim |
| Linker | regras nomeadas R-* (RULES §3) | `links.json` | sim |
| Semântica — determinística | palavras-chave BIAN ponderadas + IDF | `semantic/<id>.json` | sim |
| Semântica — LLM | Claude + taxonomia fechada + guardas + cache | `semantic/<id>.json` | reprodutível via cache |
| CALM | builder + validador (schema, referências, perfil) | `architecture.calm.json`, `validation.json` | sim |
| Grafo | LadybugDB (projeção do CALM) | `graph.lbdb` | sim |
| Wiki | Markdown + Mermaid | `wiki/` | sim |
| UI | React + Cloudscape + cytoscape | `web/dist` | — |

## Modelo do grafo

```
(Application)-[:DEPENDS_ON]->(Application|DataStore|Topic|ExternalSystem)
(Topic)-[:DEPENDS_ON]->(Application)            # entrega a consumidores
(Application)-[:EXPOSES]->(Endpoint)
(Application)-[:REALIZES {is_primary}]->(ServiceDomain)-[:IN_BUSINESS_DOMAIN]->(BusinessDomain)-[:IN_BUSINESS_AREA]->(BusinessArea)
```

## Próximos cartógrafos (roadmap)
- **cartographer-infra:** Terraform/Helm/Kubernetes → `deployed-in`, redes, ambientes.
- **cartographer-runtime:** traces OpenTelemetry/APM para confirmar arestas (confiança → 1.0).
- **cartographer-data:** schemas de banco/DDL e contratos Avro/Protobuf de tópicos.
- Linguagens: C#/.NET, Go, Kotlin e COBOL/JCL para o legado de core banking.
- Fluxos CALM (`flows`) por jornada de negócio (ex.: Pix, abertura de conta).
