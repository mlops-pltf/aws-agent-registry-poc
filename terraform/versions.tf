terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.64.0" # aws_agentregistry_registry
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = ">= 1.98.0" # first release with awscc_agentregistry_registry_record
    }
  }

  # Configure your state backend, for example:
  # backend "s3" {
  #   bucket = "my-terraform-state"
  #   key    = "agent-registry/terraform.tfstate"
  #   region = "us-east-1"
  # }
}
