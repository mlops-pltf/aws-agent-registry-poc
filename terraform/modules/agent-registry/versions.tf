terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.64.0" # first release with aws_agentregistry_registry
    }
  }
}
