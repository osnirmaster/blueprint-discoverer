"""Card transaction monitoring: consumes card events from SQS and opens fraud cases."""

import boto3

CARD_EVENTS = "https://sqs.sa-east-1.amazonaws.com/123456789012/card-events"
FRAUD_CASES = "https://sqs.sa-east-1.amazonaws.com/123456789012/fraud-cases"

sqs = boto3.client("sqs")


def poll_card_events() -> None:
    batch = sqs.receive_message(QueueUrl=CARD_EVENTS, MaxNumberOfMessages=10)
    for message in batch.get("Messages", []):
        if suspicious(message):
            sqs.send_message(QueueUrl=FRAUD_CASES, MessageBody=message["Body"])


def suspicious(message: dict) -> bool:
    return "blocked" in message["Body"]
