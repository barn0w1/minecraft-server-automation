variable "project_name" {
  description = "Project name prefix for resources"
  type        = string
}

variable "aws_region" {
  description = "AWS Region"
  type        = string
}

variable "instance_type" {
  description = "EC2 Instance Type"
  type        = string
}

variable "ebs_volume_size" {
  description = "EBS Volume Size (GB)"
  type        = number
}

variable "ebs_volume_type" {
  description = "EBS Volume Type"
  type        = string
}

variable "minecraft_port" {
  description = "Port for Minecraft server"
  type        = number
}

variable "allowed_cidr_blocks" {
  description = "Allowed IPv4 CIDR blocks for Minecraft access"
  type        = list(string)
}

variable "allowed_ipv6_cidr_blocks" {
  description = "Allowed IPv6 CIDR blocks for Minecraft access"
  type        = list(string)
}

variable "lambda_runtime" {
  description = "Runtime for Lambda function"
  type        = string
}

variable "lambda_timeout" {
  description = "Timeout for Lambda function (seconds)"
  type        = number
}

variable "idle_check_schedule" {
  description = "Schedule expression for idle check (EventBridge)"
  type        = string
}

variable "idle_timeout_seconds" {
  description = "Seconds of inactivity before shutting down server"
  type        = number
}

variable "snapshot_retention_days" {
  description = "How many days to retain data snapshots in S3 (via lifecycle rule)"
  type        = number
}

variable "cloudflare_api_token" {
  description = "Cloudflare API Token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "cloudflare_zone_id" {
  description = "Cloudflare Zone ID"
  type        = string
  default     = ""
}

variable "dns_record_name" {
  description = "DNS Record Name to update (e.g. mc.example.com)"
  type        = string
  default     = ""
}
