"""Domain model shared by every pipeline stage.

Flow: scan (facts) -> link (relationships) -> infer (semantics) -> CALM (canonical) -> graph / wiki / UI.
Everything here is plain data so each stage can be persisted as JSON and diffed between runs.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """Pointer to the exact place in source code that justifies a fact."""

    repo: str
    path: str
    line: int = 0
    snippet: str = ""

    def ref(self) -> str:
        return f"{self.repo}:{self.path}:{self.line}"


class Endpoint(BaseModel):
    """An API exposed by a component (inbound interface).

    `protocol` http: method GET/POST/... and URL path; grpc: method RPC and path /package.Service/Method;
    graphql: method QUERY/MUTATION/SUBSCRIPTION and path Query.field; soap: method SOAP and path Service.Operation.
    """

    method: str  # GET, POST, ... or "*"
    path: str
    handler: str = ""
    protocol: str = "http"  # http | grpc | graphql | soap
    evidence: Evidence


class OutboundKind(str, Enum):
    HTTP = "http"
    MESSAGING_PUBLISH = "publish"
    MESSAGING_SUBSCRIBE = "subscribe"
    DATABASE = "database"
    GRPC = "grpc"  # target: service name (pkg.Service) or channel address; detail: "RPC /pkg.Service/Method"
    GRAPHQL = "graphql"  # target: endpoint URL or ""; detail: "QUERY Query.field"
    SOAP = "soap"  # target: endpoint/WSDL URL or service name; detail: "SOAP Service.Operation"


class OutboundCall(BaseModel):
    """Something a component reaches out to. `target` is unresolved until the linker runs."""

    kind: OutboundKind
    target: str  # URL, host alias, feign client name, topic name or datastore name
    detail: str = ""  # method+path for HTTP, table for DB, etc.
    technology: str = ""  # kafka, rabbitmq, postgresql, oracle, dynamodb, s3, sqs, sns, kinesis, ...
    evidence: Evidence


class CodeSymbol(BaseModel):
    """Class/function-level facts used by the semantic layer (names carry business meaning)."""

    kind: str  # class, function, entity, interface
    name: str
    qualified_name: str = ""
    annotations: list[str] = Field(default_factory=list)
    evidence: Evidence


class Component(BaseModel):
    """A deployable application/service discovered inside a repository."""

    id: str  # stable slug, used as CALM unique-id
    name: str
    repo: str
    root_path: str = "."
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)  # hostnames/service names other apps use to call it
    kind: str = "service"  # CALM node-type: service | webclient | system | ...
    description: str = ""
    endpoints: list[Endpoint] = Field(default_factory=list)
    outbound: list[OutboundCall] = Field(default_factory=list)
    symbols: list[CodeSymbol] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    config: dict[str, str] = Field(default_factory=dict)
    readme_excerpt: str = ""


class InfraResource(BaseModel):
    """A cloud resource declared in infrastructure-as-code (Terraform, CloudFormation/SAM)."""

    ref: str  # IaC address, unique per repository: aws_sqs_queue.orders | Cfn logical id
    type: str  # dynamodb-table | s3-bucket | sqs-queue | sns-topic | kinesis-stream | lambda-function
    name: str  # physical name; unresolved parts stay as ${...}
    iac: str  # terraform | cloudformation
    owner: str = ""  # component id when declared inside a component's folder
    environment: dict[str, str] = Field(default_factory=dict)  # env var -> referenced resource ref (functions)
    evidence: Evidence


class InfraRelation(BaseModel):
    """Wiring declared in IaC: SNS->SQS subscriptions, event sources feeding a function."""

    kind: str  # subscription | event-source
    source: str  # resource ref
    destination: str  # resource ref
    evidence: Evidence


class RepoScan(BaseModel):
    repo: str
    location: str
    commit: str = ""
    components: list[Component] = Field(default_factory=list)
    files_scanned: int = 0
    warnings: list[str] = Field(default_factory=list)
    infrastructure: list[InfraResource] = Field(default_factory=list)
    infra_relations: list[InfraRelation] = Field(default_factory=list)


class DataStore(BaseModel):
    id: str
    name: str
    technology: str = ""
    kind: str = "database"  # database | object-storage
    iac: list[str] = Field(default_factory=list)  # repo:path:line of IaC declarations
    evidence: list[Evidence] = Field(default_factory=list)


class Topic(BaseModel):
    id: str
    name: str
    technology: str = "kafka"
    kind: str = "topic"  # topic | queue | stream
    iac: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class LinkKind(str, Enum):
    HTTP = "http"
    GRPC = "grpc"
    GRAPHQL = "graphql"
    SOAP = "soap"
    ASYNC = "async"  # producer -> consumer through a topic
    DATA = "data"  # component -> datastore
    EXTERNAL = "external"  # component -> unresolved/external system


class Link(BaseModel):
    """Resolved dependency between two architecture elements."""

    id: str
    kind: LinkKind
    source: str
    destination: str
    protocol: str = ""  # CALM protocol enum value
    via: str = ""  # topic id for async links
    description: str = ""
    confidence: float = 1.0
    rule: str = ""  # which deterministic rule produced the link
    evidence: list[Evidence] = Field(default_factory=list)


class ExternalSystem(BaseModel):
    id: str
    name: str
    target: str
    evidence: list[Evidence] = Field(default_factory=list)


class LinkResult(BaseModel):
    links: list[Link] = Field(default_factory=list)
    datastores: list[DataStore] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    externals: list[ExternalSystem] = Field(default_factory=list)
    unresolved: list[OutboundCall] = Field(default_factory=list)


class DomainCandidate(BaseModel):
    service_domain_id: str
    service_domain: str
    business_domain: str
    business_area: str
    score: float
    source: str  # deterministic | llm | merged
    matched_terms: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # Evidence.ref() strings


class SemanticProfile(BaseModel):
    component_id: str
    purpose: str = ""
    objectives: list[str] = Field(default_factory=list)
    business_capabilities: list[str] = Field(default_factory=list)
    business_functions: list[str] = Field(default_factory=list)
    primary_domain: DomainCandidate | None = None
    secondary_domains: list[DomainCandidate] = Field(default_factory=list)
    confidence: float = 0.0
    method: str = "deterministic"  # deterministic | llm | llm-cached
    rationale: str = ""
    review_required: bool = False
