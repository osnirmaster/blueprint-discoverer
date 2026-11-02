# Blueprint Discoverer

Mapeador de arquitetura **as-is** para bancos. Escaneia vários repositórios, entende o código por **AST**,
resolve as dependências entre aplicações, infere propósito e domínio de negócio segundo a paisagem
**BIAN** e publica tudo como **architecture-as-code [FINOS CALM](https://calm.finos.org/)** — projetado num
grafo **[LadybugDB](https://docs.ladybugdb.com/)**, numa **wiki viva** e numa UI com o
**[Cloudscape Design System](https://cloudscape.design/)**.

```
repos ─► Cartographer Code (AST) ─► Linker (regras R-*) ─► Semântica (BIAN + Claude) ─► CALM ─► validação
                                                                                         ├─► LadybugDB ─► API ─► UI Cloudscape
                                                                                         └─► Wiki Markdown/Mermaid
```

## Destaques

- **Determinístico por padrão**: extração por AST (tree-sitter para Java/TypeScript, `ast` para Python),
  regras de dependência nomeadas com confiança fixa e classificação BIAN reprodutível. Funciona sem LLM.
- **LLM com rédea curta** (opcional): Claude recebe só a evidência extraída, a taxonomia BIAN fechada e o
  [RULES.md](RULES.md); a resposta passa por guardas e fica em cache endereçado por conteúdo.
- **Tudo com evidência**: cada nó, aresta e classificação aponta para `repo:arquivo:linha`.
- **CALM como fonte da verdade**: validado em 3 camadas (JSON Schema CALM 1.0 offline, integridade
  referencial e perfil do projeto) e, opcionalmente, pelo `@finos/calm-cli`.
- **Curadoria humana persistente**: overrides de classificação e notas por componente sobrevivem às execuções.

## Começando

Requer Python 3.11–3.14 e Node 20+. Em Apple Silicon, use Python **arm64 nativo** e o OpenSSL 3 do
Homebrew arm64 (`brew install openssl@3`): sob Rosetta o LadybugDB leva ~60 s para abrir o grafo,
contra menos de 1 s no arm64 — veja [ADR 0003](docs/adr/0003-graph-ladybugdb.md).

```bash
uv python install 3.13 && uv venv --python 3.13 --seed .venv   # ou python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
```

Rodar no exemplo (5 aplicações: Java/Spring, Python/FastAPI, TypeScript/NestJS e Express):

```bash
cd examples/banking-landscape
../../.venv/bin/blueprint run
../../.venv/bin/blueprint serve        # http://127.0.0.1:8765
```

Seu próprio landscape: crie um `blueprint.yaml`:

```yaml
name: meu-banco-as-is
repositories:
  - { name: contas, path: ../contas }
  - { name: pagamentos, url: https://github.com/minha-org/pagamentos.git, ref: main }
inference:
  min_domain_score: 0.15
  llm: { enabled: true, model: claude-opus-5-5, effort: medium }   # requer ANTHROPIC_API_KEY
overrides:
  classifications:
    contas: current-account
```

## Comandos

| Comando | Etapa |
|---|---|
| `blueprint run` | pipeline completo |
| `blueprint scan` / `link` / `infer` / `calm` / `wiki` | etapas isoladas (cada uma lê o artefato anterior) |
| `blueprint validate <arquivo.calm.json> [--calm-cli]` | valida qualquer documento CALM |
| `blueprint bian list` / `bian import <csv>` | consulta/importa a paisagem BIAN |
| `blueprint serve` | API REST + UI |

Artefatos em `.blueprint/`: `facts/`, `links.json`, `semantic/`, `architecture.calm.json`,
`validation.json`, `graph.lbdb`, `wiki/`, `cache/llm/`, `overrides/`.

## UI (Cloudscape)

Visão geral · Aplicações (tabela filtrável) · Detalhe da aplicação (BIAN, APIs, dependências, impacto, nó CALM) ·
Mapa de dependências (cytoscape, filtros por tipo/domínio/confiança) · Mapa BIAN (cobertura) ·
Manifesto CALM (validação + JSON) · Wiki · Explorador Cypher.

Desenvolvimento: `blueprint serve` em um terminal e `cd web && npm run dev` em outro (proxy para `:8765`).

## Para agentes e contribuidores

- [AGENTS.md](AGENTS.md) — mapa do código, comandos e regras de trabalho.
- [RULES.md](RULES.md) — contrato de inferência (também usado como prompt de sistema).
- `.claude/skills/` — `code-cartography`, `bian-classification`, `calm-manifest`, `landscape-review`.
- [docs/architecture.md](docs/architecture.md) e [docs/adr/](docs/adr/).

## Sobre a paisagem BIAN

`semantic/bian/service_landscape.yaml` traz um **subconjunto curado** alinhado à nomenclatura da
[BIAN Service Landscape 8.0](https://bian.org/servicelandscape-8-0/index.html), com vocabulário EN + PT-BR.
Importe a lista oficial com `blueprint bian import` para cobertura completa — as palavras-chave curadas são preservadas.
