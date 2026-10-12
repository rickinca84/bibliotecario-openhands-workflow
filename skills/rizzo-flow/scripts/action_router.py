#!/usr/bin/env python3
"""Rizzo Flow action router.

Rizzo Flow does not execute arbitrary tools itself: it chooses one item from a finite
set. This module turns that typed choice into an exact candidate tool call and, when
an executor callback is supplied, dispatches only that pre-authorized call.

Default CLI behaviour is shadow mode: select and print, never execute.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rizzo_client import call


DEFAULT_URL = os.environ.get("RIZZO_URL", "http://127.0.0.1:8017").rstrip("/")


class RouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    id: str
    tool: str
    arguments: dict[str, Any]
    description: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Candidate":
        if not isinstance(raw, dict):
            raise RouterError("candidate must be an object")
        cid = raw.get("id")
        tool = raw.get("tool")
        arguments = raw.get("arguments", {})
        description = raw.get("description")
        if not isinstance(cid, str) or not cid:
            raise RouterError("candidate.id must be a non-empty string")
        if not isinstance(tool, str) or not tool:
            raise RouterError(f"candidate {cid}: tool must be a non-empty string")
        if not isinstance(arguments, dict):
            raise RouterError(f"candidate {cid}: arguments must be an object")
        if not isinstance(description, str) or not description.strip():
            raise RouterError(f"candidate {cid}: description must be non-empty")
        return cls(cid, tool, arguments, description.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "arguments": self.arguments,
            "description": self.description,
        }


def normalize_candidates(raw_candidates: Iterable[dict[str, Any]]) -> list[Candidate]:
    candidates = [Candidate.from_dict(raw) for raw in raw_candidates]
    if len(candidates) < 2:
        raise RouterError("Rizzo choice requires at least 2 candidates")
    if len(candidates) > 26:
        raise RouterError("Rizzo choice supports at most 26 candidates per decision")
    ids = [candidate.id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise RouterError("candidate ids must be unique")
    return candidates


def build_request(
    state: Any,
    candidates: list[Candidate],
    *,
    instructions: str = "Which permitted action should be taken next according to the evidence?",
    min_top_probability: float = 0.0,
) -> dict[str, Any]:
    if not 0.0 <= min_top_probability <= 1.0:
        raise RouterError("min_top_probability must be between 0 and 1")
    return {
        "state": state,
        "questions": {
            "next_action": {
                "type": "choice",
                "instructions": instructions,
                "options": [
                    {"id": candidate.id, "description": candidate.description}
                    for candidate in candidates
                ],
                "policy": {
                    "allow_abstain": True,
                    "min_top_probability": min_top_probability,
                },
            }
        },
    }


def choose_action(
    state: Any,
    raw_candidates: Iterable[dict[str, Any]],
    *,
    url: str = DEFAULT_URL,
    api_key: str | None = None,
    timeout: int = 120,
    instructions: str = "Which permitted action should be taken next according to the evidence?",
    min_top_probability: float = 0.0,
    caller: Callable[..., dict[str, Any]] = call,
) -> dict[str, Any]:
    candidates = normalize_candidates(raw_candidates)
    request = build_request(
        state,
        candidates,
        instructions=instructions,
        min_top_probability=min_top_probability,
    )
    response = caller(
        url.rstrip("/") + "/v1/decisions",
        request,
        api_key,
        timeout,
    )
    try:
        answer = response["answers"]["next_action"]
    except (KeyError, TypeError) as exc:
        raise RouterError("malformed Rizzo response") from exc

    status = answer.get("status")
    choice = answer.get("choice")
    if status != "ok" or not choice:
        return {
            "status": "escalate",
            "reason": status or "missing_choice",
            "rizzo": answer,
            "selected": None,
        }

    by_id = {candidate.id: candidate for candidate in candidates}
    selected = by_id.get(choice)
    if selected is None:
        raise RouterError(f"Rizzo returned unknown candidate id: {choice}")

    return {
        "status": "selected",
        "reason": "rizzo_choice",
        "rizzo": answer,
        "selected": selected.as_dict(),
    }


def dispatch(
    decision: dict[str, Any],
    executor: Callable[[str, dict[str, Any]], Any],
) -> dict[str, Any]:
    if decision.get("status") != "selected" or not decision.get("selected"):
        return {
            "status": "not_executed",
            "reason": decision.get("reason", "no_selection"),
            "decision": decision,
        }
    selected = decision["selected"]
    result = executor(selected["tool"], selected["arguments"])
    return {
        "status": "executed",
        "selected": selected,
        "result": result,
        "rizzo": decision.get("rizzo"),
    }


def _load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request", help="JSON file containing state + candidates, or - for stdin")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--min-top-probability", type=float, default=0.0)
    parser.add_argument("--raw", action="store_true", help="print the complete decision JSON")
    args = parser.parse_args()

    payload = _load_json(args.request)
    if not isinstance(payload, dict):
        raise SystemExit("request must be a JSON object")
    if "state" not in payload or "candidates" not in payload:
        raise SystemExit("request requires state and candidates")

    decision = choose_action(
        payload["state"],
        payload["candidates"],
        url=args.url,
        api_key=os.environ.get("RIZZO_API_KEY"),
        min_top_probability=args.min_top_probability,
        instructions=payload.get(
            "instructions",
            "Which permitted action should be taken next according to the evidence?",
        ),
    )

    if args.raw:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        selected = decision.get("selected")
        if selected:
            print(
                json.dumps(
                    {
                        "status": decision["status"],
                        "id": selected["id"],
                        "tool": selected["tool"],
                        "arguments": selected["arguments"],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(decision, ensure_ascii=False))


if __name__ == "__main__":
    main()
