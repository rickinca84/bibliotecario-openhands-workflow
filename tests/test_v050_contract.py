import json
import pathlib
import re
import shlex
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class V050ContractTests(unittest.TestCase):
    def test_manifest_is_v050(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.5.0")
        self.assertIn("deterministic INTAKE", manifest["description"])

    def test_llm_intake_agent_removed(self):
        self.assertFalse((ROOT / "agents" / "intake-spark.md").exists())

    def test_executor_has_no_terminal(self):
        text = (ROOT / "agents" / "executor-spark.md").read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^\s*- file_editor\s*$")
        self.assertNotRegex(frontmatter, r"(?m)^\s*- terminal\s*$")

    def test_planner_can_read_intake(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^\s*- read_file\s*$")
        self.assertIn("INTAKE.json", text)

    def test_state_machine_has_no_intake_subagent_or_fake_resume(self):
        text = (ROOT / "commands" / "run.md").read_text()
        self.assertNotIn('subagent_type="intake-spark"', text)
        self.assertIn("OMIT `resume`", text)

    def test_parent_has_post_task_and_stop_gates(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        self.assertIn("post_tool_use", hooks)
        self.assertIn("stop", hooks)
        task_posts = [
            item
            for item in hooks["post_tool_use"]
            if item.get("matcher") == "task"
        ]
        self.assertTrue(task_posts)

    def test_inline_python_hooks_compile(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())

        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get("command"), str):
                    yield value["command"]
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        commands = list(walk(hooks))
        self.assertTrue(commands)
        for command in commands:
            argv = shlex.split(command)
            if len(argv) >= 3 and argv[0] == "python3" and argv[1] == "-c":
                compile(argv[2], "<hook>", "exec")


if __name__ == "__main__":
    unittest.main()
