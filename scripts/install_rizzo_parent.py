#!/usr/bin/env python3
"""Preflight and optionally install the experimental Rizzo parent profiles.

Default mode is read-only: validate the LLM draft and materialize the Agent
Profile draft through the native Agent Canvas/Agent Server APIs.

Use --apply to persist two NEW profiles. Existing qwen/spark profiles and the
active agent profile are never overwritten. Use --activate only together with
--apply to activate the new agent profile after all checks pass.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE = "http://127.0.0.1:8001"
DEFAULT_ADAPTER_HEALTH = "http://127.0.0.1:18019/health"
DEFAULT_RIZZO_HEALTH = "http://127.0.0.1:8017/health"

LLM_PROFILE = "rizzo-bibliotecario-router"
AGENT_PROFILE = "bibliotecario-rizzo-experiment"

ROUTER_KEY_FILE = pathlib.Path.home() / ".openhands" / "rizzo-router" / "api-key.txt"


def _router_api_key() -> str:
    value = os.environ.get("RIZZO_ROUTER_API_KEY")
    if value and value.strip():
        return value.strip()
    if ROUTER_KEY_FILE.is_file():
        value = ROUTER_KEY_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise InstallError(
        "Rizzo router API key not found. Start openai_adapter.py once, or set "
        "RIZZO_ROUTER_API_KEY."
    )


def llm_draft(router_api_key: str) -> dict[str, Any]:
    return {
        "model": "openai/rizzo-bibliotecario-router",
        "api_key": router_api_key,
        "base_url": "http://host.docker.internal:18019/v1",
        "usage_id": "rizzo-bibliotecario-router",
        "api_mode": "chat",
        "stream": False,
    }

# Explicit tool list scopes delegated agents. This is intentionally the union of
# tools required by planner-qwen, executor-spark and reviewer-qwen plus task
# delegation. Parent hooks deny direct use of these repository tools.
AGENT_DRAFT = {
    "agent_kind": "openhands",
    "llm_profile_ref": LLM_PROFILE,
    "tools": [
        {"name": "task_tool_set"},
        {"name": "read_file"},
        {"name": "write_file"},
        {"name": "edit"},
        {"name": "glob"},
        {"name": "grep"},
    ],
    "mcp_server_refs": None,
    "disabled_skills": [],
    "condenser": {"condenser_kind": "no_op", "enabled": True},
    "verification": {
        "critic_enabled": False,
        "critic_mode": "finish_and_message",
        "enable_iterative_refinement": False,
        "critic_threshold": 0.6,
        "max_refinement_iterations": 3,
        "critic_server_url": None,
        "critic_model_name": None,
    },
    "enable_classify_and_switch_llm_tool": False,
    "tool_concurrency_limit": 1,
}


class InstallError(RuntimeError):
    pass


def _session_key() -> str:
    for env_name in (
        "SESSION_API_KEY",
        "OPENHANDS_SESSION_API_KEY",
        "OH_SESSION_API_KEYS_0",
        "LOCAL_BACKEND_API_KEY",
    ):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()

    home = pathlib.Path.home()
    for path in (
        home / ".openhands/agent-canvas/session-api-key.txt",
        home / ".openhands/agent-canvas/api-key.txt",
    ):
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    raise InstallError(
        "Agent Canvas session API key not found in environment or ~/.openhands/agent-canvas."
    )


def _request(
    url: str,
    *,
    key: str | None = None,
    method: str = "GET",
    body: Any | None = None,
    timeout: int = 60,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if key:
        headers["X-Session-API-Key"] = key
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw) if raw else None
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise InstallError(f"Cannot reach {url}: {exc.reason}") from exc
    except (http.client.RemoteDisconnected, ConnectionResetError) as exc:
        raise InstallError(
            f"Remote endpoint closed the connection while calling {method} {url}: {exc}"
        ) from exc


def _expect_ok(label: str, result: tuple[int, Any]) -> Any:
    status, body = result
    if not (200 <= status < 300):
        raise InstallError(f"{label} failed: HTTP {status}: {body}")
    return body


def _list_profiles(base: str, key: str, route: str) -> dict[str, Any]:
    body = _expect_ok(
        f"GET {route}",
        _request(base + route, key=key, timeout=30),
    )
    return body if isinstance(body, dict) else {}


def _profile_names(payload: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in payload.get("profiles") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result.add(item["name"])
    return result


def _agent_profile_id(payload: dict[str, Any], name: str) -> str | None:
    for item in payload.get("profiles") or []:
        if (
            isinstance(item, dict)
            and item.get("name") == name
            and isinstance(item.get("id"), str)
        ):
            return item["id"]
    return None


def run(base: str, apply: bool, activate: bool) -> dict[str, Any]:
    if activate and not apply:
        raise InstallError("--activate requires --apply")

    key = _session_key()
    router_key = _router_api_key()
    llm = llm_draft(router_key)
    base = base.rstrip("/")

    adapter = _expect_ok("Rizzo OpenAI adapter health", _request(DEFAULT_ADAPTER_HEALTH))
    rizzo = _expect_ok("Rizzo Flow health", _request(DEFAULT_RIZZO_HEALTH))
    _expect_ok("Agent Canvas ingress", _request(base + "/server_info", key=key))

    llm_before = _list_profiles(base, key, "/api/profiles")
    agent_before = _list_profiles(base, key, "/api/agent-profiles")

    if LLM_PROFILE in _profile_names(llm_before):
        raise InstallError(
            f"LLM profile {LLM_PROFILE!r} already exists; refusing to overwrite it."
        )
    if AGENT_PROFILE in _profile_names(agent_before):
        raise InstallError(
            f"Agent profile {AGENT_PROFILE!r} already exists; refusing to overwrite it."
        )

    llm_validation = _expect_ok(
        "native LLM profile validation",
        _request(
            f"{base}/api/profiles/{LLM_PROFILE}/validate",
            key=key,
            method="POST",
            body={"llm": llm},
            timeout=120,
        ),
    )
    if not isinstance(llm_validation, dict) or llm_validation.get("valid") is not True:
        raise InstallError(f"LLM profile validation rejected the draft: {llm_validation}")

    # materialize needs the referenced LLM profile to exist. In dry-run mode we
    # cannot persist it even temporarily, so report LLM validation only. With
    # --apply, save the LLM profile first, materialize the agent draft, and save
    # the agent profile only after materialization succeeds.
    result: dict[str, Any] = {
        "adapter_health": adapter,
        "rizzo_health": rizzo,
        "llm_validation": llm_validation,
        "mode": "apply" if apply else "dry-run",
        "llm_profile": LLM_PROFILE,
        "agent_profile": AGENT_PROFILE,
        "activated": False,
    }

    if not apply:
        result["next"] = (
            "LLM draft validated. Run again with --apply to persist the new isolated "
            "LLM profile, materialize the Agent Profile, and save it if valid."
        )
        return result

    _expect_ok(
        "save isolated LLM profile",
        _request(
            f"{base}/api/profiles/{LLM_PROFILE}",
            key=key,
            method="POST",
            body={"llm": llm, "include_secrets": True},
        ),
    )

    try:
        materialized = _expect_ok(
            "native Agent Profile materialize",
            _request(
                f"{base}/api/agent-profiles/{AGENT_PROFILE}/materialize",
                key=key,
                method="POST",
                body={"profile": AGENT_DRAFT},
                timeout=120,
            ),
        )
        result["materialize"] = materialized
        if not isinstance(materialized, dict) or materialized.get("valid") is not True:
            raise InstallError(f"Agent Profile materialize rejected the draft: {materialized}")

        _expect_ok(
            "save isolated Agent Profile",
            _request(
                f"{base}/api/agent-profiles/{AGENT_PROFILE}",
                key=key,
                method="POST",
                body=AGENT_DRAFT,
            ),
        )

        after = _list_profiles(base, key, "/api/agent-profiles")
        profile_id = _agent_profile_id(after, AGENT_PROFILE)
        if not profile_id:
            raise InstallError("saved Agent Profile is not present in native profile list")
        result["agent_profile_id"] = profile_id

        if activate:
            _expect_ok(
                "activate isolated Agent Profile",
                _request(
                    f"{base}/api/agent-profiles/{profile_id}/activate",
                    key=key,
                    method="POST",
                ),
            )
            result["activated"] = True
    except Exception:
        # Roll back only artifacts created by this invocation. The stable Qwen
        # and Spark profiles are separate names and remain untouched.
        _request(
            f"{base}/api/agent-profiles/{AGENT_PROFILE}",
            key=key,
            method="DELETE",
        )
        _request(
            f"{base}/api/profiles/{LLM_PROFILE}",
            key=key,
            method="DELETE",
        )
        raise

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base",
        default=os.environ.get("AGENT_CANVAS_BASE", DEFAULT_BASE),
        help=f"Agent Canvas ingress URL (default: {DEFAULT_BASE})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist NEW isolated profiles after native validation",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="activate the new Agent Profile after save; requires --apply",
    )
    args = parser.parse_args()
    try:
        result = run(args.base, args.apply, args.activate)
    except InstallError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        raise SystemExit(2)
    print(json.dumps({"status": "PASS", **result}, indent=2))


if __name__ == "__main__":
    main()
