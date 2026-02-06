variable "aws_region" {
  description = "AWS Region"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "Project Name (used for resource naming)"
  type        = string
  default     = "mcserver"
}

variable "common_tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default = {
    Project   = "mcserver"
    ManagedBy = "terraform"
  }
}

# --- EC2 Configuration ---

variable "instance_type" {
  description = "EC2 Instance Type"
  type        = string
  default     = "m6i.large"
}

variable "ebs_volume_size" {
  description = "EBS Volume Size (GB)"
  type        = number
  default     = 16
}

variable "ebs_volume_type" {
  description = "EBS Volume Type"
  type        = string
  default     = "gp3"
}

# --- Network Configuration ---

variable "minecraft_port" {
  description = "Port for Minecraft server"
  type        = number
  default     = 25565
}

variable "allowed_cidr_blocks" {
  description = "Allowed IPv4 CIDR blocks for Minecraft access"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "allowed_ipv6_cidr_blocks" {
  description = "Allowed IPv6 CIDR blocks for Minecraft access"
  type        = list(string)
  default     = ["::/0"]
}

# --- Lambda Configuration ---

variable "lambda_runtime" {
  description = "Runtime for Lambda function"
  type        = string
  default     = "python3.13"
}

variable "lambda_timeout" {
  description = "Timeout for Lambda function (seconds)"
  type        = number
  default     = 60
}

variable "idle_check_schedule" {
  description = "Schedule expression for idle check (EventBridge)"
  type        = string
  default     = "rate(10 minutes)"
}

variable "idle_timeout_seconds" {
  description = "Seconds of inactivity before shutting down server"
  type        = number
  default     = 1800 # 30 minutes
}

# --- S3 Snapshot Configuration ---

variable "snapshot_retention_days" {
  description = "How many days to retain data snapshots in S3 (via lifecycle rule)"
  type        = number
  default     = 1
}
