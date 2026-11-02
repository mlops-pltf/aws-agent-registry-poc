# AgentCore Runtime requires linux/arm64 images.
FROM --platform=linux/arm64 python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-east-1

RUN pip install \
    "strands-agents~=1.57.2" \
    "bedrock-agentcore[a2a,strands-agents]~=1.24.0" \
    "a2a-sdk[all]~=0.3.26" \
    "boto3>=1.43.107" \
    "aws-opentelemetry-distro~=0.21.0"

RUN useradd -m -u 1000 agent
WORKDIR /app
COPY --chown=agent:agent agent.py SYSTEMPROMPT.md ./
USER agent

# A2A protocol: AgentCore routes to 0.0.0.0:9000/
EXPOSE 9000

CMD ["opentelemetry-instrument", "python", "agent.py"]
