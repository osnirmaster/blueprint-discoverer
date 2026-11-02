# ADR 0001 — CALM é a fonte da verdade da arquitetura as-is

**Status:** aceito · 2026-11-02

## Contexto
Precisamos de uma representação de nós e relacionamentos que seja versionável, validável e interoperável
com o ecossistema de architecture-as-code. Grafo, wiki e UI precisam enxergar exatamente a mesma verdade.

## Decisão
O pipeline emite um documento **FINOS CALM 1.0** (`architecture.calm.json`) determinístico. O grafo
LadybugDB e a wiki são **projeções** reconstruídas a partir dele; nunca recebem escrita direta.
Convenções extras (evidência, regra, confiança, BIAN) vão em `metadata`, mantendo os tipos padrão do CALM.

## Consequências
- Diff de arquitetura = diff de JSON no git; revisão por PR.
- O validador oficial (`@finos/calm-cli`) e ferramentas CALM (docify, visualizer) funcionam sem adaptação.
- Qualquer consulta que o JSON não suporte bem (travessias, impacto) é feita no grafo.
