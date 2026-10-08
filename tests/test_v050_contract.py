import json
import pathlib
import shlex
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class V052ContractTests(unittest.TestCase):
    def test_manifest_is_v052(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.5.2")
        self.assertIn("native write/edit tools", manifest["description"])

    def test_llm_intake_agent_removed(self):
        self.assertFalse((ROOT / "agents" / "intake-spark.md").exists())

    def test_executor_uses_native_file_tools_without_terminal_or_file_editor(self):
        text = (ROOT / "agents" / "executor-spark.md").read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^\s*- read_file\s*$")
        self.assertRegex(frontmatter, r"(?m)^\s*- write_file\s*$")
        self.assertRegex(frontmatter, r"(?m)^\s*- edit\s*$")
        self.assertNotRegex(frontmatter, r"(?m)^\s*- terminal\s*$")
        self.assertNotRegex(frontmatter, r"(?m)^\s*- file_editor\s*$")

    def test_no_custom_mutable_directory_scaffolding(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        blob = json.dumps(hooks)
        self.assertNotIn("directory.mkdir(parents=True,exist_ok=True)", blob)
        self.assertNotIn("deterministic scaffolding", blob)

    def test_planner_requires_literal_backticked_paths_and_argv_validation(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        self.assertIn("backticked workspace-relative literal path", text)
        self.assertIn('"argv": ["python", "-m", "pytest", "-q"]', text)
        self.assertNotIn('"command": "example --check"', text)

    def test_fresh_task_accepts_empty_resume_but_rejects_nonempty(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        task_hook = next(
            item for item in hooks["pre_tool_use"] if item.get("matcher") == "task"
        )["hooks"][0]["command"]
        self.assertIn('resume not in (None, "")', task_hook)

    def test_validator_uses_shell_false_and_source_root(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        task_hooks = next(
            item for item in hooks["pre_tool_use"] if item.get("matcher") == "task"
        )["hooks"]
        validator = task_hooks[1]["command"]
        self.assertIn("subprocess.run(argv,shell=False,cwd=source_root", validator)
        self.assertNotIn("subprocess.run(cmd,shell=True", validator)
        self.assertIn('"overall":"ENVIRONMENT_ERROR"', validator)

    def test_policy_hooks_fail_closed_on_runtime_errors(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        commands = []
        for event_name in ("user_prompt_submit", "pre_tool_use", "stop"):
            for matcher in hooks.get(event_name, []):
                for hook in matcher.get("hooks", []):
                    commands.append(hook["command"])
        self.assertTrue(commands)
        for command in commands:
            self.assertTrue(
                command.rstrip().endswith("|| exit 2"),
                f"policy hook is not fail-closed: {command[:120]}",
            )

    def test_parent_has_post_task_and_stop_gates(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        self.assertIn("post_tool_use", hooks)
        self.assertIn("stop", hooks)
        self.assertTrue(
            any(item.get("matcher") == "task" for item in hooks["post_tool_use"])
        )

    def test_inline_python_hooks_compile(self):
        paths = [
            ROOT / "hooks" / "hooks.json",
        ]
        commands = []
        hooks = json.loads(paths[0].read_text())

        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get("command"), str):
                    yield value["command"]
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        commands.extend(walk(hooks))
        for agent_name in ("planner-qwen.md", "executor-spark.md", "reviewer-qwen.md"):
            text = (ROOT / "agents" / agent_name).read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("command: "):
                    value = stripped[len("command: "):]
                    try:
                        commands.append(json.loads(value))
                    except json.JSONDecodeError:
                        pass

        self.assertTrue(commands)
        for command in commands:
            argv = shlex.split(command)
            if len(argv) >= 3 and argv[0] == "python3" and argv[1] == "-c":
                compile(argv[2], "<hook>", "exec")


if __name__ == "__main__":
    unittest.main()
