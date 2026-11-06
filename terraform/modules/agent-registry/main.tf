# AWS Agent Registry: the catalog agents search to find each other.

resource "aws_agentregistry_registry" "this" {
  name        = var.name
  description = var.description

  # Who may search the registry: IAM principals with agent-registry:SearchDiscoverableRegistryRecords.
  discovery_configuration {
    authorizer_type = "AWS_IAM"
  }

  # APPROVE_ALL approves records as soon as they are submitted. Without it,
  # submitted records wait in PENDING_APPROVAL for a curator.
  dynamic "approval_configuration" {
    for_each = var.auto_approve ? [1] : []
    content {
      auto_approval_rules = ["APPROVE_ALL"]
    }
  }

  tags = var.tags
}
