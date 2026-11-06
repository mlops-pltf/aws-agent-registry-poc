variable "region" {
  description = "AWS region for the registry (same region as the agent runtimes)."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS profile to use; null uses the default credential chain (e.g. in CI)."
  type        = string
  default     = null
}

variable "registry_name" {
  description = "Name of the Agent Registry."
  type        = string
}

variable "registry_description" {
  description = "Description of the Agent Registry."
  type        = string
  default     = null
}

variable "auto_approve" {
  description = "Approve records automatically on submission. If false, a curator approves each one (or set approve_pending)."
  type        = bool
  default     = true
}

variable "submit_for_approval" {
  description = "Submit records for approval after each create or update; records left in DRAFT aren't discoverable."
  type        = bool
  default     = true
}

variable "approve_pending" {
  description = "Approve records left PENDING_APPROVAL (only matters when auto_approve is false). Needs curator permissions."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags for the registry and every record."
  type        = map(string)
  default     = {}
}

variable "agents" {
  description = <<-EOT
    Agents to register, keyed by record name (e.g. "artist-agent").
      card_file      Path to the developer's A2A agent card JSON (relative to this directory, or absolute).
      runtime_arn    AgentCore runtime ARN; if set, the card's url is filled in from it. If null, the card must already have the AgentCore url.
      display_name, description, record_version
                     Optional overrides; default to the card's name, description and version.
      tags           Extra tags for this record.
  EOT
  type = map(object({
    card_file      = string
    runtime_arn    = optional(string)
    display_name   = optional(string)
    description    = optional(string)
    record_version = optional(string)
    tags           = optional(map(string), {})
  }))
  default = {}
}
