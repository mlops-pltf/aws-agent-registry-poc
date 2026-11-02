# AWS Agent Registry POC: agents that find each other

Three [Strands](https://strandsagents.com) agents, each deployed as its own **Amazon Bedrock AgentCore Runtime**, that **discover each other at runtime through AWS Agent Registry** and talk over the **A2A (Agent-to-Agent) protocol**.

| Agent | Expertise | Domain tools |
|---|---|---|
| 🎨 Artist | Murals, exhibitions, brand visuals, commission pricing | `price_artwork` |
| 💼 Investment Banker | Budgets, investment plans, growth projections | `project_investment`, `monthly_budget` |
| 🏋️ Fitness Trainer | Training plans, posture, energy expenditure | `weekly_plan`, `calories_burned` |

No agent is configured with another agent's address. When a request goes beyond its own expertise, an agent searches the registry in natural language (for example "investment planning"), reads the matching agent's A2A card, and calls it. Once a fourth agent is registered and approved, the others can find it with no code changes.

📊 **Presentation deck:** [Agents That Find Each Other](https://claude.ai/artifact/8X5AemdF5jSsd5TzEQb7Na), 11 slides with speaker notes covering the architecture, request flow, live demo scenario, packaging, running locally and guardrails.

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
  artist/              Dockerfile · requirements.txt · agent.yaml · agent.py · SYSTEMPROMPT.md
  investment_banker/   Dockerfile · requirements.txt · agent.yaml · agent.py · SYSTEMPROMPT.md
  fitness_trainer/     Dockerfile · requirements.txt · agent.yaml · agent.py · SYSTEMPROMPT.md
registry-cards/        A2A agent cards (URL placeholder) for registration
scripts/
  register_agents.py   Creates/updates/approves the AGENT records
iam/
  agent-runtime-policy.json   Attach to each agent's execution role
  registration-policy.json    Least-privilege role for registration
docs/
  USER_GUIDE.md        Deploy, register, demo, extend, troubleshoot
```

Each agent folder is self-contained, with the five files your CI/CD uses:

| File | Purpose |
|---|---|
| `Dockerfile` | `linux/arm64` image; installs `requirements.txt`, serves on port 9000 |
| `requirements.txt` | Pinned Strands, AgentCore, A2A, boto3 and OpenTelemetry dependencies |
| `agent.yaml` | Environment variables, in a `local:` block and a `deployed:` block, injected by the framework |
| `agent.py` | AgentCore entrypoint: domain tools, peer tools, agent card, `serve_a2a` server |
| `SYSTEMPROMPT.md` | Persona, when to consult peers, how to answer peers |

## Quick start

1. **Deploy** each `agents/<name>/` folder as an AgentCore Runtime with **protocol `A2A`**. First fill in `REGISTRY_ID` in the `deployed:` block of each `agent.yaml`; the framework injects that block as environment variables:

   | Variable | Example | Notes |
   |---|---|---|
   | `REGISTRY_ID` | `reg-abc123…` | Required. Registry ID or ARN. Empty in the repo. |
   | `AWS_REGION` | `us-east-1` | Region of the registry and runtimes. |
   | `MODEL_ID` | `anthropic.claude-opus-5-5` | Bedrock model or inference profile ID. |
   | `A2A_PORT` | `9000` | AgentCore requires 9000. |

   Attach [`iam/agent-runtime-policy.json`](iam/agent-runtime-policy.json) to each execution role. It allows searching the registry, invoking the two peer runtimes, and invoking the model. See [User Guide §3](docs/USER_GUIDE.md#3-iam-permissions).

2. **Register** the agents in the registry:

   ```bash
   pip install -U boto3
   python scripts/register_agents.py --registry <registry name or ID> --dry-run   # preview
   python scripts/register_agents.py --registry <registry name or ID> --approve
   ```

3. **Demo:** send this to the Artist runtime:

   > I'm an established muralist. I've been offered a 400 sq ft mural, about 120 hours of work. What should I charge, how should I invest the fee over 10 years, and how do I stay fit through weeks on a ladder?

   The artist prices the mural itself, then finds and asks the banker and the trainer.

**Trying it before deploying?** Run all three agents on your machine in three terminals with the `local:` block of each `agent.yaml`, which uses `LOCAL_PEER_URLS` instead of the registry. Only Bedrock is needed. See [User Guide §6.1](docs/USER_GUIDE.md#61-all-three-agents-locally-no-registry-no-agentcore).

The [User Guide](docs/USER_GUIDE.md) covers IAM, manual console registration, local testing, adding new agents, and troubleshooting.

## Calling an agent (A2A API)

Each agent is a standard A2A JSON-RPC server, so `curl`, any A2A SDK client or the A2A Inspector can call it.

### Where to send it

| Where it runs | Send the request to |
|---|---|
| Locally | `POST http://localhost:<port>/` (port from `A2A_PORT`: 9000 / 9001 / 9002) |
| AgentCore | The `InvokeAgentRuntime` API, or `POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded runtime ARN>/invocations/`, signed with IAM (SigV4). AgentCore forwards it to `POST /` in the container. |

A2A servers have **no `/invocations` route** inside the container; that path belongs to AgentCore's HTTP protocol. The routes `serve_a2a` registers are:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/` | A2A JSON-RPC (`message/send`, `message/stream`, …) |
| `GET` | `/.well-known/agent-card.json` | Agent card (also at `/.well-known/agent.json`) |
| `GET` | `/ping` | Health check |

### Request payload

A JSON-RPC 2.0 `message/send` request (A2A protocol 0.3, matching the pinned `a2a-sdk~=0.3.26`):

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "msg-001",
      "parts": [
        { "kind": "text", "text": "How should I invest $30,000 over 10 years?" }
      ]
    }
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `jsonrpc` | yes | Always `"2.0"` |
| `id` | yes | Any string or number; the reply echoes it back |
| `method` | yes | `"message/send"`, or `"message/stream"` for streaming |
| `params.message.role` | yes | `"user"` |
| `params.message.messageId` | yes | Unique per message; a UUID works well |
| `params.message.parts` | yes | For text: `{"kind": "text", "text": "..."}` |
| `params.message.contextId` | no | The `contextId` from a previous reply, to continue that conversation |
| `params.configuration` | no | For example `{"blocking": true, "historyLength": 0}` |
| `params.metadata` | no | Any extra key–value pairs |

### Response

The agent replies with a **task**. The answer text is in `result.artifacts[].parts[].text`:

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "kind": "task",
    "id": "…",
    "contextId": "338773e1-…",
    "status": { "state": "completed" },
    "artifacts": [
      { "name": "agent_response", "parts": [ { "kind": "text", "text": "…the agent's answer…" } ] }
    ],
    "history": [ … ]
  }
}
```

If the request fails, the reply has an `error` object (`{"code": -32602, "message": "..."}`) instead of `result`.

### Examples

```bash
# Local: ask the banker (port 9001) and print only the answer
curl -s -X POST localhost:9001/ -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": "1", "method": "message/send",
  "params": {"message": {"role": "user", "messageId": "msg-001",
    "parts": [{"kind": "text", "text": "How should I invest $30,000 over 10 years?"}]}}}' \
  | jq -r '.result.artifacts[].parts[].text'

# Follow-up in the same conversation: copy contextId from the previous reply
#   "message": {"role": "user", "messageId": "msg-002", "contextId": "<contextId>", "parts": [...]}

# Streaming (server-sent events)
curl -N -s -X POST localhost:9001/ -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/stream","params":{"message":{"role":"user","messageId":"msg-003","parts":[{"kind":"text","text":"Quick budget tip?"}]}}}'

# AgentCore: same body, sent through InvokeAgentRuntime
aws bedrock-agentcore invoke-agent-runtime --region us-east-1 \
  --agent-runtime-arn "<runtime ARN>" --runtime-session-id "$(uuidgen)" \
  --payload fileb://req.json out.json
jq -r '.result.artifacts[].parts[].text' out.json
```

On AgentCore, `--runtime-session-id` must be at least 33 characters (`uuidgen` gives 36), and the caller needs `bedrock-agentcore:InvokeAgentRuntime` on the runtime.

## Tech stack

- [Strands Agents](https://strandsagents.com) `~=1.57.2` with `StrandsA2AExecutor`
- [Bedrock AgentCore SDK](https://github.com/aws/bedrock-agentcore-sdk-python) `~=1.24.0` (`serve_a2a`)
- [A2A SDK](https://a2a-protocol.org) `~=0.3.26`, agent card schema `0.3`
- boto3 `>=1.43.107`, clients `agent-registry`, `agent-registry-control` and `bedrock-agentcore`

> **Note:** AWS Agent Registry is now under the `agent-registry` / `agent-registry-control` namespaces. The preview-era `bedrock-agentcore` registry namespace was discontinued on October 30, 2026, so older samples that use it won't work.
