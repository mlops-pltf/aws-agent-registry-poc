output "record_id" {
  description = "Record ID."
  value       = awscc_agentregistry_registry_record.this.record_id
}

output "record_arn" {
  description = "Record ARN."
  value       = awscc_agentregistry_registry_record.this.record_arn
}

output "card_url" {
  description = "The url in the registered card; agents read the runtime ARN from it."
  value       = local.card.url
}
