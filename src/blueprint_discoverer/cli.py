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


LlmOpt = typer.Option(None, "--llm/--no-llm", help="Override inference.llm.enabled.")
CalmCliOpt = typer.Option(False, help="Also validate with npx @finos/calm-cli.")


def _fail(msg: str) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(2)


def _run_and_report(p, **kwargs) -> None:
    try:
        report = p.run(**kwargs)
    except ValueError as e:
        _fail(str(e))
    typer.echo(f"workspace: {p.ws.root}")
    typer.echo(f"CALM: {p.ws.calm_file}  ({json.dumps(report.stats)})")
    typer.echo(f"validation: {'VALID' if report.valid else 'INVALID'} - "
               f"{len(report.errors)} errors, {len(report.warnings)} warnings")
    for e in report.errors[:20]:
        typer.echo(f"  ERROR [{e.layer}] {e.path}: {e.message}")
    typer.echo(f"wiki: {p.ws.wiki_dir / 'index.md'}")
    raise typer.Exit(0 if report.valid else 1)


@app.command()
def run(config: Path = ConfigOpt, workspace: Path = WsOpt,
        repo: list[Path] = typer.Option(None, "--repo", "-r", help="Scan these paths instead of the config."),
        only: list[str] = typer.Option(None, "--only", "-o",
                                       help="Rescan only these configured repositories; reuse the others' facts."),
        refresh: bool = typer.Option(False, help="Update existing clones of remote repositories."),
        llm: bool = LlmOpt, calm_cli: bool = CalmCliOpt) -> None:
    """Full pipeline: scan -> link -> infer -> CALM -> validate -> graph -> wiki."""
    _run_and_report(_pipeline(config, workspace, repo), use_llm=llm, use_calm_cli=calm_cli,
                    only=only or None, refresh=refresh)


@app.command()
def discover(folder: Path = typer.Argument(..., help="Folder holding one or more repositories."),
             workspace: Path = WsOpt,
             depth: int = typer.Option(3, help="How many directory levels to search for git repositories."),
             list_only: bool = typer.Option(False, "--list", help="Only list the repositories found."),
             save: bool = typer.Option(False, "--save",
                                       help="Create <folder>/blueprint.yaml, or add the new repositories to it."),
             llm: bool = LlmOpt, calm_cli: bool = CalmCliOpt) -> None:
    """Find every repository under a folder and map them together, no blueprint.yaml needed.

    If <folder>/blueprint.yaml exists, its settings and repositories are kept and the discovered ones are added.
    """
    from .discovery import add_repo_to_config, discover_repos, write_config
    from .pipeline import Pipeline

    if not folder.is_dir():
        _fail(f"{folder} is not a directory")
    folder = folder.resolve()
    found = discover_repos(folder, max_depth=depth)
    if not found:
        _fail(f"no repositories (git or build manifest) found under {folder}")
    cfg_file = folder / "blueprint.yaml"
    cfg = ProjectConfig.load(cfg_file) if cfg_file.exists() else ProjectConfig(name=folder.name)
    known = {r.name for r in cfg.repos} | {str((folder / r.path).resolve()) for r in cfg.repos if r.path}
    new = [r for r in found if r.name not in known and str((folder / r.path).resolve()) not in known]
    for r in found:
        typer.echo(f"{'+' if r in new else '='} {r.name}")
    if list_only:
        return
    if save:
        if cfg_file.exists():
            for r in new:
                add_repo_to_config(cfg_file, r)
        else:
            write_config(cfg_file, folder.name, found)
        typer.echo(f"saved: {cfg_file}")
    cfg.repos = [*cfg.repos, *new]
    _run_and_report(Pipeline(cfg, Workspace(workspace or folder / ".blueprint"), folder),
                    use_llm=llm, use_calm_cli=calm_cli)


@app.command("repo")
def repo_cmd(target: str = typer.Argument(..., help="Local path, git URL or name of a configured repository."),
             config: Path = ConfigOpt, workspace: Path = WsOpt,
             name: str = typer.Option(None, help="Repository name (default: folder or URL basename)."),
             ref: str = typer.Option(None, help="Branch or tag to clone (git URLs only)."),
             save: bool = typer.Option(False, "--save", help="Add the repository to blueprint.yaml."),
             llm: bool = LlmOpt, calm_cli: bool = CalmCliOpt) -> None:
    """Scan a single repository and merge it into the landscape, reusing the other repositories' facts.

    With blueprint.yaml the landscape is its repositories plus this one (kept only with --save).

    Without it, every repository scanned into the same workspace accumulates.
    """
    from .discovery import add_repo_to_config, repo_spec_for
    from .pipeline import Pipeline

    if config.exists():
        cfg, base = ProjectConfig.load(config), config.resolve().parent
    else:
        cfg, base = ProjectConfig(name=Path.cwd().name), Path.cwd()
    ws = Workspace(workspace or base / ".blueprint")
    try:
        spec = repo_spec_for(target, cfg, base, name=name, ref=ref)
    except ValueError as e:
        _fail(str(e))
    if not config.exists():
        # no project file: the workspace itself is the registry of previously scanned repositories
        cfg.repos = [RepoSpec(name=read_json(f)["repo"]) for f in sorted(ws.facts_dir.glob("*.json"))]
    if spec.name not in {r.name for r in cfg.repos}:
        cfg.repos.append(spec)
    else:
        cfg.repos = [spec if r.name == spec.name else r for r in cfg.repos]
    if save:
        if not config.exists():
            _fail(f"{config} not found; create it first (e.g. `blueprint discover <folder> --save`)")
        try:
            added = add_repo_to_config(config, spec)
        except ValueError as e:
            _fail(str(e))
        typer.echo(f"{'added to' if added else 'already in'} {config}: {spec.name}")
    typer.echo(f"scanning {spec.name} ({spec.url or spec.path})")
    _run_and_report(Pipeline(cfg, ws, base), use_llm=llm, use_calm_cli=calm_cli, only=[spec.name], refresh=True)


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
def serve(config: Path = ConfigOpt, workspace: Path = WsOpt, host: str = "127.0.0.1", port: int = 8765,
          fs_root: Path = typer.Option(None, help="Folder the web console may browse and scan (default: home).")) -> None:
    """Serve the REST API, the web UI and its scan console."""
    import uvicorn

    from .api.app import create_app

    ws = Workspace(workspace or config.resolve().parent / ".blueprint")
    uvicorn.run(create_app(ws, config_path=config.resolve(), fs_root=fs_root), host=host, port=port)


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
