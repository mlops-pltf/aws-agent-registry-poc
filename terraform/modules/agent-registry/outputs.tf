output "registry_id" {
  description = "Registry ID; set it as REGISTRY_ID on the agents."
  value       = aws_agentregistry_registry.this.registry_id
}

output "registry_arn" {
  description = "Registry ARN; use it in the agents' IAM policies."
  value       = aws_agentregistry_registry.this.registry_arn
}
