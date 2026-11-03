"""Nutritionist agent: a normal AgentCore entrypoint that our A2A agents can also call.

Example of bringing an existing non-A2A agent into the Agent Registry setup
without moving it to serve_a2a. The runtime keeps the HTTP protocol
(POST /invocations on port 8080). The entrypoint also understands the one A2A
call our agents make, JSON-RPC "message/send", and answers in A2A format, so
it can be registered with an A2A card and found and called like the others.

Supported:
  - A2A "message/send" (what ask_peer_agent sends); other A2A methods get a
    JSON-RPC "method not found" error
  - the agent's existing {"prompt": "..."} payload, unchanged

Not supported (use serve_a2a for these): streaming, tasks/get, serving
/.well-known/agent-card.json, and our local mode (LOCAL_PEER_URLS), which
posts to "/" rather than "/invocations".

Environment:
  AWS_REGION  Region for Bedrock (default us-east-1)
  MODEL_ID    Bedrock model / inference profile ID
"""

import logging
import os
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

AGENT_NAME = "Nutritionist Agent"
REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "anthropic.claude-opus-5-5")
SYSTEM_PROMPT = (
    "You are Nutritionist Agent, a registered nutritionist. You give practical, "
    "evidence-based advice on meals, hydration and energy for people's real jobs "
    "and training. You give general guidance, not medical advice. If a message "
    "starts with [A2A request from …], another agent is asking: answer it "
    "directly and concisely."
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(AGENT_NAME)

app = BedrockAgentCoreApp()


def run_agent(prompt: str) -> str:
    """Run the agent on one prompt.

    A fresh Agent per request keeps calls independent; callers send
    self-contained questions.
    """
    agent = Agent(
        name=AGENT_NAME,
        system_prompt=SYSTEM_PROMPT,
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
    )
    return str(agent(prompt)).strip()


def handle_a2a(request: dict) -> dict:
    """Answer an A2A JSON-RPC request with a JSON-RPC response."""
    request_id = request.get("id")
    if request.get("method") != "message/send":
        return _a2a_error(request_id, -32601, "Method not found")

    message = request.get("params", {}).get("message", {})
    text = "\n".join(p["text"] for p in message.get("parts", []) if p.get("kind") == "text").strip()
    if not text:
        return _a2a_error(request_id, -32602, "Invalid params: no text part in message")

    try:
        answer = run_agent(text)
    except Exception:
        logger.exception("Agent failed on A2A request")
        return _a2a_error(request_id, -32603, "Internal error")

    # An A2A Message result; our agents read result.parts[].text
    result = {
        "kind": "message",
        "role": "agent",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": answer}],
    }
    if message.get("contextId"):
        result["contextId"] = message["contextId"]
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _a2a_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@app.entrypoint
def invoke(payload: dict) -> dict:
    if payload.get("jsonrpc") == "2.0":  # A2A caller, e.g. ask_peer_agent in our agents
        return handle_a2a(payload)
    return {"result": run_agent(payload.get("prompt", ""))}  # existing callers, unchanged


if __name__ == "__main__":
    app.run()
