output "registry_id" {
  description = "Set this as REGISTRY_ID on the agents."
  value       = module.registry.registry_id
}

output "registry_arn" {
  description = "Use this in the agents' IAM policies (agent-registry:SearchDiscoverableRegistryRecords)."
  value       = module.registry.registry_arn
}

output "agents" {
  description = "Registered agents: record ID, record ARN and the card url (from which agents read the runtime ARN)."
  value = {
    for name, agent in module.agents : name => {
      record_id  = agent.record_id
      record_arn = agent.record_arn
      card_url   = agent.card_url
    }
  }
}
