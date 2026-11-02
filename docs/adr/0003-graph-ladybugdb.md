# ADR 0003 — LadybugDB como grafo de consulta

**Status:** aceito · 2026-11-02

## Decisão
LadybugDB (embutido, Cypher, sucessor do Kùzu) guarda a projeção do CALM: `Application`, `DataStore`,
`Topic`, `ExternalSystem`, `Endpoint` e a hierarquia BIAN (`ServiceDomain → BusinessDomain → BusinessArea`),
com `DEPENDS_ON`, `EXPOSES`, `REALIZES`. Usado para impacto transitivo, cobertura BIAN e o explorador Cypher.

## Notas de implementação
- Carga em uma única transação; o grafo só é reconstruído quando o hash do CALM muda (`graph.sha256`).
- Propriedades de lista são gravadas como JSON em `STRING`.
- **Problema conhecido:** com Python x86_64 rodando sob Rosetta em Apple Silicon (ex.: Homebrew em
  `/usr/local`), ladybug 0.21–0.22 gasta ~60 s de CPU na primeira escrita/abertura de uma base com mais de
  uma tabela. Não depende de opções (`buffer_pool_size`, `compression`, threads). Recomendação: usar Python
  arm64 nativo (ex.: `uv python install 3.13` ou instalador do python.org). Validar e reportar upstream.

## Alternativas consideradas
Neo4j (servidor externo, mais operação), NetworkX (sem persistência/Cypher), consultas direto no JSON
(sem travessia eficiente).
