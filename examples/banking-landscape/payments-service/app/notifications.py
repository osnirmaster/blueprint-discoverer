"""Payment notifications fan-out (SNS)."""

import boto3

PAYMENT_NOTIFICATIONS = "arn:aws:sns:sa-east-1:123456789012:payment-notifications"

sns = boto3.client("sns")


def notify_settled(payment_order_id: str) -> None:
    sns.publish(TopicArn=PAYMENT_NOTIFICATIONS, Message=payment_order_id)
