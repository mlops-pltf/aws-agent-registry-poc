# AWS Agent Registry POC: agents that find each other

Three [Strands](https://strandsagents.com) agents, each deployed as its own **Amazon Bedrock AgentCore Runtime**, that **discover each other at runtime through AWS Agent Registry** and talk over the **A2A (Agent-to-Agent) protocol**.

| Agent | Expertise | Domain tools |
|---|---|---|
| 🎨 Artist | Murals, exhibitions, brand visuals, commission pricing | `price_artwork` |
| 💼 Investment Banker | Budgets, investment plans, growth projections | `project_investment`, `monthly_budget` |
| 🏋️ Fitness Trainer | Training plans, posture, energy expenditure | `weekly_plan`, `calories_burned` |

No agent is configured with another agent's address. When a request goes beyond its own expertise, an agent searches the registry in natural language (for example "investment planning"), reads the matching agent's A2A card, and calls it. Once a fourth agent is registered and approved, the others can find it with no code changes.

## How it works

```
                 ① publish A2A card (script / console)
                ┌──────────────────────────────┐
                ▼                              │
      ┌───────────────────┐            ┌───────┴───────┐
      │  AWS Agent        │            │  You / future │
      │  Registry         │            │  onboarding   │
      │  (AGENT records)  │            │  pipeline     │
      └───────▲───────────┘            └───────────────┘
              │ ② find_peer_agents: semantic search
              │
   ┌──────────┴─────────┐   ③ ask_peer_agent: A2A message/send    ┌────────────────────┐
   │ Artist runtime     │ ──────────────────────────────────────► │ Banker runtime     │
   │ (AgentCore, A2A)   │      via InvokeAgentRuntime (SigV4)     │ (AgentCore, A2A)   │
   └────────────────────┘                                         └────────────────────┘
          any agent can be the caller; the trainer works the same way
```

Every agent has the same two networking tools:

- **`find_peer_agents(need)`** calls `agent-registry` `SearchDiscoverableRegistryRecords`. It keeps `AGENT` records that have an A2A card, skips itself, and gets the target runtime ARN from the card's URL.
- **`ask_peer_agent(record_name, question)`** sends an A2A `message/send` JSON-RPC call through `bedrock-agentcore` `InvokeAgentRuntime`, signed with the agent's execution role, and returns the peer's text.

**Loop guard:** questions an agent sends to a peer start with `[A2A request from <agent>]`. When an agent receives one, it answers directly and `ask_peer_agent` refuses to call further agents. This stops A → B → A loops.

## Repository layout

```
agents/
  artist/              Dockerfile · agent.py · SYSTEMPROMPT.md
  investment_banker/   Dockerfile · agent.py · SYSTEMPROMPT.md
  fitness_trainer/     Dockerfile · agent.py · SYSTEMPROMPT.md
registry-cards/        A2A agent cards (URL placeholder) for registration
scripts/
  register_agents.py   Creates/updates/approves the AGENT records
docs/
  USER_GUIDE.md        Deploy, register, demo, extend, troubleshoot
```

Each agent folder is self-contained, with the three files your CI/CD builds:

| File | Purpose |
|---|---|
| `Dockerfile` | `linux/arm64` image, pinned Strands / AgentCore / A2A SDKs, serves on port 9000 |
| `agent.py` | AgentCore entrypoint: domain tools, peer tools, agent card, `serve_a2a` server |
| `SYSTEMPROMPT.md` | Persona, when to consult peers, how to answer peers |

## Quick start

1. **Deploy** each `agents/<name>/` folder as an AgentCore Runtime with **protocol `A2A`** and these environment variables:

   | Variable | Example | Notes |
   |---|---|---|
   | `REGISTRY_ID` | `reg-abc123…` | Required. Registry ID or ARN. |
   | `AWS_REGION` | `us-east-1` | Region of the registry and runtimes. |
   | `MODEL_ID` | `anthropic.claude-opus-5-5` | Bedrock model or inference profile ID. |

2. **Register** the agents in the registry:

   ```bash
   pip install -U boto3
   python scripts/register_agents.py --registry <registry name or ID> --dry-run   # preview
   python scripts/register_agents.py --registry <registry name or ID> --approve
   ```

3. **Demo:** send this to the Artist runtime:

   > I'm an established muralist. I've been offered a 400 sq ft mural, about 120 hours of work. What should I charge, how should I invest the fee over 10 years, and how do I stay fit through weeks on a ladder?

   The artist prices the mural itself, then finds and asks the banker and the trainer.

The [User Guide](docs/USER_GUIDE.md) covers IAM, manual console registration, local testing, adding new agents, and troubleshooting.

## Tech stack

- [Strands Agents](https://strandsagents.com) `~=1.57.2` with `StrandsA2AExecutor`
- [Bedrock AgentCore SDK](https://github.com/aws/bedrock-agentcore-sdk-python) `~=1.24.0` (`serve_a2a`)
- [A2A SDK](https://a2a-protocol.org) `~=0.3.26`, agent card schema `0.3`
- boto3 `>=1.43.107`, clients `agent-registry`, `agent-registry-control` and `bedrock-agentcore`

> **Note:** AWS Agent Registry is now under the `agent-registry` / `agent-registry-control` namespaces. The preview-era `bedrock-agentcore` registry namespace was discontinued on October 30, 2026, so older samples that use it won't work.
