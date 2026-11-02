"""`blueprint` command line."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from .semantic.bian.landscape import import_landscape
from .workspace import ProjectConfig, RepoSpec, Workspace, read_json

app = typer.Typer(help="Blueprint Discoverer - as-is architecture mapper (CALM + BIAN).", no_args_is_help=True)
bian_app = typer.Typer(help="BIAN service landscape utilities.")
app.add_typer(bian_app, name="bian")

ConfigOpt = typer.Option(Path("blueprint.yaml"), "--config", "-c", help="Project file listing repositories.")
WsOpt = typer.Option(None, "--workspace", "-w", help="Output directory (default: <config dir>/.blueprint).")


def _pipeline(config: Path, workspace: Path | None, repos: list[Path] | None = None):
    from .pipeline import Pipeline

    if repos:
        cfg = ProjectConfig(name="ad-hoc", repos=[RepoSpec(name=p.resolve().name, path=str(p.resolve())) for p in repos])
        base = Path.cwd()
    else:
        cfg = ProjectConfig.load(config)
        base = config.resolve().parent
    ws = Workspace(workspace or base / ".blueprint")
    return Pipeline(cfg, ws, base)


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s")


@app.command()
def run(config: Path = ConfigOpt, workspace: Path = WsOpt,
        repo: list[Path] = typer.Option(None, "--repo", "-r", help="Scan these paths instead of the config."),
        llm: bool = typer.Option(None, "--llm/--no-llm", help="Override inference.llm.enabled."),
        calm_cli: bool = typer.Option(False, help="Also validate with npx @finos/calm-cli.")) -> None:
    """Full pipeline: scan -> link -> infer -> CALM -> validate -> graph -> wiki."""
    p = _pipeline(config, workspace, repo)
    report = p.run(use_llm=llm, use_calm_cli=calm_cli)
    typer.echo(f"workspace: {p.ws.root}")
    typer.echo(f"CALM: {p.ws.calm_file}  ({json.dumps(report.stats)})")
    typer.echo(f"validation: {'VALID' if report.valid else 'INVALID'} - "
               f"{len(report.errors)} errors, {len(report.warnings)} warnings")
    for e in report.errors[:20]:
        typer.echo(f"  ERROR [{e.layer}] {e.path}: {e.message}")
    typer.echo(f"wiki: {p.ws.wiki_dir / 'index.md'}")
    raise typer.Exit(0 if report.valid else 1)


@app.command()
def scan(config: Path = ConfigOpt, workspace: Path = WsOpt) -> None:
    """Stage 1 - AST/config scan of every repository (facts/*.json)."""
    scans = _pipeline(config, workspace).scan()
    for s in scans:
        typer.echo(f"{s.repo}: {len(s.components)} components, {s.files_scanned} files, {len(s.warnings)} warnings")


@app.command()
def link(config: Path = ConfigOpt, workspace: Path = WsOpt) -> None:
    """Stage 2 - deterministic cross-repo dependency resolution (links.json)."""
    r = _pipeline(config, workspace).link()
    typer.echo(f"{len(r.links)} links, {len(r.topics)} topics, {len(r.datastores)} datastores, "
               f"{len(r.unresolved)} unresolved")


@app.command()
def infer(config: Path = ConfigOpt, workspace: Path = WsOpt,
          llm: bool = typer.Option(None, "--llm/--no-llm")) -> None:
    """Stage 3 - semantic inference (BIAN classification, purpose, capabilities)."""
    for p in _pipeline(config, workspace).infer(use_llm=llm):
        d = p.primary_domain
        typer.echo(f"{p.component_id:30} {d.service_domain if d else '-':40} conf={p.confidence:.2f} "
                   f"{p.method}{' REVIEW' if p.review_required else ''}")


@app.command()
def calm(config: Path = ConfigOpt, workspace: Path = WsOpt,
         calm_cli: bool = typer.Option(False, help="Also validate with npx @finos/calm-cli.")) -> None:
    """Stage 4 - build and validate the CALM architecture document."""
    p = _pipeline(config, workspace)
    report = p.validate(p.build_calm(), use_calm_cli=calm_cli)
    typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))
    raise typer.Exit(0 if report.valid else 1)


@app.command()
def validate(file: Path = typer.Argument(..., help="CALM architecture JSON"),
             calm_cli: bool = typer.Option(False)) -> None:
    """Validate any CALM architecture file (schema + references + Blueprint profile)."""
    from .calm.validator import validate_architecture

    report = validate_architecture(read_json(file), use_calm_cli=calm_cli, doc_path=file)
    typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))
    raise typer.Exit(0 if report.valid else 1)


@app.command()
def wiki(config: Path = ConfigOpt, workspace: Path = WsOpt) -> None:
    """Stage 5 - rebuild graph and wiki from the current CALM document."""
    p = _pipeline(config, workspace)
    pages = p.wiki(graph=p.load_graph())
    typer.echo(f"{len(pages)} pages in {p.ws.wiki_dir}")


@app.command()
def serve(config: Path = ConfigOpt, workspace: Path = WsOpt, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the REST API (and the built web UI, if present)."""
    import uvicorn

    from .api.app import create_app

    p = _pipeline(config, workspace)
    uvicorn.run(create_app(p.ws), host=host, port=port)


@bian_app.command("import")
def bian_import(source: Path, replace: bool = typer.Option(False, help="Replace instead of merge.")) -> None:
    """Import an official/extended BIAN landscape (CSV: name,business_area,business_domain,...)."""
    n = import_landscape(source, merge=not replace)
    typer.echo(f"imported {n} service domains")


@bian_app.command("list")
def bian_list() -> None:
    from .semantic.bian.landscape import load_landscape

    typer.echo(load_landscape().catalog_for_prompt())


if __name__ == "__main__":
    app()
