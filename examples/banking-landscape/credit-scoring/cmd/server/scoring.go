package main

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/kinesis"
)

const decisionsStream = "credit-decisions"

type scoringServer struct {
	ddb     *dynamodb.Client
	kinesis *kinesis.Client
}

func (s *scoringServer) ScoreCustomer(ctx context.Context, customerID string) (int32, error) {
	cached, _ := s.ddb.GetItem(ctx, &dynamodb.GetItemInput{TableName: aws.String("credit-bureau-cache")})
	_ = cached
	_, err := s.kinesis.PutRecord(ctx, &kinesis.PutRecordInput{StreamName: aws.String(decisionsStream), PartitionKey: aws.String(customerID)})
	return 720, err
}
