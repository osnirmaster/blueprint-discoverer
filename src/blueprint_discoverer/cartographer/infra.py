"""Cartographer IaC: AWS resources and their wiring declared in Terraform and CloudFormation/SAM.

Deterministic: Terraform is parsed with tree-sitter-hcl, CloudFormation with a YAML loader that keeps the
intrinsic functions (!Ref, !GetAtt, !Sub...) as data. Physical names are resolved from literals, Terraform
variable defaults/locals and CloudFormation parameter defaults; anything else stays as a ${...} placeholder.

Resources: DynamoDB tables, S3 buckets, SQS queues, SNS topics, Kinesis streams, Lambda functions
(also through terraform-aws-modules). Relations: SNS -> SQS/Lambda subscriptions, event sources feeding a
function (SQS, Kinesis, DynamoDB streams, SNS, S3 notifications) and resources referenced by a function's
environment variables.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import tree_sitter_hcl as tshcl
import yaml
from tree_sitter import Language, Node, Parser

from ..model import Evidence, InfraRelation, InfraResource

_HCL = Language(tshcl.language())

TF_TYPES = {
    "aws_dynamodb_table": ("dynamodb-table", "name"),
    "aws_s3_bucket": ("s3-bucket", "bucket"),
    "aws_sqs_queue": ("sqs-queue", "name"),
    "aws_sns_topic": ("sns-topic", "name"),
    "aws_kinesis_stream": ("kinesis-stream", "name"),
    "aws_lambda_function": ("lambda-function", "function_name"),
}
TF_MODULES = {  # terraform-aws-modules/<x>/aws
    "/sqs/": ("sqs-queue", "name"), "/sns/": ("sns-topic", "name"),
    "/dynamodb-table/": ("dynamodb-table", "name"), "/s3-bucket/": ("s3-bucket", "bucket"),
    "/kinesis": ("kinesis-stream", "name"), "/lambda/": ("lambda-function", "function_name"),
}
CFN_TYPES = {
    "AWS::DynamoDB::Table": ("dynamodb-table", "TableName"),
    "AWS::DynamoDB::GlobalTable": ("dynamodb-table", "TableName"),
    "AWS::Serverless::SimpleTable": ("dynamodb-table", "TableName"),
    "AWS::S3::Bucket": ("s3-bucket", "BucketName"),
    "AWS::SQS::Queue": ("sqs-queue", "QueueName"),
    "AWS::SNS::Topic": ("sns-topic", "TopicName"),
    "AWS::Kinesis::Stream": ("kinesis-stream", "Name"),
    "AWS::Lambda::Function": ("lambda-function", "FunctionName"),
    "AWS::Serverless::Function": ("lambda-function", "FunctionName"),
}
SAM_EVENT_SOURCE = {"SQS": "Queue", "Kinesis": "Stream", "DynamoDB": "Stream", "SNS": "Topic", "S3": "Bucket"}


class Ref(str):
    """A reference to another resource (as opposed to a resolved string value)."""


def _line(content: str, pattern: str) -> int:
    m = re.search(pattern, content, re.M)
    return content.count("\n", 0, m.start()) + 1 if m else 1


# ---------------------------------------------------------------------------------------- Terraform
class TerraformScanner:
    """Collects every .tf file first: variables and locals are shared by all files of a module directory."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.parser = Parser(_HCL)
        self.files: list[tuple[str, bytes, Node]] = []

    def add(self, rel: str, source: bytes) -> None:
        self.files.append((rel, source, self.parser.parse(source).root_node))

    @staticmethod
    def _blocks(root: Node):
        body = next((c for c in root.named_children if c.type == "body"), None)
        for b in body.named_children if body else []:
            if b.type == "block":
                ident = b.named_children[0]
                labels = [c.text.decode() for c in b.named_children if c.type == "string_lit"]
                yield ident.text.decode(), [lbl.strip('"') for lbl in labels], b

    @staticmethod
    def _attrs(block: Node) -> dict[str, Node]:
        body = next((c for c in block.named_children if c.type == "body"), None)
        out = {}
        for a in body.named_children if body else []:
            if a.type == "attribute":
                out[a.named_children[0].text.decode()] = a.named_children[1]
            elif a.type == "block":  # nested blocks (environment { ... }) are exposed under their name
                out[a.named_children[0].text.decode()] = a
        return out

    def _eval(self, node: Node, scope: dict[str, str]):
        """str for values, Ref for resource references, dict for objects, None when unknown."""
        if node.type == "block":
            return {k: self._eval(v, scope) for k, v in self._attrs(node).items()}
        if node.type == "expression":
            kids = node.named_children
            if kids and kids[0].type == "variable_expr":
                parts = [kids[0].text.decode()] + [k.named_children[0].text.decode() for k in kids[1:]
                                                     if k.type == "get_attr" and k.named_children]
                if parts[0] in ("var", "local") and len(parts) > 1:
                    key = f"{parts[0]}.{parts[1]}"
                    return scope.get(key, "${" + key + "}")
                if parts[0] == "module" and len(parts) > 1:
                    return Ref(f"module.{parts[1]}")
                if parts[0].startswith("aws_") and len(parts) > 1:
                    return Ref(f"{parts[0]}.{parts[1]}")
                return None
            return self._eval(kids[0], scope) if kids else None
        if node.type == "literal_value":
            return self._eval(node.named_children[0], scope) if node.named_children else node.text.decode()
        if node.type == "string_lit":
            return "".join(c.text.decode() for c in node.named_children if c.type == "template_literal")
        if node.type in ("template_expr", "quoted_template"):
            out = []
            for c in (node.named_children[0].named_children if node.type == "template_expr" else node.named_children):
                if c.type == "template_literal":
                    out.append(c.text.decode())
                elif c.type == "template_interpolation":
                    expr = next((x for x in c.named_children if x.type == "expression"), None)
                    v = self._eval(expr, scope) if expr is not None else None
                    out.append(v if isinstance(v, str) and not isinstance(v, Ref) else
                               "${" + (expr.text.decode() if expr is not None else "") + "}")
            return "".join(out)
        if node.type == "collection_value":
            obj = node.named_children[0] if node.named_children else None
            if obj is not None and obj.type == "object":
                out = {}
                for el in (e for e in obj.named_children if e.type == "object_elem"):
                    key = el.named_children[0].text.decode().strip('"')
                    out[key] = self._eval(el.named_children[1], scope)
                return out
        return None

    def scan(self) -> tuple[list[InfraResource], list[InfraRelation]]:
        scopes: dict[str, dict[str, str]] = defaultdict(dict)
        for rel, _, root in self.files:  # pass 1: variables and locals per module directory
            d = str(Path(rel).parent)
            for kind, labels, block in self._blocks(root):
                attrs = self._attrs(block)
                if kind == "variable" and labels and "default" in attrs:
                    v = self._eval(attrs["default"], {})
                    if isinstance(v, str):
                        scopes[d][f"var.{labels[0]}"] = v
        for _ in range(2):  # locals may reference each other
            for rel, _, root in self.files:
                d = str(Path(rel).parent)
                for kind, _labels, block in self._blocks(root):
                    if kind == "locals":
                        for k, node in self._attrs(block).items():
                            v = self._eval(node, scopes[d])
                            if isinstance(v, str) and not isinstance(v, Ref):
                                scopes[d][f"local.{k}"] = v

        resources: list[InfraResource] = []
        relations: list[InfraRelation] = []
        for rel, source, root in self.files:
            d = str(Path(rel).parent)
            scope = scopes[d]
            content = source.decode("utf-8", "replace")

            def key(local_ref: str, d=d) -> str:
                return f"{d}:{local_ref}"

            for kind, labels, block in self._blocks(root):
                attrs = self._attrs(block)
                line = block.start_point.row + 1
                ev = Evidence(repo=self.repo, path=rel, line=line, snippet=content.splitlines()[line - 1].strip())
                rtype, name_attr, address = None, "", ""
                if kind == "resource" and len(labels) == 2 and labels[0] in TF_TYPES:
                    (rtype, name_attr), address = TF_TYPES[labels[0]], f"{labels[0]}.{labels[1]}"
                elif kind == "module" and len(labels) == 1 and "source" in attrs:
                    src = self._eval(attrs["source"], scope) or ""
                    hit = next((v for k, v in TF_MODULES.items() if k in f"{src}/"), None)
                    if hit:
                        (rtype, name_attr), address = hit, f"module.{labels[0]}"
                if rtype:
                    name = self._eval(attrs[name_attr], scope) if name_attr in attrs else None
                    env = {}
                    if rtype == "lambda-function" and isinstance(e := (self._eval(attrs["environment"], scope)
                                                                     if "environment" in attrs else None), dict):
                        variables = e.get("variables") or {}
                        env = {k: key(v) for k, v in variables.items() if isinstance(v, Ref)} \
                            if isinstance(variables, dict) else {}
                    resources.append(InfraResource(
                        ref=key(address), type=rtype, iac="terraform", environment=env, evidence=ev,
                        name=name if isinstance(name, str) and not isinstance(name, Ref) and name else labels[-1]))
                    continue
                if kind != "resource" or len(labels) != 2:
                    continue
                values = {k: self._eval(v, scope) for k, v in attrs.items()}
                if labels[0] == "aws_sns_topic_subscription" and isinstance(values.get("topic_arn"), Ref) \
                        and isinstance(values.get("endpoint"), Ref):
                    relations.append(InfraRelation(kind="subscription", source=key(values["topic_arn"]),
                                                   destination=key(values["endpoint"]), evidence=ev))
                elif labels[0] == "aws_lambda_event_source_mapping" and isinstance(values.get("event_source_arn"), Ref) \
                        and isinstance(values.get("function_name"), Ref):
                    relations.append(InfraRelation(kind="event-source", source=key(values["event_source_arn"]),
                                                   destination=key(values["function_name"]), evidence=ev))
                elif labels[0] == "aws_s3_bucket_notification" and isinstance(values.get("bucket"), Ref):
                    for target_key, arn_key in (("queue", "queue_arn"), ("lambda_function", "lambda_function_arn"),
                                                ("topic", "topic_arn")):
                        target = values.get(target_key)
                        if isinstance(target, dict) and isinstance(target.get(arn_key), Ref):
                            relations.append(InfraRelation(kind="event-source", source=key(values["bucket"]),
                                                           destination=key(target[arn_key]), evidence=ev))
        return resources, relations


# ------------------------------------------------------------------------------------ CloudFormation
class _CfnLoader(yaml.SafeLoader):
    pass


def _intrinsic(loader: yaml.SafeLoader, suffix: str, node: yaml.Node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    if suffix == "Ref":
        return {"Ref": value}
    if suffix == "GetAtt" and isinstance(value, str):
        value = value.split(".", 1)
    return {f"Fn::{suffix}": value}


_CfnLoader.add_multi_constructor("!", _intrinsic)


def is_cloudformation(content: str) -> bool:
    return "AWSTemplateFormatVersion" in content or "AWS::Serverless" in content or \
        bool(re.search(r"""["']?Type["']?\s*:\s*["']?AWS::""", content) and "Resources" in content)


def scan_cloudformation(content: str, repo: str, rel: str) -> tuple[list[InfraResource], list[InfraRelation]]:
    try:
        doc = json.loads(content) if rel.lower().endswith(".json") else yaml.load(content, Loader=_CfnLoader)  # noqa: S506 - SafeLoader subclass
    except (yaml.YAMLError, json.JSONDecodeError):
        return [], []
    if not isinstance(doc, dict) or not isinstance(doc.get("Resources"), dict):
        return [], []
    resources_doc: dict = doc["Resources"]
    params = {k: str(v.get("Default")) for k, v in (doc.get("Parameters") or {}).items()
              if isinstance(v, dict) and v.get("Default") is not None}

    def key(logical: str) -> str:
        return f"{rel}:{logical}"

    def resolve(value):
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and len(value) == 1:
            (fn, arg), = value.items()
            if fn == "Ref":
                return Ref(arg) if arg in resources_doc else params.get(arg, "${" + str(arg) + "}")
            if fn == "Fn::GetAtt":
                return Ref(arg[0]) if isinstance(arg, list) and arg and arg[0] in resources_doc else None
            if fn == "Fn::Sub":
                template = arg[0] if isinstance(arg, list) else arg
                return re.sub(r"\$\{([\w:.]+)\}", lambda m: params.get(m.group(1), m.group(0)), str(template))
            if fn == "Fn::Join" and isinstance(arg, list) and len(arg) == 2:
                parts = [resolve(p) for p in arg[1]]
                return str(arg[0]).join(p if isinstance(p, str) else "${?}" for p in parts)
        return None

    resources: list[InfraResource] = []
    relations: list[InfraRelation] = []
    for logical, spec in sorted(resources_doc.items()):
        if not isinstance(spec, dict):
            continue
        rtype = spec.get("Type", "")
        props = spec.get("Properties") or {}
        line = _line(content, rf"""^\s*["']?{re.escape(logical)}["']?\s*:""")
        ev = Evidence(repo=repo, path=rel, line=line, snippet=f"{logical}: {rtype}")
        if rtype in CFN_TYPES:
            kind, name_prop = CFN_TYPES[rtype]
            name = resolve(props.get(name_prop)) if name_prop in props else None
            env = {}
            if kind == "lambda-function":
                variables = ((props.get("Environment") or {}).get("Variables") or {})
                env = {k: key(r) for k, v in variables.items() if isinstance(r := resolve(v), Ref)}
                for event in (props.get("Events") or {}).values():  # SAM event sources
                    if isinstance(event, dict) and event.get("Type") in SAM_EVENT_SOURCE:
                        src = resolve((event.get("Properties") or {}).get(SAM_EVENT_SOURCE[event["Type"]]))
                        if isinstance(src, Ref):
                            relations.append(InfraRelation(kind="event-source", source=key(src),
                                                           destination=key(logical), evidence=ev))
            if kind == "sns-topic":
                for sub in props.get("Subscription") or []:
                    dest = resolve(sub.get("Endpoint")) if isinstance(sub, dict) else None
                    if isinstance(dest, Ref):
                        relations.append(InfraRelation(kind="subscription", source=key(logical),
                                                       destination=key(dest), evidence=ev))
            resources.append(InfraResource(
                ref=key(logical), type=kind, iac="cloudformation", environment=env, evidence=ev,
                name=name if isinstance(name, str) and not isinstance(name, Ref) and name else logical))
        elif rtype == "AWS::SNS::Subscription":
            topic, dest = resolve(props.get("TopicArn")), resolve(props.get("Endpoint"))
            if isinstance(topic, Ref) and isinstance(dest, Ref):
                relations.append(InfraRelation(kind="subscription", source=key(topic), destination=key(dest),
                                               evidence=ev))
        elif rtype == "AWS::Lambda::EventSourceMapping":
            src, fn = resolve(props.get("EventSourceArn")), resolve(props.get("FunctionName"))
            if isinstance(src, Ref) and isinstance(fn, Ref):
                relations.append(InfraRelation(kind="event-source", source=key(src), destination=key(fn),
                                               evidence=ev))
    return resources, relations
