import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class V063ContractTests(unittest.TestCase):
    def hooks(self):
        return json.loads((ROOT / "hooks" / "hooks.json").read_text())

    def task_hooks(self):
        return next(
            x for x in self.hooks()["pre_tool_use"] if x.get("matcher") == "task"
        )["hooks"]

    def finish_hook(self):
        return next(
            x for x in self.hooks()["pre_tool_use"]
            if x.get("matcher") == "finish"
        )["hooks"][0]["command"]

    def post_finish_hook(self):
        return next(
            x for x in self.hooks()["post_tool_use"]
            if x.get("matcher") == "finish"
        )["hooks"][0]["command"]

    def stop_hook(self):
        return self.hooks()["stop"][0]["hooks"][0]["command"]

    def run_hook(self, command, payload, cwd, extra_env=None):
        env = {
            **os.environ,
            "OH_PERSISTENCE_DIR": str(pathlib.Path(cwd) / ".openhands"),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", "-lc", command],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=cwd,
            env=env,
        )

    def write_agent_message_event(self, conversations, session_id, text, event_id):
        events = conversations / session_id.replace("-", "") / "events"
        events.mkdir(parents=True, exist_ok=True)
        path = events / f"event-00001-{event_id}.json"
        path.write_text(
            json.dumps({
                "id": event_id,
                "kind": "MessageEvent",
                "source": "agent",
                "llm_message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }),
            encoding="utf-8",
        )
        return path

    def test_manifest_is_v063(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.6.3")
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

    def test_subagents_do_not_depend_on_obsolete_rizzo_mcp_bridge(self):
        for rel in (
            "agents/planner-qwen.md",
            "agents/executor-spark.md",
            "agents/reviewer-qwen.md",
        ):
            text = (ROOT / rel).read_text()
            front = text.split("---", 2)[1]
            self.assertNotIn("mcp_config:", front)
            self.assertNotIn("18018/mcp", text)
            self.assertNotIn("decisions_v1_decisions_post", text)

        planner = (ROOT / "agents" / "planner-qwen.md").read_text()
        self.assertIn("native/upstream/installable", planner)

    def test_intake_preserves_request_and_does_not_treat_readme_as_instruction(self):
        command = self.hooks()["user_prompt_submit"][0]["hooks"][0]["command"]
        self.assertIn('"request_text":message', command)
        self.assertNotIn('low.startswith("readme")', command)

    def test_planner_is_bounded_and_context_lean(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        front = text.split("---", 2)[1]
        self.assertRegex(front, r"(?m)^max_iteration_per_run:\s*5\s*$")
        self.assertRegex(front, r"(?m)^condenser:\s*none\s*$")
        self.assertNotRegex(front, r"(?m)^\s*-\s*glob\s*$")
        self.assertRegex(front, r"(?m)^\s*-\s*grep\s*$")
        self.assertIn("issue all three `write_file` calls in the same LLM turn", text)

    def test_planner_anchors_to_request_text_and_requires_validation_commands(self):
        text = (ROOT / "agents" / "planner-qwen.md").read_text()
        self.assertIn("INTAKE.json.request_text", text)
        self.assertIn("authoritative human request", text)
        self.assertIn("MUST contain 1..20 deterministic commands", text)

    def test_session_start_seeds_subagent_llm_profiles(self):
        hooks = self.hooks()
        session = hooks["session_start"][0]["hooks"][0]["command"]
        self.assertIn('"spark2.5-4b"', session)
        self.assertIn('"qwen80b-planner"', session)
        self.assertIn(
            '"http://host.docker.internal:30000/v1"',
            session,
        )
        self.assertIn(
            '"http://host.docker.internal:8080/v1"',
            session,
        )

    def test_subagent_models_are_explicit_under_rizzo_parent(self):
        planner = (ROOT / "agents" / "planner-qwen.md").read_text()
        reviewer = (ROOT / "agents" / "reviewer-qwen.md").read_text()
        executor = (ROOT / "agents" / "executor-spark.md").read_text()

        self.assertRegex(
            planner.split("---", 2)[1],
            r"(?m)^model:\s*qwen80b-planner\s*$",
        )
        self.assertRegex(
            reviewer.split("---", 2)[1],
            r"(?m)^model:\s*qwen80b-planner\s*$",
        )
        self.assertRegex(
            executor.split("---", 2)[1],
            r"(?m)^model:\s*spark2\.5-4b\s*$",
        )

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

    def test_preflight_rejects_vacuous_validation_commands(self):
        gate = self.task_hooks()[0]["command"]
        self.assertIn("inline Python validator must fail explicitly", gate)
        self.assertIn("validator may not compare a path with itself", gate)
        planner = (ROOT / "agents" / "planner-qwen.md").read_text()
        self.assertIn("never use a bare boolean expression", planner)
        self.assertIn("Never compare a file with itself", planner)

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

    def test_executor_native_completed_status_hands_off_to_validation(self):
        command = self.hooks()["post_tool_use"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            tmp = wd / ".agents_tmp"
            tmp.mkdir()

            payload = {
                "working_dir": str(wd),
                "tool_input": {
                    "subagent_type": "executor-spark",
                },
                "tool_response": {
                    "content": [
                        {
                            "type": "text",
                            "text": "The fix is complete.",
                        }
                    ],
                    "is_error": False,
                    "task_id": "task_00000002",
                    "subagent": "executor-spark",
                    "status": "completed",
                    "kind": "TaskObservation",
                },
            }

            p = self.run_hook(command, payload, wd)
            self.assertEqual(p.returncode, 0, p.stderr)

            result = json.loads((tmp / "EXECUTOR_RESULT.json").read_text())
            self.assertEqual(result["verdict"], "READY_FOR_VALIDATION")
            self.assertEqual(result["task_status"], "completed")
            self.assertEqual(
                result["completion_source"],
                "native_task_status",
            )
            self.assertFalse((tmp / "TERMINAL_FAILURE.json").exists())

    def test_finish_denies_success_claim_after_terminal_failure(self):
        command = self.finish_hook()

        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            tmp = wd / ".agents_tmp"
            tmp.mkdir()

            failure = {
                "phase": "executor",
                "reason": "executor failed or returned invalid result",
            }
            (tmp / "TERMINAL_FAILURE.json").write_text(
                json.dumps(failure),
                encoding="utf-8",
            )

            bad = self.run_hook(
                command,
                {
                    "working_dir": str(wd),
                    "tool_input": {
                        "message": "The bug has been fixed successfully."
                    },
                },
                wd,
            )
            self.assertEqual(bad.returncode, 0, bad.stderr)
            bad_out = json.loads(bad.stdout)
            self.assertEqual(bad_out["decision"], "deny")
            self.assertIn("cannot report success", bad_out["reason"])

            expected = (
                "BIBLIOTECARIO_FAILURE: executor: "
                "executor failed or returned invalid result"
            )

            good = self.run_hook(
                command,
                {
                    "working_dir": str(wd),
                    "tool_input": {
                        "message": expected
                    },
                },
                wd,
            )
            self.assertEqual(good.returncode, 0, good.stderr)
            good_out = json.loads(good.stdout)
            self.assertEqual(good_out["decision"], "allow")

            # PreToolUse only authorizes the message. It must not create proof.
            self.assertFalse((tmp / "FAILURE_REPORT.json").exists())

            post = self.run_hook(
                self.post_finish_hook(),
                {
                    "working_dir": str(wd),
                    "tool_input": {
                        "message": expected
                    },
                    "tool_response": {
                        "kind": "FinishObservation",
                        "content": [
                            {
                                "type": "text",
                                "text": expected,
                            }
                        ],
                    },
                },
                wd,
            )
            self.assertEqual(post.returncode, 0, post.stderr)

            report_path = tmp / "FAILURE_REPORT.json"
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text())
            self.assertEqual(report["proof"], "PostToolUse:finish")
            self.assertEqual(
                report["terminal_sha256"],
                hashlib.sha256(
                    (tmp / "TERMINAL_FAILURE.json").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(report["message"], expected)

            stop = self.run_hook(
                self.stop_hook(),
                {
                    "working_dir": str(wd),
                },
                wd,
            )
            self.assertEqual(stop.returncode, 0, stop.stderr)
            stop_out = json.loads(stop.stdout)
            self.assertEqual(stop_out["decision"], "allow")

    def test_stop_denies_direct_completion_after_terminal_failure(self):
        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            tmp = wd / ".agents_tmp"
            tmp.mkdir()

            failure = {
                "phase": "executor",
                "reason": "executor failed or returned invalid result",
            }
            (tmp / "TERMINAL_FAILURE.json").write_text(
                json.dumps(failure),
                encoding="utf-8",
            )

            p = self.run_hook(
                self.stop_hook(),
                {
                    "working_dir": str(wd),
                },
                wd,
            )

            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)

            self.assertEqual(out["decision"], "deny")
            self.assertIn(
                "terminal failure requires canonical failure completion",
                out["reason"],
            )
            self.assertIn("Do not call any tool.", out["additionalContext"])
            self.assertIn("Do not write finish(...).", out["additionalContext"])
            self.assertIn(
                "BIBLIOTECARIO_FAILURE: executor: "
                "executor failed or returned invalid result",
                out["additionalContext"],
            )
            self.assertFalse((tmp / "FAILURE_REPORT.json").exists())

    def test_stop_accepts_exact_canonical_agent_message(self):
        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            tmp = wd / ".agents_tmp"
            tmp.mkdir()

            terminal = tmp / "TERMINAL_FAILURE.json"
            terminal.write_text(
                json.dumps({
                    "phase": "provenance",
                    "reason": "CONTRACT hash differs from deterministic lock",
                }),
                encoding="utf-8",
            )
            expected = (
                "BIBLIOTECARIO_FAILURE: provenance: "
                "CONTRACT hash differs from deterministic lock"
            )

            conversations = wd / "conversations"
            session_id = "2709b4e0-9208-482c-98f9-fdb5926bc170"
            event_id = "canonical-failure-event"
            self.write_agent_message_event(
                conversations, session_id, expected, event_id
            )

            p = self.run_hook(
                self.stop_hook(),
                {"working_dir": str(wd)},
                wd,
                {
                    "OH_CONVERSATIONS_PATH": str(conversations),
                    "OPENHANDS_SESSION_ID": session_id,
                },
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertEqual(out["decision"], "allow")
            self.assertIn("canonical terminal failure", out["reason"])

            report = json.loads((tmp / "FAILURE_REPORT.json").read_text())
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(report["proof"], "Stop:verified-agent-message")
            self.assertEqual(report["event_id"], event_id)
            self.assertEqual(report["message"], expected)
            self.assertEqual(
                report["terminal_sha256"],
                hashlib.sha256(terminal.read_bytes()).hexdigest(),
            )

    def test_stop_rejects_pseudo_finish_text_and_requests_plain_failure(self):
        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            tmp = wd / ".agents_tmp"
            tmp.mkdir()

            terminal = tmp / "TERMINAL_FAILURE.json"
            terminal.write_text(
                json.dumps({
                    "phase": "provenance",
                    "reason": "CONTRACT hash differs from deterministic lock",
                }),
                encoding="utf-8",
            )
            expected = (
                "BIBLIOTECARIO_FAILURE: provenance: "
                "CONTRACT hash differs from deterministic lock"
            )
            pseudo = f'finish(message="{expected}")'

            conversations = wd / "conversations"
            session_id = "2709b4e0-9208-482c-98f9-fdb5926bc170"
            self.write_agent_message_event(
                conversations, session_id, pseudo, "pseudo-finish-event"
            )

            p = self.run_hook(
                self.stop_hook(),
                {"working_dir": str(wd)},
                wd,
                {
                    "OH_CONVERSATIONS_PATH": str(conversations),
                    "OPENHANDS_SESSION_ID": session_id,
                },
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertEqual(out["decision"], "deny")
            self.assertIn(expected, out["additionalContext"])
            self.assertIn("Do not call any tool.", out["additionalContext"])
            self.assertIn("Do not write finish(...).", out["additionalContext"])
            self.assertFalse((tmp / "FAILURE_REPORT.json").exists())

    def test_stop_rejects_stale_failure_report_hash(self):
        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            tmp = wd / ".agents_tmp"
            tmp.mkdir()

            terminal = tmp / "TERMINAL_FAILURE.json"
            terminal.write_text(
                json.dumps({
                    "phase": "executor",
                    "reason": "first failure",
                }),
                encoding="utf-8",
            )

            expected = (
                "BIBLIOTECARIO_FAILURE: executor: first failure"
            )

            pre = self.run_hook(
                self.finish_hook(),
                {
                    "working_dir": str(wd),
                    "tool_input": {
                        "message": expected,
                    },
                },
                wd,
            )
            self.assertEqual(pre.returncode, 0, pre.stderr)
            self.assertEqual(json.loads(pre.stdout)["decision"], "allow")

            post = self.run_hook(
                self.post_finish_hook(),
                {
                    "working_dir": str(wd),
                    "tool_input": {
                        "message": expected,
                    },
                    "tool_response": {
                        "kind": "FinishObservation",
                    },
                },
                wd,
            )
            self.assertEqual(post.returncode, 0, post.stderr)
            self.assertTrue((tmp / "FAILURE_REPORT.json").is_file())

            # Change the authoritative terminal failure after the proof.
            terminal.write_text(
                json.dumps({
                    "phase": "executor",
                    "reason": "second failure",
                }),
                encoding="utf-8",
            )

            stop = self.run_hook(
                self.stop_hook(),
                {
                    "working_dir": str(wd),
                },
                wd,
            )
            self.assertEqual(stop.returncode, 0, stop.stderr)

            out = json.loads(stop.stdout)
            self.assertEqual(out["decision"], "deny")
            self.assertIn(
                "BIBLIOTECARIO_FAILURE: executor: second failure",
                out["additionalContext"],
            )

    def test_finish_denies_unverified_success(self):
        command = self.finish_hook()

        with tempfile.TemporaryDirectory() as td:
            wd = pathlib.Path(td)
            (wd / ".agents_tmp").mkdir()

            p = self.run_hook(
                command,
                {
                    "working_dir": str(wd),
                    "tool_input": {
                        "message": "Everything passed."
                    },
                },
                wd,
            )

            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertEqual(out["decision"], "deny")
            self.assertIn("verified final success", out["reason"])

    def test_intake_ignores_agents_tmp_and_clears_failure_report(self):
        intake = self.hooks()["user_prompt_submit"][0]["hooks"][0]["command"]
        self.assertIn(":(exclude).agents_tmp/**", intake)
        self.assertIn("FAILURE_REPORT.json", intake)

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
