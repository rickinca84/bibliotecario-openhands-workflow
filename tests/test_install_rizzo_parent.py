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

    def test_llm_profile_points_to_host_adapter(self):
        self.assertEqual(
            mod.LLM_DRAFT["base_url"],
            "http://host.docker.internal:18019/v1",
        )
        self.assertFalse(mod.LLM_DRAFT["stream"])

    def test_activate_requires_apply(self):
        with self.assertRaises(mod.InstallError):
            mod.run("http://127.0.0.1:8001", False, True)

    @patch.object(mod, "_session_key", return_value="secret")
    @patch.object(mod, "_request")
    def test_dry_run_is_read_only_after_validation(self, request, _key):
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
    @patch.object(mod, "_request")
    def test_apply_rolls_back_new_profiles_on_materialize_failure(self, request, _key):
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


if __name__ == "__main__":
    unittest.main()
