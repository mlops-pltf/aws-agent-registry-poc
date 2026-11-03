# AWS Agent Registry POC: agents that find each other

Three [Strands](https://strandsagents.com) agents, each deployed as its own **Amazon Bedrock AgentCore Runtime**, that **discover each other at runtime through AWS Agent Registry** and talk over the **A2A (Agent-to-Agent) protocol**.

| Agent | Expertise | Domain tools |
|---|---|---|
| 🎨 Artist | Murals, exhibitions, brand visuals, commission pricing | `price_artwork` |
| 💼 Investment Banker | Budgets, investment plans, growth projections | `project_investment`, `monthly_budget` |
| 🏋️ Fitness Trainer | Training plans, posture, energy expenditure | `weekly_plan`, `calories_burned` |

No agent is configured with another agent's address. When a request goes beyond its own expertise, an agent searches the registry in natural language (for example "investment planning"), reads the matching agent's A2A card, and calls it. Once a fourth agent is registered and approved, the others can find it with no code changes.

📊 **Presentation deck:** [Agents That Find Each Other](https://claude.ai/artifact/8X5AemdF5jSsd5TzEQb7Na), 12 slides with speaker notes covering the architecture, request flow, live demo scenario, packaging, running locally, bringing in existing agents and guardrails.

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
  invoker.py           Sends prompts to a locally running agent (CLI, stdlib only)
examples/
  http_entrypoint_agent/   @a2a_compatible decorator + example entrypoint agent callable by our A2A agents
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

3. **Demo:** send this question to **any** of the three agents:

   > I'm a 35-year-old freelance muralist taking home $6,000 a month, with $3,200 in fixed costs. I've been offered a 400 sq ft outdoor mural, about 120 hours of work. What should I charge for it, how much of the fee should I invest and what could that grow to in 10 years, and what training plan will keep my back and shoulders healthy through weeks of painting on a ladder?

   It needs all three areas of expertise, so whichever agent receives it answers its own part and consults the other two:

   | Sent to | Answers itself | Consults |
   |---|---|---|
   | Artist | The price (`price_artwork`) | Banker: budget and 10-year projection. Trainer: training plan. |
   | Investment Banker | Budget split and projection (`monthly_budget`, `project_investment`) | Artist: what to charge. Trainer: training plan. |
   | Fitness Trainer | Training plan and calories (`weekly_plan`, `calories_burned`) | Artist: what to charge. Banker: what to invest. |

   ```bash
   python scripts/invoker.py banker "I'm a 35-year-old freelance muralist taking home \$6,000 a month, with \$3,200 in fixed costs. I've been offered a 400 sq ft outdoor mural, about 120 hours of work. What should I charge for it, how much of the fee should I invest and what could that grow to in 10 years, and what training plan will keep my back and shoulders healthy through weeks of painting on a ladder?"
   ```

   Watch the logs: the receiving agent logs `A2A -> … Agent: …` for each consultation, and each peer logs an incoming request starting with `[A2A request from …]`. The answer can take a few minutes. Whether to consult is the model's decision; if an agent answers a part itself, add "check with the other specialists" to the question.

**Trying it before deploying?** Run all three agents on your machine in three terminals with the `local:` block of each `agent.yaml`, which uses `LOCAL_PEER_URLS` instead of the registry. Only Bedrock is needed. See [User Guide §6.1](docs/USER_GUIDE.md#61-all-three-agents-locally-no-registry-no-agentcore).

The [User Guide](docs/USER_GUIDE.md) covers IAM, manual console registration, local testing, adding new agents, and troubleshooting.

## Registering agents in Agent Registry

Agents find each other only through **approved** `AGENT` records in AWS Agent Registry. Each record holds the agent's A2A card, and the card's `url` tells callers which AgentCore runtime to invoke. Register each agent once after its runtime is deployed, and again whenever its runtime ARN or card changes.

### The record

| Field | Value |
|---|---|
| Record type | `AGENT` |
| Name | `artist-agent`, `investment-banker-agent`, `fitness-trainer-agent` (agents pass this name to `ask_peer_agent`) |
| Descriptor | `a2aAgentCard`, `dataSchemaVersion` `"0.3"` |
| Card `url` | `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded runtime ARN>/invocations/` |
| Status | Must be `APPROVED`; search only returns approved records |

The URL matters: `find_peer_agents` reads the runtime ARN from it and `ask_peer_agent` invokes that ARN. If the URL is wrong, the agent is still *found*, but calling it fails.

### Sample agent card

The Artist's card as stored in the registry, for an example runtime ARN `arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/artist_agent-AbC123XyZ9`:

```json
{
  "name": "Artist Agent",
  "description": "Visual artist and creative director. Designs artwork concepts, murals, brand visuals and exhibitions, and prices commissioned artwork.",
  "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fartist_agent-AbC123XyZ9/invocations/",
  "version": "1.0.0",
  "protocolVersion": "0.3.0",
  "preferredTransport": "JSONRPC",
  "capabilities": { "streaming": true },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "art-concept",
      "name": "Artwork and visual concept design",
      "description": "Designs murals, exhibitions, brand visuals and artwork concepts.",
      "tags": ["art", "design", "creative", "branding", "mural", "exhibition"]
    },
    {
      "id": "art-pricing",
      "name": "Artwork pricing",
      "description": "Estimates commission prices for artwork by medium, size and effort.",
      "tags": ["art", "pricing", "commission"]
    }
  ]
}
```

- **`url`:** the runtime ARN, URL-encoded with no characters left unencoded (`:` → `%3A`, `/` → `%2F`). To encode your own:
  `python3 -c "from urllib.parse import quote; print(quote('<runtime ARN>', safe=''))"`
- **`name`, `description` and `skills`:** these are what registry search matches against, so make them specific; tags help too.
- **Templates:** the cards for all three agents are in [`registry-cards/`](registry-cards), with `<REGION>` and `<URL_ENCODED_RUNTIME_ARN>` placeholders in `url`. They match the `agent_card` in each `agent.py`; if you change one, change the other.

### Option A: `scripts/register_agents.py` (recommended)

Creates or updates all three records from `registry-cards/`, fills in each runtime URL, submits the records for approval and, with `--approve`, approves them. Running it again updates the records in place, so it never creates duplicates.

```bash
pip install -U boto3   # needs the agent-registry-control client (boto3 >= 1.43.107)

# Preview: prints the account, resolved runtime ARNs and final cards. Changes nothing.
python scripts/register_agents.py --registry <registry name or ID> --dry-run

# Register and approve all three
python scripts/register_agents.py --registry <registry name or ID> --approve

# Runtime names that don't contain artist / investment_banker / fitness_trainer
python scripts/register_agents.py --registry <registry> --approve \
  --runtime artist=arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/artist_agent-AbC123XyZ9 \
  --runtime investment_banker=<runtime name or ARN> \
  --runtime fitness_trainer=<runtime name or ARN>
```

| Option | Default | Description |
|---|---|---|
| `--registry` | (required) | Registry ID, ARN or name |
| `--profile` | `non-prod-admin` | AWS profile |
| `--region` | `us-east-1` | AWS region |
| `--runtime AGENT=ARN_OR_NAME` | auto-detect by name | Set an agent's runtime explicitly; repeatable |
| `--only AGENT` | all three | Register only these agents; repeatable |
| `--approve` | off | Also approve records left in `PENDING_APPROVAL` |
| `--dry-run` | off | Show what would be registered and stop |

For each agent the script:
1. Finds the runtime ARN.
2. Fills the card's `url`.
3. Creates the record, or updates the existing one.
4. Waits while it's `CREATING` or `UPDATING`.
5. Submits a `DRAFT` for approval, and approves it if you passed `--approve`.

The permissions it needs are in [`iam/registration-policy.json`](iam/registration-policy.json).

### Option B: AWS console

1. Copy `registry-cards/<agent>.agent-card.json` and replace `<REGION>` and `<URL_ENCODED_RUNTIME_ARN>` in `url` (see the sample above).
2. In your registry, create a record:
   - **Record type:** Agent
   - **Descriptor:** A2A Agent Card, schema 0.3
   - **Card:** paste the JSON
   - **Name:** for example `artist-agent`
3. Submit the record for approval and approve it.
4. Check it: search for "investment planning" and confirm the banker's record comes back.

### When to register again

| Change | Action |
|---|---|
| Runtime updated in place (same ARN) | Nothing |
| New runtime created (new ARN) | Re-run the script, or update the card's `url` |
| Agent's skills or description changed | Update `registry-cards/` and `agent_card` in `agent.py`, then re-run |
| Agent retired | Set its record to `DEPRECATED` so it's no longer found |

Local mode (`LOCAL_PEER_URLS`) doesn't use the registry, so you don't need to register anything to run the agents locally.

## Bringing in a non-A2A agent

An agent served as a normal AgentCore entrypoint (`BedrockAgentCoreApp` with `@app.entrypoint`, HTTP protocol, `POST /invocations` on port 8080) **can be registered with an A2A card, but registering alone isn't enough**. The registry checks the card's format, not whether the runtime actually speaks A2A. Our agents send it an A2A `message/send` JSON-RPC request through `InvokeAgentRuntime`, and AgentCore passes that body to the entrypoint unchanged as `payload`. Unless the entrypoint answers in A2A format, `ask_peer_agent` gets no answer text.

### The `@a2a_compatible` decorator

[`examples/http_entrypoint_agent/a2a_adapter.py`](examples/http_entrypoint_agent/a2a_adapter.py) fixes this without changing your entrypoint. Copy the file next to your agent and add one decorator **below** `@app.entrypoint`:

```python
from a2a_adapter import a2a_compatible

@app.entrypoint
@a2a_compatible                 # the only line added
def invoke(payload, context):   # unchanged: still receives {"prompt": "..."}
    ...
    return {"result": answer}
```

For an A2A `message/send` request, the decorator:
1. Joins the text parts of `params.message` into one string.
2. Calls your function with `{"prompt": <text>}`.
3. Wraps what it returns in an A2A Message result: `{"jsonrpc": "2.0", "id": …, "result": {"kind": "message", "parts": [{"kind": "text", "text": …}]}}`.

Any other payload, such as `{"prompt": ...}`, goes to your function untouched, so existing callers are unaffected.

| Your entrypoint | Supported |
|---|---|
| Sync or `async def` | Yes |
| With or without the `context` argument | Yes; the SDK still passes `context` |
| Streaming (sync or async generator) | Yes: A2A calls get the chunks joined into one answer (plain strings, or Strands events with a `data` text delta); other callers still stream |
| Returns a `str`, a dict, or a Strands `AgentResult` | Yes. For a dict, the answer is the first of `result`, `response`, `output`, `answer`, `text`, `message` or `content` that's present |

If your payload or return shape differs, pass options:
```python
@a2a_compatible(prompt_key="input")                                   # payload key isn't "prompt"
@a2a_compatible(build_payload=lambda text, req: {"prompt": text, "user": "a2a"})
@a2a_compatible(extract_answer=lambda result: result["output"]["text"])
```

Errors come back as JSON-RPC errors: `-32601` for A2A methods other than `message/send`, `-32602` for a message with no text, and `-32603` if your entrypoint raises (the exception is logged).

[`examples/http_entrypoint_agent/agent.py`](examples/http_entrypoint_agent/agent.py) is a complete example: a Nutritionist agent with an ordinary `{"prompt": ...}` entrypoint plus the decorator.

**To bring it into the setup:**

1. **Deploy** it as usual: HTTP protocol, port 8080 (`app.run()`), no `serve_a2a`. Its runtime is invoked through the same `/runtimes/<ARN>/invocations/` URL as A2A runtimes, so our agents read the ARN from its card the same way.
2. **Register** it with an A2A card, using the template [`examples/http_entrypoint_agent/agent-card.json`](examples/http_entrypoint_agent/agent-card.json) and filling in `url` as for the others. To use the script, copy the card to `registry-cards/nutritionist.agent-card.json` and add `"nutritionist": "nutritionist-agent"` to `AGENTS` in `scripts/register_agents.py`.
3. **Grant** each calling agent's role `bedrock-agentcore:InvokeAgentRuntime` on the new runtime (both ARN forms, as in [`iam/agent-runtime-policy.json`](iam/agent-runtime-policy.json)). The existing agents need no code change: they find it on their next registry search.

**Limits compared with `serve_a2a`:**
- **Only `message/send`.** No A2A streaming (`message/stream`) or `tasks/get`, which is why the card sets `"streaming": false`. Your own callers can still stream.
- **No card at `/.well-known/agent-card.json`.** Callers get the card from the registry, as ours do; A2A clients that fetch the card from the runtime won't work.
- **No local mode.** `LOCAL_PEER_URLS` posts to `/`, but this app only has `/invocations`.
- **The loop guard is up to you.** If you give this agent peer tools, honour the `[A2A request from …]` prefix like `ask_peer_agent` does.

If the agent is yours to change, moving it to `serve_a2a` (like the three agents here) gives it the full A2A protocol.

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

### From Python: `scripts/invoker.py`

[`scripts/invoker.py`](scripts/invoker.py) is a standard-library CLI for agents running on localhost. It sends `message/send`, reads the answer from the task's artifacts, and handles JSON-RPC errors, failed tasks and unreachable agents.

```bash
python scripts/invoker.py banker "How should I invest \$30,000 over 10 years?"   # one question
python scripts/invoker.py artist                                                 # interactive chat; keeps contextId
python scripts/invoker.py --port 9002 "Build me a 3-day posture plan"            # any port
python scripts/invoker.py trainer "Quick tip?" --raw                             # full JSON-RPC reply
```

Agent names map to the local ports in `agent.yaml`: `artist` → 9000, `banker` / `investment_banker` → 9001, `trainer` / `fitness_trainer` → 9002. Use `--url` for any other address, and `--timeout` (default 300 s) for slow answers. Works on Python 3.9+.

To use it from your own code, `send_message()` returns the JSON-RPC reply and `extract_answer()` turns it into `(text, contextId)`:

```python
from invoker import send_message, extract_answer   # with scripts/ on sys.path

answer, context_id = extract_answer(send_message("http://localhost:9001/", "How should I invest $30,000?"))
```

### Examples with curl and the AWS CLI

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
