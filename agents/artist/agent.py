"""Artist agent — AgentCore Runtime (A2A protocol) entrypoint.

Serves a Strands agent over A2A on port 9000. The agent discovers peer agents
(investment banker, fitness trainer, ...) at runtime through AWS Agent Registry
and calls them via AgentCore InvokeAgentRuntime. Nothing about the peers is
hard-coded: add a new approved AGENT record to the registry and this agent can
find and use it.

Environment:
  REGISTRY_ID      Agent Registry ID or ARN to search (required unless LOCAL_PEER_URLS is set)
  AWS_REGION       Region of the registry and runtimes (default us-east-1)
  MODEL_ID         Bedrock model / inference profile ID
  A2A_PORT         Port to serve on (default 9000, which AgentCore requires)

Local mode (all agents on one machine; no registry or AgentCore needed):
  LOCAL_PEER_URLS  Comma-separated base URLs of the other agents, e.g.
                   http://localhost:9001,http://localhost:9002. Peers are then
                   discovered from their /.well-known/agent-card.json and called
                   over plain HTTP. The model still runs on Bedrock.
"""

import json
import logging
import os
import re
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import unquote

import boto3
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from botocore.config import Config
from strands import Agent, ToolContext, tool
from strands.models import BedrockModel
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from bedrock_agentcore.runtime import serve_a2a

AGENT_NAME = "Artist Agent"
AGENT_DESCRIPTION = (
    "Visual artist and creative director. Designs artwork concepts, murals, "
    "brand visuals and exhibitions, and prices commissioned artwork."
)
REGION = os.environ.get("AWS_REGION", "us-east-1")
REGISTRY_ID = os.environ.get("REGISTRY_ID")
LOCAL_PEER_URLS = [u.strip().rstrip("/") + "/" for u in os.environ.get("LOCAL_PEER_URLS", "").split(",") if u.strip()]
if not REGISTRY_ID and not LOCAL_PEER_URLS:
    raise SystemExit("Set REGISTRY_ID, or LOCAL_PEER_URLS to run in local mode.")
MODEL_ID = os.environ.get("MODEL_ID", "anthropic.claude-opus-5-5")
SYSTEM_PROMPT = (Path(__file__).parent / "SYSTEMPROMPT.md").read_text()

# Messages an agent sends to a peer start with this prefix. A peer that sees it
# answers directly instead of calling further agents, which prevents A -> B -> A loops.
PEER_PREFIX = "[A2A request from "

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(AGENT_NAME)

_boto_config = Config(read_timeout=300, retries={"max_attempts": 3, "mode": "adaptive"})
registry = boto3.client("agent-registry", region_name=REGION, config=_boto_config)
agentcore = boto3.client("bedrock-agentcore", region_name=REGION, config=_boto_config)

# record name -> {"name", "card", and "arn" (AgentCore) or "url" (local mode)};
# filled by find_peer_agents
_peers: dict[str, dict] = {}

_RUNTIME_ARN = re.compile(r"arn:aws[\w-]*:bedrock-agentcore:[\w-]+:\d{12}:runtime/[\w-]+")


# --------------------------------------------------------------------------
# Agent Registry discovery + A2A invocation
# --------------------------------------------------------------------------

def _runtime_arn(record: dict, card: dict) -> str | None:
    """Recover the AgentCore runtime ARN from the agent card URL.

    AgentCore A2A card URLs look like
    https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded ARN>/invocations/
    """
    for text in (unquote(card.get("url", "")), json.dumps(record.get("descriptors", {}))):
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


def _current_request(agent: Agent) -> str:
    """Text of the user turn that is being handled right now."""
    for message in reversed(agent.messages):
        if message["role"] != "user":
            continue
        texts = [block["text"] for block in message["content"] if "text" in block]
        if texts:
            return "\n".join(texts)
    return ""


def _registry_candidates(need: str) -> list[tuple[str, dict, dict]]:
    """Semantic search in AWS Agent Registry -> (record name, card, address)."""
    response = registry.search_discoverable_registry_records(
        searchQuery=need,
        registryIds=[REGISTRY_ID],
        maxResults=5,
    )
    candidates = []
    for record in response["registryRecords"]:
        descriptor = record.get("descriptors", {}).get("a2aAgentCard")
        if record.get("recordType") != "AGENT" or not descriptor:
            continue
        card = json.loads(descriptor["data"])
        arn = _runtime_arn(record, card)
        if not arn:
            logger.warning("No runtime ARN for registry record %s", record["name"])
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
    for url in LOCAL_PEER_URLS:
        try:
            with urllib.request.urlopen(url + ".well-known/agent-card.json", timeout=10) as response:
                card = json.loads(response.read())
        except OSError as exc:
            logger.warning("Local peer %s is not reachable: %s", url, exc)
            continue
        text = json.dumps([card.get("name"), card.get("description"), card.get("skills")]).lower()
        score = sum(word in text for word in words)
        # same names the registration script gives the registry records
        record_name = re.sub(r"[^a-z0-9]+", "-", card.get("name", url).lower()).strip("-")
        ranked.append((score, record_name, card, {"url": url}))
    ranked.sort(key=lambda r: r[0], reverse=True)
    return [(name, card, address) for _, name, card, address in ranked]


@tool
def find_peer_agents(need: str) -> str:
    """Search the agent directory for other agents that can help with a need.

    Args:
        need: Natural-language description of the expertise required,
            e.g. "investment planning for a creative business".
    """
    candidates = _local_candidates(need) if LOCAL_PEER_URLS else _registry_candidates(need)
    found = []
    for record_name, card, address in candidates:
        if card.get("name") == AGENT_NAME:
            continue
        _peers[record_name] = {"name": card.get("name"), "card": card, **address}
        found.append({
            "record_name": record_name,
            "agent": card.get("name"),
            "description": card.get("description"),
            "skills": [s.get("name") for s in card.get("skills", [])],
        })
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
    if _current_request(tool_context.agent).startswith(PEER_PREFIX):
        return ("Not allowed: this request came from another agent. "
                "Answer it yourself without consulting further agents.")
    peer = _peers.get(record_name)
    if not peer:
        return f"Unknown agent '{record_name}'. Call find_peer_agents first."
    logger.info("A2A -> %s: %s", peer["name"], question)
    try:
        answer = _send_a2a(peer, f"{PEER_PREFIX}{AGENT_NAME}] {question}")
    except Exception as exc:  # surface failures to the model instead of crashing the turn
        logger.exception("A2A call to %s failed", peer["name"])
        return f"{peer['name']} could not be reached: {exc}"
    return f"{peer['name']} says:\n{answer}"


# --------------------------------------------------------------------------
# Domain tools
# --------------------------------------------------------------------------

_MEDIUM_RATE = {  # USD per square foot, before tier multiplier
    "digital": 15, "watercolor": 40, "acrylic": 55, "oil": 80, "mural": 25, "sculpture": 120,
}
_TIER_MULTIPLIER = {"emerging": 1.0, "established": 2.0, "renowned": 4.0}


@tool
def price_artwork(medium: str, size_sq_ft: float, hours: float, artist_tier: str = "emerging") -> dict:
    """Estimate a fair commission price for a piece of artwork.

    Args:
        medium: One of digital, watercolor, acrylic, oil, mural, sculpture.
        size_sq_ft: Size of the piece in square feet.
        hours: Estimated hours of work.
        artist_tier: emerging, established, or renowned.
    """
    rate = _MEDIUM_RATE.get(medium.lower(), 50)
    multiplier = _TIER_MULTIPLIER.get(artist_tier.lower(), 1.0)
    materials_and_size = rate * size_sq_ft
    labour = 45 * hours
    price = round((materials_and_size + labour) * multiplier, -1)
    return {
        "medium": medium,
        "estimated_price_usd": price,
        "range_usd": [round(price * 0.8, -1), round(price * 1.25, -1)],
        "breakdown": {"size_and_materials": materials_and_size, "labour": labour,
                      "tier_multiplier": multiplier},
    }


# --------------------------------------------------------------------------
# A2A server
# --------------------------------------------------------------------------

def create_agent(_context_id: str) -> Agent:
    return Agent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        system_prompt=SYSTEM_PROMPT,
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[price_artwork, find_peer_agents, ask_peer_agent],
    )


agent_card = AgentCard(
    name=AGENT_NAME,
    description=AGENT_DESCRIPTION,
    url="http://localhost:9000/",  # replaced with AGENTCORE_RUNTIME_URL by the SDK when deployed
    version="1.0.0",
    protocol_version="0.3.0",
    capabilities=AgentCapabilities(streaming=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="art-concept",
            name="Artwork and visual concept design",
            description="Designs murals, exhibitions, brand visuals and artwork concepts.",
            tags=["art", "design", "creative", "branding", "mural", "exhibition"],
        ),
        AgentSkill(
            id="art-pricing",
            name="Artwork pricing",
            description="Estimates commission prices for artwork by medium, size and effort.",
            tags=["art", "pricing", "commission"],
        ),
    ],
)

if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent_factory=create_agent), agent_card=agent_card)
