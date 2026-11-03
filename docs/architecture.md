# Arquitetura do Blueprint Discoverer

## Camadas

| Camada | Tipo | Saída | Determinística? |
|---|---|---|---|
| Cartographer Code | AST (tree-sitter Java/Kotlin/C#/Go/TS, `ast` Python) + configuração + contratos (`.proto`, GraphQL, WSDL) | `facts/<repo>.json` | sim |
| Cartographer IaC | Terraform (tree-sitter-hcl), CloudFormation/SAM | `facts/<repo>.json › infrastructure` | sim |
| Linker | regras nomeadas R-* (RULES §3) | `links.json` | sim |
| Semântica — determinística | palavras-chave BIAN ponderadas + IDF | `semantic/<id>.json` | sim |
| Semântica — LLM | Claude + taxonomia fechada + guardas + cache | `semantic/<id>.json` | reprodutível via cache |
| CALM | builder + validador (schema, referências, perfil) | `architecture.calm.json`, `validation.json` | sim |
| Grafo | LadybugDB (projeção do CALM) | `graph.lbdb` | sim |
| Wiki | Markdown + Mermaid | `wiki/` | sim |
| UI | React + Cloudscape + React Flow (canvas no estilo do Infrastructure Composer) | `web/dist` | — |

## Modelo do grafo

```
(Application)-[:DEPENDS_ON]->(Application|DataStore|Topic|ExternalSystem)   # downstream -> upstream
(Topic)-[:DEPENDS_ON]->(Application|Topic)      # entrega a consumidores; assinaturas SNS -> SQS
(DataStore)-[:DEPENDS_ON]->(Application)        # streams DynamoDB / notificações S3 acionando funções
(Application)-[:EXPOSES]->(Endpoint)
(Application)-[:REALIZES {is_primary}]->(ServiceDomain)-[:IN_BUSINESS_DOMAIN]->(BusinessDomain)-[:IN_BUSINESS_AREA]->(BusinessArea)
```

## Próximos cartógrafos (roadmap)
- **cartographer-infra:** além de recursos AWS (feito), Helm/Kubernetes → `deployed-in`, redes, ambientes;
  outros provedores (Azure Service Bus, GCP Pub/Sub).
- **cartographer-runtime:** traces OpenTelemetry/APM para confirmar arestas (confiança → 1.0).
- **cartographer-data:** schemas de banco/DDL e contratos Avro/Protobuf de tópicos.
- Linguagens: COBOL/JCL e CICS para o legado de core banking (Java, Kotlin, C#, Go, TS e Python já cobertos).
- Fluxos CALM (`flows`) por jornada de negócio (ex.: Pix, abertura de conta).
