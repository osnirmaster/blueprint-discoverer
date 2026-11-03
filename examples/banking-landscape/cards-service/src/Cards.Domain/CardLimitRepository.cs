using Amazon.DynamoDBv2;
using Amazon.DynamoDBv2.Model;
using System.ServiceModel;

namespace Cards.Domain;

public class CardLimitRepository
{
    private readonly IAmazonDynamoDB _ddb;

    public Task SaveLimit(CardLimit limit) =>
        _ddb.PutItemAsync(new PutItemRequest { TableName = "card-accounts", Item = limit.ToItem() });

    public ICoreBanking LegacyLedger() =>
        new ChannelFactory<ICoreBanking>(new BasicHttpBinding(),
            new EndpointAddress("http://core-banking-legacy:8080/ws/core")).CreateChannel();
}
