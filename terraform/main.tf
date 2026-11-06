# Agent Registry and agent registrations.
# Agent runtimes are deployed separately; this only creates the registry and
# registers each agent from its developer-provided A2A card (JSON).

provider "aws" {
  region  = var.region
  profile = var.aws_profile
}

provider "awscc" {
  region  = var.region
  profile = var.aws_profile
}

module "registry" {
  source = "./modules/agent-registry"

  name         = var.registry_name
  description  = var.registry_description
  auto_approve = var.auto_approve
  tags         = var.tags
}

module "agents" {
  source   = "./modules/agent-record"
  for_each = var.agents

  registry_id     = module.registry.registry_id
  record_name     = each.key
  agent_card_json = file(startswith(each.value.card_file, "/") ? each.value.card_file : "${path.root}/${each.value.card_file}")
  runtime_arn     = each.value.runtime_arn
  display_name    = each.value.display_name
  description     = each.value.description
  record_version  = each.value.record_version
  tags            = merge(var.tags, each.value.tags)

  submit_for_approval = var.submit_for_approval
  approve             = var.approve_pending
  aws_region          = var.region
  aws_profile         = var.aws_profile
}
