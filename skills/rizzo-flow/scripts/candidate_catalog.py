#!/usr/bin/env python3
"""Build complete Rizzo candidate tool calls from deterministic workflow state.

Rizzo only sees calls whose arguments are already known. Generative file mutations
remain delegated to Spark/Qwen unless a complete prepared mutation is supplied.
"""

from __future__ import annotations

from typing import Any, Iterable


class CatalogError(RuntimeError):
    pass


def _candidate(cid: str, tool: str, arguments: dict[str, Any], description: str) -> dict[str, Any]:
    return {
        "id": cid,
        "tool": tool,
        "arguments": arguments,
        "description": description,
    }


def build_candidates(
    state: dict[str, Any],
    available_tools: Iterable[str],
) -> list[dict[str, Any]]:
    """Return complete calls that are legal and meaningful for the current state."""
    if not isinstance(state, dict):
        raise CatalogError("state must be an object")

    tools = set(available_tools)
    candidates: list[dict[str, Any]] = []
    validation = state.get("validation") or {}
    prepared = state.get("prepared_calls") or []

    def add_if(tool: str, candidate: dict[str, Any]) -> None:
        if tool in tools:
            candidates.append(candidate)

    if state.get("has_contract"):
        add_if(
            "read_file",
            _candidate(
                "read_contract",
                "read_file",
                {"file_path": ".agents_tmp/CONTRACT.json"},
                "Read the locked global contract to recover objective, constraints, and final validation requirements.",
            ),
        )

    if state.get("has_current_work_item"):
        add_if(
            "read_file",
            _candidate(
                "read_current_work_item",
                "read_file",
                {"file_path": ".agents_tmp/CURRENT_WORK_ITEM.json"},
                "Read the current bounded work item to recover its local goal, mutable paths, and acceptance criteria.",
            ),
        )

    if state.get("has_validation"):
        add_if(
            "read_file",
            _candidate(
                "read_validation",
                "read_file",
                {"file_path": ".agents_tmp/VALIDATION.json"},
                "Read the latest deterministic validation evidence before deciding whether to retry, inspect, or escalate.",
            ),
        )

    for idx, query in enumerate(state.get("prepared_grep_queries") or []):
        if isinstance(query, dict) and isinstance(query.get("pattern"), str):
            args = {"pattern": query["pattern"]}
            if isinstance(query.get("path"), str):
                args["path"] = query["path"]
            add_if(
                "grep",
                _candidate(
                    f"grep_{idx}",
                    "grep",
                    args,
                    query.get("description")
                    or f"Search the repository for the prepared pattern {query['pattern']!r}.",
                ),
            )

    for idx, pattern in enumerate(state.get("prepared_globs") or []):
        if isinstance(pattern, dict) and isinstance(pattern.get("pattern"), str):
            add_if(
                "glob",
                _candidate(
                    f"glob_{idx}",
                    "glob",
                    {"pattern": pattern["pattern"]},
                    pattern.get("description")
                    or f"List repository paths matching the prepared glob {pattern['pattern']!r}.",
                ),
            )

    if state.get("has_current_work_item") and not state.get("terminal_failure"):
        add_if(
            "task",
            _candidate(
                "delegate_spark",
                "task",
                {
                    "agent": "executor-spark",
                    "prompt": (
                        "Execute only the current bounded work item in "
                        ".agents_tmp/CURRENT_WORK_ITEM.json. Respect mutable and forbidden "
                        "paths and return normally for deterministic validation."
                    ),
                },
                "Delegate the already-bounded implementation checkpoint to Spark, the fast coding executor.",
            ),
        )

    if (
        validation.get("scope") == "local"
        and validation.get("status") == "FAIL"
        and state.get("retry_allowed")
    ):
        add_if(
            "task",
            _candidate(
                "retry_spark",
                "task",
                {
                    "agent": "executor-spark",
                    "prompt": (
                        "Repair only the current bounded work item using the latest "
                        ".agents_tmp/VALIDATION.json failure evidence. Do not expand scope."
                    ),
                },
                "Retry Spark on the same bounded work item because deterministic local validation failed and retry budget remains.",
            ),
        )

    if state.get("needs_replan") or state.get("routing_uncertain"):
        add_if(
            "task",
            _candidate(
                "escalate_qwen",
                "task",
                {
                    "agent": "planner-qwen",
                    "prompt": (
                        "Reassess the current workflow state and update only the allowed "
                        "structured planning artifacts. Preserve the locked CONTRACT.json."
                    ),
                },
                "Escalate to Qwen because the next action requires semantic replanning or Rizzo routing is uncertain.",
            ),
        )

    for idx, call in enumerate(prepared):
        if not isinstance(call, dict):
            continue
        tool = call.get("tool")
        arguments = call.get("arguments")
        description = call.get("description")
        if tool not in {"edit", "write_file"}:
            continue
        if tool not in tools or not isinstance(arguments, dict):
            continue
        if not isinstance(description, str) or not description.strip():
            continue
        candidates.append(
            _candidate(
                call.get("id") or f"prepared_mutation_{idx}",
                tool,
                arguments,
                description.strip(),
            )
        )

    frozen = state.get("frozen_validation_command")
    if isinstance(frozen, dict) and isinstance(frozen.get("argv"), list):
        add_if(
            "terminal",
            _candidate(
                "run_frozen_validation",
                "terminal",
                {"argv": frozen["argv"]},
                frozen.get("description")
                or "Run the frozen deterministic validation command for the current checkpoint.",
            ),
        )

    return candidates


def validate_candidate_set(candidates: list[dict[str, Any]]) -> None:
    if len(candidates) > 26:
        raise CatalogError(
            "more than 26 executable candidates: reduce or cascade before calling Rizzo"
        )
    ids = [item.get("id") for item in candidates]
    if len(ids) != len(set(ids)):
        raise CatalogError("candidate ids must be unique")
    for item in candidates:
        if not isinstance(item.get("tool"), str):
            raise CatalogError("every candidate needs a tool")
        if not isinstance(item.get("arguments"), dict):
            raise CatalogError("every candidate needs complete arguments")
        if not isinstance(item.get("description"), str) or not item["description"].strip():
            raise CatalogError("every candidate needs a description")
