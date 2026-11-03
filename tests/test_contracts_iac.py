from blueprint_discoverer.cartographer.code.contracts import parse_graphql, parse_proto, parse_wsdl
from blueprint_discoverer.cartographer.infra import TerraformScanner, scan_cloudformation
from blueprint_discoverer.linker.resolver import cloud_key, resolve
from blueprint_discoverer.model import (
    Component,
    Evidence,
    InfraRelation,
    InfraResource,
    OutboundCall,
    OutboundKind,
    RepoScan,
)

EV = Evidence(repo="r", path="x", line=1)


def test_proto_sdl_and_wsdl_contracts():
    protos = parse_proto("package bank.v1;\n// service Fake {\nservice Scoring {\n rpc Score(A) returns (B);\n}\n",
                         "r", "s.proto")
    assert [(p.full_name, [m for m, _ in p.methods], p.evidence.line) for p in protos] == \
        [("bank.v1.Scoring", ["Score"], 3)]
    endpoints, calls = parse_graphql("type Query {\n  customer(filter: F = {a: 1}): C\n}\nextend type Mutation { x: Int }",
                                     "r", "s.graphqls")
    assert [(e.path, e.evidence.line) for e in endpoints] == [("Query.customer", 2), ("Mutation.x", 4)] and not calls
    _, calls = parse_graphql("query { customer(id: 1) { id } }", "r", "q.graphql")
    assert [c.detail for c in calls] == ["QUERY Query.customer"]
    wsdl = parse_wsdl('<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" '
                      'xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"><portType name="P"><operation name="Get"/>'
                      '</portType><service name="Core"><port name="p"><soap:address location="http://core:8080/ws"/>'
                      '</port></service></definitions>', "r", "c.wsdl")
    assert [(w.name, w.location, w.operations) for w in wsdl] == [("Core", "http://core:8080/ws", ["Get"])]


def test_terraform_resources_variables_modules_and_relations():
    tf = TerraformScanner("infra")
    tf.add("prd/vars.tf", b'variable "env" { default = "prd" }\nlocals { prefix = "bank-${var.env}" }\n')
    tf.add("prd/main.tf", b'''
resource "aws_sqs_queue" "q" { name = "${local.prefix}-orders" }
resource "aws_sns_topic" "t" { name = "orders-topic" }
resource "aws_sns_topic_subscription" "s" {
  topic_arn = aws_sns_topic.t.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.q.arn
}
resource "aws_lambda_function" "f" {
  function_name = "orders-worker"
  environment { variables = { TABLE = aws_dynamodb_table.d.name } }
}
resource "aws_dynamodb_table" "d" { name = "orders" }
resource "aws_lambda_event_source_mapping" "m" {
  event_source_arn = aws_sqs_queue.q.arn
  function_name    = aws_lambda_function.f.arn
}
module "ledger" {
  source = "terraform-aws-modules/dynamodb-table/aws"
  name   = "ledger"
}
''')
    resources, relations = tf.scan()
    by_ref = {r.ref: r for r in resources}
    assert by_ref["prd:aws_sqs_queue.q"].name == "bank-prd-orders"
    assert by_ref["prd:module.ledger"].type == "dynamodb-table"
    assert by_ref["prd:aws_lambda_function.f"].environment == {"TABLE": "prd:aws_dynamodb_table.d"}
    assert {(r.kind, r.source, r.destination) for r in relations} == {
        ("subscription", "prd:aws_sns_topic.t", "prd:aws_sqs_queue.q"),
        ("event-source", "prd:aws_sqs_queue.q", "prd:aws_lambda_function.f")}


def test_cloudformation_sam_intrinsics_and_events():
    template = '''AWSTemplateFormatVersion: "2010-09-09"
Parameters:
  Env: {Type: String, Default: prd}
Resources:
  Table:
    Type: AWS::DynamoDB::Table
    Properties: {TableName: !Sub "${Env}-loans"}
  Topic:
    Type: AWS::SNS::Topic
    Properties:
      Subscription: [{Protocol: sqs, Endpoint: !GetAtt Queue.Arn}]
  Queue:
    Type: AWS::SQS::Queue
  Worker:
    Type: AWS::Serverless::Function
    Properties:
      Environment: {Variables: {TABLE: !Ref Table}}
      Events: {Q: {Type: SQS, Properties: {Queue: !GetAtt Queue.Arn}}}
'''
    resources, relations = scan_cloudformation(template, "r", "template.yaml")
    by_ref = {r.ref: r for r in resources}
    assert by_ref["template.yaml:Table"].name == "prd-loans"
    assert by_ref["template.yaml:Queue"].name == "Queue"  # unnamed resources keep the logical id
    assert by_ref["template.yaml:Worker"].environment == {"TABLE": "template.yaml:Table"}
    assert {(r.kind, r.source, r.destination) for r in relations} == {
        ("subscription", "template.yaml:Topic", "template.yaml:Queue"),
        ("event-source", "template.yaml:Queue", "template.yaml:Worker")}


def _res(ref, rtype, name, owner="", environment=None):
    return InfraResource(ref=ref, type=rtype, name=name, iac="terraform", owner=owner,
                         environment=environment or {}, evidence=EV)


def test_linker_iac_event_sources_env_refs_and_suffix_match():
    worker = Component(id="orders-worker", name="orders-worker", repo="svc", outbound=[
        OutboundCall(kind=OutboundKind.MESSAGING_PUBLISH, target="orders", technology="sqs", evidence=EV)])
    infra = RepoScan(repo="svc", location=".", components=[worker], infrastructure=[
        _res("q", "sqs-queue", "bank-${var.env}-orders"),
        _res("d", "dynamodb-table", "orders-db"),
        _res("f", "lambda-function", "orders-worker", owner="orders-worker", environment={"TABLE": "d"})],
        infra_relations=[InfraRelation(kind="event-source", source="q", destination="f", evidence=EV)])
    result = resolve([infra])
    links = {(l.source, l.destination): l for l in result.links}
    assert cloud_key("bank-${var.env}-orders") == "bank-orders"
    assert links[("sqs-bank-orders", "orders-worker")].rule == "R-IAC-EVENT-SOURCE"
    assert links[("orders-worker", "dynamodb-orders-db")].rule == "R-IAC-ENV-REF"
    assert links[("orders-worker", "sqs-bank-orders")].rule == "R-CLOUD-IAC-SUFFIX"  # "orders" ~ "bank-orders"
