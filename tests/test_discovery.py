import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from blueprint_discoverer.cli import app
from blueprint_discoverer.discovery import add_repo_to_config, discover_repos, repo_spec_for
from blueprint_discoverer.pipeline import Pipeline
from blueprint_discoverer.workspace import ProjectConfig, Workspace, read_json

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "banking-landscape"
EXAMPLE_REPOS = ["accounts-service", "customer-service", "digital-banking-bff", "fraud-detection", "payments-service"]


@pytest.fixture
def landscape(tmp_path) -> Path:
    """Copy of the example landscape without its project file and workspace."""
    dest = tmp_path / "landscape"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns(".blueprint", "blueprint.yaml"))
    return dest


def test_discover_falls_back_to_manifest_folders():
    assert [r.name for r in discover_repos(EXAMPLE)] == EXAMPLE_REPOS


def test_discover_git_repositories(tmp_path):
    for d in ("a/.git", "a/nested/.git", "group/b", "node_modules/c/.git", ".hidden/d/.git", "plain"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "group/b/.git").write_text("gitdir: ../../.git/worktrees/b\n")  # worktree/submodule
    assert [(r.name, r.path) for r in discover_repos(tmp_path)] == [("a", "a"), ("group/b", "group/b")]
    assert [(r.name, r.path) for r in discover_repos(tmp_path / "a")] == [("a", ".")]


def test_add_repo_keeps_comments(tmp_path):
    cfg = tmp_path / "blueprint.yaml"
    shutil.copy(EXAMPLE / "blueprint.yaml", cfg)
    spec = repo_spec_for(str(tmp_path), ProjectConfig.load(cfg), tmp_path, name="ledger")
    assert add_repo_to_config(cfg, spec) is True
    assert add_repo_to_config(cfg, spec) is False
    assert "# remote repositories are shallow-cloned" in cfg.read_text()
    assert [r.name for r in ProjectConfig.load(cfg).repos][-1] == "ledger"


def test_repo_spec_for_git_url():
    spec = repo_spec_for("git@github.com:acme/core-banking.git", ProjectConfig(), Path.cwd(), ref="main")
    assert (spec.name, spec.url, spec.ref) == ("core-banking", "git@github.com:acme/core-banking.git", "main")


def test_incremental_rescan_matches_full_run(landscape, tmp_path):
    cfg = ProjectConfig(name="x", repos=discover_repos(landscape))
    p = Pipeline(cfg, Workspace(tmp_path / "ws"), landscape)
    p.run(use_llm=False)
    full = p.ws.calm_file.read_text()
    facts = {f.name: f.stat().st_mtime_ns for f in p.ws.facts_dir.glob("*.json")}

    p.run(use_llm=False, only=["payments-service"])
    after = {f.name: f.stat().st_mtime_ns for f in p.ws.facts_dir.glob("*.json")}
    assert [n for n in facts if facts[n] != after[n]] == ["payments-service.json"]
    assert p.ws.calm_file.read_text() == full

    with pytest.raises(ValueError, match="unknown repositories"):
        p.run(use_llm=False, only=["ghost"])


def test_unlisted_repositories_are_pruned(landscape, tmp_path):
    ws = Workspace(tmp_path / "ws")
    repos = discover_repos(landscape)
    Pipeline(ProjectConfig(name="x", repos=repos), ws, landscape).run(use_llm=False)
    kept = [r for r in repos if r.name != "fraud-detection"]
    Pipeline(ProjectConfig(name="x", repos=kept), ws, landscape).run(use_llm=False)
    assert not (ws.facts_dir / "fraud-detection.json").exists()
    assert not (ws.semantic_dir / "fraud-detection.json").exists()
    assert "fraud-detection" not in {n["unique-id"] for n in read_json(ws.calm_file)["nodes"]}


def test_cli_discover_and_repo(landscape, tmp_path, monkeypatch):
    runner = CliRunner()
    r = runner.invoke(app, ["discover", str(landscape), "--save", "--no-llm"])
    assert r.exit_code == 0, r.output
    cfg = landscape / "blueprint.yaml"
    assert [x.name for x in ProjectConfig.load(cfg).repos] == EXAMPLE_REPOS
    assert "VALID" in r.output

    r = runner.invoke(app, ["repo", str(landscape / "payments-service"), "-c", str(cfg), "--save", "--no-llm"])
    assert r.exit_code == 0, r.output
    assert "already in" in r.output

    # no project file: repositories scanned into the same workspace accumulate
    monkeypatch.chdir(tmp_path)
    for name in ("accounts-service", "payments-service"):
        r = runner.invoke(app, ["repo", str(landscape / name), "--no-llm"])
        assert r.exit_code == 0, r.output
    nodes = {n["unique-id"] for n in read_json(tmp_path / ".blueprint" / "architecture.calm.json")["nodes"]}
    assert {"accounts-service", "payments-service"} <= nodes and "fraud-detection" not in nodes
