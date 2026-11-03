# User Guide

1. [Prerequisites](#1-prerequisites)
2. [Deploying the agents](#2-deploying-the-agents)
3. [IAM permissions](#3-iam-permissions)
4. [Registering agents in Agent Registry](#4-registering-agents-in-agent-registry)
5. [Running the demo](#5-running-the-demo)
6. [Running locally](#6-running-locally)
7. [Customising the agents](#7-customising-the-agents)
8. [Adding a new agent](#8-adding-a-new-agent)
9. [Bringing in an existing (non-A2A) agent](#9-bringing-in-an-existing-non-a2a-agent)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

- An AWS account and region with **AgentCore Runtime** and **AWS Agent Registry**. The examples use `us-east-1`.
- An **Agent Registry** (create one in the console if needed). Note its ID.
- Access to a Claude model in **Amazon Bedrock** in that region.
- For the registration script: Python 3.10+, a recent `boto3` (tested with 1.43.123), and an AWS profile with Agent Registry admin rights. The default profile is `non-prod-admin`.

## 2. Deploying the agents

Your CI/CD pipeline handles deployment. For each folder under `agents/`:

1. Build the image from that folder's `Dockerfile`. It targets **`linux/arm64`**, which AgentCore Runtime requires.
2. Push the image to ECR.
3. Create or update an AgentCore Runtime with these settings:
   - **Protocol:** `A2A`. The container serves A2A JSON-RPC on `0.0.0.0:9000/`, the health check on `/ping`, and its card on `/.well-known/agent-card.json`.
   - **Inbound auth:** IAM (SigV4). The agents call each other with SigV4-signed `InvokeAgentRuntime`, so peer runtimes must accept IAM auth.
   - **Environment variables:** declared in each agent's `agent.yaml`, which the framework injects into the runtime. The `deployed:` block is used on AgentCore and the `local:` block on your machine (§6.1). Before deploying, **set `REGISTRY_ID`** in each `deployed:` block; it's empty in the repo.

     | Variable | `deployed:` | `local:` | Description |
     |---|---|---|---|
     | `REGISTRY_ID` | **required** | (not set) | Agent Registry ID or ARN to search |
     | `LOCAL_PEER_URLS` | **must not be set** | the other two agents' URLs | Turns on local mode; takes precedence over `REGISTRY_ID` |
     | `A2A_PORT` | `9000` | `9000` / `9001` / `9002` | Port to serve on; AgentCore requires 9000 |
     | `AWS_REGION` | `us-east-1` | `us-east-1` | Region of the registry, runtimes and Bedrock |
     | `MODEL_ID` | `anthropic.claude-opus-5-5` | same | Bedrock model or inference profile ID; set it to what your region uses |

     The code falls back to defaults for `AWS_REGION` (`us-east-1`), `MODEL_ID` (`anthropic.claude-opus-5-5`) and `A2A_PORT` (9000), and refuses to start if neither `REGISTRY_ID` nor `LOCAL_PEER_URLS` is set.

**Naming tip:** if each runtime's name contains `artist`, `investment_banker` or `fitness_trainer`, the registration script finds the runtimes automatically. Underscores and hyphens are ignored when matching.

## 3. IAM permissions

Ready-to-use policies are in [`iam/`](../iam). Replace the `<PLACEHOLDERS>` before attaching them. The action names and ARN formats come from the AWS Service Authorization Reference for [Agent Registry](https://docs.aws.amazon.com/service-authorization/latest/reference/list_agent-registry.html), [Bedrock AgentCore](https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-agentcore.html) and Amazon Bedrock.

### 3.1 Agent execution roles: [`iam/agent-runtime-policy.json`](../iam/agent-runtime-policy.json)

Attach this policy to **each** agent's AgentCore Runtime execution role, alongside the baseline AgentCore execution-role permissions your pipeline already grants (ECR pull, CloudWatch Logs, X-Ray, CloudWatch metrics, workload identity).

| Statement | Action | Resource | Code that needs it |
|---|---|---|---|
| `DiscoverPeersInAgentRegistry` | `agent-registry:SearchDiscoverableRegistryRecords` | `arn:aws:agent-registry:<REGION>:<ACCOUNT_ID>:registry/<REGISTRY_ID>` | `find_peer_agents` |
| `InvokePeerAgentsOverA2A` | `bedrock-agentcore:InvokeAgentRuntime` | The **two peer** runtimes, `runtime/<ID>` **and** `runtime/<ID>/runtime-endpoint/*` | `ask_peer_agent` |
| `InvokeClaudeOnBedrock` | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | The foundation model, plus the inference profile if `MODEL_ID` is one | Strands `BedrockModel` |

Peers for each role:

| Execution role of | `<PEER_1_RUNTIME_ID>` | `<PEER_2_RUNTIME_ID>` |
|---|---|---|
| Artist | investment banker runtime | fitness trainer runtime |
| Investment Banker | artist runtime | fitness trainer runtime |
| Fitness Trainer | artist runtime | investment banker runtime |

Notes:

- **Both runtime ARN forms are needed.** `InvokeAgentRuntime` authorizes against the `runtime` and `runtime-endpoint` resource types. The code calls the `DEFAULT` endpoint, which is `runtime/<ID>/runtime-endpoint/DEFAULT`.
- **Not needed:** `bedrock-agentcore:InvokeAgentRuntimeForUser`. That applies only when a caller passes a runtime user ID, and this code doesn't. The agents also don't need `ListDiscoverableRegistryRecords` or `GetDiscoverableRegistryRecord`, because they only search.
- **Model:** if `MODEL_ID` is a cross-region inference profile, Bedrock checks both the profile ARN and the foundation-model ARN in every region the profile routes to; the `*` region in the foundation-model ARN covers that. If you use a different model, change both ARNs.
- **Leaving out a peer** denies that pair. For example, removing the trainer from the banker's policy means the banker can still *find* the trainer but gets `AccessDeniedException` when calling it.

### 3.2 Registration profile: [`iam/registration-policy.json`](../iam/registration-policy.json)

These are the permissions `scripts/register_agents.py` uses. The `non-prod-admin` profile already has them; use this policy for a least-privilege onboarding pipeline role later.

| Action | Resource | Used for |
|---|---|---|
| `agent-registry:ListRegistries` | `*` | Resolving `--registry <name>` |
| `bedrock-agentcore:ListAgentRuntimes` | `*` | Finding runtimes by name (not needed if you pass ARNs) |
| `agent-registry:CreateRegistryRecord`, `agent-registry:ListRegistryRecords` | `registry/<REGISTRY_ID>` | Create, or find an existing record |
| `agent-registry:GetRegistryRecord`, `agent-registry:UpdateRegistryRecord`, `agent-registry:SubmitRegistryRecordForApproval` | `registry/<REGISTRY_ID>/record/*` | Update, wait for status, submit |
| `agent-registry:UpdateRegistryRecordStatus` | `registry/<REGISTRY_ID>/record/*` | Only for `--approve`. Leave it out to keep approval a separate curator step |

`sts:GetCallerIdentity`, which the script uses to print the account, needs no IAM permission. The extra permissions AWS lists for `CreateRegistryRecord` and `UpdateRegistryRecord` (`iam:PassRole`, `bedrock-agentcore:GetWorkloadAccessToken`, `bedrock-agentcore:GetResourceOauth2Token`) are only for records that sync their card from an OAuth-protected URL. This script sends the card inline, so it doesn't need them.

> Discovery is not authorization. The registry only returns metadata; IAM decides who can actually invoke which runtime.

## 4. Registering agents in Agent Registry

Each agent is registered as one record:

- **Record type:** `AGENT`
- **Descriptor:** `a2aAgentCard`, `dataSchemaVersion` `0.3`
- **Card `url`:** `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded runtime ARN>/invocations/`

The agents get the runtime ARN to call from the card's `url`. If the URL is wrong, search still finds the agent but the call fails.

### Option A: the script (recommended)

```bash
# Preview: prints the account, resolved runtime ARNs and final cards. Changes nothing.
python scripts/register_agents.py --registry <registry name or ID> --dry-run

# Create or update all three records, submit them, and approve them
python scripts/register_agents.py --registry <registry name or ID> --approve
```

| Option | Default | Description |
|---|---|---|
| `--registry` | (required) | Registry ID, ARN or name |
| `--profile` | `non-prod-admin` | AWS profile |
| `--region` | `us-east-1` | AWS region |
| `--runtime AGENT=ARN_OR_NAME` | auto-detect | Set an agent's runtime explicitly. Repeatable. `AGENT` is `artist`, `investment_banker` or `fitness_trainer`. |
| `--only AGENT` | all | Register only these agents. Repeatable. |
| `--approve` | off | Also approve records left in `PENDING_APPROVAL` (needs curator rights) |
| `--dry-run` | off | Show what would be registered and stop |

For each agent the script:

1. Finds the runtime ARN.
2. Fills the URL into `registry-cards/<agent>.agent-card.json`.
3. Creates the record named `artist-agent`, `investment-banker-agent` or `fitness-trainer-agent`, or updates it if it already exists.
4. Waits while the record is `CREATING` or `UPDATING`.
5. Submits it for approval if it's a `DRAFT`, and approves it if you passed `--approve`.

Running it again never creates duplicates. Re-run it whenever a runtime ARN or a card changes.

### Option B: the AWS console

1. Copy the runtime ARN from the AgentCore console and URL-encode it:
   ```bash
   python3 -c "from urllib.parse import quote; print(quote('<runtime ARN>', safe=''))"
   ```
2. Open `registry-cards/<agent>.agent-card.json` and replace `<REGION>` and `<URL_ENCODED_RUNTIME_ARN>` in `url`.
3. In Agent Registry, open your registry and create a record:
   - **Record type:** Agent
   - **Descriptor:** A2A Agent Card, schema 0.3
   - **Card:** paste the edited JSON
   - **Name:** `artist-agent`, `investment-banker-agent` or `fitness-trainer-agent`
4. Submit the record for approval and approve it. **Search only returns `APPROVED` records.**
5. Check it: search for "investment planning" in the console and confirm the banker record comes back.

### When to re-register

| Change | Action |
|---|---|
| Runtime updated in place (same ARN) | Nothing |
| New runtime created (new ARN) | Re-run the script or update the card URL |
| Agent's skills or description changed | Update the card in `registry-cards/` and the `agent_card` in `agent.py`, then re-run |
| Agent retired | Set the record to `DEPRECATED` so it's no longer discoverable |

## 5. Running the demo

Send any of these to an agent's runtime with the AgentCore console's test pane, an A2A client, or `InvokeAgentRuntime`. Each crosses domains, so the agent consults its peers.

| Send to | Prompt | Expected collaboration |
|---|---|---|
| Artist | *I'm an established muralist. I've been offered a 400 sq ft mural, about 120 hours of work. What should I charge, how should I invest the fee over 10 years, and how do I stay fit through weeks on a ladder?* | Prices it (~$30,800), asks the banker and the trainer |
| Investment Banker | *I run a small gym and want to spend $15,000 on a wall mural to rebrand. Is it worth it, and how should I budget for it on $6,000/month take-home with $3,200 fixed costs?* | Runs the budget, asks the artist about the mural |
| Fitness Trainer | *I'm a desk-bound analyst. Build me a 4-day posture plan, and tell me whether a $1,500 home gym is a sensible spend if I'm saving $800/month.* | Builds the plan, asks the banker |

**What to watch** (CloudWatch logs for each runtime):

- `A2A -> Investment Banker Agent: …`: the outbound call, with the self-contained question.
- In the peer's logs, the incoming request starting with `[A2A request from Artist Agent]`.
- The final answer credits each peer ("Our investment banker suggests…").

Example A2A request body, if you call a runtime directly:

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "messageId": "b7d1c1d0-0000-4000-8000-000000000001",
      "parts": [{"kind": "text", "text": "What should I charge for a 400 sq ft mural?"}]
    }
  }
}
```

## 6. Running locally

There are two ways to run on your machine:

| Mode | Set | Peers are found | Peers are called |
|---|---|---|---|
| **Local** (6.1) | `LOCAL_PEER_URLS` | From each peer's `/.well-known/agent-card.json` | Plain HTTP to `localhost` |
| **Hybrid** (6.2) | `REGISTRY_ID` | AWS Agent Registry search | Deployed runtimes through `InvokeAgentRuntime` |

In both modes the model runs on **Amazon Bedrock**, so your AWS profile needs `bedrock:InvokeModel` on the model. If both variables are set, local mode wins.

### 6.1 All three agents locally (no registry, no AgentCore)

Install the dependencies once. They're the same for all three agents.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r agents/artist/requirements.txt
```

Then start each agent in its own terminal, from the repo root with the virtual environment active. Each agent's `local:` block in `agent.yaml` already gives it its own port and the other two as peers:

| Agent | `A2A_PORT` | `LOCAL_PEER_URLS` |
|---|---|---|
| Artist | 9000 | `http://localhost:9001,http://localhost:9002` |
| Investment Banker | 9001 | `http://localhost:9000,http://localhost:9002` |
| Fitness Trainer | 9002 | `http://localhost:9000,http://localhost:9001` |

**With your framework:** start each agent through it with the `local:` block, and set your AWS credentials (for example `AWS_PROFILE=non-prod-admin`) in the shell. `agent.yaml` doesn't set credentials.

**Without the framework**, export the same values by hand:

```bash
# Terminal 1: Artist on 9000
export AWS_PROFILE=non-prod-admin AWS_REGION=us-east-1
A2A_PORT=9000 LOCAL_PEER_URLS=http://localhost:9001,http://localhost:9002 python agents/artist/agent.py

# Terminal 2: Investment Banker on 9001
export AWS_PROFILE=non-prod-admin AWS_REGION=us-east-1
A2A_PORT=9001 LOCAL_PEER_URLS=http://localhost:9000,http://localhost:9002 python agents/investment_banker/agent.py

# Terminal 3: Fitness Trainer on 9002
export AWS_PROFILE=non-prod-admin AWS_REGION=us-east-1
A2A_PORT=9002 LOCAL_PEER_URLS=http://localhost:9000,http://localhost:9001 python agents/fitness_trainer/agent.py
```

Set `MODEL_ID` too if the default isn't available in your account. On ports 9001 and 9002 the SDK logs a warning that AgentCore requires port 9000. That's expected locally and can be ignored.

From a fourth terminal, check the agents and send the demo prompt to the artist:

```bash
for p in 9000 9001 9002; do curl -s localhost:$p/.well-known/agent-card.json | jq -r '"\(.name)  \(.url)"'; done

curl -s -X POST localhost:9000/ -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": "1", "method": "message/send",
  "params": {"message": {"role": "user", "messageId": "demo-1", "parts": [{"kind": "text",
    "text": "I am an established muralist. I have been offered a 400 sq ft mural, about 120 hours of work. What should I charge, how should I invest the fee over 10 years, and how do I stay fit through weeks on a ladder?"}]}}}' \
  | jq -r '.result.artifacts[].parts[].text'
```

The answer can take a minute or two, because the artist calls the other two agents. Watch the logs:
- The artist's terminal shows `A2A -> Investment Banker Agent: …` and `A2A -> Fitness Trainer Agent: …`.
- The banker's and trainer's terminals show the incoming requests, which start with `[A2A request from Artist Agent]`.

How local mode differs from the deployed setup:
- **Matching:** the registry's semantic search isn't available locally. `find_peer_agents` returns every reachable peer, with the best keyword match first, and the model picks which one to call.
- **Names:** local peer names come from the card name, for example `investment-banker-agent`, the same names the registration script uses in the registry.
- **Unreachable peers:** a peer that isn't running is skipped with a warning, so you can run just two agents.

### 6.2 One agent locally, peers deployed

The local agent finds its peers in the real registry and calls the **deployed** runtimes, using your local AWS credentials.

```bash
cd agents/artist
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_PROFILE=non-prod-admin AWS_REGION=us-east-1 REGISTRY_ID=<registry ID>
python agent.py          # serves on http://127.0.0.1:9000
```

In another terminal:

```bash
curl -s localhost:9000/ping
curl -s localhost:9000/.well-known/agent-card.json | jq .
curl -s -X POST localhost:9000/ -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send",
  "params":{"message":{"role":"user","messageId":"m-1",
  "parts":[{"kind":"text","text":"Price a 100 sq ft oil painting, 40 hours, emerging artist."}]}}}' | jq .
```

Or run the container exactly as AgentCore would:

```bash
docker build --platform linux/arm64 -t artist-agent agents/artist
docker run --rm -p 9000:9000 -e REGISTRY_ID=<id> -e AWS_REGION=us-east-1 \
  -v ~/.aws:/home/agent/.aws:ro -e AWS_PROFILE=non-prod-admin artist-agent
```

## 7. Customising the agents

| To change | Edit |
|---|---|
| Persona, tone, when to consult peers | `SYSTEMPROMPT.md` (read at startup; no code change) |
| Domain tools | The **Domain tools** section of `agent.py`; add new tools to `tools=[…]` in `create_agent` |
| Python dependencies | That agent's `requirements.txt` (the Dockerfile installs it) |
| How the agent is described to others | `AGENT_NAME`, `AGENT_DESCRIPTION` and `agent_card.skills` in `agent.py`, **and** the matching `registry-cards/*.json`, then re-register |
| Model | `MODEL_ID` environment variable |
| Number of search results | `maxResults` in `find_peer_agents` |

Search is semantic, over the record name, description and card. Specific skill descriptions and tags make an agent easier to find for the right requests.

## 8. Adding a new agent

1. Copy an existing folder, for example `cp -r agents/fitness_trainer agents/nutritionist`.
2. In `agent.py`, change the docstring, `AGENT_NAME`, `AGENT_DESCRIPTION`, the domain tools, the `tools=[…]` list and `agent_card.skills`. Leave the **Agent Registry discovery + A2A invocation** section as it is.
3. Rewrite `SYSTEMPROMPT.md`. Keep the "Working with other agents" section.
4. In its `agent.yaml`, give the `local:` block a free port (for example 9003) and the other agents' URLs. Add the new agent's URL to the other agents' `LOCAL_PEER_URLS`.
5. Add `registry-cards/nutritionist.agent-card.json`, the same card with the URL placeholder.
6. Add `"nutritionist": "nutritionist-agent"` to `AGENTS` in `scripts/register_agents.py`.
7. Deploy it, grant IAM, and register: `python scripts/register_agents.py --registry <id> --only nutritionist --approve`.
8. Give the existing agents' roles `InvokeAgentRuntime` on the new runtime. **No code changes or redeploys are needed** for them; they find it on their next search.

## 9. Bringing in an existing (non-A2A) agent

This section is for agents that already run on AgentCore as a **normal entrypoint**: `BedrockAgentCoreApp` with `@app.entrypoint`, HTTP protocol, `POST /invocations` on port 8080, taking a payload such as `{"prompt": "..."}`. There are two independent steps:

| Goal | What you add | Section |
|---|---|---|
| **Other agents can call it** | The `@a2a_compatible` decorator, plus registering it with an A2A card | 9.1 |
| **It can call other agents** | The `find_peer_agents` and `ask_peer_agent` tools | 9.2 |

You can do either step or both. Neither changes how the agent's existing callers use it.

### 9.1 Let other agents call it

Our agents send peers an A2A `message/send` JSON-RPC request. A normal entrypoint receives that as its `payload` and doesn't understand it, so registering the card alone isn't enough.

1. **Copy** [`examples/http_entrypoint_agent/a2a_adapter.py`](../examples/http_entrypoint_agent/a2a_adapter.py) next to your agent's entrypoint file. It only uses the Python standard library.
2. **Add the decorator** below `@app.entrypoint`. Don't change the function itself:
   ```python
   from a2a_adapter import a2a_compatible

   @app.entrypoint
   @a2a_compatible                 # the only line added
   def invoke(payload, context):   # still receives {"prompt": "..."}
       ...
   ```
   - **A2A requests:** the decorator turns them into `{"prompt": <text>}` and wraps your return value as an A2A reply.
   - **Every other payload:** passed through unchanged, so existing callers keep working, including streaming ones.
   - **Other shapes:** if your payload key isn't `prompt`, or the answer isn't under one of the usual keys, use the options `prompt_key=`, `build_payload=` or `extract_answer=` (see the file's docstring).
3. **Write its agent card.** Start from [`examples/http_entrypoint_agent/agent-card.json`](../examples/http_entrypoint_agent/agent-card.json) and set:
   - **`name`:** this becomes the agent's identity for the others; see 9.2.
   - **`description` and `skills`:** registry search matches on these, so be specific.
   - **`url`:** `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded runtime ARN>/invocations/`. This is the same URL form as A2A runtimes, so callers read the ARN from it the same way.
   - **`"capabilities": {"streaming": false}`:** keep this.
4. **Register** the card as an `AGENT` record and approve it (§4), using the console, or the script after copying the card to `registry-cards/<key>.agent-card.json` and adding the key to `AGENTS` in `scripts/register_agents.py`.
5. **Grant callers access:** every agent that should be able to call it needs `bedrock-agentcore:InvokeAgentRuntime` on its runtime, both `runtime/<ID>` and `runtime/<ID>/runtime-endpoint/*` (§3.1).

The agents already deployed need no code change; they find the new agent on their next registry search.

### 9.2 Let it call other agents

The peer tools are ordinary Strands tools, so any Strands agent can use them, decorated or not. Copy them from [`agents/artist/agent.py`](../agents/artist/agent.py); the networking code is identical in all three agents.

**Step 1: dependencies.** Make sure the agent's requirements include `boto3>=1.43.107` (for the `agent-registry` client), `strands-agents` and `bedrock-agentcore`. The copied code uses `str | None`-style type hints, so it needs **Python 3.10 or newer**.

**Step 2: imports.** Add whichever of these the file doesn't already have:
```python
import json
import logging
import os
import re
import urllib.request
import uuid
from urllib.parse import unquote

import boto3
from botocore.config import Config
from strands import Agent, ToolContext, tool
```

**Step 3: configuration.** Add this block near the top of the file and set `AGENT_NAME`:
```python
AGENT_NAME = "Nutritionist Agent"   # MUST equal "name" in this agent's registry card
REGION = os.environ.get("AWS_REGION", "us-east-1")
REGISTRY_ID = os.environ.get("REGISTRY_ID")
LOCAL_PEER_URLS = [u.strip().rstrip("/") + "/" for u in os.environ.get("LOCAL_PEER_URLS", "").split(",") if u.strip()]
if not REGISTRY_ID and not LOCAL_PEER_URLS:
    raise SystemExit("Set REGISTRY_ID, or LOCAL_PEER_URLS to run in local mode.")

# Messages an agent sends to a peer start with this prefix. A peer that sees it
# answers directly instead of calling further agents, which prevents A -> B -> A loops.
PEER_PREFIX = "[A2A request from "

logger = logging.getLogger(AGENT_NAME)

_boto_config = Config(read_timeout=300, retries={"max_attempts": 3, "mode": "adaptive"})
registry = boto3.client("agent-registry", region_name=REGION, config=_boto_config)
agentcore = boto3.client("bedrock-agentcore", region_name=REGION, config=_boto_config)

# record name -> {"name", "card", and "arn" (AgentCore) or "url" (local mode)};
# filled by find_peer_agents
_peers: dict[str, dict] = {}

_RUNTIME_ARN = re.compile(r"arn:aws[\w-]*:bedrock-agentcore:[\w-]+:\d{12}:runtime/[\w-]+")
```
- **`AGENT_NAME` must match the card's `name` exactly.** `find_peer_agents` uses it to skip the agent's own record; with a mismatch the agent can find, and call, itself.
- **Startup check:** with this block the agent **refuses to start** unless `REGISTRY_ID` or `LOCAL_PEER_URLS` is set (step 6).

**Step 4: the networking code.** Copy everything in `agents/artist/agent.py` between the comment banner `# Agent Registry discovery + A2A invocation` and the `# Domain tools` banner, unchanged. That's:

| Function | Purpose |
|---|---|
| `_runtime_arn` | Reads the runtime ARN from a card's URL |
| `_send_a2a` | Sends A2A `message/send` through `InvokeAgentRuntime`, or plain HTTP in local mode |
| `_extract_text` | Reads the answer text from an A2A reply |
| `_current_request` | Finds the user message being handled, for the loop guard |
| `_registry_candidates` | Registry search |
| `_local_candidates` | Local-mode discovery from `LOCAL_PEER_URLS` |
| `find_peer_agents` | **Tool:** finds agents for a need |
| `ask_peer_agent` | **Tool:** asks one of them a question |

Then add the two tools to the agent:
```python
agent = Agent(
    ...,
    tools=[*your_existing_tools, find_peer_agents, ask_peer_agent],
)
```
The agent can be created per request or once and reused; the loop guard works either way.

**Step 5: system prompt.** Append the "Working with other agents" section from any of our `SYSTEMPROMPT.md` files, adjusting the list of topics that are outside this agent's expertise. Without it the model rarely uses the tools, or uses them for questions it could answer itself.

**Step 6: environment.**

| Where | Set |
|---|---|
| Deployed | `REGISTRY_ID`, plus `AWS_REGION` if not `us-east-1` |
| Locally | `LOCAL_PEER_URLS` with the URLs of the agents it should call, e.g. `http://localhost:9000,http://localhost:9001` |

**Step 7: IAM.** Add to the agent's execution role (§3.1 has the full policy):
- `agent-registry:SearchDiscoverableRegistryRecords` on the registry.
- `bedrock-agentcore:InvokeAgentRuntime` on each runtime it may call (both ARN forms).

**Step 8: pass the prompt to the agent unchanged.** This is the one rule that keeps the loop guard working. `ask_peer_agent` refuses to fan out when the user message being handled **starts with** `[A2A request from …]`. The decorator passes that prefix through in `payload["prompt"]`, so the guard works as long as the entrypoint hands the prompt to the agent as-is:

```python
@app.entrypoint
@a2a_compatible
def invoke(payload):
    agent = Agent(..., tools=[..., find_peer_agents, ask_peer_agent])

    result = agent(payload["prompt"])                       # ✅ guard works
    # result = agent(f"User asks: {payload['prompt']}")     # ❌ guard bypassed: loops possible
    # result = agent(payload["prompt"] + "\nBe concise.")   # ✅ fine: text added after the prompt
    return {"result": str(result)}
```

Tested with the decorator and the copied tools:

| Request | `ask_peer_agent` |
|---|---|
| From a user (`{"prompt": ...}`) | Allowed |
| From a peer, prompt passed unchanged | Blocked: "this request came from another agent" |
| From a peer, prompt prefixed by the entrypoint | **Not blocked** |

To add instructions, put them in the system prompt or **after** the prompt text, never before it.

### 9.3 Checking it works

1. **Startup:** the agent starts without `SystemExit`, so `REGISTRY_ID` (or `LOCAL_PEER_URLS`) is set.
2. **It can be found:** run `python scripts/register_agents.py --registry <id> --dry-run`, or search in the console, and confirm the record is `APPROVED`.
3. **Others can call it:** send one of our agents a question in this agent's area. Its log shows `A2A -> <your agent's name>: …`, and the answer credits it.
4. **It can call others:** send it a `{"prompt": ...}` question that needs another agent's expertise. Its log shows `A2A -> …` lines, and the peers' logs show `[A2A request from <your agent's name>]`.
5. **No loops:** when a peer asks it a question, its log should show **no** `A2A ->` line for that request.

### 9.4 What it can't do (compared with `serve_a2a`)

| | Decorated entrypoint | `serve_a2a` agent |
|---|---|---|
| Called by our agents (`message/send`) | ✅ | ✅ |
| Calls other agents (with 9.2) | ✅ | ✅ |
| A2A streaming (`message/stream`), `tasks/get` | ❌ (JSON-RPC `-32601`) | ✅ |
| Serves `/.well-known/agent-card.json` | ❌ (card lives only in the registry) | ✅ |
| Called by peers in local mode | ❌ (no `/` route) | ✅ |
| Calls peers in local mode | ✅ | ✅ |
| Existing `{"prompt": ...}` callers | ✅ unchanged | n/a |

If the agent is yours to change and you need any of the ❌ rows, move it to `serve_a2a` like the three agents in `agents/`.

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `find_peer_agents` returns "No matching agents" | Record not `APPROVED`, wrong `REGISTRY_ID`, or the query doesn't match the card | Check record status; check the env var; improve the card's description and skills |
| `<agent> could not be reached: AccessDeniedException` | Caller role lacks `InvokeAgentRuntime` on the peer, or the peer uses OAuth inbound auth | Grant the permission; set the peer's inbound auth to IAM |
| `No runtime ARN for registry record …` in logs | Card `url` doesn't contain the runtime ARN | Re-register; the URL must be the AgentCore runtime URL |
| `ResourceNotFoundException` / JSON-RPC `-32051` on peer call | Runtime deleted or replaced (new ARN) | Re-run `register_agents.py` |
| `RuntimeClientError` / HTTP 424 | Peer container crashed or isn't on port 9000 | Check the peer's CloudWatch logs |
| Unknown service `agent-registry` | Old boto3 | `pip install -U boto3` (≥ 1.43.107) |
| Script: "Could not pick one runtime" | Zero or several runtime names match | Pass `--runtime AGENT=<ARN or exact name>` |
| Script: record `CREATE_FAILED` / `UPDATE_FAILED` | Card failed schema validation | Read the printed `statusReason`, fix the card JSON |
| Model error at startup or first call | `MODEL_ID` not enabled in the region | Use a model or inference profile ID available in your account |
| Agents keep calling each other | Shouldn't happen: the loop guard blocks calls from peer requests | Make sure the `PEER_PREFIX` logic in `agent.py` is unchanged in every agent |
