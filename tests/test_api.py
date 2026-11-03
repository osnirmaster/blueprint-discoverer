from fastapi.testclient import TestClient

from blueprint_discoverer.api.app import create_app


def test_api_endpoints(pipeline, tmp_path):
    client = TestClient(create_app(pipeline.ws, web_dist=tmp_path / "none"))
    assert client.get("/api/summary").json()["applications"] == 9
    graph = client.get("/api/graph").json()
    assert any(e["source"] == "payments-service" for e in graph["edges"])
    detail = client.get("/api/applications/accounts-service").json()
    assert {i["id"] for i in detail["impact"]} >= {"payments-service"}
    assert client.post("/api/cypher", json={"query": "MATCH (a:Application) RETURN count(a) AS n"}).json() == [{"n": 9}]
    assert client.post("/api/cypher", json={"query": "MATCH (a) DELETE a"}).status_code == 400
    assert "Payment" in client.get("/api/wiki/page", params={"path": "index.md"}).json()["markdown"]
    assert client.get("/api/wiki/page", params={"path": "../../etc/passwd"}).status_code == 404
