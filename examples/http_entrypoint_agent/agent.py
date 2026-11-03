"""Nutritionist agent: a normal AgentCore entrypoint that our A2A agents can also call.

Example of bringing an existing non-A2A agent into the Agent Registry setup
without moving it to serve_a2a. The runtime keeps the HTTP protocol
(POST /invocations on port 8080) and the entrypoint keeps its
{"prompt": "..."} payload. The only change is the @a2a_compatible decorator
(a2a_adapter.py), which converts the A2A "message/send" requests our agents
send into {"prompt": ...} and wraps the answer in A2A format.

Not supported (use serve_a2a for these): streaming over A2A, tasks/get,
serving /.well-known/agent-card.json, and our local mode (LOCAL_PEER_URLS),
which posts to "/" rather than "/invocations".

Environment:
  AWS_REGION  Region for Bedrock (default us-east-1)
  MODEL_ID    Bedrock model / inference profile ID
"""

import os

from a2a_adapter import a2a_compatible
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

app = BedrockAgentCoreApp()


@app.entrypoint
@a2a_compatible  # the only line added to an existing entrypoint
def invoke(payload: dict) -> dict:
    # A fresh Agent per request keeps calls independent; callers send
    # self-contained questions.
    agent = Agent(
        name=AGENT_NAME,
        system_prompt=SYSTEM_PROMPT,
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
    )
    return {"result": str(agent(payload.get("prompt", ""))).strip()}


if __name__ == "__main__":
    app.run()
