#!/usr/bin/env python3
"""Send prompts to an agent running locally and print its answer.

Talks A2A JSON-RPC (message/send) to the agent's server on localhost, using
only the Python standard library.

Examples:
  # one question to the investment banker
  python scripts/invoker.py banker "How should I invest $30,000 over 10 years?"

  # interactive chat with the artist; follow-ups stay in the same conversation
  python scripts/invoker.py artist

  # any port, or the raw JSON-RPC reply
  python scripts/invoker.py --port 9002 "Build me a 3-day posture plan"
  python scripts/invoker.py trainer "Quick tip?" --raw
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

# local ports, matching the local: block of each agent.yaml
AGENT_PORTS = {
    "artist": 9000,
    "banker": 9001,
    "investment_banker": 9001,
    "trainer": 9002,
    "fitness_trainer": 9002,
}


class InvokeError(RuntimeError):
    """The agent could not be reached or did not return an answer."""


def send_message(url: str, text: str, context_id: str | None = None, timeout: int = 300) -> dict:
    """Send one A2A message/send request and return the JSON-RPC reply."""
    message = {
        "role": "user",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}],
    }
    if context_id:
        message["contextId"] = context_id  # continue an earlier conversation

    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send", "params": {"message": message}}
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise InvokeError(f"HTTP {exc.code} from {url}: {exc.read().decode(errors='replace')}") from exc
    except urllib.error.URLError as exc:
        raise InvokeError(f"Cannot reach {url}: {exc.reason}. Is the agent running?") from exc
    except TimeoutError as exc:
        raise InvokeError(f"No answer from {url} within {timeout}s") from exc


def extract_answer(reply: dict) -> tuple[str, str | None]:
    """Return (answer text, contextId) from a message/send reply."""
    if "error" in reply:
        error = reply["error"]
        raise InvokeError(f"A2A error {error.get('code')}: {error.get('message')}")

    result = reply["result"]
    status = result.get("status", {})
    if status.get("state") in ("failed", "rejected", "canceled"):
        reason = _text(status.get("message", {}).get("parts", []))
        raise InvokeError(f"Task {status['state']}: {reason or 'no reason given'}")

    # A Task carries the answer in artifacts; a plain Message carries it in parts
    parts = [p for artifact in result.get("artifacts", []) for p in artifact.get("parts", [])]
    if not parts:
        parts = result.get("parts", []) or status.get("message", {}).get("parts", [])
    return _text(parts), result.get("contextId")


def _text(parts: list[dict]) -> str:
    return "\n".join(p["text"] for p in parts if p.get("kind") == "text").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("words", nargs="*", metavar="[AGENT] [PROMPT]",
                        help=f"Agent ({', '.join(sorted(AGENT_PORTS))}) and/or the question; "
                             "omit the question for interactive chat")
    parser.add_argument("--port", type=int, help="Local port of the agent (instead of an agent name)")
    parser.add_argument("--url", help="Full A2A URL, e.g. http://localhost:9001/")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait for an answer (default 300)")
    parser.add_argument("--raw", action="store_true", help="Print the full JSON-RPC reply instead of the text")
    args = parser.parse_args()

    words = list(args.words)
    agent = words.pop(0) if words and words[0] in AGENT_PORTS else None
    args.prompt = " ".join(words) or None

    if args.url:
        args.target = args.url
    elif args.port:
        args.target = f"http://localhost:{args.port}/"
    elif agent:
        args.target = f"http://localhost:{AGENT_PORTS[agent]}/"
    else:
        parser.error("name an agent (artist, banker, trainer) or pass --port / --url")
    return args


def ask(url: str, prompt: str, context_id: str | None, timeout: int, raw: bool) -> str | None:
    reply = send_message(url, prompt, context_id, timeout)
    if raw:
        print(json.dumps(reply, indent=2))
        return reply.get("result", {}).get("contextId", context_id)
    answer, context_id = extract_answer(reply)
    print(answer or "(empty answer)")
    return context_id


def main() -> int:
    args = parse_args()
    try:
        if args.prompt:
            ask(args.target, args.prompt, None, args.timeout, args.raw)
            return 0

        print(f"Chatting with {args.target}  (empty line or Ctrl-D to quit)")
        context_id = None
        while True:
            try:
                prompt = input("\nyou> ").strip()
            except EOFError:
                print()
                return 0
            if not prompt:
                return 0
            print("agent> ", end="", flush=True)
            context_id = ask(args.target, prompt, context_id, args.timeout, args.raw)
    except InvokeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
