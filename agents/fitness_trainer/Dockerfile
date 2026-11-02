# AgentCore Runtime requires linux/arm64 images.
FROM --platform=linux/arm64 python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-east-1

RUN useradd -m -u 1000 agent
WORKDIR /app

# Dependencies first so this layer is cached when only the code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=agent:agent agent.py SYSTEMPROMPT.md ./
USER agent

# A2A protocol: AgentCore routes to 0.0.0.0:9000/
EXPOSE 9000

CMD ["opentelemetry-instrument", "python", "agent.py"]
