import json

from blueprint_discoverer.calm.validator import validate_architecture
from blueprint_discoverer.workspace import read_json


def test_pipeline_produces_valid_calm(pipeline):
    assert pipeline.report.valid, pipeline.report.errors
    assert pipeline.report.stats["services"] == 5


def test_expected_dependencies(pipeline):
    links = {(l["source"], l["destination"], l["rule"]) for l in read_json(pipeline.ws.links_file)["links"]}
    assert ("payments-service", "accounts-service", "R-HTTP-HOST") in links
    assert ("accounts-service", "customer-service", "R-HTTP-CLIENT-NAME") in links
    assert ("topic-payment-orders", "fraud-detection", "R-ASYNC-TOPIC") in links
    assert ("digital-banking-bff", "payments-service", "R-HTTP-HOST") in links


def test_bian_classification(pipeline):
    doc = read_json(pipeline.ws.calm_file)
    sd = {n["unique-id"]: n["metadata"]["bian"]["service-domain-id"]
          for n in doc["nodes"] if n["node-type"] == "service"}
    assert sd["accounts-service"] == "current-account"
    assert sd["payments-service"] == "payment-order"
    assert sd["fraud-detection"] == "fraud-detection"
    assert sd["digital-banking-bff"] == "session-dialogue"


def test_calm_is_deterministic(pipeline):
    first = pipeline.ws.calm_file.read_text()
    pipeline.build_calm()
    assert pipeline.ws.calm_file.read_text() == first


def test_validator_catches_broken_references(pipeline):
    doc = read_json(pipeline.ws.calm_file)
    doc = json.loads(json.dumps(doc))
    rel = next(r for r in doc["relationships"] if "connects" in r["relationship-type"])
    rel["relationship-type"]["connects"]["destination"]["node"] = "ghost"
    del rel["metadata"]["evidence"]
    doc["nodes"][0].pop("name")
    report = validate_architecture(doc)
    layers = {e.layer for e in report.errors}
    assert not report.valid and {"schema", "reference", "profile"} <= layers


def test_graph_impact_analysis(pipeline):
    g = pipeline.load_graph()
    impacted = {r["id"] for r in g.impact("accounts-service")}
    assert {"payments-service", "digital-banking-bff"} <= impacted
    coverage = {r["service_domain_id"]: r["primary_apps"] for r in g.bian_coverage()}
    assert coverage["payment-order"] == ["payments-service"]


def test_wiki_pages(pipeline):
    wiki = pipeline.ws.wiki_dir
    assert (wiki / "index.md").exists()
    page = (wiki / "aplicacoes" / "payments-service.md").read_text()
    assert "Payment Order" in page and "```mermaid" in page and "R-HTTP-HOST" in page
