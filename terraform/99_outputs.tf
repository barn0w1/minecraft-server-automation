output "function_url" {
  description = "The URL of the Lambda function to control the server"
  value       = module.mcserver.function_url
}

output "s3_bucket_name" {
  description = "(Legacy) The name of the S3 bucket storing scripts/compose"
  value       = module.mcserver.data_bucket_name
}

output "config_bucket_name" {
  description = "S3 bucket for scripts, compose.yaml, and latest data.tar"
  value       = module.mcserver.config_bucket_name
}

output "data_bucket_name" {
  description = "S3 bucket for latest world state (compose.yaml + data.tar)"
  value       = module.mcserver.data_bucket_name
}

output "snapshot_bucket_name" {
  description = "S3 bucket for data snapshots (tar archives)"
  value       = module.mcserver.snapshot_bucket_name
}

output "dynamodb_table_name" {
  description = "The name of the DynamoDB table storing server state"
  value       = module.mcserver.dynamodb_table_name
}
