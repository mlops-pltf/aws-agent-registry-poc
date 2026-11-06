# One AGENT record in AWS Agent Registry, built from a developer-provided A2A card.
# Terraform counterpart of scripts/register_agents.py: create or update the
# record, then submit it for approval (and optionally approve it).

locals {
  card_from_file = jsondecode(var.agent_card_json)

  # With runtime_arn set, the card's url is replaced by the AgentCore runtime URL
  # (the agents read the runtime ARN from it). Without it, the card's own url is kept.
  runtime_region = var.runtime_arn == null ? null : split(":", var.runtime_arn)[3]
  card = var.runtime_arn == null ? local.card_from_file : merge(local.card_from_file, {
    url = "https://bedrock-agentcore.${local.runtime_region}.amazonaws.com/runtimes/${urlencode(var.runtime_arn)}/invocations/"
  })
  card_json = jsonencode(local.card)
}

resource "awscc_agentregistry_registry_record" "this" {
  registry_id    = var.registry_id
  name           = var.record_name
  record_type    = "AGENT"
  display_name   = try(coalesce(var.display_name, local.card.name), null)
  description    = try(coalesce(var.description, local.card.description), null)
  record_version = try(coalesce(var.record_version, local.card.version), null)

  descriptors = {
    a2_a_agent_card = {
      data                = local.card_json
      data_schema_version = var.card_schema_version
    }
  }

  tags = length(var.tags) > 0 ? [for k, v in var.tags : { key = k, value = v }] : null

  lifecycle {
    precondition {
      condition     = can(local.card.name) && can(local.card.url)
      error_message = "The agent card for ${var.record_name} needs at least \"name\" and \"url\" (or set runtime_arn so the url is filled in)."
    }
  }
}

# Creating or updating a record leaves it in DRAFT, which search never returns.
# There is no Terraform/CloudFormation property for submission, so submit it
# (and approve it, if asked) with the AWS CLI whenever the record or card changes.
resource "terraform_data" "submit" {
  count = var.submit_for_approval ? 1 : 0

  triggers_replace = [
    awscc_agentregistry_registry_record.this.record_id,
    sha256(local.card_json),
    awscc_agentregistry_registry_record.this.display_name,
    awscc_agentregistry_registry_record.this.description,
    awscc_agentregistry_registry_record.this.record_version,
  ]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = file("${path.module}/finalize_record.sh")
    environment = merge(
      {
        REGISTRY_ID = var.registry_id
        RECORD_ID   = awscc_agentregistry_registry_record.this.record_id
        APPROVE     = var.approve ? "true" : "false"
      },
      var.aws_region == null ? {} : { AWS_REGION = var.aws_region, AWS_DEFAULT_REGION = var.aws_region },
      var.aws_profile == null ? {} : { AWS_PROFILE = var.aws_profile },
    )
  }
}
