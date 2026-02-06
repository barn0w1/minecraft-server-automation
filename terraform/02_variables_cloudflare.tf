
# --- Cloudflare Configuration ---

variable "cloudflare_api_token" {
  description = "Cloudflare API Token"
  type        = string
  sensitive   = true
  default     = "" # User must provide this
}

variable "cloudflare_zone_id" {
  description = "Cloudflare Zone ID"
  type        = string
  default     = "" # User must provide this
}

variable "dns_record_name" {
  description = "DNS Record Name to update (e.g. mc.example.com)"
  type        = string
  default     = "mc.hss-science.org"
}
