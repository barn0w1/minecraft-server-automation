module "mcserver" {
  source = "./modules/mcserver"

  project_name = var.project_name
  aws_region   = var.aws_region

  instance_type   = var.instance_type
  ebs_volume_size = var.ebs_volume_size
  ebs_volume_type = var.ebs_volume_type

  minecraft_port           = var.minecraft_port
  allowed_cidr_blocks      = var.allowed_cidr_blocks
  allowed_ipv6_cidr_blocks = var.allowed_ipv6_cidr_blocks

  lambda_runtime = var.lambda_runtime
  lambda_timeout = var.lambda_timeout

  idle_check_schedule  = var.idle_check_schedule
  idle_timeout_seconds = var.idle_timeout_seconds

  snapshot_retention_days = var.snapshot_retention_days

  cloudflare_api_token = var.cloudflare_api_token
  cloudflare_zone_id   = var.cloudflare_zone_id
  dns_record_name      = var.dns_record_name
}
