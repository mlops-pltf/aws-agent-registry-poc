"""Nutritionist agent: an existing AgentCore entrypoint agent joined to the agent network.

Example of bringing a non-A2A agent into the Agent Registry setup with
migration/a2a_bridge.py, without moving it to serve_a2a. The runtime keeps the
HTTP protocol (POST /invocations on port 8080) and the entrypoint keeps its
{"prompt": "..."} payload. Two additions:

  - @a2a_compatible lets the other agents call it: their A2A "message/send"
    requests become {"prompt": ...} and the answer is wrapped in A2A format.
  - PEER_TOOLS (find_peer_agents, ask_peer_agent) let it call the other agents.

Deploying: copy migration/a2a_bridge.py into this folder (next to agent.py)
so the image contains it. Running from the repo also works without the copy.

Environment:
  REGISTRY_ID      Agent Registry ID or ARN (needed for the peer tools when deployed)
  LOCAL_PEER_URLS  Local A2A agents to call instead of the registry, e.g. http://localhost:9000
  AWS_REGION       Region for Bedrock, the registry and peer runtimes (default us-east-1)
  MODEL_ID         Bedrock model / inference profile ID
"""

import os
import sys
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

try:
    from a2a_bridge import PEER_TOOLS, a2a_compatible, configure
except ImportError:  # running from the repo: use the shared copy in migration/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "migration"))
    from a2a_bridge import PEER_TOOLS, a2a_compatible, configure

AGENT_NAME = "Nutritionist Agent"  # must equal "name" in agent-card.json
REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "anthropic.claude-opus-5-5")
SYSTEM_PROMPT = """You are Nutritionist Agent, a registered nutritionist. You give practical,
evidence-based advice on meals, hydration and energy for people's real jobs and
training. You give general guidance, not medical advice.

# Working with other agents

You are one agent in a network of specialists registered in AWS Agent Registry.
When part of a request is outside nutrition (money, investing, art, design,
fitness training), call find_peer_agents with a plain description of the
expertise you need, then ask_peer_agent with a self-contained question that
includes every number and fact the peer needs. Say which agent contributed.

If a message starts with [A2A request from …], another agent is asking you.
Answer it directly and concisely. Do not consult further agents for it."""

configure(agent_name=AGENT_NAME, region=REGION)

app = BedrockAgentCoreApp()


@app.entrypoint
@a2a_compatible  # lets the other agents call this one
def invoke(payload: dict) -> dict:
    # A fresh Agent per request keeps calls independent; callers send
    # self-contained questions.
    agent = Agent(
        name=AGENT_NAME,
        system_prompt=SYSTEM_PROMPT,
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[*PEER_TOOLS],  # lets this one call the other agents
    )
    # Pass the prompt unchanged: the loop guard looks for the peer prefix at its start.
    return {"result": str(agent(payload.get("prompt", ""))).strip()}


if __name__ == "__main__":
    app.run()
