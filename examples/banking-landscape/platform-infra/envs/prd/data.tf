resource "aws_dynamodb_table" "card_accounts" {
  name         = "card-accounts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cardId"
}

resource "aws_dynamodb_table" "credit_bureau_cache" {
  name     = "credit-bureau-cache"
  hash_key = "document"
}

resource "aws_s3_bucket" "statements" {
  bucket = "${local.prefix}-statements"
}
