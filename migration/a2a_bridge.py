"""A2A bridge: bring an existing AgentCore entrypoint agent into the agent network.

Full guide: docs/A2A_ONBOARDING_GUIDE.md in the aws-agent-registry-poc repository.

One self-contained file. Copy it next to your agent's entrypoint and use the
parts you need:

1. Let other agents call yours: the @a2a_compatible decorator

       from a2a_bridge import a2a_compatible

       @app.entrypoint
       @a2a_compatible                 # below @app.entrypoint
       def invoke(payload, context):   # unchanged; still receives {"prompt": "..."}
           ...

   A2A "message/send" requests (what peers send) are turned into
   {"prompt": <text>}, your function runs as usual, and its return value is
   wrapped as an A2A reply. Every other payload passes through untouched.
   Works with sync, async and streaming entrypoints, with or without `context`.

   If the answer isn't under result/response/output/answer/text/message/content,
   say where it is. For replies shaped {"status", "agent", "message", "finding"}:

       @a2a_compatible(extract_answer=a2a_answer_from)

   or for any other single field: extract_answer=lambda r: r["output"]["text"].

2. Let your agent call others: the peer tools

       from a2a_bridge import PEER_TOOLS, configure

       configure(agent_name="Nutritionist Agent")   # must equal "name" in your registry card
       agent = Agent(..., tools=[*your_tools, *PEER_TOOLS])
       result = agent(payload["prompt"])             # pass the prompt UNCHANGED (loop guard)

   find_peer_agents searches AWS Agent Registry (or, locally, the agent cards
   at LOCAL_PEER_URLS); ask_peer_agent calls the chosen agent over A2A through
   AgentCore InvokeAgentRuntime (or plain HTTP locally).

   Loop guard: requests from peers start with "[A2A request from <agent>]".
   While handling one, ask_peer_agent refuses to call further agents. This only
   works if the prompt reaches the agent unchanged: add instructions to the
   system prompt or after the prompt text, never before it.

Configuration (environment variables, or configure(...) which takes precedence):
  AGENT_NAME       This agent's name, exactly as in its registry card (required for peer tools)
  REGISTRY_ID      Agent Registry ID or ARN to search (deployed)
  LOCAL_PEER_URLS  Comma-separated base URLs of local A2A agents; overrides REGISTRY_ID
  AWS_REGION       Region of the registry and peer runtimes (default us-east-1)

IAM for the agent's execution role (peer tools only):
  agent-registry:SearchDiscoverableRegistryRecords  on the registry
  bedrock-agentcore:InvokeAgentRuntime              on each peer's runtime/<ID> and runtime/<ID>/runtime-endpoint/*

Requirements: Python 3.9+. The decorator uses only the standard library; the
peer tools also need strands-agents and boto3>=1.43.107 (for the agent-registry
client). If strands isn't installed, the decorator still works and PEER_TOOLS
is empty.

Limits compared with serve_a2a: only "message/send" is answered (other A2A
methods get JSON-RPC -32601), no agent card is served from the runtime, and
peers can't call a decorated agent in local mode (it has no "/" route).
"""

from __future__ import annotations

import functools
import http.client
import inspect
import json
import logging
import os
import re
import urllib.request
import uuid
from typing import Any, Callable, Optional
from urllib.parse import unquote

logger = logging.getLogger("a2a_bridge")

# Messages an agent sends to a peer start with this prefix. A peer that sees it
# answers directly instead of calling further agents, which prevents A -> B -> A loops.
PEER_PREFIX = "[A2A request from "

# dict keys tried, in order, when an entrypoint returns a dict
ANSWER_KEYS = ("result", "response", "output", "answer", "text", "message", "content")


# ==========================================================================
# Configuration
# ==========================================================================

_settings: dict[str, Any] = {}


def configure(
    agent_name: Optional[str] = None,
    registry_id: Optional[str] = None,
    region: Optional[str] = None,
    local_peer_urls: Optional[list[str] | str] = None,
    max_results: int = 5,
) -> None:
    """Set the bridge configuration in code; anything not given falls back to the environment."""
    global _registry_client, _agentcore_client
    _settings.update({
        "agent_name": agent_name,
        "registry_id": registry_id,
        "region": region,
        "local_peer_urls": local_peer_urls,
        "max_results": max_results,
    })
    _registry_client = _agentcore_client = None  # recreate clients with the new region


def _agent_name() -> str:
    return _settings.get("agent_name") or os.environ.get("AGENT_NAME", "")


def _registry_id() -> Optional[str]:
    return _settings.get("registry_id") or os.environ.get("REGISTRY_ID")


def _region() -> str:
    return _settings.get("region") or os.environ.get("AWS_REGION", "us-east-1")


def _local_peer_urls() -> list[str]:
    urls = _settings.get("local_peer_urls")
    if urls is None:
        urls = os.environ.get("LOCAL_PEER_URLS", "")
    if isinstance(urls, str):
        urls = urls.split(",")
    return [u.strip().rstrip("/") + "/" for u in urls if u and u.strip()]


def _max_results() -> int:
    return _settings.get("max_results") or 5


# ==========================================================================
# Part 1: @a2a_compatible decorator (standard library only)
# ==========================================================================

def a2a_compatible(
    func: Optional[Callable] = None,
    *,
    prompt_key: str = "prompt",
    build_payload: Optional[Callable[[str, dict], Any]] = None,
    extract_answer: Optional[Callable[[Any], str]] = None,
) -> Callable:
    """Make an AgentCore entrypoint answer A2A "message/send" requests.

    Usable bare (@a2a_compatible) or with options:
        prompt_key:      payload key your entrypoint reads (default "prompt")
        build_payload:   (text, a2a_request) -> payload, for other payload shapes
        extract_answer:  (return value) -> answer text, for other return shapes
    """
    to_payload = build_payload or (lambda text, request: {prompt_key: text})
    to_text = extract_answer or default_extract_answer

    def decorate(fn: Callable) -> Callable:
        # AgentCore checks whether the handler itself is async, so match the original
        if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def wrapper(payload, *args, **kwargs):
                if not _is_a2a(payload):
                    result = fn(payload, *args, **kwargs)
                    # async generator: hand the stream back to AgentCore unchanged
                    return await result if inspect.iscoroutine(result) else result
                return await _answer_async(fn, payload, args, kwargs, to_payload, to_text)
        else:
            @functools.wraps(fn)
            def wrapper(payload, *args, **kwargs):
                if not _is_a2a(payload):
                    return fn(payload, *args, **kwargs)
                return _answer_sync(fn, payload, args, kwargs, to_payload, to_text)
        return wrapper

    return decorate(func) if func is not None else decorate


def default_extract_answer(result: Any) -> str:
    """Turn an entrypoint's return value into answer text."""
    if result is None:
        return ""
    if isinstance(result, bytes):
        return result.decode(errors="replace")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ANSWER_KEYS:
            if key in result and result[key] is not None:
                return default_extract_answer(result[key])
        return json.dumps(result, default=str)
    return str(result)  # e.g. a Strands AgentResult


def a2a_answer_from(result: Any, answer_key: str = "finding") -> str:
    """extract_answer for replies shaped {"status", "agent", "message", "finding"}.

    Returns the answer field, which is only present when the execution
    succeeded (formatted as JSON if it isn't text). Without it, returns the
    status and message as text, so the calling agent learns what went wrong
    instead of getting a generic error.

        @a2a_compatible(extract_answer=a2a_answer_from)

    For a differently named answer field:

        @a2a_compatible(extract_answer=functools.partial(a2a_answer_from, answer_key="output"))
    """
    if not isinstance(result, dict):
        return default_extract_answer(result)
    answer = result.get(answer_key)
    if answer:
        return answer if isinstance(answer, str) else json.dumps(answer, indent=2, default=str)
    return f"No {answer_key} (status: {result.get('status')}): {result.get('message')}"


def _is_a2a(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("jsonrpc") == "2.0" and "method" in payload


def _prepare(request: dict, to_payload: Callable) -> tuple[Any, Optional[dict]]:
    """Return (payload for the entrypoint, None) or (None, JSON-RPC error)."""
    if request.get("method") != "message/send":
        return None, _rpc_error(request, -32601, "Method not found")
    message = (request.get("params") or {}).get("message") or {}
    text = "\n".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind") == "text").strip()
    if not text:
        return None, _rpc_error(request, -32602, "Invalid params: no text part in message")
    return to_payload(text, request), None


def _answer_sync(fn, request, args, kwargs, to_payload, to_text) -> dict:
    payload, error = _prepare(request, to_payload)
    if error:
        return error
    try:
        result = fn(payload, *args, **kwargs)
        if inspect.isgenerator(result):
            result = _join_chunks(list(result))
        return _rpc_message(request, to_text(result))
    except Exception:
        logger.exception("Entrypoint failed on A2A request")
        return _rpc_error(request, -32603, "Internal error")


async def _answer_async(fn, request, args, kwargs, to_payload, to_text) -> dict:
    payload, error = _prepare(request, to_payload)
    if error:
        return error
    try:
        result = fn(payload, *args, **kwargs)
        if inspect.isasyncgen(result):
            result = _join_chunks([chunk async for chunk in result])
        elif inspect.iscoroutine(result):
            result = await result
        return _rpc_message(request, to_text(result))
    except Exception:
        logger.exception("Entrypoint failed on A2A request")
        return _rpc_error(request, -32603, "Internal error")


def _join_chunks(chunks: list) -> str:
    """Join streamed chunks: strings, or Strands stream events with a "data" text delta."""
    parts = []
    for chunk in chunks:
        if isinstance(chunk, str):
            parts.append(chunk)
        elif isinstance(chunk, dict) and isinstance(chunk.get("data"), str):
            parts.append(chunk["data"])
    return "".join(parts)


def _rpc_message(request: dict, text: str) -> dict:
    result = {
        "kind": "message",
        "role": "agent",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text.strip()}],
    }
    context_id = (((request.get("params") or {}).get("message")) or {}).get("contextId")
    if context_id:
        result["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


def _rpc_error(request: dict, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": code, "message": message}}


# ==========================================================================
# Part 2: peer discovery and A2A calls (needs boto3 when deployed)
# ==========================================================================

_registry_client = None
_agentcore_client = None

# record name -> {"name", "card", and "arn" (AgentCore) or "url" (local mode)};
# filled by find_peer_agents
_peers: dict[str, dict] = {}

_RUNTIME_ARN = re.compile(r"arn:aws[\w-]*:bedrock-agentcore:[\w-]+:\d{12}:runtime/[\w-]+")

# errors that mean "this local peer can't be used", not "the search failed"
_PEER_FETCH_ERRORS = (OSError, ValueError, http.client.HTTPException)


def _clients():
    global _registry_client, _agentcore_client
    if _registry_client is None:
        import boto3
        from botocore.config import Config

        config = Config(read_timeout=300, retries={"max_attempts": 3, "mode": "adaptive"})
        _registry_client = boto3.client("agent-registry", region_name=_region(), config=config)
        _agentcore_client = boto3.client("bedrock-agentcore", region_name=_region(), config=config)
    return _registry_client, _agentcore_client


def search_peers(need: str) -> list[dict]:
    """Find other agents for a need. Returns [{"record_name", "agent", "description", "skills"}]."""
    if _local_peer_urls():
        candidates = _local_candidates(need)
    elif _registry_id():
        candidates = _registry_candidates(need)
    else:
        raise RuntimeError("No peer directory configured: set REGISTRY_ID, or LOCAL_PEER_URLS locally.")

    found = []
    for record_name, card, address in candidates:
        if card.get("name") == _agent_name():
            continue  # skip ourselves
        _peers[record_name] = {"name": card.get("name"), "card": card, **address}
        found.append({
            "record_name": record_name,
            "agent": card.get("name"),
            "description": card.get("description"),
            "skills": [s.get("name") for s in card.get("skills", [])],
        })
    return found


def call_peer(record_name: str, question: str) -> str:
    """Ask a peer found by search_peers; the question is sent with this agent's A2A prefix."""
    peer = _peers.get(record_name)
    if not peer:
        raise KeyError(f"Unknown agent '{record_name}'. Search for peers first.")
    logger.info("A2A -> %s: %s", peer["name"], question)
    return _send_a2a(peer, f"{PEER_PREFIX}{_agent_name() or 'an agent'}] {question}")


def is_peer_request(text: str) -> bool:
    """True if this text is a request sent by another agent."""
    return text.startswith(PEER_PREFIX)


def _registry_candidates(need: str) -> list[tuple[str, dict, dict]]:
    """Semantic search in AWS Agent Registry -> (record name, card, address)."""
    registry, _ = _clients()
    response = registry.search_discoverable_registry_records(
        searchQuery=need,
        registryIds=[_registry_id()],
        maxResults=_max_results(),
    )
    candidates = []
    for record in response["registryRecords"]:
        descriptor = record.get("descriptors", {}).get("a2aAgentCard")
        if record.get("recordType") != "AGENT" or not descriptor:
            continue
        try:
            card = json.loads(descriptor["data"])
        except ValueError:
            logger.warning("Registry record %s has an invalid agent card", record.get("name"))
            continue
        arn = _runtime_arn(record, card)
        if not arn:
            logger.warning("No runtime ARN for registry record %s", record.get("name"))
            continue
        candidates.append((record["name"], card, {"arn": arn}))
    return candidates


def _local_candidates(need: str) -> list[tuple[str, dict, dict]]:
    """Local mode: read each peer's agent card, best keyword match first.

    The registry's semantic search is not available locally, so every reachable
    peer is returned and the model picks; the ranking just puts likely ones first.
    """
    words = set(re.findall(r"[a-z]{3,}", need.lower()))
    ranked = []
    for url in _local_peer_urls():
        try:
            with urllib.request.urlopen(url + ".well-known/agent-card.json", timeout=10) as response:
                card = json.loads(response.read())
        except _PEER_FETCH_ERRORS as exc:
            logger.warning("Local peer %s is not usable: %s", url, exc)
            continue
        text = json.dumps([card.get("name"), card.get("description"), card.get("skills")]).lower()
        score = sum(word in text for word in words)
        # same names the registration script gives the registry records
        record_name = re.sub(r"[^a-z0-9]+", "-", card.get("name", url).lower()).strip("-")
        ranked.append((score, record_name, card, {"url": url}))
    ranked.sort(key=lambda r: r[0], reverse=True)
    return [(name, card, address) for _, name, card, address in ranked]


def _runtime_arn(record: dict, card: dict) -> Optional[str]:
    """Recover the AgentCore runtime ARN from the agent card URL.

    AgentCore card URLs look like
    https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded ARN>/invocations/
    """
    for text in (card.get("url", ""), json.dumps(record.get("descriptors", {}))):
        match = _RUNTIME_ARN.search(unquote(text))
        if match:
            return match.group(0)
    return None


def _send_a2a(peer: dict, text: str) -> str:
    """Send one A2A message/send JSON-RPC call to a peer and return its text reply."""
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "messageId": str(uuid.uuid4()),
            }
        },
    }
    if "url" in peer:  # local mode: plain HTTP to the peer's A2A server
        request = urllib.request.Request(
            peer["url"],
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            reply = json.loads(response.read())
    else:
        _, agentcore = _clients()
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=peer["arn"],
            runtimeSessionId=str(uuid.uuid4()),
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(body).encode(),
        )
        reply = json.loads(response["response"].read())
    if "error" in reply:
        raise RuntimeError(reply["error"].get("message", str(reply["error"])))
    return _extract_text(reply.get("result", {}))


def _extract_text(result: dict) -> str:
    """Pull text out of an A2A result, which is either a Message or a Task."""
    parts = list(result.get("parts", []))
    for artifact in result.get("artifacts", []):
        parts.extend(artifact.get("parts", []))
    if not parts:
        parts = result.get("status", {}).get("message", {}).get("parts", [])
    return "\n".join(p["text"] for p in parts if p.get("kind") == "text").strip()


def _current_request(agent) -> str:
    """Text of the user turn that is being handled right now."""
    for message in reversed(agent.messages):
        if message["role"] != "user":
            continue
        texts = [block["text"] for block in message["content"] if "text" in block]
        if texts:
            return "\n".join(texts)
    return ""


# ==========================================================================
# Strands tools (only defined when strands is installed)
# ==========================================================================

try:
    from strands import ToolContext, tool
except ImportError:  # decorator-only use: no peer tools
    PEER_TOOLS: list = []
else:
    @tool
    def find_peer_agents(need: str) -> str:
        """Search the agent directory for other agents that can help with a need.

        Args:
            need: Natural-language description of the expertise required,
                e.g. "investment planning for a creative business".
        """
        try:
            found = search_peers(need)
        except Exception as exc:  # surface failures to the model instead of crashing the turn
            logger.exception("Peer search failed")
            return f"Agent search failed: {exc}"
        if not found:
            return "No matching agents are registered for that need."
        return json.dumps(found, indent=2)

    @tool(context=True)
    def ask_peer_agent(record_name: str, question: str, tool_context: ToolContext) -> str:
        """Ask another agent a question over A2A and return its answer.

        Call find_peer_agents first; use a record_name it returned.

        Args:
            record_name: The registry record name of the agent to ask.
            question: A self-contained question with all the context the peer needs.
        """
        if is_peer_request(_current_request(tool_context.agent)):
            return ("Not allowed: this request came from another agent. "
                    "Answer it yourself without consulting further agents.")
        peer = _peers.get(record_name)
        if not peer:
            return f"Unknown agent '{record_name}'. Call find_peer_agents first."
        try:
            answer = call_peer(record_name, question)
        except Exception as exc:  # surface failures to the model instead of crashing the turn
            logger.exception("A2A call to %s failed", peer["name"])
            return f"{peer['name']} could not be reached: {exc}"
        return f"{peer['name']} says:\n{answer}"

    PEER_TOOLS = [find_peer_agents, ask_peer_agent]


__all__ = [
    "a2a_compatible",
    "default_extract_answer",
    "a2a_answer_from",
    "configure",
    "search_peers",
    "call_peer",
    "is_peer_request",
    "PEER_PREFIX",
    "PEER_TOOLS",
] + (["find_peer_agents", "ask_peer_agent"] if PEER_TOOLS else [])
