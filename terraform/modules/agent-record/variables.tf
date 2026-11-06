variable "registry_id" {
  description = "ID or ARN of the registry to create the record in."
  type        = string
}

variable "record_name" {
  description = "Record name, unique in the registry (e.g. artist-agent). Agents pass it to ask_peer_agent."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,254}$", var.record_name))
    error_message = "Record name must start with a letter or digit, use only letters, digits, _ - . /, and be at most 255 characters."
  }
}

variable "agent_card_json" {
  description = "The A2A agent card as a JSON string, usually file(\"path/to/card.json\")."
  type        = string

  validation {
    condition     = can(jsondecode(var.agent_card_json))
    error_message = "agent_card_json must be valid JSON."
  }
}

variable "runtime_arn" {
  description = "AgentCore runtime ARN. If set, the card's url is replaced with this runtime's invocation URL; if null, the card's url is used as is."
  type        = string
  default     = null

  validation {
    condition     = var.runtime_arn == null || can(regex("^arn:aws[a-z-]*:bedrock-agentcore:[a-z0-9-]+:[0-9]{12}:runtime/[a-zA-Z0-9_-]+$", var.runtime_arn))
    error_message = "runtime_arn must be an AgentCore runtime ARN (arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>)."
  }
}

variable "display_name" {
  description = "Display name; defaults to the card's name."
  type        = string
  default     = null
}

variable "description" {
  description = "Record description; defaults to the card's description."
  type        = string
  default     = null
}

variable "record_version" {
  description = "Record version; defaults to the card's version."
  type        = string
  default     = null
}

variable "card_schema_version" {
  description = "A2A agent card schema version the registry validates against."
  type        = string
  default     = "0.3"
}

variable "tags" {
  description = "Tags for the record."
  type        = map(string)
  default     = {}
}

variable "submit_for_approval" {
  description = "Submit the record for approval after each create or update. Without it the record stays DRAFT and isn't discoverable."
  type        = bool
  default     = true
}

variable "approve" {
  description = "Approve the record if it is left PENDING_APPROVAL (registry without auto-approval). Needs agent-registry:UpdateRegistryRecordStatus."
  type        = bool
  default     = false
}

variable "aws_region" {
  description = "Region for the AWS CLI submit step; should match the provider region."
  type        = string
  default     = null
}

variable "aws_profile" {
  description = "AWS profile for the AWS CLI submit step, if the providers use one."
  type        = string
  default     = null
}
