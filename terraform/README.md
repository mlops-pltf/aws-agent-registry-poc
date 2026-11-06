# Terraform: Agent Registry and agent registration

Creates the **AWS Agent Registry** and registers agents in it from their **A2A agent cards** (JSON provided by each agent's developer). It's the Terraform counterpart of [`scripts/register_agents.py`](../scripts/register_agents.py).

**Agent runtimes are not deployed here.** Deploy them with your existing pipeline first, then register them here with their runtime ARNs.

## What it creates

| Resource | Provider | What it does |
|---|---|---|
| `aws_agentregistry_registry` | `hashicorp/aws` ≥ 6.64.0 | The registry: IAM-authorized search, optional auto-approval (`APPROVE_ALL`) |
| `awscc_agentregistry_registry_record` (one per agent) | `hashicorp/awscc` ≥ 1.98.0 | An `AGENT` record with the agent's A2A card (`a2aAgentCard`, schema `0.3`) |
| `terraform_data` + `finalize_record.sh` (one per agent) | built in | Submits each record for approval after every create or update, and optionally approves it |

**Why the submit step:** a new or updated record ends up in `DRAFT`, and registry search only returns `APPROVED` records. Neither Terraform nor CloudFormation has a property that submits a record, so `finalize_record.sh` does it with the AWS CLI. That's the same sequence `register_agents.py` runs:
1. Wait while the record is `CREATING` or `UPDATING`.
2. Submit it if it's `DRAFT`. A registry with auto-approval then approves it straight away.
3. Approve it, if `approve_pending = true` and it's still `PENDING_APPROVAL`.

The hashicorp/aws provider has no record resource yet, so records use the awscc (Cloud Control) provider.

## Layout

```
terraform/
  main.tf, variables.tf, outputs.tf, versions.tf   Root: providers, registry, one record per agent
  terraform.tfvars.example                         Copy to terraform.tfvars
  modules/
    agent-registry/      The registry
    agent-record/        One agent record + the submit step (finalize_record.sh)
  tests/
    agent_registry.tftest.hcl   Offline tests with mock providers
```

## Prerequisites

- **Terraform 1.5 or newer.** Running the tests needs 1.7 or newer.
- **AWS credentials** for the target account, with [`iam/terraform-registry-policy.json`](../iam/terraform-registry-policy.json) (see [IAM](#iam)).
- **AWS CLI v2, recent enough to include `aws agent-registry-control`,** on the machine or runner that executes `terraform apply`, because the submit step uses it. Check with `aws agent-registry-control help`. Older v2 releases don't have the command, and the step then fails with a clear error.
- **The agent runtimes are already deployed,** with their runtime ARNs to hand.

## Usage

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars    # edit: registry name, agents, runtime ARNs
terraform init
terraform plan
terraform apply
```

Then give the agents the registry ID:
```bash
terraform output -raw registry_id    # set as REGISTRY_ID on each agent runtime
```

Configure a remote state backend in `versions.tf` before using this from CI.

## Registering an agent (developer workflow)

1. **The developer provides the agent's A2A card** as a JSON file, for example `agent-cards/nutritionist.json`. The minimum is `name` and `url`. Add `description`, `version` and `skills` with tags, because registry search matches on them. The card format is described in the [main README](../README.md#sample-agent-card).
2. **Add an entry to `agents` in `terraform.tfvars`,** keyed by the record name. The other agents use that name when they call it.
   ```hcl
   agents = {
     "nutritionist-agent" = {
       card_file   = "../agent-cards/nutritionist.json"
       runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/nutritionist_agent-AbCdEf1234"
     }
   }
   ```
3. **Run `terraform apply`.** The record is created, submitted and, with auto-approval, approved.

| Field | Required | Description |
|---|---|---|
| `card_file` | yes | Path to the card JSON, relative to `terraform/` or absolute |
| `runtime_arn` | no | If set, the card's `url` is replaced with `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded ARN>/invocations/`. If not set, the card's own `url` is used, so it must already be that URL. |
| `display_name`, `description`, `record_version` | no | Overrides; default to the card's `name`, `description` and `version` |
| `tags` | no | Extra tags for this record, merged with the global `tags` |

**Changing a card** (skills, description and so on): edit the JSON and run `terraform apply`. The record is updated in place and submitted again.
**Removing an agent:** delete its entry and run `terraform apply`. The record is deleted, so the agent can no longer be found.

## Settings

| Variable | Default | Description |
|---|---|---|
| `region` | `us-east-1` | Region of the registry; the same region as the agent runtimes |
| `aws_profile` | `null` | AWS profile for the providers and the submit step; `null` uses the default credential chain |
| `registry_name` | (required) | Registry name |
| `registry_description` | `null` | Registry description |
| `auto_approve` | `true` | Approve records automatically on submission. With `false`, a curator approves each one. |
| `submit_for_approval` | `true` | Submit records after each create or update. With `false`, records stay `DRAFT` and can't be found. |
| `approve_pending` | `false` | Approve records left `PENDING_APPROVAL`; only relevant when `auto_approve = false` |
| `tags` | `{}` | Tags for the registry and every record |
| `agents` | `{}` | Agents to register (above) |

| Output | Use |
|---|---|
| `registry_id` | `REGISTRY_ID` on each agent runtime |
| `registry_arn` | Resource of `agent-registry:SearchDiscoverableRegistryRecords` in the agents' IAM policies |
| `agents` | Per agent: `record_id`, `record_arn`, and the `card_url` agents read the runtime ARN from |

## IAM

**The identity that runs Terraform** needs [`iam/terraform-registry-policy.json`](../iam/terraform-registry-policy.json), with `<REGION>` and `<ACCOUNT_ID>` replaced. It's based on the CloudFormation handler permissions for `AWS::AgentRegistry::Registry` and `AWS::AgentRegistry::RegistryRecord`:

- **Registry:** create, read, update, delete and tag. Creating a registry also uses `bedrock-agentcore:CreateWorkloadIdentity` and `iam:CreateServiceLinkedRole`.
- **Records:** create, read, update, delete and tag, plus `SubmitRegistryRecordForApproval`. `UpdateRegistryRecordStatus` is only needed for `approve_pending = true`, and you can drop that statement otherwise.
- **Cloud Control API** (`cloudformation:CreateResource` and related actions), used by the awscc provider for records.
- **Not included:**
  - KMS permissions, needed only if you add a customer-managed key.
  - The OAuth, `iam:PassRole` and Secrets Manager permissions that CloudFormation lists for records. Those apply to cards synced from a protected URL, and this setup sends the card inline.

**The agents' own roles** are unchanged ([`iam/agent-runtime-policy.json`](../iam/agent-runtime-policy.json)). Use the `registry_arn` output there.

## Testing

```bash
cd terraform
terraform init -backend=false
terraform test
```

The tests use mock providers and make no AWS calls. They check:
- **The registry:** IAM discovery, `APPROVE_ALL` with `auto_approve` and none without it, and rejection of invalid names.
- **Building the card:** the runtime URL is encoded exactly as `register_agents.py` encodes it, and the rest of the card is stored unchanged. Without `runtime_arn`, the card's own `url` is kept.
- **Defaults:** display name, description and version come from the card unless overridden, and tags are passed as key–value pairs.
- **Input checks:** a card without a name, invalid JSON and a runtime ARN that isn't an AgentCore one are all rejected.
- **The submit step:** it's present by default, and absent with `submit_for_approval = false`.

They don't run the submit script, which only runs during `apply`.

## Notes

- **Use one tool per record.** Records managed here shouldn't also be registered with `register_agents.py`, or the two will overwrite each other. To bring an existing record under Terraform, import it: `terraform import 'module.agents["artist-agent"].awscc_agentregistry_registry_record.this' <record ARN>`. An existing registry imports by ID: `terraform import module.registry.aws_agentregistry_registry.this <registry ID>`.
- **Terraform doesn't track approval status,** because it's read-only in both providers. The submit step re-runs whenever the record or its card changes. A record rejected or deprecated in the console stays that way until its card next changes.
- **Destroying deletes everything.** `terraform destroy` deletes the records, then the registry. The agents can't find each other until it's recreated and they're configured with the new `REGISTRY_ID`.
