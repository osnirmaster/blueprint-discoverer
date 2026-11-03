import json

from blueprint_discoverer.calm.validator import validate_architecture
from blueprint_discoverer.workspace import read_json


def test_pipeline_produces_valid_calm(pipeline):
    assert pipeline.report.valid, pipeline.report.errors
    assert pipeline.report.stats["services"] == 9


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


def _connects(pipeline):
    doc = read_json(pipeline.ws.calm_file)
    return {(r["relationship-type"]["connects"]["source"]["node"], r["relationship-type"]["connects"]["destination"]["node"]):
            r for r in doc["relationships"] if "connects" in r["relationship-type"]}


def test_grpc_graphql_soap_dependencies_point_to_upstream_operations(pipeline):
    rels = _connects(pipeline)
    grpc = rels[("loans-service", "credit-scoring")]
    assert grpc["metadata"]["dependency-kind"] == "grpc"
    assert grpc["metadata"]["direction"] == {"downstream": "loans-service", "upstream": "credit-scoring"}
    assert "credit-scoring-rpc-bank-credit-v1-credit-scoring-score-customer" in \
        grpc["relationship-type"]["connects"]["destination"]["interfaces"]
    assert rels[("cards-service", "credit-scoring")]["metadata"]["dependency-kind"] == "grpc"
    soap = rels[("accounts-service", "core-banking-legacy")]
    assert soap["metadata"]["dependency-kind"] == "soap"
    assert len(soap["relationship-type"]["connects"]["destination"]["interfaces"]) == 2
    graphql = [r for r in read_json(pipeline.ws.calm_file)["relationships"]
               if r["unique-id"] == "digital-banking-bff-graphql-customer-service"]
    assert graphql and graphql[0]["relationship-type"]["connects"]["destination"]["interfaces"] == \
        ["customer-service-query-query-customer"]


def test_aws_resources_link_sdk_usage_to_iac(pipeline):
    rels = _connects(pipeline)
    nodes = {n["unique-id"]: n for n in read_json(pipeline.ws.calm_file)["nodes"]}
    assert rels[("cards-service", "sqs-card-events")]["metadata"]["rule"] == "R-ASYNC-TOPIC"
    assert ("sqs-card-events", "fraud-detection") in rels
    assert rels[("sns-payment-notifications", "sqs-payment-notifications-email")]["metadata"]["rule"] == \
        "R-IAC-SUBSCRIPTION"
    assert ("sns-loan-events", "sqs-loan-notifications") in rels  # SAM template inside loans-service
    assert rels[("credit-scoring", "dynamodb-credit-bureau-cache")]["metadata"]["rule"] == "R-DATA-CLOUD"
    assert nodes["sqs-card-events"]["metadata"]["asset-kind"] == "messaging-queue"
    assert nodes["kinesis-credit-decisions"]["metadata"]["asset-kind"] == "event-stream"
    assert nodes["s3-bank-prd-statements"]["metadata"]["asset-kind"] == "object-storage"  # ${local.prefix} resolved
    assert nodes["dynamodb-card-accounts"]["metadata"]["declared-in"][0].startswith("platform-infra:envs/prd/data.tf")
    assert "platform-infra" not in nodes  # infrastructure-only repositories are not applications


def test_new_languages_are_classified(pipeline):
    sd = {n["unique-id"]: n["metadata"]["bian"]["service-domain-id"]
          for n in read_json(pipeline.ws.calm_file)["nodes"] if n["node-type"] == "service"}
    assert sd["loans-service"] == "consumer-loan"
    assert sd["credit-scoring"] == "customer-credit-rating"
    assert sd["cards-service"] == "card-authorization"
    assert sd["core-banking-legacy"] == "position-keeping"
