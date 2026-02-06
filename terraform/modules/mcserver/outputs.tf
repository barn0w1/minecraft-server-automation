output "function_url" {
  description = "Lambda Function URL for mc-control"
  value       = aws_lambda_function_url.mc_control_url.function_url
}

output "config_bucket_name" {
  description = "S3 bucket for scripts, compose.yaml, and latest data.tar"
  value       = aws_s3_bucket.mc_data.bucket
}

output "data_bucket_name" {
  description = "S3 bucket for latest world state (compose.yaml + data.tar)"
  value       = aws_s3_bucket.mc_data.bucket
}

output "snapshot_bucket_name" {
  description = "S3 bucket for data snapshots (tar archives)"
  value       = aws_s3_bucket.mc_snapshots.bucket
}

output "dynamodb_table_name" {
  description = "DynamoDB table for world state"
  value       = aws_dynamodb_table.mc_state.name
}
