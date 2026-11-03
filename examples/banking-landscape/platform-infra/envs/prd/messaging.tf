resource "aws_sqs_queue" "card_events" {
  name = "card-events"
}

module "fraud_cases_queue" {
  source = "terraform-aws-modules/sqs/aws"
  name   = "fraud-cases"
}

resource "aws_sns_topic" "payment_notifications" {
  name = "payment-notifications"
}

resource "aws_sqs_queue" "payment_notifications_email" {
  name = "payment-notifications-email"
}

resource "aws_sns_topic_subscription" "payment_notifications_email" {
  topic_arn = aws_sns_topic.payment_notifications.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.payment_notifications_email.arn
}

resource "aws_kinesis_stream" "credit_decisions" {
  name        = "credit-decisions"
  shard_count = 2
}
