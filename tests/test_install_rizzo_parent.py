import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_rizzo_parent.py"
spec = importlib.util.spec_from_file_location("install_rizzo_parent", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class InstallRizzoParentTests(unittest.TestCase):
    def test_agent_profile_exposes_subagent_tool_union(self):
        names = {tool["name"] for tool in mod.AGENT_DRAFT["tools"]}
        self.assertEqual(
            names,
            {"task_tool_set", "read_file", "write_file", "edit", "glob", "grep"},
        )
        self.assertFalse(mod.AGENT_DRAFT["verification"]["critic_enabled"])
        self.assertFalse(mod.AGENT_DRAFT["enable_classify_and_switch_llm_tool"])

    def test_llm_profile_separates_host_validation_and_docker_runtime_urls(self):
        runtime = mod.llm_draft("private-test-key")
        validation = mod.validation_llm_draft("private-test-key")
        self.assertEqual(
            runtime["base_url"],
            "http://host.docker.internal:18019/v1",
        )
        self.assertEqual(
            validation["base_url"],
            "http://127.0.0.1:18019/v1",
        )
        self.assertEqual(runtime["api_key"], "private-test-key")
        self.assertEqual(validation["api_key"], "private-test-key")
        self.assertEqual(runtime["api_mode"], "chat")
        self.assertEqual(validation["api_mode"], "chat")
        self.assertFalse(runtime["stream"])
        self.assertFalse(validation["stream"])

    def test_activate_requires_apply(self):
        with self.assertRaises(mod.InstallError):
            mod.run("http://127.0.0.1:8001", False, True)

    @patch.object(mod, "_session_key", return_value="secret")
    @patch.object(mod, "_router_api_key", return_value="router-secret")
    @patch.object(mod, "_request")
    def test_dry_run_is_read_only_after_validation(self, request, _router_key, _key):
        request.side_effect = [
            (200, {"status": "ready"}),  # adapter
            (200, {"status": "ready"}),  # rizzo
            (200, {"ok": True}),         # server_info
            (200, {"profiles": []}),     # llm list
            (200, {"profiles": []}),     # agent list
            (200, {"valid": True}),      # llm validate
        ]
        result = mod.run("http://127.0.0.1:8001", False, False)
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(request.call_count, 6)
        methods = [c.kwargs.get("method", "GET") for c in request.call_args_list]
        self.assertEqual(methods.count("POST"), 1)
        self.assertNotIn("DELETE", methods)

    @patch.object(mod, "_session_key", return_value="secret")
    @patch.object(mod, "_router_api_key", return_value="router-secret")
    @patch.object(mod, "_request")
    def test_apply_rolls_back_new_profiles_on_materialize_failure(
        self, request, _router_key, _key
    ):
        request.side_effect = [
            (200, {"status": "ready"}),
            (200, {"status": "ready"}),
            (200, {"ok": True}),
            (200, {"profiles": []}),
            (200, {"profiles": []}),
            (200, {"valid": True}),
            (201, {"name": mod.LLM_PROFILE}),
            (200, {"valid": False, "errors": ["bad tool"]}),
            (200, {"name": mod.AGENT_PROFILE}),
            (200, {"name": mod.LLM_PROFILE}),
        ]
        with self.assertRaises(mod.InstallError):
            mod.run("http://127.0.0.1:8001", True, False)
        methods = [c.kwargs.get("method", "GET") for c in request.call_args_list]
        self.assertEqual(methods[-2:], ["DELETE", "DELETE"])

    @patch.object(mod, "_session_key", return_value="secret")
    @patch.object(mod, "_router_api_key", return_value="router-secret")
    @patch.object(mod, "_request")
    def test_apply_reuses_matching_existing_llm_and_creates_only_agent(
        self, request, _router_key, _key
    ):
        runtime = mod.llm_draft("router-secret")
        llm_detail = {
            "name": mod.LLM_PROFILE,
            "config": {**runtime, "api_key": None},
            "api_key_set": True,
        }

        request.side_effect = [
            (200, {"status": "ready"}),
            (200, {"status": "ready"}),
            (200, {"ok": True}),
            (200, {"profiles": [{"name": mod.LLM_PROFILE}], "active_profile": "stable"}),
            (200, {"profiles": [], "active_agent_profile_id": "stable-agent"}),
            (200, llm_detail),
            (200, {"valid": True}),
            (200, {"valid": True, "errors": [], "warnings": []}),
            (201, {"name": mod.AGENT_PROFILE}),
            (
                200,
                {
                    "profiles": [{"name": mod.LLM_PROFILE}],
                    "active_profile": "stable",
                },
            ),
            (
                200,
                {
                    "profiles": [
                        {"name": mod.AGENT_PROFILE, "id": "rizzo-agent-id"}
                    ],
                    "active_agent_profile_id": "stable-agent",
                },
            ),
            (200, llm_detail),
            (
                200,
                {
                    "name": mod.AGENT_PROFILE,
                    "profile": {
                        **mod.AGENT_DRAFT,
                        "name": mod.AGENT_PROFILE,
                        "id": "rizzo-agent-id",
                    },
                },
            ),
        ]

        result = mod.run("http://127.0.0.1:8001", True, False)
        self.assertTrue(result["preexisting_llm_profile"])
        self.assertFalse(result["preexisting_agent_profile"])
        self.assertEqual(
            result["created_this_run"],
            {"llm_profile": False, "agent_profile": True},
        )

        posts = [
            c.args[0]
            for c in request.call_args_list
            if c.kwargs.get("method", "GET") == "POST"
        ]
        self.assertNotIn(
            "http://127.0.0.1:8001/api/profiles/" + mod.LLM_PROFILE,
            posts,
        )

    @patch.object(mod, "_session_key", return_value="secret")
    @patch.object(mod, "_router_api_key", return_value="router-secret")
    @patch.object(mod, "_request")
    def test_mismatched_existing_llm_fails_without_mutation(
        self, request, _router_key, _key
    ):
        runtime = mod.llm_draft("router-secret")
        bad = {
            "name": mod.LLM_PROFILE,
            "config": {
                **runtime,
                "api_key": None,
                "base_url": "http://wrong.invalid/v1",
            },
            "api_key_set": True,
        }
        request.side_effect = [
            (200, {"status": "ready"}),
            (200, {"status": "ready"}),
            (200, {"ok": True}),
            (200, {"profiles": [{"name": mod.LLM_PROFILE}], "active_profile": "stable"}),
            (200, {"profiles": [], "active_agent_profile_id": "stable-agent"}),
            (200, bad),
        ]

        with self.assertRaises(mod.InstallError):
            mod.run("http://127.0.0.1:8001", True, False)

        methods = [c.kwargs.get("method", "GET") for c in request.call_args_list]
        self.assertNotIn("DELETE", methods)


if __name__ == "__main__":
    unittest.main()
