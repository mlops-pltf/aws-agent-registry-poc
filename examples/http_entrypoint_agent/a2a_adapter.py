"""Decorator that makes an existing AgentCore entrypoint answer A2A calls.

Drop this file next to your agent and add one line:

    from a2a_adapter import a2a_compatible

    @app.entrypoint
    @a2a_compatible                 # below @app.entrypoint
    def invoke(payload, context):   # unchanged; still receives {"prompt": "..."}
        ...

For an A2A JSON-RPC "message/send" request (what our agents' ask_peer_agent
sends), the decorator:
  1. joins the text parts of params.message into one string,
  2. calls your function with {"prompt": <text>},
  3. turns whatever it returns into an A2A Message result.
Any other payload goes to your function untouched, so existing callers,
including streaming ones, work exactly as before.

Works with sync and async entrypoints, with or without the `context`
argument, and with ones that stream (sync or async generators): for A2A
calls the streamed chunks are collected into one answer.

Options:
    @a2a_compatible(prompt_key="input")              # your payload key isn't "prompt"
    @a2a_compatible(build_payload=lambda text, request: {"prompt": text, "user": "a2a"})
    @a2a_compatible(extract_answer=lambda result: result["output"]["text"])

Only "message/send" is supported; other A2A methods get a JSON-RPC
"method not found" error. Use serve_a2a for full A2A (streaming, tasks,
an agent card served from the runtime).
"""

import functools
import inspect
import json
import logging
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# dict keys tried, in order, when your entrypoint returns a dict
ANSWER_KEYS = ("result", "response", "output", "answer", "text", "message", "content")


def a2a_compatible(
    func: Optional[Callable] = None,
    *,
    prompt_key: str = "prompt",
    build_payload: Optional[Callable[[str, dict], Any]] = None,
    extract_answer: Optional[Callable[[Any], str]] = None,
) -> Callable:
    """Make an AgentCore entrypoint answer A2A "message/send" requests.

    Usable bare (@a2a_compatible) or with options (@a2a_compatible(prompt_key="input")).
    """
    to_payload = build_payload or (lambda text, request: {prompt_key: text})
    to_text = extract_answer or default_extract_answer

    def decorate(fn: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)

        if is_async:
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


def _is_a2a(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("jsonrpc") == "2.0" and "method" in payload


def _prepare(request: dict, to_payload: Callable) -> tuple[Any, Optional[dict]]:
    """Return (payload for your entrypoint, None) or (None, JSON-RPC error)."""
    if request.get("method") != "message/send":
        return None, _error(request, -32601, "Method not found")
    message = (request.get("params") or {}).get("message") or {}
    text = "\n".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind") == "text").strip()
    if not text:
        return None, _error(request, -32602, "Invalid params: no text part in message")
    return to_payload(text, request), None


def _answer_sync(fn, request, args, kwargs, to_payload, to_text) -> dict:
    payload, error = _prepare(request, to_payload)
    if error:
        return error
    try:
        result = fn(payload, *args, **kwargs)
        if inspect.isgenerator(result):
            result = _join_chunks(list(result))
        return _message(request, to_text(result))
    except Exception:
        logger.exception("Entrypoint failed on A2A request")
        return _error(request, -32603, "Internal error")


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
        return _message(request, to_text(result))
    except Exception:
        logger.exception("Entrypoint failed on A2A request")
        return _error(request, -32603, "Internal error")


def _join_chunks(chunks: list) -> str:
    """Join streamed chunks: strings, or Strands stream events with a "data" text delta."""
    parts = []
    for chunk in chunks:
        if isinstance(chunk, str):
            parts.append(chunk)
        elif isinstance(chunk, dict) and isinstance(chunk.get("data"), str):
            parts.append(chunk["data"])
    return "".join(parts)


def _message(request: dict, text: str) -> dict:
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


def _error(request: dict, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": code, "message": message}}
