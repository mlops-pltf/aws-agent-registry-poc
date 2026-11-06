variable "name" {
  description = "Registry name: starts with a letter or digit; letters, digits, _ - . / allowed; up to 64 characters."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,63}$", var.name))
    error_message = "Registry name must start with a letter or digit, use only letters, digits, _ - . /, and be at most 64 characters."
  }
}

variable "description" {
  description = "Registry description."
  type        = string
  default     = null
}

variable "auto_approve" {
  description = "Approve records automatically when they are submitted (APPROVE_ALL). If false, a curator must approve each submitted record."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags for the registry."
  type        = map(string)
  default     = {}
}
