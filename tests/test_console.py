import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient

from blueprint_discoverer.api.app import create_app
from blueprint_discoverer.workspace import ProjectConfig, Workspace

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "banking-landscape"


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(600):
        job = client.get(f"/api/scans/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_console_browse_discover_and_scan(tmp_path):
    root = tmp_path / "landscape"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".blueprint"))
    config = root / "blueprint.yaml"
    client = TestClient(create_app(Workspace(root / ".blueprint"), web_dist=tmp_path / "none",
                                   config_path=config, fs_root=tmp_path))

    sources = client.get("/api/sources").json()
    assert len(sources["repositories"]) == 10 and not any(r["scanned"] for r in sources["repositories"])

    folder = client.get("/api/fs", params={"path": str(root)}).json()
    entries = {e["name"]: e for e in folder["entries"]}
    assert entries["cards-service"]["manifest"] and entries["platform-infra"]["manifest"]
    assert client.get("/api/fs", params={"path": "/"}).status_code == 403  # outside the browsable root
    assert len(client.post("/api/sources/discover", json={"path": str(root)}).json()) == 10

    job = _wait(client, client.post("/api/scans", json={"full": True}).json()["id"])
    assert job["status"] == "succeeded" and job["result"]["valid"] and job["result"]["stats"]["services"] == 9
    assert all(r["scanned"] for r in client.get("/api/sources").json()["repositories"])

    new_repo = tmp_path / "statements-service"
    (new_repo / "src").mkdir(parents=True)
    (new_repo / "package.json").write_text('{"name": "statements-service", "dependencies": {"express": "4"}}')
    (new_repo / "src" / "index.js").write_text(
        'const app = require("express")();\napp.get("/statements/:accountId", h);\n'
        'axios.get("http://accounts-service:8080/accounts/1/balance");\n')
    started = client.post("/api/scans", json={"repositories": [{"path": str(new_repo)}], "save": True}).json()
    assert started["mode"] == "incremental" and started["repositories"] == ["statements-service"]
    job = _wait(client, started["id"])
    assert job["status"] == "succeeded" and job["result"]["stats"]["services"] == 10
    assert any("scanning statements-service" in line for line in job["log"])
    assert "statements-service" in [r.name for r in ProjectConfig.load(config).repos]

    assert client.post("/api/scans", json={"repositories": [{"url": "not a url"}]}).status_code == 400
    assert client.post("/api/scans", json={"repositories": [{"path": "/etc"}]}).status_code == 403
    assert client.post("/api/scans", json={}).status_code == 400
