data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.root}/../lambda/mc_control"
  output_path = "${path.root}/.build/lambda_function.zip"
}

locals {
  lambda_env = {
    INSTANCE_PROFILE_ARN = aws_iam_instance_profile.ec2_profile.arn
    SECURITY_GROUP_ID    = aws_security_group.mc_sg.id
    S3_BUCKET_NAME       = aws_s3_bucket.mc_data.id
    CONFIG_BUCKET_NAME   = aws_s3_bucket.mc_data.id
    SNAPSHOT_BUCKET_NAME = aws_s3_bucket.mc_snapshots.id
    DYNAMODB_TABLE       = aws_dynamodb_table.mc_state.name
    INSTANCE_TYPE        = var.instance_type
    EBS_VOLUME_SIZE      = tostring(var.ebs_volume_size)
    EBS_VOLUME_TYPE      = var.ebs_volume_type
    REGION               = var.aws_region
    IDLE_TIMEOUT         = tostring(var.idle_timeout_seconds)
    CLOUDFLARE_API_TOKEN = var.cloudflare_api_token
    CLOUDFLARE_ZONE_ID   = var.cloudflare_zone_id
    DNS_RECORD_NAME      = var.dns_record_name
  }
}

resource "aws_lambda_function" "mc_control" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "${var.project_name}-control"
  role             = aws_iam_role.lambda_role.arn
  handler          = "main.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout

  environment {
    variables = local.lambda_env
  }
}

resource "aws_lambda_function" "mc_monitor" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "${var.project_name}-monitor"
  role             = aws_iam_role.lambda_role.arn
  handler          = "main.monitor_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = var.lambda_runtime
  timeout          = var.lambda_timeout

  environment {
    variables = local.lambda_env
  }
}

resource "aws_lambda_function_url" "mc_control_url" {
  function_name      = aws_lambda_function.mc_control.function_name
  authorization_type = "NONE"
}
