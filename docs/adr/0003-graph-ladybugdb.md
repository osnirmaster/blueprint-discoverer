# ADR 0003 — LadybugDB como grafo de consulta

**Status:** aceito · 2026-11-02

## Decisão
LadybugDB (embutido, Cypher, sucessor do Kùzu) guarda a projeção do CALM: `Application`, `DataStore`,
`Topic`, `ExternalSystem`, `Endpoint` e a hierarquia BIAN (`ServiceDomain → BusinessDomain → BusinessArea`),
com `DEPENDS_ON`, `EXPOSES`, `REALIZES`. Usado para impacto transitivo, cobertura BIAN e o explorador Cypher.

## Notas de implementação
- Carga em uma única transação; o grafo só é reconstruído quando o hash do CALM muda (`graph.sha256`).
- Propriedades de lista são gravadas como JSON em `STRING`.
- **Python x86_64 sob Rosetta é inviável.** Com Python x86_64 rodando via Rosetta em Apple Silicon
  (ex.: Homebrew em `/usr/local`), ladybug 0.21–0.22 gasta ~60 s de CPU na primeira escrita e em cada
  abertura de uma base com mais de uma tabela, independentemente de opções (`buffer_pool_size`,
  `compression`, threads). Medido em 2026-11-02 num Apple M4 (macOS 26), ladybug 0.22.1, mesmo benchmark:

  | Python | criar + carregar | reabrir + consultar | `pytest` (15 testes) |
  |---|---|---|---|
  | 3.13.16 x86_64 (Rosetta) | 62,5 s | 64,3 s | ~195 s |
  | 3.13.9 arm64 nativo (`uv python install 3.13`) | 0,7 s | 0,05 s | 0,6 s |

- **Use Python arm64 nativo** (`uv python install 3.13` ou instalador do python.org).
- **Dependência de OpenSSL no macOS arm64:** a extensão `_lbug` do wheel arm64 está ligada a
  `/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib`. Com Homebrew arm64 basta `brew install openssl@3`.
  Sem ele (só Homebrew x86 em `/usr/local`), o import falha com "Library not loaded". Contorno local:
  extrair `libssl.3.dylib`/`libcrypto.3.dylib` do bottle `arm64_<versão-do-macOS>` do `openssl@3`
  (ghcr.io/homebrew/core), corrigir os install names com `install_name_tool`, e apontar a `_lbug*.so`
  do venv para eles (`install_name_tool -change` + `codesign -f -s -`). O ajuste se perde ao reinstalar
  o `ladybug`; a solução definitiva é o Homebrew arm64.

## Alternativas consideradas
Neo4j (servidor externo, mais operação), NetworkX (sem persistência/Cypher), consultas direto no JSON
(sem travessia eficiente).
