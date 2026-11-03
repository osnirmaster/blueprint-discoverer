using Bank.Credit.V1;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddControllers();
builder.Services.AddGrpcClient<CreditScoring.CreditScoringClient>(o => o.Address = new Uri("http://credit-scoring:9090"));

var app = builder.Build();
app.MapControllers();
var ops = app.MapGroup("/ops");
ops.MapGet("/health", () => "ok");
app.Run();
