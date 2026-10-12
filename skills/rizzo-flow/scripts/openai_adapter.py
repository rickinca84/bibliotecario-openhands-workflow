#!/usr/bin/env python3
"""Tiny OpenAI-compatible HTTP adapter that makes Rizzo the parent selector.

Point a normal OpenHands LLM profile at this server. OpenHands still owns the
Agent, tool schemas, ActionEvents, hooks, confirmation/security and execution.
This process only returns one Chat Completions tool_call per turn.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from transcript_router import RoutingError, choose_next


MODEL_ID = os.environ.get("RIZZO_OPENAI_MODEL", "rizzo-bibliotecario-router")
MAX_BODY_BYTES = int(os.environ.get("RIZZO_OPENAI_MAX_BODY_BYTES", str(2 * 1024 * 1024)))


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def is_native_validation_probe(payload: dict[str, Any]) -> bool:
    """Recognize OpenHands' own /profiles/{name}/validate one-token probe only."""
    messages = payload.get("messages")
    tools = payload.get("tools")
    if tools not in (None, []):
        return False
    if payload.get("max_tokens") != 1 or not isinstance(messages, list) or len(messages) != 2:
        return False
    system, user = messages
    if not isinstance(system, dict) or not isinstance(user, dict):
        return False
    return (
        system.get("role") == "system"
        and _message_text(system).strip() == "Reply with one token."
        and user.get("role") == "user"
        and _message_text(user).strip() == "ping"
    )


def _probe_completion(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-rizzo-probe-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.get("model") or MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1},
        "x_rizzo": {"route_source": "openhands_profile_validation_probe"},
    }


def completion_for(payload: dict[str, Any], *, chooser=None) -> dict[str, Any]:
    messages = payload.get("messages")
    tools = payload.get("tools")
    if not isinstance(messages, list):
        raise RoutingError("messages must be a list")
    if is_native_validation_probe(payload):
        return _probe_completion(payload)
    if not isinstance(tools, list) or not tools:
        raise RoutingError(
            "real router turns require native OpenHands tools; refusing tool-less completion"
        )

    kwargs = {} if chooser is None else {"chooser": chooser}
    candidate, state, route_source = choose_next(messages, tools, **kwargs)

    call_id = "call_rizzo_" + uuid.uuid4().hex
    return {
        "id": "chatcmpl-rizzo-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.get("model") or MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": candidate.tool,
                                "arguments": json.dumps(
                                    candidate.arguments,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "x_rizzo": {
            "route_source": route_source,
            "candidate_id": candidate.id,
            "last_subagent": state.get("last_subagent"),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "RizzoOpenAIAdapter/0.1"

    def _json(self, status: int, body: Any) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise RoutingError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise RoutingError("request body is empty or exceeds adapter limit")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise RoutingError("request body must be a JSON object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/healthz"}:
            self._json(HTTPStatus.OK, {"status": "ready", "model": MODEL_ID})
            return
        if self.path in {"/v1/models", "/models"}:
            self._json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_ID,
                            "object": "model",
                            "created": 0,
                            "owned_by": "local-rizzo",
                        }
                    ],
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/v1/chat/completions", "/chat/completions"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})
            return
        try:
            payload = self._read_json()
            if payload.get("stream") is True:
                raise RoutingError(
                    "streaming is intentionally disabled for the Rizzo router profile"
                )
            result = completion_for(payload)
        except (RoutingError, json.JSONDecodeError, ValueError) as exc:
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": {"message": str(exc), "type": "rizzo_router_error"}},
            )
            return
        self._json(HTTPStatus.OK, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[rizzo-openai] " + (fmt % args) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("RIZZO_OPENAI_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("RIZZO_OPENAI_PORT", "18019")),
    )
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(
        json.dumps(
            {
                "status": "ready",
                "model": MODEL_ID,
                "listen": f"http://{args.host}:{args.port}",
            }
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
