# A2A Onboarding Guide

How to bring an **existing AgentCore agent** into the agent network with [`migration/a2a_bridge.py`](../migration/a2a_bridge.py), without rewriting it.

1. [Is this guide for you?](#1-is-this-guide-for-you)
2. [How it works](#2-how-it-works)
3. [Prerequisites](#3-prerequisites)
4. [Step 1: Add the bridge file](#4-step-1-add-the-bridge-file)
5. [Step 2: Let other agents call it](#5-step-2-let-other-agents-call-it)
6. [Step 3: Let it call other agents](#6-step-3-let-it-call-other-agents)
7. [Step 4: Configuration](#7-step-4-configuration)
8. [Step 5: IAM permissions](#8-step-5-iam-permissions)
9. [Step 6: Agent card and registration](#9-step-6-agent-card-and-registration)
10. [Step 7: Test it](#10-step-7-test-it)
11. [Reference: what `a2a_bridge` provides](#11-reference-what-a2a_bridge-provides)
12. [Limits compared with `serve_a2a`](#12-limits-compared-with-serve_a2a)
13. [Troubleshooting](#13-troubleshooting)
14. [Checklist](#14-checklist)

A complete working example is [`examples/http_entrypoint_agent/`](../examples/http_entrypoint_agent): a Nutritionist agent with an ordinary `{"prompt": ...}` entrypoint that uses everything in this guide.

---

## 1. Is this guide for you?

Use this guide if your agent:

- **Runs on AgentCore Runtime as a normal entrypoint:** `BedrockAgentCoreApp` with `@app.entrypoint`, HTTP protocol, `POST /invocations` on port 8080.
- **Takes a payload such as `{"prompt": "..."}`** and you want to keep it that way for its existing callers.

If you're writing a **new** agent, or can rewrite this one, serve it with `serve_a2a` like the agents in [`agents/`](../agents) instead. That gives it the full A2A protocol (see [§12](#12-limits-compared-with-serve_a2a)), and the main [User Guide](USER_GUIDE.md) covers it.

## 2. How it works

The agents in this network find each other in **AWS Agent Registry** and talk over **A2A**: they send each other a JSON-RPC `message/send` request through AgentCore's `InvokeAgentRuntime`. A normal entrypoint doesn't understand that request, so registering its card alone isn't enough.

`a2a_bridge.py` closes the gap in both directions:

```
                 A2A message/send                            {"prompt": text}
 other agent ───────────────────────►  @a2a_compatible  ───────────────────────►  your entrypoint
             ◄─────────────────────── (converts both ways) ◄───────────────────────  (unchanged)
                 A2A reply                                    your return value

 your agent ── find_peer_agents ──► Agent Registry search ──► peer's A2A card (runtime ARN)
            ── ask_peer_agent  ──► InvokeAgentRuntime ──────► peer agent
```

| Goal | From `a2a_bridge` | Step |
|---|---|---|
| **Other agents can call yours** | `@a2a_compatible` decorator, plus registering an A2A card | [Step 2](#5-step-2-let-other-agents-call-it), [Step 6](#9-step-6-agent-card-and-registration) |
| **Yours can call other agents** | `PEER_TOOLS` (`find_peer_agents`, `ask_peer_agent`) and `configure()` | [Step 3](#6-step-3-let-it-call-other-agents) |

You can do either direction or both. Neither changes how your agent's existing callers use it.

## 3. Prerequisites

- **Python 3.9 or newer.**
- **For the decorator only:** nothing extra; it uses the standard library.
- **For the peer tools:** `strands-agents` and `boto3>=1.43.107` (the first version with the `agent-registry` client). Without Strands, the decorator still works and `PEER_TOOLS` is empty.
- **An Agent Registry** in the same account and region as the agent runtimes, and its ID.
- **For testing locally:** at least one A2A agent running locally (the [main User Guide §6.1](USER_GUIDE.md#61-all-three-agents-locally-no-registry-no-agentcore) shows how to start ours).

## 4. Step 1: Add the bridge file

1. **Copy** [`migration/a2a_bridge.py`](../migration/a2a_bridge.py) into your agent's folder, next to the entrypoint file.
2. **Include it in the image.** For example, in the Dockerfile:
   ```dockerfile
   COPY agent.py a2a_bridge.py ./
   ```
3. **Check the requirements** ([§3](#3-prerequisites)) if you'll use the peer tools.

The file only sets things up when imported. It doesn't create AWS clients until they're first used, and missing configuration doesn't stop your agent from starting.

## 5. Step 2: Let other agents call it

### 5.1 Add the decorator

Add `@a2a_compatible` **below** `@app.entrypoint`, and leave the function itself unchanged:

```python
from a2a_bridge import a2a_compatible

@app.entrypoint
@a2a_compatible                 # the only line added
def invoke(payload, context):   # still receives {"prompt": "..."}
    ...
    return {"result": answer}
```

The decorator only acts on A2A requests. It recognises them by `"jsonrpc": "2.0"` together with a `"method"` key:

| Incoming payload | What happens |
|---|---|
| `{"prompt": "..."}`, or any other existing format | Passed to your function **unchanged**; your return value goes back **unchanged**, streaming included |
| A2A `message/send` | The message's text parts are joined into one string, your function is called with `{"prompt": <text>}`, and the return value is wrapped as an A2A reply |

Supported entrypoint styles:

| Your entrypoint | Supported |
|---|---|
| Sync or `async def` | Yes |
| With or without the `context` argument | Yes; the SDK still passes `context` |
| Streaming (sync or async generator) | Yes. For A2A calls the chunks are joined into one answer (plain strings, or Strands events with a `data` text delta); other callers still stream |
| Returns a `str`, a dict or a Strands `AgentResult` | Yes (see 5.2 for dicts) |

### 5.2 Make sure the right field is sent back

For a dict return value, the decorator uses the first of these keys that's present: `result`, `response`, `output`, `answer`, `text`, `message`, `content`. If your answer is somewhere else, **tell the decorator where**, or the caller gets the wrong text.

**Replies shaped `{"status", "agent", "message", "finding"}`.** By default the decorator picks `message`, so the calling agent only hears "Request processed successfully". The bridge has a ready-made reader for this shape, `a2a_answer_from`:

```python
from a2a_bridge import a2a_answer_from, a2a_compatible

@app.entrypoint
@a2a_compatible(extract_answer=a2a_answer_from)
def invoke(payload, context):
    ...
```

| Your entrypoint returns | The calling agent receives |
|---|---|
| `finding` as text | The finding as is |
| `finding` as a dict or list | The finding as formatted JSON |
| No `finding` (the execution failed) | `No finding (status: <status>): <message>`, so it knows what went wrong |
| Something that isn't a dict | The default behaviour (as `default_extract_answer`) |

If the answer field has another name, pass it with `functools.partial`:
```python
import functools

@a2a_compatible(extract_answer=functools.partial(a2a_answer_from, answer_key="output"))
```

**Any other shape:** pass your own function, for example `@a2a_compatible(extract_answer=lambda r: r["output"]["text"])`. Return text on failure rather than raising. If `extract_answer` raises, the caller only gets a generic "Internal error" and loses your message.

### 5.3 Other options

| Option | Use when | Example |
|---|---|---|
| `prompt_key=` | Your payload key isn't `prompt` | `@a2a_compatible(prompt_key="input")` |
| `build_payload=` | Your payload needs more than one key | `@a2a_compatible(build_payload=lambda text, request: {"prompt": text, "source": "a2a"})` |
| `extract_answer=` | The answer isn't under a standard key (5.2) | `@a2a_compatible(extract_answer=lambda r: r["output"]["text"])` |

### 5.4 Errors returned to callers

| Situation | JSON-RPC error |
|---|---|
| An A2A method other than `message/send` (for example `message/stream`) | `-32601` Method not found |
| A message with no text part | `-32602` Invalid params |
| Your entrypoint (or `extract_answer`) raises | `-32603` Internal error; the exception is logged |

## 6. Step 3: Let it call other agents

### 6.1 Configure and add the tools

```python
from a2a_bridge import PEER_TOOLS, a2a_compatible, configure

configure(agent_name="Nutritionist Agent")   # MUST equal "name" in this agent's registry card

@app.entrypoint
@a2a_compatible
def invoke(payload, context):
    agent = Agent(..., tools=[*your_tools, *PEER_TOOLS])
    result = agent(payload["prompt"])          # pass the prompt UNCHANGED (6.3)
    return {"result": str(result)}
```

- **`agent_name` must match the card's `name` exactly.** It's used to skip the agent's own registry record and appears in the `[A2A request from …]` prefix the peers see. With a mismatch, the agent can find, and call, itself.
- **`configure()` is optional.** It overrides the environment variables in [Step 4](#7-step-4-configuration). It also takes `max_results`, the number of registry results per search (default 5).
- **The agent can be created per request or once and reused;** the loop guard works either way.
- **Agents that don't use Strands tools** can call `search_peers(need)` and `call_peer(record_name, question)` directly, and use `is_peer_request(text)` to apply the loop guard themselves ([§11](#11-reference-what-a2a_bridge-provides)).

### 6.2 Tell the model when to use them

Add a section like this to the agent's system prompt, adapted to its area. Without it the model rarely uses the tools, or uses them for questions it could answer itself:

```text
# Working with other agents

You are one agent in a network of specialists registered in AWS Agent Registry.
When part of a request is outside <your area> (<other areas, e.g. money, investing,
art, design, fitness training>), call find_peer_agents with a plain description of
the expertise you need, then ask_peer_agent with a self-contained question that
includes every number and fact the peer needs. Say which agent contributed.

If a message starts with [A2A request from …], another agent is asking you.
Answer it directly and concisely. Do not consult further agents for it.
```

Listing the other areas makes consulting more predictable, but new kinds of agent won't be consulted until you add their area. A general wording ("when any part of a request is outside <your area>") picks up new agents automatically, but it's less predictable about when the model consults.

### 6.3 The loop guard: pass the prompt unchanged

Without a guard, agents could ask each other in circles: A asks B, B asks A, and so on. Every request one agent sends to another starts with `[A2A request from <agent>]`. While an agent is handling such a request, `ask_peer_agent` refuses to call further agents.

The check looks for that prefix **at the start** of the user message the agent is handling. The decorator passes it through in `payload["prompt"]`, so the guard works as long as your entrypoint hands the prompt to the agent as-is:

```python
result = agent(payload["prompt"])                       # ✅ guard works
# result = agent(f"User asks: {payload['prompt']}")     # ❌ guard bypassed: loops possible
# result = agent(payload["prompt"] + "\nBe concise.")   # ✅ fine: text added after the prompt
```

| Request | `ask_peer_agent` |
|---|---|
| From a user (`{"prompt": ...}`) | Allowed |
| From a peer, prompt passed unchanged | Blocked: "this request came from another agent" |
| From a peer, prompt prefixed by the entrypoint | **Not blocked** |

To add instructions, put them in the system prompt or **after** the prompt text, never before it.

### 6.4 What the tools handle for you

- **Registry search:** results are limited to approved `AGENT` records with an A2A card. The agent's own record is skipped, and so are records with invalid cards or with a card URL that has no runtime ARN.
- **Local mode:** peers that aren't usable are skipped with a warning: one that's down, one that returns something other than JSON, or a malformed URL.
- **Failures:** registry errors (for example `AccessDenied`) and failed peer calls come back to the model as text, so the request carries on instead of crashing.

## 7. Step 4: Configuration

Set these as environment variables, or in code with `configure(...)`, which takes priority:

| Variable | `configure()` argument | Needed for | Description |
|---|---|---|---|
| `AGENT_NAME` | `agent_name` | Peer tools | This agent's name, exactly as in its registry card |
| `REGISTRY_ID` | `registry_id` | Peer tools, deployed | Agent Registry ID or ARN to search |
| `LOCAL_PEER_URLS` | `local_peer_urls` | Peer tools, local | Comma-separated base URLs of local A2A agents, e.g. `http://localhost:9000,http://localhost:9001`. Takes precedence over `REGISTRY_ID` |
| `AWS_REGION` | `region` | Peer tools | Region of the registry and peer runtimes (default `us-east-1`) |
| — | `max_results` | Peer tools | Registry results per search (default 5) |

The decorator needs no configuration. Make sure `LOCAL_PEER_URLS` is **not** set in deployed environments, because it would switch the agent to local mode.

## 8. Step 5: IAM permissions

**This agent's execution role,** if it uses the peer tools:

```json
[
  {
    "Sid": "DiscoverPeersInAgentRegistry",
    "Effect": "Allow",
    "Action": "agent-registry:SearchDiscoverableRegistryRecords",
    "Resource": "arn:aws:agent-registry:<REGION>:<ACCOUNT_ID>:registry/<REGISTRY_ID>"
  },
  {
    "Sid": "InvokePeerAgentsOverA2A",
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": [
      "arn:aws:bedrock-agentcore:<REGION>:<ACCOUNT_ID>:runtime/<PEER_RUNTIME_ID>",
      "arn:aws:bedrock-agentcore:<REGION>:<ACCOUNT_ID>:runtime/<PEER_RUNTIME_ID>/runtime-endpoint/*"
    ]
  }
]
```

Repeat the two `runtime` lines for each peer it may call. Both forms are needed, because `InvokeAgentRuntime` is authorized against the runtime and its endpoint.

**Every other agent's execution role** that should be able to call this one needs `bedrock-agentcore:InvokeAgentRuntime` on **this** agent's runtime, again in both forms.

The full policy for our agents is in [`iam/agent-runtime-policy.json`](../iam/agent-runtime-policy.json).

Being found is not the same as being allowed to call. An agent can find this one in the registry and still get `AccessDeniedException` if its role lacks `InvokeAgentRuntime` on this runtime.

## 9. Step 6: Agent card and registration

Other agents find yours only through an **approved** `AGENT` record in the registry.

### 9.1 Write the card

Start from [`examples/http_entrypoint_agent/agent-card.json`](../examples/http_entrypoint_agent/agent-card.json):

| Field | Set to |
|---|---|
| `name` | Exactly the `agent_name` from [§6.1](#61-configure-and-add-the-tools) |
| `description`, `skills` (with `tags`) | What the agent does. Registry search matches on these, so be specific |
| `url` | `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded runtime ARN>/invocations/` |
| `capabilities.streaming` | `false` (the decorator doesn't support A2A streaming) |
| `protocolVersion` | `"0.3.0"` |

To encode the runtime ARN for `url`:
```bash
python3 -c "from urllib.parse import quote; print(quote('<runtime ARN>', safe=''))"
```

A runtime using the HTTP protocol is invoked through the same `/runtimes/<ARN>/invocations/` URL as an A2A one, so the other agents read the ARN from it the same way.

### 9.2 Register it

**With the script:**
1. Copy the card to `registry-cards/<key>.agent-card.json`, for example `registry-cards/nutritionist.agent-card.json`, keeping the `<REGION>` and `<URL_ENCODED_RUNTIME_ARN>` placeholders.
2. Add the key and a record name to `AGENTS` in [`scripts/register_agents.py`](../scripts/register_agents.py):
   ```python
   AGENTS = {
       ...
       "nutritionist": "nutritionist-agent",
   }
   ```
3. Run it:
   ```bash
   python scripts/register_agents.py --registry <registry> --only nutritionist --dry-run
   python scripts/register_agents.py --registry <registry> --only nutritionist --approve \
     --runtime nutritionist=<runtime ARN or exact runtime name>
   ```

**With the console:**
1. Fill in `url` in the card.
2. In your registry, create a record: **Record type** Agent, **Descriptor** A2A Agent Card (schema 0.3). Paste the card and give it a name such as `nutritionist-agent`.
3. Submit it for approval and approve it.

The agents already deployed need **no code change or redeploy**; they find the new agent on their next registry search. Two things aren't automatic: their roles need `InvokeAgentRuntime` on the new runtime ([§8](#8-step-5-iam-permissions)), and their system prompts may need its area of expertise added ([§6.2](#62-tell-the-model-when-to-use-them)).

## 10. Step 7: Test it

### 10.1 Locally

`app.run()` serves on `http://127.0.0.1:8080` by default; use `app.run(port=...)` for a different port.

**The decorator:**
```bash
# existing callers: unchanged reply
curl -s -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"prompt": "What should a muralist eat on a 10-hour painting day?"}'

# A2A caller: reply in A2A format, answer in result.parts[].text
curl -s -X POST localhost:8080/invocations -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": "1", "method": "message/send",
  "params": {"message": {"role": "user", "messageId": "m-1",
    "parts": [{"kind": "text", "text": "What should a muralist eat on a 10-hour painting day?"}]}}}' \
  | jq -r '.result.parts[].text'
```
If the A2A answer is a status line instead of the real content, fix `extract_answer` ([§5.2](#52-make-sure-the-right-field-is-sent-back)).

**The peer tools:** start one or more of our agents locally ([User Guide §6.1](USER_GUIDE.md#61-all-three-agents-locally-no-registry-no-agentcore)), start yours with `LOCAL_PEER_URLS` pointing at them, and send it a question that needs their expertise:
```bash
LOCAL_PEER_URLS=http://localhost:9000,http://localhost:9001 AWS_PROFILE=<profile> python agent.py
```

**Not testable locally:** our agents calling yours. Local mode posts to `/`, and an entrypoint app only has `/invocations` ([§12](#12-limits-compared-with-serve_a2a)).

### 10.2 Deployed

1. **It's registered:** the record is `APPROVED`. Check with `register_agents.py --dry-run` or in the console.
2. **Existing callers still work:**
   ```bash
   echo '{"prompt": "What should a muralist eat on a 10-hour painting day?"}' > p.json
   aws bedrock-agentcore invoke-agent-runtime --region <region> \
     --agent-runtime-arn "<this agent's runtime ARN>" --runtime-session-id "$(uuidgen)" \
     --payload fileb://p.json out.json && jq . out.json
   ```
3. **Other agents can call it:** ask one of our agents a question in your agent's area. Its log shows `A2A -> <your agent's name>: …`, and its answer credits your agent with real content.
4. **It can call others:** send your agent a `{"prompt": ...}` question that needs another agent's expertise. Its log shows `A2A -> …` lines, and the peers' logs show `[A2A request from <your agent's name>]`.
5. **No loops:** when a peer asks your agent a question, your agent's log shows **no** `A2A ->` line for that request.

## 11. Reference: what `a2a_bridge` provides

| Name | Kind | Purpose |
|---|---|---|
| `a2a_compatible` | Decorator | Answer A2A `message/send` in an existing entrypoint ([Step 2](#5-step-2-let-other-agents-call-it)) |
| `default_extract_answer(result)` | Function | The decorator's default way of turning a return value into answer text |
| `a2a_answer_from(result, answer_key="finding")` | Function | `extract_answer` for `{"status", "agent", "message", "finding"}` replies: the answer field, or status and message when it's missing ([§5.2](#52-make-sure-the-right-field-is-sent-back)) |
| `PEER_TOOLS` | List | `[find_peer_agents, ask_peer_agent]` for `Agent(tools=...)`; empty without Strands |
| `find_peer_agents(need)` | Strands tool | Searches for agents that can help with a need |
| `ask_peer_agent(record_name, question)` | Strands tool | Asks one of them a question, with the loop guard |
| `configure(...)` | Function | Sets `agent_name`, `registry_id`, `region`, `local_peer_urls`, `max_results` |
| `search_peers(need)` | Function | Like `find_peer_agents` but returns a list of dicts; raises if nothing is configured |
| `call_peer(record_name, question)` | Function | Like `ask_peer_agent` but returns the text and raises on failure. No loop guard; check `is_peer_request` yourself |
| `is_peer_request(text)` | Function | `True` if the text is a request from another agent |
| `PEER_PREFIX` | Constant | `"[A2A request from "` |

## 12. Limits compared with `serve_a2a`

| | Entrypoint + `a2a_bridge` | `serve_a2a` agent |
|---|---|---|
| Called by other agents (`message/send`) | ✅ | ✅ |
| Calls other agents | ✅ (with `PEER_TOOLS`) | ✅ |
| Existing `{"prompt": ...}` callers | ✅ unchanged | n/a |
| A2A streaming (`message/stream`), `tasks/get` | ❌ (JSON-RPC `-32601`) | ✅ |
| Serves `/.well-known/agent-card.json` | ❌ (the card lives only in the registry) | ✅ |
| Called by peers in local mode | ❌ (no `/` route) | ✅ |
| Calls peers in local mode | ✅ | ✅ |

If you need any of the ❌ rows and the agent is yours to change, move it to `serve_a2a` like the agents in [`agents/`](../agents).

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Calling agent only gets "Request processed successfully" (or another status line) | The decorator picked a status field from your return dict | Use `extract_answer=a2a_answer_from` for `status`/`message`/`finding` replies, or point `extract_answer` at the real field ([§5.2](#52-make-sure-the-right-field-is-sent-back)) |
| Calling agent gets "Internal error" | Your entrypoint or `extract_answer` raised | Check this agent's logs for "Entrypoint failed on A2A request" |
| Calling agent gets "Method not found" | It sent something other than `message/send` (e.g. streaming) | Only `message/send` is supported ([§12](#12-limits-compared-with-serve_a2a)) |
| Your agent isn't found by others | Record not `APPROVED`, card `name`/`description`/skills too vague, or not in the top results | Approve it; make the card specific ([§9.1](#91-write-the-card)) |
| Others find it but get `AccessDeniedException` | Their roles lack `InvokeAgentRuntime` on your runtime | Add both ARN forms to their roles ([§8](#8-step-5-iam-permissions)) |
| `find_peer_agents` says "No peer directory configured" | Neither `REGISTRY_ID` nor `LOCAL_PEER_URLS` is set | Set one ([§7](#7-step-4-configuration)) |
| `find_peer_agents` says "Agent search failed: …AccessDenied…" | Role lacks `agent-registry:SearchDiscoverableRegistryRecords` | Add it ([§8](#8-step-5-iam-permissions)) |
| Your agent finds or calls **itself** | `agent_name` doesn't match the card's `name` | Make them identical ([§6.1](#61-configure-and-add-the-tools)) |
| Agents keep calling each other | The entrypoint changes the start of the prompt | Pass `payload["prompt"]` unchanged ([§6.3](#63-the-loop-guard-pass-the-prompt-unchanged)) |
| Local mode warns `nonnumeric port` or "not usable" | Malformed URL in `LOCAL_PEER_URLS`, peer not running, or not an A2A server | Use `http://host:port` entries separated by commas; start the peer |
| "Unknown agent '…'. Call find_peer_agents first." | The model skipped the search, or used a different name | Usually self-corrects; otherwise strengthen the system prompt ([§6.2](#62-tell-the-model-when-to-use-them)) |
| `ImportError: a2a_bridge` in AgentCore | The file isn't in the image | Copy it next to the entrypoint and `COPY` it in the Dockerfile ([Step 1](#4-step-1-add-the-bridge-file)) |
| `PEER_TOOLS` is empty | `strands-agents` isn't installed | Add it to the requirements ([§3](#3-prerequisites)) |

## 14. Checklist

**Other agents can call it**
- [ ] `a2a_bridge.py` is next to the entrypoint and copied into the image
- [ ] `@a2a_compatible` is below `@app.entrypoint`
- [ ] `extract_answer` is set if the answer isn't under a standard key (`a2a_answer_from` for `status`/`message`/`finding` replies)
- [ ] The A2A card is written: `name`, specific `description` and skills, encoded `url`, `streaming: false`
- [ ] The record is registered and `APPROVED`
- [ ] Callers' roles have `InvokeAgentRuntime` on this runtime (both ARN forms)

**It can call other agents**
- [ ] `configure(agent_name=...)` matches the card's `name`
- [ ] `PEER_TOOLS` is added to the agent's tools
- [ ] The system prompt has a "Working with other agents" section
- [ ] The prompt is passed to the agent unchanged
- [ ] `REGISTRY_ID` is set when deployed, and `LOCAL_PEER_URLS` only locally
- [ ] Its role has `SearchDiscoverableRegistryRecords` and `InvokeAgentRuntime` on its peers
