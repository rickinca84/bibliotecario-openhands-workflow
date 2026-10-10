import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class V060ContractTests(unittest.TestCase):
    def hooks(self):
        return json.loads((ROOT / "hooks" / "hooks.json").read_text())

    def task_hooks(self):
        return next(
            x for x in self.hooks()["pre_tool_use"] if x.get("matcher") == "task"
        )["hooks"]

    def run_hook(self, command, payload, cwd):
        return subprocess.run(
            ["bash", "-lc", command],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=cwd,
            env={**os.environ, "OH_PERSISTENCE_DIR": str(pathlib.Path(cwd) / ".openhands")},
        )

    def test_manifest_is_v060(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.6.0")
        self.assertIn("dynamic work graph", manifest["description"])

    def test_no_authoritative_plan_md(self):
        for rel in [
            "hooks/hooks.json",
            "agents/planner-qwen.md",
            "agents/executor-spark.md",
            "agents/reviewer-qwen.md",
            "commands/run.md",
        ]:
            self.assertNotIn(".agents_tmp/PLAN.md", (ROOT / rel).read_text())

    def test_planner_uses_native_write_file_not_planning_editor(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        front = text.split("---", 2)[1]
        self.assertRegex(front, r"(?m)^\s*- write_file\s*$")
        self.assertNotRegex(front, r"(?m)^\s*- planning_file_editor\s*$")
        for artifact in ("CONTRACT.json", "WORK_GRAPH.json", "CURRENT_WORK_ITEM.json"):
            self.assertIn(artifact, text)

    def test_executor_is_native_file_tools_only(self):
        text = (ROOT / "agents" / "executor-spark.md").read_text()
        front = text.split("---", 2)[1]
        for tool in ("read_file", "write_file", "edit"):
            self.assertRegex(front, rf"(?m)^\s*- {tool}\s*$")
        self.assertNotRegex(front, r"(?m)^\s*- terminal\s*$")
        self.assertNotRegex(front, r"(?m)^\s*- file_editor\s*$")

    def test_graph_nodes_are_minimal_by_contract(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        self.assertIn("Every graph node contains ONLY `id`, `goal`, and `depends_on`", text)
        self.assertIn("completed", text.lower())

    def test_final_global_validation_is_in_locked_contract(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        self.assertIn("final_validation_commands", text)
        gate = self.task_hooks()[0]["command"]
        self.assertIn("CONTRACT final_validation_commands", gate)

    def test_preflight_rejects_bad_graph_before_incrementing_executor(self):
        command = self.task_hooks()[0]["command"]
        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            (wd / ".agents_tmp").mkdir()
            (wd / ".openhands" / "profiles").mkdir(parents=True)
            (wd / ".openhands" / "profiles" / "spark2.5-4b.json").write_text("{}")
            subprocess.run(["git", "init"], cwd=wd, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=wd, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=wd, check=True)
            (wd / "x.py").write_text("x=1\n")
            subprocess.run(["git", "add", "x.py"], cwd=wd, check=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=wd, check=True, capture_output=True)

            request_sha = hashlib.sha256(b"fix").hexdigest()
            intake = {
                "schema_version": 2,
                "request_sha256": request_sha,
                "source": {"kind": "git_local", "source_root": "."},
            }
            state = {
                "schema_version": 2,
                "planner_calls": 1,
                "executor_calls": 0,
                "reviewer_calls": 0,
                "executor_attempts": {},
                "completed_nodes": {},
                "contract_sha256": None,
            }
            contract = {
                "schema_version": 1,
                "request_sha256": request_sha,
                "objective": "fix",
                "acceptance_criteria": ["works"],
                "constraints": [],
                "global_forbidden_paths": [".agents_tmp/", ".git/"],
                "final_validation_commands": [{
                    "argv": ["python", "-c", "raise SystemExit(0)"],
                    "expected_exit_code": 0,
                    "timeout_seconds": 30,
                }],
            }
            # Intentionally cyclic.
            graph = {
                "schema_version": 1,
                "nodes": [
                    {"id": "a", "goal": "a", "depends_on": ["b"]},
                    {"id": "b", "goal": "b", "depends_on": ["a"]},
                ],
                "current_node_id": "a",
            }
            work = {
                "schema_version": 1,
                "node_id": "a",
                "goal": "fix",
                "reuse_analysis": "checked existing code",
                "mutable_paths": ["x.py"],
                "forbidden_paths": [".agents_tmp/", ".git/"],
                "acceptance_criteria": ["works"],
                "validation_commands": [{
                    "argv": ["python", "-c", "raise SystemExit(0)"],
                    "expected_exit_code": 0,
                    "timeout_seconds": 30,
                }],
            }
            tmp = wd / ".agents_tmp"
            for name, obj in [
                ("INTAKE.json", intake),
                ("STATE.json", state),
                ("CONTRACT.json", contract),
                ("WORK_GRAPH.json", graph),
                ("CURRENT_WORK_ITEM.json", work),
                ("PLANNER_RESULT.json", {"verdict": "READY"}),
            ]:
                (tmp / name).write_text(json.dumps(obj))

            payload = {
                "working_dir": str(wd),
                "tool_input": {"subagent_type": "executor-spark"},
            }
            p = self.run_hook(command, payload, wd)
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertEqual(out["decision"], "deny")
            self.assertIn("acyclic", out["reason"])
            after = json.loads((tmp / "STATE.json").read_text())
            self.assertEqual(after["executor_calls"], 0)

    def test_completed_node_mutation_is_preflight_rejected(self):
        gate = self.task_hooks()[0]["command"]
        self.assertIn("WORK_GRAPH mutated a completed node", gate)
        self.assertIn("WORK_GRAPH removed a completed node", gate)

    def test_same_work_item_retry_requires_local_validation_fail(self):
        gate = self.task_hooks()[0]["command"]
        self.assertIn("same work item may be retried only after deterministic WORK_ITEM validation FAIL", gate)

    def test_validator_uses_shell_false(self):
        validator = self.task_hooks()[1]["command"]
        self.assertIn("shell=False", validator)
        self.assertIn("PYTHONDONTWRITEBYTECODE", validator)
        self.assertIn("-p no:cacheprovider", validator)
        self.assertIn("ENVIRONMENT_ERROR", validator)

    def test_intermediate_pass_returns_to_planner(self):
        validator = self.task_hooks()[1]["command"]
        self.assertIn("current work item PASS; graph still has incomplete nodes", validator)
        self.assertIn("Call planner-qwen", validator)

    def test_global_fail_returns_to_planner_not_same_executor(self):
        validator = self.task_hooks()[1]["command"]
        self.assertIn("global deterministic validation FAIL after graph completion", validator)
        self.assertIn("add a bounded corrective node", validator)

    def test_state_owns_completed_nodes(self):
        validator = self.task_hooks()[1]["command"]
        self.assertIn('completed[nid]=node_fingerprint(byid[nid])', validator)

    def test_parent_denies_direct_repo_tools(self):
        matcher = next(
            x["matcher"] for x in self.hooks()["pre_tool_use"]
            if "terminal|file_editor" in x.get("matcher", "")
        )
        for name in ("read_file", "write_file", "edit", "glob", "grep"):
            self.assertIn(name, matcher)

    def test_phase_marker_regexes_are_not_overescaped(self):
        command = self.hooks()["post_tool_use"][0]["hooks"][0]["command"]
        for marker in (
            "PLANNING_RESULT: READY",
            "EXECUTION_RESULT: READY_FOR_VALIDATION",
            "BLOCKED: REPLAN_REQUIRED",
        ):
            self.assertIn(marker + r"\s*$", command)
            self.assertNotIn(marker + r"\\s*$", command)

    def test_intake_ignores_agents_tmp_for_dirty_probe(self):
        intake = self.hooks()["user_prompt_submit"][0]["hooks"][0]["command"]
        self.assertIn(":(exclude).agents_tmp/**", intake)

    def test_policy_hooks_are_fail_closed(self):
        hooks = self.hooks()
        for event in ("session_start", "user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"):
            for matcher in hooks.get(event, []):
                for hook in matcher.get("hooks", []):
                    self.assertTrue(
                        hook["command"].rstrip().endswith("|| exit 2"),
                        hook["command"][:120],
                    )

    def test_inline_python_hooks_compile(self):
        commands = []
        hooks = self.hooks()

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

        for agent in ("planner-qwen.md", "executor-spark.md", "reviewer-qwen.md"):
            text = (ROOT / "agents" / agent).read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("command: "):
                    value = stripped[len("command: "):]
                    commands.append(json.loads(value))

        self.assertTrue(commands)
        for command in commands:
            argv = shlex.split(command)
            if len(argv) >= 3 and argv[0] == "python3" and argv[1] == "-c":
                compile(argv[2], "<hook>", "exec")


if __name__ == "__main__":
    unittest.main()
