resource "aws_dynamodb_table" "mc_state" {
  name         = "${var.project_name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "world"

  attribute {
    name = "world"
    type = "S"
  }
}
