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


def llm_draft(
    router_api_key: str,
    *,
    base_url: str = "http://host.docker.internal:18019/v1",
) -> dict[str, Any]:
    """Build the saved runtime LLM profile.

    Conversation runtimes are Docker containers, where host.docker.internal resolves
    to the WSL/Docker host. Native profile validation, however, runs in the host-side
    Agent Server process, so validation uses a separate localhost draft.
    """
    return {
        "model": "openai/rizzo-bibliotecario-router",
        "api_key": router_api_key,
        "base_url": base_url,
        "usage_id": "rizzo-bibliotecario-router",
        "api_mode": "chat",
        "stream": False,
    }


def validation_llm_draft(router_api_key: str) -> dict[str, Any]:
    return llm_draft(
        router_api_key,
        base_url="http://127.0.0.1:18019/v1",
    )

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


def _get_llm_profile(base: str, key: str) -> dict[str, Any]:
    body = _expect_ok(
        "read existing experimental LLM profile",
        _request(f"{base}/api/profiles/{LLM_PROFILE}", key=key, timeout=30),
    )
    return body if isinstance(body, dict) else {}


def _get_agent_profile(base: str, key: str) -> dict[str, Any]:
    body = _expect_ok(
        "read existing experimental Agent Profile",
        _request(f"{base}/api/agent-profiles/{AGENT_PROFILE}", key=key, timeout=30),
    )
    return body if isinstance(body, dict) else {}


def _assert_subset(actual: Any, expected: Any, path: str = "profile") -> None:
    """Require every expected field/value to be present; tolerate server-added defaults."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise InstallError(f"{path} is not an object")
        for key, value in expected.items():
            if key not in actual:
                raise InstallError(f"{path}.{key} is missing")
            _assert_subset(actual[key], value, f"{path}.{key}")
        return
    if isinstance(expected, list):
        if actual != expected:
            raise InstallError(f"{path} differs from the expected experimental profile")
        return
    if actual != expected:
        raise InstallError(
            f"{path} differs from the expected experimental profile "
            f"(expected {expected!r}, got {actual!r})"
        )


def _verify_existing_llm(detail: dict[str, Any], runtime_llm: dict[str, Any]) -> None:
    config = detail.get("config")
    if not isinstance(config, dict):
        raise InstallError("existing experimental LLM profile has no config object")

    # Secrets are intentionally masked on ordinary GET. Verify every non-secret field
    # and require the profile store to report that a key exists.
    expected = {k: v for k, v in runtime_llm.items() if k != "api_key"}
    _assert_subset(config, expected, "existing_llm.config")
    if detail.get("api_key_set") is not True:
        raise InstallError("existing experimental LLM profile has no stored API key")


def _normalize_tools(value: Any) -> list[tuple[str, str]]:
    """Normalize profile tool specs; missing params and {} are equivalent."""
    if not isinstance(value, list):
        raise InstallError("existing_agent.profile.tools is not a list")

    normalized: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise InstallError(
                f"existing_agent.profile.tools[{index}] is not a valid tool spec"
            )
        params = item.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise InstallError(
                f"existing_agent.profile.tools[{index}].params is not an object"
            )
        normalized.append(
            (
                item["name"],
                json.dumps(params, sort_keys=True, separators=(",", ":")),
            )
        )

    # Tool selection is semantic, not positional. OpenHands canonicalizes/merges
    # profile tool specs before persistence.
    return sorted(normalized)


def _verify_existing_agent(detail: dict[str, Any]) -> None:
    profile = detail.get("profile")
    if not isinstance(profile, dict):
        raise InstallError("existing experimental Agent Profile has no profile object")

    expected_without_tools = {
        key: value for key, value in AGENT_DRAFT.items() if key != "tools"
    }
    _assert_subset(
        profile,
        expected_without_tools,
        "existing_agent.profile",
    )

    actual_tools = _normalize_tools(profile.get("tools"))
    expected_tools = _normalize_tools(AGENT_DRAFT["tools"])
    if actual_tools != expected_tools:
        raise InstallError(
            "existing_agent.profile.tools differs from the expected experimental "
            f"profile (expected {expected_tools!r}, got {actual_tools!r})"
        )


def run(base: str, apply: bool, activate: bool) -> dict[str, Any]:
    if activate and not apply:
        raise InstallError("--activate requires --apply")

    key = _session_key()
    router_key = _router_api_key()
    runtime_llm = llm_draft(router_key)
    validation_llm = validation_llm_draft(router_key)
    base = base.rstrip("/")

    adapter = _expect_ok("Rizzo OpenAI adapter health", _request(DEFAULT_ADAPTER_HEALTH))
    rizzo = _expect_ok("Rizzo Flow health", _request(DEFAULT_RIZZO_HEALTH))
    _expect_ok("Agent Canvas ingress", _request(base + "/server_info", key=key))

    llm_before = _list_profiles(base, key, "/api/profiles")
    agent_before = _list_profiles(base, key, "/api/agent-profiles")
    active_llm_before = llm_before.get("active_profile")
    active_agent_before = agent_before.get("active_agent_profile_id")

    llm_exists = LLM_PROFILE in _profile_names(llm_before)
    agent_exists = AGENT_PROFILE in _profile_names(agent_before)

    if agent_exists and not llm_exists:
        raise InstallError(
            "experimental Agent Profile exists but its LLM profile is missing; "
            "refusing to mutate this inconsistent pre-existing state"
        )

    if llm_exists:
        _verify_existing_llm(_get_llm_profile(base, key), runtime_llm)
    if agent_exists:
        _verify_existing_agent(_get_agent_profile(base, key))

    llm_validation = _expect_ok(
        "native LLM profile validation",
        _request(
            f"{base}/api/profiles/{LLM_PROFILE}/validate",
            key=key,
            method="POST",
            body={"llm": validation_llm},
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
        "validation_base_url": validation_llm["base_url"],
        "runtime_base_url": runtime_llm["base_url"],
        "mode": "apply" if apply else "dry-run",
        "llm_profile": LLM_PROFILE,
        "agent_profile": AGENT_PROFILE,
        "activated": False,
        "preexisting_llm_profile": llm_exists,
        "preexisting_agent_profile": agent_exists,
    }

    if not apply:
        result["next"] = (
            "LLM draft validated. Run again with --apply to persist the new isolated "
            "LLM profile, materialize the Agent Profile, and save it if valid."
        )
        return result

    created_llm = False
    created_agent = False

    if not llm_exists:
        _expect_ok(
            "save isolated LLM profile",
            _request(
                f"{base}/api/profiles/{LLM_PROFILE}",
                key=key,
                method="POST",
                body={"llm": runtime_llm, "include_secrets": True},
            ),
        )
        created_llm = True

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
        if not isinstance(materialized, dict) or materialized.get("valid") is not True:
            raise InstallError(f"Agent Profile materialize rejected the draft: {materialized}")

        result["materialize"] = {
            "valid": materialized.get("valid"),
            "errors": materialized.get("errors") or [],
            "warnings": materialized.get("warnings") or [],
        }

        if not agent_exists:
            _expect_ok(
                "save isolated Agent Profile",
                _request(
                    f"{base}/api/agent-profiles/{AGENT_PROFILE}",
                    key=key,
                    method="POST",
                    body=AGENT_DRAFT,
                ),
            )
            created_agent = True

        llm_after = _list_profiles(base, key, "/api/profiles")
        agent_after = _list_profiles(base, key, "/api/agent-profiles")
        profile_id = _agent_profile_id(agent_after, AGENT_PROFILE)
        if LLM_PROFILE not in _profile_names(llm_after):
            raise InstallError("saved LLM profile is not present in native profile list")
        if not profile_id:
            raise InstallError("saved Agent Profile is not present in native profile list")

        if llm_after.get("active_profile") != active_llm_before:
            raise InstallError("saving experimental LLM profile changed the active LLM profile")
        if agent_after.get("active_agent_profile_id") != active_agent_before:
            raise InstallError("saving experimental Agent Profile changed the active Agent Profile")

        llm_detail = _expect_ok(
            "verify saved LLM profile",
            _request(
                f"{base}/api/profiles/{LLM_PROFILE}",
                key=key,
                timeout=30,
            ),
        )
        agent_detail = _expect_ok(
            "verify saved Agent Profile",
            _request(
                f"{base}/api/agent-profiles/{AGENT_PROFILE}",
                key=key,
                timeout=30,
            ),
        )

        llm_config = llm_detail.get("config") if isinstance(llm_detail, dict) else None
        if not isinstance(llm_config, dict):
            raise InstallError("saved LLM profile detail has no config object")
        if llm_config.get("base_url") != runtime_llm["base_url"]:
            raise InstallError(
                "saved LLM profile runtime base_url does not match Docker runtime URL"
            )
        if llm_config.get("api_mode") != "chat":
            raise InstallError("saved LLM profile did not preserve api_mode=chat")
        if llm_detail.get("api_key_set") is not True:
            raise InstallError("saved LLM profile does not report an API key")

        stored_agent = (
            agent_detail.get("profile") if isinstance(agent_detail, dict) else None
        )
        if not isinstance(stored_agent, dict):
            raise InstallError("saved Agent Profile detail has no profile object")
        if stored_agent.get("llm_profile_ref") != LLM_PROFILE:
            raise InstallError("saved Agent Profile points to the wrong LLM profile")

        result["agent_profile_id"] = profile_id
        result["created_this_run"] = {
            "llm_profile": created_llm,
            "agent_profile": created_agent,
        }
        result["post_install_verification"] = {
            "llm_profile_present": True,
            "agent_profile_present": True,
            "runtime_base_url": llm_config.get("base_url"),
            "api_mode": llm_config.get("api_mode"),
            "api_key_set": llm_detail.get("api_key_set"),
            "active_llm_unchanged": True,
            "active_agent_unchanged": True,
        }

        if activate:
            _expect_ok(
                "activate isolated Agent Profile",
                _request(
                    f"{base}/api/agent-profiles/{profile_id}/activate",
                    key=key,
                    method="POST",
                ),
            )
            activated = _list_profiles(base, key, "/api/agent-profiles")
            if activated.get("active_agent_profile_id") != profile_id:
                raise InstallError("Agent Profile activation did not set the expected profile id")
            result["activated"] = True
    except Exception:
        # Roll back ONLY artifacts created by this invocation. Never delete or
        # overwrite a matching profile that existed before this run.
        if created_agent:
            _request(
                f"{base}/api/agent-profiles/{AGENT_PROFILE}",
                key=key,
                method="DELETE",
            )
        if created_llm:
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
