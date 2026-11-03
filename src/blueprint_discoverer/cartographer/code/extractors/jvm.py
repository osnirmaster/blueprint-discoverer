"""JVM framework semantics shared by the Java and Kotlin extractors.

Both extractors translate their own syntax tree into the neutral `TypeDecl` / `Call` records below;
the rules about what Spring, JAX-RS, Feign, Retrofit, gRPC, GraphQL, SOAP and the AWS SDK mean live
here once.

Recognised facts:
  * Spring MVC (@RestController + @GetMapping...), JAX-RS (@Path + @GET)        -> endpoints
  * @FeignClient, @HttpExchange, MicroProfile @RegisterRestClient, Retrofit @GET -> outbound HTTP
  * RestTemplate / WebClient / RestClient / OkHttp / JAX-RS client / Ktor client, Apache HttpGet,
    URI.create with literal URLs                                                 -> outbound HTTP
  * gRPC: *ImplBase subclasses / @GrpcService (server); *Grpc.newBlockingStub, *CoroutineStub,
    @GrpcClient fields, ManagedChannelBuilder.forAddress (client)
  * GraphQL: @QueryMapping/@MutationMapping/@SchemaMapping, DGS @DgsQuery/@DgsData (server);
    operation documents in string literals (client)
  * SOAP: JAX-WS @WebService/@WebMethod, Spring-WS @Endpoint/@PayloadRoot (server);
    WebServiceTemplate.marshalSendAndReceive, BindingProvider.ENDPOINT_ADDRESS_PROPERTY (client)
  * Kafka / RabbitMQ / JMS / SQS listeners and templates                        -> pub / sub
  * AWS SDK v2 request builders (.tableName/.bucket/.queueUrl/.topicArn/.streamName), Spring Cloud
    AWS SqsTemplate/SnsTemplate, DynamoDB enhanced client                        -> cloud resources
  * @Entity/@Table, Spring Data repositories                                     -> persistence
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ....model import CodeSymbol, Endpoint, OutboundCall, OutboundKind
from .base import (
    HTTP_METHODS,
    URL_RE,
    FileContext,
    FileFacts,
    aws_call,
    graphql_calls,
    grpc_client,
    grpc_endpoint,
    join_paths,
    looks_like_graphql,
)

MAPPINGS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
            "PatchMapping": "PATCH", "RequestMapping": "*"}
EXCHANGES = {"GetExchange": "GET", "PostExchange": "POST", "PutExchange": "PUT", "DeleteExchange": "DELETE",
             "PatchExchange": "PATCH", "HttpExchange": "*"}
VERB_ANNOTATIONS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}  # JAX-RS and Retrofit
GRAPHQL_MAPPINGS = {"QueryMapping": "Query", "MutationMapping": "Mutation", "SubscriptionMapping": "Subscription",
                    "DgsQuery": "Query", "DgsMutation": "Mutation", "DgsSubscription": "Subscription"}
LISTENERS = {"KafkaListener": "kafka", "RabbitListener": "rabbitmq", "JmsListener": "jms", "SqsListener": "sqs"}
REST_CLIENT_METHODS = {
    "getForObject", "getForEntity", "postForObject", "postForEntity", "put", "delete", "exchange",
    "patchForObject", "uri", "retrieve", "url", "baseUrl", "target",
}
HTTP_REQUEST_CLASSES = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE",
                        "HttpPatch": "PATCH"}
STUB_FACTORIES = {"newBlockingStub", "newStub", "newFutureStub", "newBlockingV2Stub"}
AWS_BUILDER_FIELDS = {"tableName", "bucket", "queueUrl", "queueName", "topicArn", "targetArn", "streamName",
                      "streamARN"}
STEREOTYPES = {
    "RestController": "spring-web", "Controller": "spring-web", "Service": "spring",
    "Repository": "spring-data", "Entity": "jpa", "FeignClient": "spring-cloud-openfeign",
    "KafkaListener": "spring-kafka", "RabbitListener": "spring-amqp", "SpringBootApplication": "spring-boot",
    "GrpcService": "grpc", "GrpcClient": "grpc", "WebService": "jax-ws", "Endpoint": "spring-ws",
    "Path": "jax-rs", "RegisterRestClient": "microprofile-rest-client", "HttpExchange": "spring-http-interface",
    "QueryMapping": "spring-graphql", "MutationMapping": "spring-graphql", "DgsComponent": "netflix-dgs",
    "SqsListener": "spring-cloud-aws",
}
GRPC_BASE_RE = re.compile(r"(\w+)Grpc(?:Kt)?\.(\w+?)(?:Coroutine)?ImplBase")
GRPC_TYPE_RE = re.compile(r"(\w+)Grpc(?:Kt)?\.\w+Stub")


@dataclass
class Ann:
    name: str
    args: dict[str, list[str]]
    line: int
    text: str = ""

    def first(self, *keys: str) -> str | None:
        for k in keys:
            if self.args.get(k):
                return self.args[k][0]
        return None


@dataclass
class Member:
    name: str
    kind: str  # method | field
    anns: list[Ann]
    line: int
    public: bool = True
    type_text: str = ""


@dataclass
class TypeDecl:
    name: str
    kind: str  # class | interface
    anns: list[Ann]
    supers: str
    members: list[Member]
    line: int
    package: str = ""
    line_text: str = ""


@dataclass
class Call:
    receiver: str  # text of the receiver expression ("" for bare calls)
    method: str  # method name, or the class name for constructor calls
    args: list[str | None]  # resolved string values (None when not statically known)
    line: int
    statement: str = ""  # text of the enclosing statement (direction hints for AWS SDK calls)
    is_new: bool = False
    extra: dict[str, str] = field(default_factory=dict)


def _mapping(ann: Ann) -> tuple[str, list[str]] | None:
    table = MAPPINGS if ann.name in MAPPINGS else EXCHANGES if ann.name in EXCHANGES else None
    if table is None:
        return None
    paths = ann.args.get("value", []) + ann.args.get("path", []) or [""]
    method = table[ann.name]
    if method == "*" and ann.args.get("method"):
        method = ann.args["method"][0].split(".")[-1].upper()
    return method, paths


def grpc_service_of(supers: str) -> str | None:
    m = GRPC_BASE_RE.search(supers)
    return m.group(1) if m else None


def type_facts(td: TypeDecl, ctx: FileContext, facts: FileFacts) -> None:
    names = [a.name for a in td.anns]
    by_name = {a.name: a for a in td.anns}
    for n in names:
        if n in STEREOTYPES:
            facts.frameworks.add(STEREOTYPES[n])

    kind = td.kind
    if "Entity" in names or "Table" in names:
        kind = "entity"
    if any(r in td.supers for r in ("JpaRepository", "CrudRepository", "MongoRepository", "ReactiveCrudRepository")):
        kind = "repository"
        facts.frameworks.add("spring-data")
    ev_type = ctx.evidence(td.line, td.line_text or ctx.line_text(td.line))
    facts.symbols.append(CodeSymbol(kind=kind, name=td.name,
                                    qualified_name=f"{td.package}.{td.name}" if td.package else td.name,
                                    annotations=names, evidence=ev_type))
    if kind == "entity":
        table = by_name["Table"].first("name") if "Table" in by_name else None
        facts.outbound.append(OutboundCall(kind=OutboundKind.DATABASE, target="", detail=table or td.name,
                                           technology="jpa", evidence=ctx.evidence(td.line, f"@Entity {td.name}")))

    # declarative HTTP clients: the type names the target, its methods the operations
    client_target: str | None = None
    client_tech = ""
    if "FeignClient" in by_name:
        client_target = by_name["FeignClient"].first("url", "name", "value") or td.name
        client_tech = "feign"
    elif "RegisterRestClient" in by_name:
        client_target = by_name["RegisterRestClient"].first("baseUri", "configKey") or td.name
        client_tech = "microprofile-rest-client"
    elif "HttpExchange" in by_name and td.kind == "interface":
        client_target = by_name["HttpExchange"].first("url") or ""
        client_tech = "http-interface"

    prefixes = [""]
    for a in td.anns:
        if (mapping := _mapping(a)) is not None:
            prefixes = mapping[1]
        elif a.name == "Path":
            prefixes = [a.first("value") or ""]
    is_controller = "RestController" in names or "Controller" in names
    is_jaxrs = "Path" in by_name and client_target is None

    grpc_service = grpc_service_of(td.supers)
    if grpc_service:
        facts.frameworks.add("grpc")
        facts.endpoints.append(grpc_endpoint(grpc_service, ev_type, handler=td.name))

    soap_service = None
    if "WebService" in by_name and td.kind == "class":
        soap_service = by_name["WebService"].first("serviceName", "name") or td.name.removesuffix("Impl")
    has_web_method = any(a.name == "WebMethod" for m in td.members for a in m.anns)

    for member in td.members:
        if member.kind == "field":
            for a in member.anns:
                if a.name == "GrpcClient":
                    svc = GRPC_TYPE_RE.search(member.type_text)
                    target = svc.group(1) if svc else (a.first("value") or member.name)
                    facts.outbound.append(grpc_client(target, ctx.evidence(a.line, a.text), "grpc-spring"))
            continue
        handler = f"{td.name}.{member.name}"
        verb = next((a for a in member.anns if a.name in VERB_ANNOTATIONS), None)
        sub_path = next((a.first("value") or "" for a in member.anns if a.name == "Path"), "")
        for a in member.anns:
            ev = ctx.evidence(a.line, a.text)
            if a.name in LISTENERS:
                for topic in (a.args.get("topics", []) + a.args.get("queues", []) + a.args.get("destination", [])
                              + a.args.get("queueNames", []) + a.args.get("value", [])):
                    facts.outbound.append(OutboundCall(kind=OutboundKind.MESSAGING_SUBSCRIBE, target=topic,
                                                       technology=LISTENERS[a.name], evidence=ev))
            elif a.name in GRAPHQL_MAPPINGS:
                field_name = a.first("name", "field", "value") or member.name
                root = GRAPHQL_MAPPINGS[a.name]
                facts.endpoints.append(Endpoint(method=root.upper(), path=f"{root}.{field_name}", handler=handler,
                                                protocol="graphql", evidence=ev))
            elif a.name in ("SchemaMapping", "DgsData") and (a.first("typeName", "parentType") in
                                                            ("Query", "Mutation", "Subscription")):
                root = a.first("typeName", "parentType") or "Query"
                field_name = a.first("field", "value") or member.name
                facts.endpoints.append(Endpoint(method=root.upper(), path=f"{root}.{field_name}", handler=handler,
                                                protocol="graphql", evidence=ev))
            elif a.name == "PayloadRoot":
                op = a.first("localPart") or member.name
                facts.endpoints.append(Endpoint(method="SOAP", path=f"{td.name}.{op}", handler=handler,
                                                protocol="soap", evidence=ev))
            elif (mapping := _mapping(a)) is not None:
                method, paths = mapping
                for prefix in prefixes:
                    for p in paths:
                        full = join_paths(prefix, p)
                        if client_target is not None:
                            facts.outbound.append(OutboundCall(kind=OutboundKind.HTTP, target=client_target,
                                                               detail=f"{method} {full}", technology=client_tech,
                                                               evidence=ev))
                        elif is_controller:
                            facts.endpoints.append(Endpoint(method=method, path=full, handler=handler, evidence=ev))
            elif a is verb:
                retrofit_path = a.first("value")
                if retrofit_path is not None and td.kind == "interface" and not is_jaxrs and client_target is None:
                    facts.frameworks.add("retrofit")
                    facts.outbound.append(OutboundCall(kind=OutboundKind.HTTP, target="",
                                                       detail=f"{a.name} {join_paths(retrofit_path)}",
                                                       technology="retrofit", evidence=ev))
                    continue
                for prefix in prefixes:
                    full = join_paths(prefix, sub_path)
                    if client_target is not None:
                        facts.outbound.append(OutboundCall(kind=OutboundKind.HTTP, target=client_target,
                                                           detail=f"{a.name} {full}", technology=client_tech,
                                                           evidence=ev))
                    elif is_jaxrs:
                        facts.endpoints.append(Endpoint(method=a.name, path=full, handler=handler, evidence=ev))
        if soap_service and member.public and (not has_web_method or any(a.name == "WebMethod" for a in member.anns)):
            op = next((a.first("operationName") for a in member.anns if a.name == "WebMethod"), None) or member.name
            facts.endpoints.append(Endpoint(method="SOAP", path=f"{soap_service}.{op}", handler=handler,
                                            protocol="soap", evidence=ctx.evidence(member.line)))


def call_facts(call: Call, ctx: FileContext, facts: FileFacts) -> None:
    lits = [a for a in call.args if a]
    recv, m = call.receiver.lower(), call.method
    ev = ctx.evidence(call.line, ctx.line_text(call.line))

    def out(kind: OutboundKind, target: str, technology: str, detail: str = "") -> None:
        facts.outbound.append(OutboundCall(kind=kind, target=target, detail=detail, technology=technology,
                                           evidence=ev))

    # messaging templates
    if m == "send" and "kafka" in recv and lits:
        return out(OutboundKind.MESSAGING_PUBLISH, lits[0], "kafka")
    if m == "convertAndSend" and "rabbit" in recv and lits:
        return out(OutboundKind.MESSAGING_PUBLISH, lits[0], "rabbitmq")
    if "sqs" in recv and m in ("send", "sendAsync", "convertAndSend", "receive", "receiveAsync") and lits:
        kind = OutboundKind.MESSAGING_SUBSCRIBE if m.startswith("receive") else OutboundKind.MESSAGING_PUBLISH
        return out(kind, lits[0], "sqs")
    if ("sns" in recv or "notification" in recv) and m in ("sendNotification", "convertAndSend", "send") and lits:
        return out(OutboundKind.MESSAGING_PUBLISH, lits[0], "sns")

    # AWS SDK v2
    if m in AWS_BUILDER_FIELDS and lits:
        if c := aws_call(m, lits[0], call.statement or m, ev):
            facts.outbound.append(c)
        return
    if m == "table" and ("enhanced" in recv or "dynamo" in recv) and lits:
        return out(OutboundKind.DATABASE, f"dynamodb://{lits[0]}", "dynamodb", "table")

    # gRPC clients
    if m in STUB_FACTORIES and call.receiver.endswith("Grpc"):
        facts.frameworks.add("grpc")
        facts.outbound.append(grpc_client(call.receiver.split(".")[-1].removesuffix("Grpc"), ev))
        return
    if m.endswith("CoroutineStub") and m != "CoroutineStub":
        facts.frameworks.add("grpc")
        facts.outbound.append(grpc_client(m.removesuffix("CoroutineStub"), ev, "grpc-kotlin"))
        return
    if m in ("forAddress", "forTarget") and call.receiver.endswith("ChannelBuilder") and lits:
        address = re.sub(r"^\w+:/*", "", lits[0]) if "://" in lits[0] or lits[0].startswith("dns:") else lits[0]
        port = f":{call.args[1]}" if m == "forAddress" and len(call.args) > 1 and call.args[1] else ""
        return out(OutboundKind.GRPC, f"grpc://{address}{port}", "grpc", "channel")

    # SOAP clients
    if m == "marshalSendAndReceive" and lits and URL_RE.search(lits[0]):
        return out(OutboundKind.SOAP, lits[0], "spring-ws")
    if m == "put" and "ENDPOINT_ADDRESS_PROPERTY" in call.statement and lits and URL_RE.search(lits[-1]):
        return out(OutboundKind.SOAP, lits[-1], "jax-ws")

    # GraphQL operation documents (HttpGraphQlClient.document, DGS client, raw strings)
    docs = [v for v in lits if looks_like_graphql(v)]
    if docs:
        for d in docs:
            facts.outbound += graphql_calls(d, "", ev, "graphql-client")
        return

    # HTTP clients
    http_method = ""
    if call.is_new and m in HTTP_REQUEST_CLASSES:
        http_method = HTTP_REQUEST_CLASSES[m]
    elif m in REST_CLIENT_METHODS or (m == "create" and call.receiver.endswith("URI")):
        http_method = m
    elif m in HTTP_METHODS and ("client" in recv or "http" in recv):
        http_method = m.upper()  # Ktor/OkHttp style client.get("http://...")
    elif call.is_new and m == "URL" and lits and "wsdl" in lits[0].lower():
        return out(OutboundKind.SOAP, lits[0], "jax-ws")
    if not http_method:
        return
    for lit in lits:
        if url := URL_RE.search(lit):
            out(OutboundKind.HTTP, url.group(0), "rest-client", http_method)
