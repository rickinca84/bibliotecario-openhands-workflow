#!/usr/bin/env python3
"""Transcript-to-tool-call router for the installable Rizzo parent.

This module is deliberately independent from OpenHands internals. It consumes the
OpenAI-compatible messages/tools that a normal OpenHands Agent sends to its LLM,
derives a small structured workflow state, and returns exactly one native tool call.

Deterministic workflow directives win. Rizzo is consulted only when more than one
semantically legal next action remains.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


RIZZO_URL = os.environ.get("RIZZO_URL", "http://127.0.0.1:8017").rstrip("/")
RIZZO_API_KEY = os.environ.get("RIZZO_API_KEY")
RIZZO_MIN_TOP_PROBABILITY = float(os.environ.get("RIZZO_ROUTER_MIN_TOP_PROBABILITY", "0.70"))


class RoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteCandidate:
    id: str
    tool: str
    arguments: dict[str, Any]
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "arguments": self.arguments,
            "description": self.description,
        }


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else json.dumps(content, ensure_ascii=False)
    return ""


def _tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    return names


def _assistant_task_calls(messages: list[dict[str, Any]]) -> list[str]:
    calls: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            if fn.get("name") != "task":
                continue
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            if isinstance(args, dict) and isinstance(args.get("subagent_type"), str):
                calls.append(args["subagent_type"])
    return calls


def _latest_user_request(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _content_text(message.get("content")).strip()
            if text:
                # UserPromptSubmit may append orchestration context to the same message.
                return re.split(
                    r"\n+BIBLIOTECARIO v[0-9.]+ ACTIVE\.",
                    text, maxsplit=1,
                )[0].rstrip()
    return ""


def _evidence_text(messages: list[dict[str, Any]], limit: int = 32000) -> str:
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        text = _content_text(message.get("content")).strip()
        if text:
            chunks.append(f"[{role}]\n{text}")
    return "\n".join(chunks)[-limit:]


def _extract_failure_message(evidence: str) -> str | None:
    matches = re.findall(r"BIBLIOTECARIO_FAILURE:[^\r\n]+", evidence)
    return matches[-1].strip() if matches else None


def _extract_contract_invalid(evidence: str) -> str | None:
    matches = re.findall(r"WORK_CONTRACT_INVALID before executor:[^\r\n]+", evidence)
    return matches[-1].strip() if matches else None


def derive_state(messages: list[dict[str, Any]]) -> dict[str, Any]:
    # A new human request starts a new workflow. Within it, only the latest
    # task's tool observations describe the current phase: older rejections and
    # retry directives must not become permanent routing instructions.
    request_start = 0
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            request_start = index + 1
    workflow_messages = messages[request_start:]
    phase_start = 0
    for index, message in enumerate(workflow_messages):
        if _assistant_task_calls([message]):
            phase_start = index + 1
    phase_observations = [
        message for message in workflow_messages[phase_start:]
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    evidence = _evidence_text(phase_observations)
    lower = evidence.lower()
    calls = _assistant_task_calls(workflow_messages)
    last = calls[-1] if calls else None

    approved = last == "reviewer-qwen" and bool(re.search(r"(?mi)^\s*APPROVED\s*$", evidence))
    rejected = last == "reviewer-qwen" and bool(re.search(r"(?mi)^\s*REJECTED\b", evidence))
    replan_required = "REPLAN_REQUIRED" in evidence

    retry_executor = any(
        token in lower
        for token in (
            "call executor-spark again under the same current_work_item",
            "authoritative work_item validation fail",
            "same current_work_item",
        )
    )
    planner_directive = any(
        token in lower
        for token in (
            "call planner-qwen",
            "graph still has incomplete nodes",
            "global deterministic validation fail",
            "add a bounded corrective node",
        )
    )
    reviewer_allowed = any(
        token in lower
        for token in (
            "reviewer may start",
            "all graph nodes and final deterministic validation pass",
        )
    )
    contract_invalid = _extract_contract_invalid(evidence)
    pending_match = re.findall(r"WORK_ITEM_REPLAN_REQUIRED:[^\r\n]+", evidence)
    retry_match = (
        re.search(r"(?m)^PLANNER_RETRY_REQUIRED: (task_[0-9]+)\b", evidence)
        if last == "planner-qwen" else None
    )

    last_task_completed = False
    if last:
        # TaskObservation renders Status: completed; explicit phase markers are also
        # accepted so this remains compatible with both old and new task output.
        last_task_completed = bool(
            re.search(r"(?mi)^Status:\s*completed\s*$", evidence)
            or "PLANNING_RESULT: READY" in evidence
            or "EXECUTION_RESULT: READY_FOR_VALIDATION" in evidence
        )

    return {
        "human_request": _latest_user_request(messages),
        "task_calls": calls,
        "last_subagent": last,
        "last_task_completed": last_task_completed,
        "approved": approved,
        "rejected": rejected,
        "replan_required": replan_required,
        "retry_executor": retry_executor,
        "planner_directive": planner_directive,
        "reviewer_allowed": reviewer_allowed,
        "next_work_item_ready": "NEXT_WORK_ITEM_READY:" in evidence,
        "contract_invalid": contract_invalid,
        "pending_work_item": pending_match[-1].strip() if pending_match else None,
        "planner_retry_id": retry_match.group(1) if retry_match else None,
        "failure_message": _extract_failure_message(evidence),
        "evidence_tail": evidence[-6000:],
    }


def _task(subagent: str, prompt: str, description: str) -> RouteCandidate:
    return RouteCandidate(
        id=f"task_{subagent}",
        tool="task",
        arguments={
            "subagent_type": subagent,
            "prompt": prompt,
        },
        description=description,
    )


def deterministic_candidates(
    state: dict[str, Any],
    available_tools: set[str],
) -> list[RouteCandidate]:
    """Return the legal next-action set.

    A one-item set is a deterministic route. A multi-item set is the only case in
    which Rizzo is allowed to decide.
    """
    if state.get("failure_message"):
        if "finish" not in available_tools:
            raise RoutingError("terminal failure exists but finish tool is unavailable")
        return [
            RouteCandidate(
                id="finish_failure",
                tool="finish",
                arguments={"message": state["failure_message"]},
                description="Finish with the exact canonical deterministic failure message.",
            )
        ]

    if state.get("approved"):
        if "finish" not in available_tools:
            raise RoutingError("review is approved but finish tool is unavailable")
        return [
            RouteCandidate(
                id="finish_success",
                tool="finish",
                arguments={
                    "message": "Bibliotecario completed with deterministic global PASS and final semantic review APPROVED."
                },
                description="Finish only after the final reviewer has explicitly approved the globally validated result.",
            )
        ]

    if "task" not in available_tools:
        raise RoutingError("task tool is unavailable; refusing to bypass native delegation")

    human = str(state.get("human_request") or "").strip()
    last = state.get("last_subagent")

    if state.get("planner_retry_id"):
        return [
            RouteCandidate(
                id="retry_planner_after_iteration_limit",
                tool="task",
                arguments={
                    "subagent_type": "planner-qwen",
                    "prompt": (
                        "Start one fresh bounded planner task after MaxIterationsReached. "
                        "FIRST read .agents_tmp/INTAKE.json, then continue from the "
                        "existing evidence and any partial planning artifacts. "
                        "Complete the three authoritative JSON artifacts and return "
                        "PLANNING_RESULT: READY, or report the exact blocker with "
                        "PLANNING_RESULT: BLOCKED. Do not claim validation PASS."
                    ),
                },
                description="Retry Qwen planning once with a fresh bounded task after its iteration limit.",
            )
        ]

    if state.get("pending_work_item"):
        return [
            _task(
                "planner-qwen",
                (
                    "FIRST read .agents_tmp/INTAKE.json. Then inspect VALIDATION.json, "
                    "STATE.json, WORK_GRAPH.json, and CURRENT_WORK_ITEM.json. "
                    "The work item failed twice under deterministic validation. "
                    "Keep the failed node pending and incomplete. Choose a different "
                    "dependency-ready independent node only if it is within the "
                    "authorized checkpoint; otherwise revise the failed work item "
                    "using the actual failure evidence or report a precise blocker. "
                    "Never mark an unchecked node complete. Exact directive:\n\n"
                    + str(state["pending_work_item"])
                ),
                "Replan after two deterministic failures without claiming PASS.",
            )
        ]

    if state.get("contract_invalid"):
        return [
            _task(
                "planner-qwen",
                (
                    "FIRST read .agents_tmp/INTAKE.json. Then read the required existing "
                    "JSON artifacts together in one turn. The deterministic preflight "
                    "rejected the current planning artifacts. Correct the existing CONTRACT.json / WORK_GRAPH.json / CURRENT_WORK_ITEM.json "
                    "without changing the human objective. Write only the necessary corrections "
                    "together, then return PLANNING_RESULT: READY. Exact rejection:\n\n"
                    + str(state["contract_invalid"])
                ),
                "Repair an invalid planning contract using the exact deterministic rejection.",
            )
        ]

    if not last:
        if not human:
            raise RoutingError("no human request found for planner bootstrap")
        return [
            _task(
                "planner-qwen",
                (
                    "Interpret the complete human request below. Follow the Bibliotecario "
                    "planner contract, inspect INTAKE.json first, and create/update only "
                    "the authoritative planning artifacts. Human request:\n\n" + human
                ),
                "Bootstrap the free-form request through the capable Qwen planner.",
            )
        ]

    if state.get("next_work_item_ready"):
        return [
            _task(
                "executor-spark",
                (
                    "Execute the dependency-ready CURRENT_WORK_ITEM.json selected and "
                    "validated by the deterministic parent hook. Read CONTRACT.json and "
                    "CURRENT_WORK_ITEM.json first; preserve the bounded scope."
                ),
                "Execute the prepared dependency-ready node without another planner call.",
            )
        ]

    if state.get("retry_executor"):
        return [
            _task(
                "executor-spark",
                (
                    "Retry only the SAME bounded CURRENT_WORK_ITEM after deterministic "
                    "WORK_ITEM validation failure. Read CONTRACT.json and "
                    "CURRENT_WORK_ITEM.json first; do not expand scope."
                ),
                "Retry Spark on the same bounded work item after deterministic local validation failure.",
            )
        ]

    if state.get("rejected") or state.get("replan_required") or state.get("planner_directive"):
        return [
            _task(
                "planner-qwen",
                (
                    "FIRST read .agents_tmp/INTAKE.json. Batch subsequent artifact reads. "
                    "Replan from the authoritative Bibliotecario artifacts and latest "
                    "deterministic/reviewer evidence. Preserve CONTRACT.json and completed "
                    "nodes; modify only future graph work as permitted."
                ),
                "Return to Qwen for a bounded replan or corrective node.",
            )
        ]

    if state.get("reviewer_allowed"):
        return [
            _task(
                "reviewer-qwen",
                (
                    "Perform the final project-read-only semantic review. Read INTAKE.json, "
                    "CONTRACT.json, WORK_GRAPH.json, and final PASS VALIDATION.json before "
                    "reviewing the implementation."
                ),
                "Run final Qwen semantic review after deterministic global validation PASS.",
            )
        ]

    if last == "planner-qwen":
        return [
            _task(
                "executor-spark",
                (
                    "Execute only the current bounded CURRENT_WORK_ITEM.json. Read "
                    "CONTRACT.json and CURRENT_WORK_ITEM.json first, respect mutable and "
                    "forbidden paths, and return for deterministic validation."
                ),
                "Execute the planner-selected bounded work item with Spark.",
            )
        ]

    if last == "executor-spark":
        return [
            _task(
                "reviewer-qwen",
                (
                    "Attempt final review now. Parent PreToolUse is authoritative: it will "
                    "first run deterministic scope/work-item/global validation and either "
                    "deny this call with the exact next-step directive or allow final review."
                ),
                "Attempt reviewer delegation so the deterministic parent validator decides the next phase.",
            )
        ]

    if last == "reviewer-qwen":
        # If the transcript lacks an explicit verdict, do not guess success. The only
        # safe semantic options are a fresh reviewer attempt or planner escalation.
        return [
            _task(
                "reviewer-qwen",
                (
                    "Re-read the required final evidence and return an explicit final verdict "
                    "beginning exactly with APPROVED or REJECTED."
                ),
                "Ask the reviewer for an explicit final verdict because the prior result is ambiguous.",
            ),
            _task(
                "planner-qwen",
                (
                    "FIRST read .agents_tmp/INTAKE.json. Batch subsequent artifact reads. "
                    "Inspect the latest workflow/reviewer evidence and decide whether a bounded "
                    "corrective node is required. Preserve locked contract and completed nodes."
                ),
                "Escalate ambiguous post-review state to the capable Qwen planner.",
            ),
        ]

    return [
        _task(
            "planner-qwen",
            (
                "FIRST read .agents_tmp/INTAKE.json. Batch subsequent artifact reads. "
                "Recover the Bibliotecario workflow from the authoritative artifacts and "
                "latest transcript evidence. Preserve the locked contract and completed nodes."
            ),
            "Recover an otherwise ambiguous workflow state through the Qwen planner.",
        ),
        _task(
            "reviewer-qwen",
            (
                "Attempt read-only review; deterministic parent hooks must deny this unless "
                "all required validation preconditions are satisfied."
            ),
            "Probe the deterministic reviewer gate without bypassing it.",
        ),
    ]


def _rizzo_request(state: dict[str, Any], candidates: list[RouteCandidate]) -> dict[str, Any]:
    return {
        "state": {
            "last_subagent": state.get("last_subagent"),
            "last_task_completed": state.get("last_task_completed"),
            "replan_required": state.get("replan_required"),
            "approved": state.get("approved"),
            "rejected": state.get("rejected"),
            "evidence_tail": state.get("evidence_tail"),
        },
        "questions": {
            "next_action": {
                "type": "choice",
                "instructions": (
                    "Which permitted Bibliotecario action should be taken next according "
                    "only to the supplied workflow evidence?"
                ),
                "options": [
                    {"id": c.id, "description": c.description} for c in candidates
                ],
                "policy": {
                    "allow_abstain": True,
                    "min_top_probability": RIZZO_MIN_TOP_PROBABILITY,
                },
            }
        },
    }


def rizzo_choose(
    state: dict[str, Any],
    candidates: list[RouteCandidate],
    *,
    url: str = RIZZO_URL,
    api_key: str | None = RIZZO_API_KEY,
    timeout: int = 120,
) -> RouteCandidate | None:
    if len(candidates) < 2:
        raise ValueError("rizzo_choose requires at least two candidates")
    body = json.dumps(_rizzo_request(state, candidates), ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/decisions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RoutingError(f"Rizzo decision service unavailable: {exc}") from exc

    try:
        answer = result["answers"]["next_action"]
    except (KeyError, TypeError) as exc:
        raise RoutingError("malformed Rizzo response") from exc

    if answer.get("status") != "ok":
        return None
    choice = answer.get("choice")
    return next((c for c in candidates if c.id == choice), None)


def choose_next(
    messages: list[dict[str, Any]],
    tools: Any,
    *,
    chooser=rizzo_choose,
) -> tuple[RouteCandidate, dict[str, Any], str]:
    available = _tool_names(tools)
    state = derive_state(messages)
    candidates = deterministic_candidates(state, available)

    if len(candidates) == 1:
        return candidates[0], state, "deterministic"

    selected = chooser(state, candidates)
    if selected is not None:
        return selected, state, "rizzo"

    # Fail closed on Rizzo uncertainty. Prefer planner only if it is already one of
    # the legal candidates; never manufacture a new fallback action.
    planner = next(
        (c for c in candidates if c.arguments.get("subagent_type") == "planner-qwen"),
        None,
    )
    if planner is not None:
        return planner, state, "rizzo_abstain_escalate_qwen"
    raise RoutingError("Rizzo abstained and no safe escalation candidate exists")
