using Amazon.SQS;
using Amazon.SQS.Model;
using Microsoft.AspNetCore.Mvc;

namespace Cards.Api.Controllers;

[ApiController]
[Route("api/[controller]")]
public class CardsController : ControllerBase
{
    private const string Accounts = "http://accounts-service:8080";
    private const string CardEventsQueue = "https://sqs.sa-east-1.amazonaws.com/123456789012/card-events";
    private readonly HttpClient _http;
    private readonly IAmazonSQS _sqs;

    [HttpGet("{cardId}")]
    public Task<Card> GetCard(string cardId) => _cards.Find(cardId);

    [HttpPost("{cardId}/block")]
    public async Task BlockCard(string cardId)
    {
        await _sqs.SendMessageAsync(new SendMessageRequest { QueueUrl = CardEventsQueue, MessageBody = cardId });
    }

    [HttpPost("authorizations")]
    public async Task<Authorization> AuthorizeTransaction(AuthorizationRequest request)
    {
        var balance = await _http.GetFromJsonAsync<Balance>($"{Accounts}/accounts/{request.AccountId}/balance");
        return _limits.Authorize(request, balance);
    }
}
