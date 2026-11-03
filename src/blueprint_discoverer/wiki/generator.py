"""Living wiki: Markdown + Mermaid pages regenerated from the CALM document on every run.

Generated pages are never edited by hand. Human knowledge goes into `<workspace>/overrides/<id>.md`
and is merged into the page under "Curadoria" so it survives regeneration.
"""

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path

from ..graph.store import GraphStore
from ..model import OutboundCall
from ..workspace import slugify

HEADER = "<!-- Gerado por blueprint-discoverer. Não edite: use overrides/{id}.md para curadoria. -->\n"
TYPE_LABEL = {"service": "Serviço", "webclient": "Front-end", "database": "Banco de dados",
              "data-asset": "Tópico/Fila", "system": "Sistema externo", "ecosystem": "Domínio BIAN"}


PROTOCOL_LABEL = {"http": "REST", "grpc": "gRPC", "graphql": "GraphQL", "soap": "SOAP"}
ASSET_LABEL = {"messaging-topic": "Tópico", "messaging-queue": "Fila", "event-stream": "Stream",
               "object-storage": "Bucket"}


def _asset_kind(n: dict) -> str:
    return (n.get("metadata") or {}).get("asset-kind", "")


def _type_label(n: dict) -> str:
    return ASSET_LABEL.get(_asset_kind(n)) or TYPE_LABEL.get(n.get("node-type", ""), n.get("node-type", "?"))


def _mid(node_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", node_id)


def _mermaid_node(n: dict) -> str:
    label = n["name"].replace('"', "'")
    nid = _mid(n["unique-id"])
    t = n["node-type"]
    if t == "database":
        return f'{nid}[("{label}")]'
    if _asset_kind(n) == "object-storage":
        return f'{nid}[/"{label}"/]'
    if t == "data-asset":
        return f'{nid}>"{label}"]'
    if t == "system":
        return f'{nid}{{{{"{label}"}}}}'
    if t == "webclient":
        return f'{nid}(["{label}"])'
    return f'{nid}["{label}"]'


def _mermaid(nodes: list[dict], rels: list[dict], highlight: str | None = None) -> str:
    ids = {n["unique-id"] for n in nodes}
    lines = ["```mermaid", "flowchart LR"]
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        dom = ((n.get("metadata") or {}).get("bian") or {}).get("business-domain") if n["node-type"] in (
            "service", "webclient") else None
        by_domain[dom or ""].append(n)
    for dom, members in sorted(by_domain.items()):
        if dom:
            lines.append(f'  subgraph {_mid("bd_" + dom)}["BIAN · {dom}"]')
        lines += [("    " if dom else "  ") + _mermaid_node(n) for n in members]
        if dom:
            lines.append("  end")
    for r in rels:
        c = r["relationship-type"].get("connects")
        if not c or c["source"]["node"] not in ids or c["destination"]["node"] not in ids:
            continue
        conf = (r.get("metadata") or {}).get("confidence", 1)
        arrow = "-.->" if conf < 0.8 else "-->"
        kind = (r.get("metadata") or {}).get("dependency-kind", "")
        label = PROTOCOL_LABEL[kind] if kind in ("grpc", "graphql", "soap") else r.get("protocol", "")
        lines.append(f'  {_mid(c["source"]["node"])} {arrow}|{label}| {_mid(c["destination"]["node"])}')
    if highlight:
        lines.append(f"  style {_mid(highlight)} stroke:#0972d3,stroke-width:3px")
    lines.append("```")
    return "\n".join(lines)


def _ev_link(ref: str, sources: dict[str, dict]) -> str:
    """repo:path:line -> markdown link to the file when the repo location is known."""
    parts = ref.split(":")
    if len(parts) < 3:
        return f"`{ref}`"
    repo, path, line = parts[0], ":".join(parts[1:-1]), parts[-1]
    src = sources.get(repo)
    if src and src.get("location"):
        return f"[`{path}:{line}`]({Path(src['location']).as_uri()}/{path}#L{line})"
    return f"`{repo}/{path}:{line}`"


class WikiGenerator:
    def __init__(self, calm: dict, out_dir: Path, graph: GraphStore | None = None,
                 validation: dict | None = None, unresolved: list[OutboundCall] | None = None,
                 overrides_dir: Path | None = None):
        self.calm = calm
        self.out = out_dir
        self.graph = graph
        self.validation = validation or {}
        self.unresolved = unresolved or []
        self.overrides_dir = overrides_dir
        self.nodes = {n["unique-id"]: n for n in calm.get("nodes", [])}
        self.rels = calm.get("relationships", [])
        self.apps = [n for n in calm["nodes"] if n["node-type"] in ("service", "webclient")]
        self.sources = {}
        for n in self.apps:
            src = (n.get("metadata") or {}).get("source", {})
            self.sources[src.get("repository", "")] = src

    def _connects(self):
        for r in self.rels:
            c = r["relationship-type"].get("connects")
            if c:
                yield r, c["source"]["node"], c["destination"]["node"]

    def _override(self, node_id: str) -> str:
        if self.overrides_dir is None:
            return ""
        f = self.overrides_dir / f"{node_id}.md"
        return f"\n## Curadoria\n\n{f.read_text().strip()}\n" if f.exists() else ""

    def generate(self) -> list[Path]:
        if self.out.exists():
            shutil.rmtree(self.out)
        (self.out / "aplicacoes").mkdir(parents=True)
        (self.out / "bian").mkdir()
        written = [self._index(), self._data_and_messaging(), self._review_queue(), self._sidebar()]
        written += [self._app_page(a) for a in self.apps]
        written += self._bian_pages()
        return written

    def _write(self, rel: str, content: str, node_id: str = "index") -> Path:
        path = self.out / rel
        path.write_text(HEADER.format(id=node_id) + content.rstrip() + "\n")
        return path

    # -------------------------------------------------------------- pages
    def _index(self) -> Path:
        stats = self.validation.get("stats", {})
        valid = self.validation.get("valid")
        rows = []
        for a in sorted(self.apps, key=lambda n: n["name"]):
            meta = a.get("metadata") or {}
            bian, sem = meta.get("bian", {}), meta.get("semantics", {})
            flag = "⚠️ revisar" if sem.get("review-required") else "✅"
            rows.append(f"| [{a['name']}](aplicacoes/{a['unique-id']}.md) | {TYPE_LABEL.get(a['node-type'])} | "
                        f"{bian.get('service-domain', '—')} | {bian.get('business-domain', '—')} | "
                        f"{sem.get('confidence', 0):.2f} | {flag} |")
        md = [
            f"# {self.calm.get('name', 'Paisagem as-is')}", "",
            self.calm.get("description", ""), "",
            "## Resumo", "",
            f"- Aplicações: **{stats.get('services', len(self.apps))}** · Bancos de dados: **{stats.get('databases', 0)}**"
            f" · Tópicos: **{stats.get('topics', 0)}** · Sistemas externos: **{stats.get('external-systems', 0)}**",
            f"- Relacionamentos: **{stats.get('relationships', len(self.rels))}**",
            f"- Manifesto CALM: **{'válido' if valid else 'com erros' if valid is not None else 'não validado'}**"
            f" ({len(self.validation.get('errors', []))} erros, {len(self.validation.get('warnings', []))} avisos)",
            "", "## Mapa de dependências", "", _mermaid(list(self.nodes.values()), self.rels), "",
            "## Aplicações", "",
            "| Aplicação | Tipo | Service Domain (BIAN) | Business Domain | Confiança | Status |",
            "|---|---|---|---|---|---|", *rows, "",
            "Veja também: [Dados e mensageria](dados-e-mensageria.md) · [Fila de revisão](fila-de-revisao.md)",
        ]
        return self._write("index.md", "\n".join(md))

    def _app_page(self, a: dict) -> Path:
        aid = a["unique-id"]
        meta = a.get("metadata") or {}
        sem, bian, tech, src = (meta.get(k, {}) for k in ("semantics", "bian", "technology", "source"))
        outs = [(r, d) for r, s, d in self._connects() if s == aid]
        ins = [(r, s) for r, s, d in self._connects() if d == aid]
        neighbours = {aid} | {d for _, d in outs} | {s for _, s in ins}
        # topic consumers/producers one hop away make async flows readable
        md = [f"# {a['name']}", "", f"> {sem.get('purpose') or a['description']}", ""]
        if sem.get("review-required"):
            md += ["> ⚠️ **Classificação requer revisão humana** "
                   f"(confiança {sem.get('confidence', 0):.2f}, método `{sem.get('method')}`).", ""]
        md += ["## Propósito e objetivos", ""]
        md += [f"- {o}" for o in sem.get("objectives", [])] or ["- _Não inferido (habilite a camada LLM)._"]
        md += ["", "## Classificação BIAN", ""]
        if bian:
            md += ["| Business Area | Business Domain | Service Domain |", "|---|---|---|",
                   f"| {bian['business-area']} | [{bian['business-domain']}](../bian/{slugify(bian['business-domain'])}.md)"
                   f" | **{bian['service-domain']}** (`{bian['service-domain-id']}`) |", ""]
            if bian.get("secondary"):
                md += ["Domínios secundários: " + ", ".join(
                    f"{s['service-domain']} ({s['score']:.2f})" for s in bian["secondary"]), ""]
            md += [f"**Racional:** {sem.get('rationale', '')}", ""]
            if bian.get("evidence"):
                md += ["**Evidências:** " + ", ".join(_ev_link(e, self.sources) for e in bian["evidence"][:8]), ""]
        else:
            md += ["_Sem classificação._", ""]
        if sem.get("business-capabilities"):
            md += ["## Capacidades de negócio", "", *[f"- {c}" for c in sem["business-capabilities"]], ""]
        if sem.get("business-functions"):
            md += ["## Funcionalidades", "", *[f"- {f}" for f in sem["business-functions"]], ""]
        md += ["## Contexto", "", _mermaid([self.nodes[n] for n in sorted(neighbours) if n in self.nodes],
                                           [r for r, _, _ in self._connects()], highlight=aid), ""]
        endpoints = [i for i in a.get("interfaces", []) if "path" in i]
        if endpoints:
            md += ["## APIs expostas", "", "| Protocolo | Operação | Caminho | Handler | Evidência |",
                   "|---|---|---|---|---|"]
            md += [f"| {PROTOCOL_LABEL.get(e.get('protocol', 'http'), e.get('protocol'))} | {e['method']} | `{e['path']}` | "
                   f"{e.get('handler') or '—'} | {_ev_link(e['evidence'], self.sources)} |" for e in endpoints]
            md.append("")
        for title, items, key in (("Upstream — depende de", outs, "destino"),
                                  ("Downstream — usado por", ins, "origem")):
            if not items:
                continue
            md += [f"## {title}", "", f"| {key.capitalize()} | Tipo | Dependência | Protocolo | Detalhe | Confiança | Regra | Evidência |",
                   "|---|---|---|---|---|---|---|---|"]
            for r, other in sorted(items, key=lambda x: x[1]):
                o = self.nodes.get(other, {"name": other, "node-type": "?"})
                link = f"[{o['name']}]({other}.md)" if o["node-type"] in ("service", "webclient") else o["name"]
                m = r.get("metadata") or {}
                ev = ", ".join(_ev_link(e, self.sources) for e in m.get("evidence", [])[:3])
                md.append(f"| {link} | {_type_label(o)} | {m.get('dependency-kind', '')} | {r.get('protocol', '')} | "
                          f"{r.get('description', '')} | {m.get('confidence', 0):.2f} | `{m.get('rule', '')}` | {ev} |")
            md.append("")
        if self.graph is not None:
            impact = self.graph.impact(aid)
            if impact:
                md += ["## Análise de impacto", "", "Componentes afetados (transitivamente) por mudanças aqui:", ""]
                md += [f"- {i['name']} ({i['type']})" for i in impact]
                md.append("")
        md += ["## Tecnologia e origem", "",
               f"- Repositório: `{src.get('repository')}` · caminho `{src.get('path')}` · commit `{(src.get('commit') or '—')[:12]}`",
               f"- Linguagens: {', '.join(tech.get('languages', [])) or '—'}",
               f"- Frameworks: {', '.join(tech.get('frameworks', [])) or '—'}",
               f"- Aliases de rede: {', '.join(meta.get('aliases', [])) or '—'}"]
        md.append(self._override(aid))
        return self._write(f"aplicacoes/{aid}.md", "\n".join(md), aid)

    def _bian_pages(self) -> list[Path]:
        if self.graph is None:
            return []
        by_dom: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in self.graph.bian_coverage():
            by_dom[(row["business_area"], row["business_domain"])].append(row)
        pages = []
        index = ["# Mapa BIAN", "", "| Business Area | Business Domain | Service Domains cobertos |", "|---|---|---|"]
        for (area, dom), rows in sorted(by_dom.items()):
            covered = [r for r in rows if r["primary_apps"]]
            index.append(f"| {area} | [{dom}]({slugify(dom)}.md) | {len(covered)}/{len(rows)} |")
            md = [f"# {dom}", "", f"Business Area: **{area}**", "",
                  "| Service Domain | Padrão funcional | Aplicações (primário) | Aplicações (secundário) |",
                  "|---|---|---|---|"]
            for r in rows:
                prim = ", ".join(f"[{x}](../aplicacoes/{x}.md)" for x in r["primary_apps"]) or "—"
                sec = ", ".join(f"[{x}](../aplicacoes/{x}.md)" for x in r["secondary_apps"]) or "—"
                md.append(f"| {r['service_domain']} | {r['pattern']} | {prim} | {sec} |")
            pages.append(self._write(f"bian/{slugify(dom)}.md", "\n".join(md), "bian-" + slugify(dom)))
        pages.append(self._write("bian/index.md", "\n".join(index), "bian-index"))
        return pages

    def _data_and_messaging(self) -> Path:
        md = ["# Dados e mensageria", ""]
        groups = (("Bancos de dados", lambda n: n["node-type"] == "database"),
                  ("Armazenamento de objetos", lambda n: _asset_kind(n) == "object-storage"),
                  ("Tópicos, filas e streams", lambda n: n["node-type"] == "data-asset" and _asset_kind(n) != "object-storage"),
                  ("Sistemas externos", lambda n: n["node-type"] == "system"))
        for title, belongs in groups:
            items = [n for n in self.nodes.values() if belongs(n)]
            if not items:
                continue
            md += [f"## {title}", "", "| Nome | Tipo | Tecnologia | Declarado em (IaC) | Escritores/produtores | Leitores/consumidores |",
                   "|---|---|---|---|---|---|"]
            for n in sorted(items, key=lambda x: x["name"]):
                srcs = sorted({s for _, s, d in self._connects() if d == n["unique-id"]})
                dsts = sorted({d for _, s, d in self._connects() if s == n["unique-id"]})
                meta = n.get("metadata") or {}
                iac = ", ".join(_ev_link(e, self.sources) for e in meta.get("declared-in", [])) or "—"
                md.append(f"| {n['name']} | {_type_label(n)} | {meta.get('technology', '') or '—'} | {iac} | "
                          f"{', '.join(srcs) or '—'} | {', '.join(dsts) or '—'} |")
            md.append("")
        if self.graph is not None:
            shared = self.graph.shared_datastores()
            if shared:
                md += ["## ⚠️ Bancos compartilhados", "",
                       *[f"- **{s['name']}** usado por {', '.join(s['apps'])}" for s in shared], ""]
        return self._write("dados-e-mensageria.md", "\n".join(md))

    def _review_queue(self) -> Path:
        md = ["# Fila de revisão", "", "Itens que as camadas determinísticas não conseguiram afirmar com segurança.", ""]
        sem = [a for a in self.apps if (a.get("metadata") or {}).get("semantics", {}).get("review-required")]
        if sem:
            md += ["## Classificações semânticas", ""]
            md += [f"- [{a['name']}](aplicacoes/{a['unique-id']}.md): "
                   f"{a['metadata']['semantics'].get('rationale', '')}" for a in sem]
            md.append("")
        low = [(r, s, d) for r, s, d in self._connects() if (r.get("metadata") or {}).get("confidence", 1) < 0.8]
        if low:
            md += ["## Dependências de baixa confiança", "", "| Origem | Destino | Regra | Confiança |", "|---|---|---|---|"]
            md += [f"| {s} | {d} | `{r['metadata'].get('rule')}` | {r['metadata'].get('confidence'):.2f} |"
                   for r, s, d in low]
            md.append("")
        if self.unresolved:
            md += ["## Chamadas não resolvidas", "", "| Tipo | Alvo | Evidência |", "|---|---|---|"]
            md += [f"| {u.kind.value} | `{u.target}` | {_ev_link(u.evidence.ref(), self.sources)} |" for u in self.unresolved]
            md.append("")
        for issue in self.validation.get("warnings", []):
            md.append(f"- [{issue['layer']}] `{issue['path']}`: {issue['message']}")
        return self._write("fila-de-revisao.md", "\n".join(md))

    def _sidebar(self) -> Path:
        md = ["- [Visão geral](index.md)", "- [Mapa BIAN](bian/index.md)", "- [Dados e mensageria](dados-e-mensageria.md)",
              "- [Fila de revisão](fila-de-revisao.md)", "- Aplicações"]
        md += [f"  - [{a['name']}](aplicacoes/{a['unique-id']}.md)" for a in sorted(self.apps, key=lambda n: n["name"])]
        return self._write("_sidebar.md", "\n".join(md))
