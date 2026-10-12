#!/usr/bin/env python3
"""Experimental top-level Rizzo orchestrator for OpenHands.

Rizzo chooses a complete pre-authorized candidate call. This agent emits that choice
as a normal OpenHands ActionEvent; it NEVER executes tools directly. Therefore the
existing OpenHands pending-action path, PreToolUse/PostToolUse hooks, confirmation
policy, security handling, logging, and deterministic Bibliotecario gates remain in
control of execution.
"""

from __future__ import annotations

import json
import pathlib
import sys
import uuid
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from action_router import choose_action
from candidate_catalog import build_candidates, validate_candidate_set

from openhands.sdk.agent.agent import Agent
from openhands.sdk.event.llm_convertible import ActionEvent, MessageEvent
from openhands.sdk.llm import MessageToolCall, TextContent, content_to_str


class RizzoAgent(Agent):
    """Agent whose next-action selection is delegated to Rizzo Flow.

    The class intentionally reuses Agent's native execution path. Only _step() is
    replaced: it emits one native ActionEvent instead of asking an LLM to generate
    the tool call.
    """

    def _latest_human_request(self, conversation) -> str | None:
        for event in reversed(conversation.state.active_branch()):
            if isinstance(event, MessageEvent) and event.source == "user":
                parts = content_to_str(event.llm_message.content)
                text = "".join(parts).strip()
                if text:
                    return text
        return None

    @staticmethod
    def _read_json(path: pathlib.Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _snapshot(self, conversation) -> dict[str, Any]:
        root = pathlib.Path(conversation.workspace.working_dir)
        tmp = root / ".agents_tmp"

        contract_path = tmp / "CONTRACT.json"
        work_item_path = tmp / "CURRENT_WORK_ITEM.json"
        validation_path = tmp / "VALIDATION.json"
        state_path = tmp / "STATE.json"

        validation = self._read_json(validation_path)
        deterministic_state = self._read_json(state_path)

        snapshot: dict[str, Any] = {
            "human_request": self._latest_human_request(conversation),
            "has_contract": contract_path.is_file(),
            "has_current_work_item": work_item_path.is_file(),
            "has_validation": validation_path.is_file(),
            "validation": validation,
            "terminal_failure": (tmp / "TERMINAL_FAILURE.json").is_file(),
            "retry_allowed": bool(deterministic_state.get("retry_allowed", False)),
            "needs_replan": bool(deterministic_state.get("needs_replan", False)),
            "routing_uncertain": False,
        }

        # Optional prepared actions are machine-produced complete calls. Rizzo is
        # allowed to choose them but never to invent their arguments.
        prepared_path = tmp / "RIZZO_PREPARED_CALLS.json"
        prepared = self._read_json(prepared_path)
        calls = prepared.get("calls")
        if isinstance(calls, list):
            snapshot["prepared_calls"] = calls

        return snapshot

    @staticmethod
    def _pick_escalation(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        for cid in ("escalate_qwen", "bootstrap_qwen_contract"):
            for candidate in candidates:
                if candidate.get("id") == cid:
                    return candidate
        return None

    def _select_candidate(
        self,
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        decision = choose_action(state, candidates)
        if decision.get("status") == "selected":
            return decision["selected"]

        # Rizzo abstention/uncertainty never becomes an arbitrary action.
        # Escalate only if the deterministic catalog already exposed Qwen.
        return self._pick_escalation(candidates)

    def _emit_native_action(
        self,
        candidate: dict[str, Any],
        on_event,
    ) -> None:
        tool_name = candidate["tool"]
        tool = self.tools_map.get(tool_name)
        if tool is None:
            raise KeyError(f"Rizzo selected unavailable OpenHands tool: {tool_name}")

        arguments = candidate["arguments"]
        action = tool.action_type.model_validate(arguments)

        tool_call_id = "rizzo_" + uuid.uuid4().hex
        response_id = "rizzo_response_" + uuid.uuid4().hex
        tool_call = MessageToolCall(
            id=tool_call_id,
            name=tool_name,
            arguments=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
            origin="completion",
        )

        action_event = ActionEvent(
            source="agent",
            thought=[TextContent(text=f"Rizzo selected permitted action: {candidate['id']}")],
            action=action,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_call=tool_call,
            llm_response_id=response_id,
            summary=candidate.get("description"),
        )

        # Critical boundary: only emit. OpenHands then owns normal hook/security/tool
        # execution on the following pending-action cycle.
        on_event(action_event)

    def _step(self, conversation, on_event, stream) -> None:
        # Preserve native pending-action execution. A Rizzo-emitted ActionEvent must be
        # executed by Agent._execute_actions, never by a direct executor call here.
        pending = conversation.state.get_unmatched_actions(
            conversation.state.active_branch()
        )
        if pending:
            self._execute_actions(conversation, pending, on_event)
            return

        snapshot = self._snapshot(conversation)
        candidates = build_candidates(snapshot, self.tools_map.keys())

        # If there is no meaningful action, expose semantic replanning only when task is
        # actually available. This still passes through the same deterministic catalog.
        if not candidates and "task" in self.tools_map:
            snapshot["routing_uncertain"] = True
            candidates = build_candidates(snapshot, self.tools_map.keys())

        validate_candidate_set(candidates)
        selected = self._select_candidate(snapshot, candidates)
        if selected is None:
            raise RuntimeError(
                "Rizzo orchestrator has no safe executable candidate; refusing to invent one"
            )

        self._emit_native_action(selected, on_event)


__all__ = ["RizzoAgent"]
