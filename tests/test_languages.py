import textwrap

from blueprint_discoverer.cartographer.code.extractors.base import FileContext, graphql_operation_fields
from blueprint_discoverer.cartographer.code.extractors.csharp import CSharpExtractor
from blueprint_discoverer.cartographer.code.extractors.go import GoExtractor
from blueprint_discoverer.cartographer.code.extractors.java import JavaExtractor
from blueprint_discoverer.cartographer.code.extractors.kotlin import KotlinExtractor
from blueprint_discoverer.cartographer.code.extractors.python import PythonExtractor
from blueprint_discoverer.cartographer.code.extractors.typescript import TypeScriptExtractor


def run(extractor, path, src):
    # top-level declarations start at column 0, as in real files (the Kotlin grammar is strict about it)
    f = extractor.extract(FileContext(repo="r", path=path, source=textwrap.dedent(src).encode()))
    endpoints = {(e.protocol, e.method, e.path) for e in f.endpoints}
    outbound = {(o.kind.value, o.target, o.technology) for o in f.outbound}
    return f, endpoints, outbound


def test_java_jaxrs_retrofit_grpc_graphql_soap_and_aws():
    src = '''
    @Path("/limits") public class LimitResource { @GET @Path("/{id}") public Limit get() { return null; } }
    @RegisterRestClient(configKey = "accounts-service") @Path("/accounts")
    public interface AccountsApi { @GET @Path("/{id}") Account get(String id); }
    public interface PixApi { @GET("pix/keys/{key}") Call<Key> key(String key); }
    public class ScoringImpl extends CreditScoringGrpc.CreditScoringImplBase { }
    @Controller public class Gql { @QueryMapping public Customer customer() { return null; } }
    @WebService(serviceName = "CoreBankingService")
    public class CoreImpl { @WebMethod(operationName = "GetBalance") public int bal() { return 1; } public int x() { return 2; } }
    class Clients {
      static final String QUEUE = "https://sqs.us-east-1.amazonaws.com/123/card-events";
      void run() {
        CreditScoringGrpc.newBlockingStub(ch);
        ManagedChannelBuilder.forAddress("credit-scoring", 9090).build();
        sqs.receiveMessage(ReceiveMessageRequest.builder().queueUrl(QUEUE).build());
        ddb.putItem(PutItemRequest.builder().tableName("card-accounts").build());
        template.marshalSendAndReceive("http://core-banking:8080/ws/core", req);
        http.execute(new HttpGet("http://rates-service/rates"));
      }
    }
    '''
    _, endpoints, outbound = run(JavaExtractor(), "A.java", src)
    assert {("http", "GET", "/limits/{id}"), ("grpc", "RPC", "/CreditScoring/*"),
            ("graphql", "QUERY", "Query.customer"), ("soap", "SOAP", "CoreBankingService.GetBalance")} <= endpoints
    assert ("soap", "SOAP", "CoreBankingService.x") not in endpoints  # only @WebMethod when present
    assert {("http", "accounts-service", "microprofile-rest-client"), ("http", "", "retrofit"),
            ("grpc", "CreditScoring", "grpc"), ("grpc", "grpc://credit-scoring:9090", "grpc"),
            ("subscribe", "card-events", "sqs"), ("database", "dynamodb://card-accounts", "dynamodb"),
            ("soap", "http://core-banking:8080/ws/core", "spring-ws"),
            ("http", "http://rates-service/rates", "rest-client")} <= outbound


def test_kotlin_spring_feign_placeholder_grpc_ktor_and_aws():
    src = r'''
    package com.bank.loans

    const val TABLE = "loan-contracts"

    @RestController
    @RequestMapping("/loans")
    class LoanController(@GrpcClient("scoring") private val stub: CreditScoringGrpcKt.CreditScoringCoroutineStub) {
        @GetMapping("/{id}")
        fun get(@PathVariable id: String): Loan = repo.find(id)
    }

    @FeignClient(name = "customers", url = "\${clients.customer.url}")
    interface CustomerClient {
        @GetMapping("/customers/{id}")
        fun get(@PathVariable("id") id: String): Customer
    }

    class LoanEvents {
        @KafkaListener(topics = ["loan-events"])
        fun on(e: String) {}
    }

    fun main() {
        ddb.putItem(PutItemRequest.builder().tableName(TABLE).build())
        routing { route("/api") { get("/health") { call.respond("ok") } } }
        val r = httpClient.get("http://rates-service/rates")
    }
    '''
    f, endpoints, outbound = run(KotlinExtractor(), "A.kt", src)
    assert {("http", "GET", "/loans/{id}"), ("http", "GET", "/api/health")} <= endpoints
    assert {("grpc", "CreditScoring", "grpc-spring"), ("http", "${clients.customer.url}", "feign"),
            ("subscribe", "loan-events", "kafka"), ("database", "dynamodb://loan-contracts", "dynamodb"),
            ("http", "http://rates-service/rates", "rest-client")} <= outbound
    assert "ktor" in f.frameworks and "com.bank.loans" in f.packages


def test_csharp_aspnet_grpc_wcf_hotchocolate_refit_and_aws():
    src = '''
    using Amazon.SQS; using Microsoft.AspNetCore.Mvc;
    namespace Cards.Api;
    [ApiController]
    [Route("api/[controller]")]
    public class CardsController : ControllerBase {
        private const string Accounts = "http://accounts-service:8080";
        [HttpGet("{id}")]
        public async Task<Card> Get(string id) {
            var a = await _http.GetFromJsonAsync<Account>($"{Accounts}/accounts/{id}");
            await _sqs.SendMessageAsync(new SendMessageRequest { QueueUrl = "https://sqs.us-east-1.amazonaws.com/1/card-events" });
            var c = new CreditScoring.CreditScoringClient(channel);
            await producer.ProduceAsync("card-authorizations", msg);
            return null;
        }
        [HttpPost("/cards/block")] public void Block() {}
    }
    public class ScoringImpl : CreditScoring.CreditScoringBase { }
    [ServiceContract(Name = "CardService")] public interface ICardService { [OperationContract] Card GetCard(string id); }
    public class LegacyClient : ClientBase<ICoreBanking> { }
    [QueryType] public class CardQueries { public Card GetCardAsync(string id) => null; }
    public class CardsDb : DbContext { public DbSet<Card> Cards { get; set; } }
    public interface IPixApi { [Get("/pix/keys/{key}")] Task<Key> GetKey(string key); }
    '''
    _, endpoints, outbound = run(CSharpExtractor(), "A.cs", src)
    assert {("http", "GET", "/api/cards/{id}"), ("http", "POST", "/cards/block"), ("grpc", "RPC", "/CreditScoring/*"),
            ("soap", "SOAP", "CardService.GetCard"), ("graphql", "QUERY", "Query.card")} <= endpoints
    assert {("http", "http://accounts-service:8080/accounts/{id}", "httpclient"), ("publish", "card-events", "sqs"),
            ("grpc", "CreditScoring", "grpc-dotnet"), ("publish", "card-authorizations", "kafka"),
            ("soap", "CoreBanking", "wcf"), ("database", "", "efcore"), ("http", "", "refit")} <= outbound

    program = '''
    var api = app.MapGroup("/api");
    api.MapGet("/health", () => "ok");
    builder.Services.AddGrpcClient<Limits.LimitsClient>(o => o.Address = new Uri("http://limits-service:9090"));
    var ep = new EndpointAddress("http://core-banking:8080/ws/core");
    '''
    _, endpoints, outbound = run(CSharpExtractor(), "Program.cs", program)
    assert ("http", "GET", "/api/health") in endpoints
    assert {("grpc", "Limits", "grpc-dotnet"), ("grpc", "http://limits-service:9090", "grpc-dotnet"),
            ("soap", "http://core-banking:8080/ws/core", "wcf")} <= outbound


def test_go_routes_clients_grpc_kafka_and_aws():
    src = '''package main
    import ("net/http"; pb "github.com/acme/credit/v1"; "github.com/gin-gonic/gin")
    const baseURL = "http://accounts-service:8080"
    func main() {
        r := gin.Default()
        api := r.Group("/api")
        api.GET("/pix/:id", h)
        http.HandleFunc("GET /health", h)
        http.Get(baseURL + "/accounts/1")
        http.NewRequest("POST", "http://payments-service:8000/payment-orders", nil)
        grpc.NewClient("credit-scoring:9090", opts)
        pb.NewCreditScoringClient(conn)
        pb.RegisterPixServiceServer(s, &server{})
        client.PutRecord(ctx, &kinesis.PutRecordInput{StreamName: aws.String("pix-events")})
        w := &kafka.Writer{Topic: "pix-transfers"}
        rd := kafka.NewReader(kafka.ReaderConfig{Topic: "payment-orders"})
    }
    '''
    f, endpoints, outbound = run(GoExtractor(), "main.go", src)
    assert {("http", "GET", "/api/pix/:id"), ("http", "GET", "/health"), ("grpc", "RPC", "/PixService/*")} <= endpoints
    assert {("http", "http://accounts-service:8080/accounts/1", "http"),
            ("http", "http://payments-service:8000/payment-orders", "http"),
            ("grpc", "grpc://credit-scoring:9090", "grpc-go"), ("grpc", "CreditScoring", "grpc-go"),
            ("publish", "pix-events", "kinesis"), ("publish", "pix-transfers", "kafka"),
            ("subscribe", "payment-orders", "kafka")} <= outbound
    assert {"gin", "net-http"} <= f.frameworks


def test_python_grpc_graphql_soap_and_boto3():
    src = '''import boto3, grpc, strawberry, zeep
import credit_pb2_grpc
QUEUE = "https://sqs.sa-east-1.amazonaws.com/1/fraud-cases"
class Scoring(credit_pb2_grpc.CreditScoringServicer): pass
@strawberry.type
class Query:
    @strawberry.field
    def fraud_case(self, id: str) -> str: return ""
def run():
    sqs.send_message(QueueUrl=QUEUE, MessageBody="x")
    sqs.receive_message(QueueUrl="payment-orders-queue")
    boto3.resource("dynamodb").Table("fraud-scores")
    credit_pb2_grpc.CreditScoringStub(grpc.insecure_channel("credit-scoring:9090"))
    zeep.Client("http://core-banking:8080/ws/core?wsdl")
'''
    _, endpoints, outbound = run(PythonExtractor(), "a.py", src)
    assert {("grpc", "RPC", "/CreditScoring/*"), ("graphql", "QUERY", "Query.fraudCase")} <= endpoints
    assert {("publish", "fraud-cases", "sqs"), ("subscribe", "payment-orders-queue", "sqs"),
            ("database", "dynamodb://fraud-scores", "dynamodb"), ("grpc", "CreditScoring", "grpc"),
            ("grpc", "grpc://credit-scoring:9090", "grpc"),
            ("soap", "http://core-banking:8080/ws/core?wsdl", "zeep")} <= outbound


def test_typescript_resolvers_grpc_soap_and_aws_sdk_v3():
    src = '''
    @Resolver()
    export class CustomerResolver {
      @Query(() => Customer, { name: "customer" }) getCustomer() {}
      @Mutation() updateCustomer() {}
    }
    @Controller()
    export class Scoring { @GrpcMethod("CreditScoring", "Score") score() {} }
    const GET = gql`query GetAccount { account(id: 1) { id } }`;
    async function run() {
      await sqs.send(new SendMessageCommand({ QueueUrl: "https://sqs.us-east-1.amazonaws.com/1/customer-events" }));
      await doc.put({ TableName: "customer-index", Item: {} });
      const svc = this.client.getService("Limits");
      const apollo = new ApolloClient({ uri: "http://digital-banking-bff:3000/graphql" });
      await soap.createClientAsync("http://core-banking:8080/ws/core?wsdl");
    }
    '''
    _, endpoints, outbound = run(TypeScriptExtractor(), "a.ts", src)
    assert {("graphql", "QUERY", "Query.customer"), ("graphql", "MUTATION", "Mutation.updateCustomer"),
            ("grpc", "RPC", "/CreditScoring/Score")} <= endpoints
    assert {("graphql", "", "graphql-client"), ("publish", "customer-events", "sqs"),
            ("database", "dynamodb://customer-index", "dynamodb"), ("grpc", "Limits", "nestjs-grpc"),
            ("graphql", "http://digital-banking-bff:3000/graphql", "graphql-client"),
            ("soap", "http://core-banking:8080/ws/core?wsdl", "node-soap")} <= outbound


def test_graphql_operation_fields_ignore_aliases_fragments_and_directives():
    doc = "query Q($id: ID!) { account(id: $id) { id } me: customer { name } ...Frag @include(if: true) }"
    assert graphql_operation_fields(doc) == [("QUERY", "account"), ("QUERY", "customer")]
