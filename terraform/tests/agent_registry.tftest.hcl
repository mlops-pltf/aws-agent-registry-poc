# Offline tests with mock providers: `terraform test` (Terraform 1.7+), no AWS calls.

mock_provider "aws" {}
mock_provider "awscc" {}

variables {
  registry_name = "test-registry"
  agents = {
    "artist-agent" = {
      card_file   = "../registry-cards/artist.agent-card.json"
      runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/artist_agent-AbC123XyZ9"
    }
    "investment-banker-agent" = {
      card_file   = "../registry-cards/investment_banker.agent-card.json"
      runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/investment_banker_agent-B4nK3r0001"
    }
  }
}

run "root_registers_every_agent_with_its_runtime_url" {
  command = plan

  assert {
    condition     = length(module.agents) == 2
    error_message = "Expected one record per agent."
  }
  assert {
    # same encoding as scripts/register_agents.py (urllib.parse.quote(arn, safe=""))
    condition     = output.agents["artist-agent"].card_url == "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fartist_agent-AbC123XyZ9/invocations/"
    error_message = "Card url must be the AgentCore runtime URL with the ARN fully URL-encoded."
  }
}

run "record_from_card_and_runtime_arn" {
  command = plan
  module {
    source = "./modules/agent-record"
  }
  variables {
    registry_id     = "reg1abcdefghij"
    record_name     = "artist-agent"
    agent_card_json = file("../registry-cards/artist.agent-card.json")
    runtime_arn     = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/artist_agent-AbC123XyZ9"
  }

  assert {
    condition     = awscc_agentregistry_registry_record.this.record_type == "AGENT"
    error_message = "Record type must be AGENT."
  }
  assert {
    condition     = awscc_agentregistry_registry_record.this.descriptors.a2_a_agent_card.data_schema_version == "0.3"
    error_message = "A2A card schema version must be 0.3."
  }
  assert {
    condition     = jsondecode(awscc_agentregistry_registry_record.this.descriptors.a2_a_agent_card.data).url == "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fartist_agent-AbC123XyZ9/invocations/"
    error_message = "The stored card must carry the runtime URL."
  }
  assert {
    condition     = jsondecode(awscc_agentregistry_registry_record.this.descriptors.a2_a_agent_card.data).skills[1].id == "art-pricing"
    error_message = "The rest of the card must be stored unchanged."
  }
  assert {
    condition     = awscc_agentregistry_registry_record.this.display_name == "Artist Agent" && awscc_agentregistry_registry_record.this.record_version == "1.0.0"
    error_message = "display_name and record_version must default to the card's name and version."
  }
  assert {
    condition     = startswith(awscc_agentregistry_registry_record.this.description, "Visual artist")
    error_message = "description must default to the card's description."
  }
  assert {
    condition     = length(terraform_data.submit) == 1
    error_message = "The record must be submitted for approval by default."
  }
}

run "card_url_kept_without_runtime_arn" {
  command = plan
  module {
    source = "./modules/agent-record"
  }
  variables {
    registry_id     = "reg1abcdefghij"
    record_name     = "external-agent"
    agent_card_json = jsonencode({ name = "External Agent", url = "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/already-encoded/invocations/" })
    display_name    = "Shown Name"
    tags            = { team = "data" }
  }

  assert {
    condition     = jsondecode(awscc_agentregistry_registry_record.this.descriptors.a2_a_agent_card.data).url == "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/already-encoded/invocations/"
    error_message = "Without runtime_arn the card's url must be kept."
  }
  assert {
    condition     = awscc_agentregistry_registry_record.this.display_name == "Shown Name"
    error_message = "display_name override must win over the card's name."
  }
  assert {
    condition     = awscc_agentregistry_registry_record.this.tags[0].key == "team" && awscc_agentregistry_registry_record.this.tags[0].value == "data"
    error_message = "Tags must be passed as key/value pairs."
  }
}

run "card_without_name_is_rejected" {
  command = plan
  module {
    source = "./modules/agent-record"
  }
  variables {
    registry_id     = "reg1abcdefghij"
    record_name     = "nameless"
    agent_card_json = jsonencode({ url = "https://example.com/" })
  }
  expect_failures = [awscc_agentregistry_registry_record.this]
}

run "invalid_json_is_rejected" {
  command = plan
  module {
    source = "./modules/agent-record"
  }
  variables {
    registry_id     = "reg1abcdefghij"
    record_name     = "broken"
    agent_card_json = "{not json"
  }
  expect_failures = [var.agent_card_json]
}

run "bad_runtime_arn_is_rejected" {
  command = plan
  module {
    source = "./modules/agent-record"
  }
  variables {
    registry_id     = "reg1abcdefghij"
    record_name     = "artist-agent"
    agent_card_json = file("../registry-cards/artist.agent-card.json")
    runtime_arn     = "arn:aws:lambda:us-east-1:123456789012:function:not-a-runtime"
  }
  expect_failures = [var.runtime_arn]
}

run "submit_step_can_be_disabled" {
  command = plan
  module {
    source = "./modules/agent-record"
  }
  variables {
    registry_id         = "reg1abcdefghij"
    record_name         = "artist-agent"
    agent_card_json     = file("../registry-cards/artist.agent-card.json")
    submit_for_approval = false
  }

  assert {
    condition     = length(terraform_data.submit) == 0
    error_message = "No submit step when submit_for_approval is false."
  }
}

run "registry_auto_approves_by_default" {
  command = plan
  module {
    source = "./modules/agent-registry"
  }
  variables {
    name = "agent-network"
  }

  assert {
    condition     = aws_agentregistry_registry.this.approval_configuration[0].auto_approval_rules == toset(["APPROVE_ALL"])
    error_message = "auto_approve must set APPROVE_ALL."
  }
  assert {
    condition     = aws_agentregistry_registry.this.discovery_configuration[0].authorizer_type == "AWS_IAM"
    error_message = "Discovery must use IAM authorization."
  }
}

run "registry_manual_approval" {
  command = plan
  module {
    source = "./modules/agent-registry"
  }
  variables {
    name         = "agent-network"
    auto_approve = false
  }

  assert {
    condition     = length(aws_agentregistry_registry.this.approval_configuration) == 0
    error_message = "auto_approve = false must leave out the approval rules."
  }
}

run "bad_registry_name_is_rejected" {
  command = plan
  module {
    source = "./modules/agent-registry"
  }
  variables {
    name = "-starts-with-dash"
  }
  expect_failures = [var.name]
}
