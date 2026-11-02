# AGENTS.md

Guia para agentes (Claude Code, Codex, Copilot...) e pessoas que trabalham neste repositório.
As regras de inferência e do manifesto estão em [RULES.md](RULES.md) — leia antes de mexer em linker,
semântica ou CALM.

## O que é

Blueprint Discoverer mapeia a arquitetura **as-is** de um banco: escaneia vários repositórios, extrai fatos
por AST, resolve dependências entre aplicações, classifica cada aplicação na paisagem BIAN e publica tudo
como **architecture-as-code FINOS CALM**, projetado num grafo **LadybugDB**, numa **wiki** Markdown e numa
UI **Cloudscape**.

```
repos ─► Cartographer Code (AST + config) ─► facts/*.json
             └─► Linker (regras R-*)        ─► links.json
                    └─► Semântica (BIAN determinístico ► Claude opcional ► guardas ► overrides) ─► semantic/*.json
                           └─► CALM builder ─► architecture.calm.json ─► validador (schema+refs+perfil)
                                                     ├─► LadybugDB (graph.lbdb) ─► API FastAPI ─► UI Cloudscape
                                                     └─► wiki/*.md (Mermaid)
```

## Mapa do código

| Caminho | Responsabilidade |
|---|---|
| `src/blueprint_discoverer/cartographer/code/` | scanner, detecção de componentes, extratores AST (`extractors/java.py`, `typescript.py`, `python.py`), configuração (`config.py`) |
| `src/blueprint_discoverer/linker/resolver.py` | regras determinísticas de dependência (tabela em RULES §3) |
| `src/blueprint_discoverer/semantic/` | paisagem BIAN, classificador determinístico, camada LLM (`llm.py`) e motor (`inference.py`) |
| `src/blueprint_discoverer/calm/` | construção (`builder.py`) e validação (`validator.py`) do CALM |
| `src/blueprint_discoverer/graph/store.py` | projeção LadybugDB + consultas (impacto, cobertura BIAN) |
| `src/blueprint_discoverer/wiki/generator.py` | wiki Markdown/Mermaid |
| `src/blueprint_discoverer/api/app.py` | API REST para a UI |
| `web/` | UI React + Cloudscape (cytoscape para o grafo) |
| `schemas/calm/1.0/` | meta-schemas CALM vendorizados (validação offline) |
| `examples/banking-landscape/` | 5 repositórios de exemplo (Java/Spring, Python/FastAPI, TS/Nest/Express) |
| `.claude/skills/` | skills que guiam agentes nas tarefas recorrentes |

## Comandos

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Python 3.11–3.14
.venv/bin/pytest -q                                              # testes
cd examples/banking-landscape && ../../.venv/bin/blueprint run   # pipeline completo
.venv/bin/blueprint serve -c examples/banking-landscape/blueprint.yaml   # API + UI em :8765
cd web && npm install && npm run dev                              # UI em modo dev (:5173, proxy p/ :8765)
cd web && npm run typecheck && npm run build                      # checagem e build da UI
```

## Regras de trabalho

1. **Determinismo é requisito.** Saídas ordenadas, ids por `slugify`, sem timestamps no CALM. O teste
   `test_calm_is_deterministic` precisa continuar passando.
2. **Nada sem evidência.** Novo fato ⇒ `Evidence(repo, path, line)`. Nova aresta ⇒ regra `R-*` nomeada com
   confiança fixa documentada em RULES §3.
3. **Extratores não interpretam negócio.** Eles registram sintaxe; significado é papel da camada semântica.
4. **Taxonomia BIAN fechada.** Novo service domain vai em `service_landscape.yaml` (ou via
   `blueprint bian import`), nunca hard-coded.
5. **Mudou RULES.md?** Incremente `PROMPT_VERSION` em `semantic/llm.py`.
6. **Artefatos gerados não são editados à mão** (`.blueprint/`, `web/dist/`). Curadoria vai em
   `blueprint.yaml › overrides` ou `.blueprint/overrides/<id>.md`.
7. **Claude API:** modelo padrão `claude-opus-5-5`, saída estruturada (`messages.parse`), `fallbacks: "default"`,
   tratar `stop_reason == "refusal"`. Sem credenciais o pipeline segue só determinístico.
8. Código e comentários em inglês; wiki, UI e documentação de usuário em português.

## Papéis (agentes do sistema)

| Agente | Faz | Skill |
|---|---|---|
| **Cartographer** | adiciona/ajusta extratores de linguagem/framework | `.claude/skills/code-cartography` |
| **Semantic Analyst** | revisa/explica classificações BIAN, ajusta palavras-chave e overrides | `.claude/skills/bian-classification` |
| **CALM Steward** | mantém mapeamento e validação do manifesto CALM | `.claude/skills/calm-manifest` |
| **Landscape Reviewer** | trabalha a fila de revisão (baixa confiança, não resolvidos) | `.claude/skills/landscape-review` |

## Problemas conhecidos

- **LadybugDB sob Rosetta:** com Python x86_64 rodando em Apple Silicon, criar ou abrir o banco custa ~60 s
  de CPU; no arm64 nativo leva menos de 1 s. Use um Python **arm64** nativo
  (`uv python install 3.13`). No macOS o wheel arm64 do ladybug precisa do OpenSSL 3 em
  `/opt/homebrew/opt/openssl@3` (`brew install openssl@3` com Homebrew arm64). Medições e contorno sem
  Homebrew arm64 em `docs/adr/0003-graph-ladybugdb.md`.
