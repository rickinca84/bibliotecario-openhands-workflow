import copy
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile
import unittest

import test_v062_contract as existing
import test_rizzo_openai_adapter as routing


class WorkflowRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.wd = pathlib.Path(self.temp.name)
        self.helper = existing.V063ContractTests()
        self.hooks = self.helper.hooks()
        subprocess.run(["git", "init", "-b", "fixture"], cwd=self.wd,
                       check=True, capture_output=True)
        (self.wd / "README.md").write_text("Keep this unchanged.\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.wd, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-m", "baseline"],
                       cwd=self.wd, check=True, capture_output=True)
        self.run_hook(self.hooks["session_start"][0]["hooks"][0]["command"], {})
        self.run_hook(self.hooks["user_prompt_submit"][0]["hooks"][0]["command"],
                      {"message": "Create hello.txt with exactly Bibliotecario Rizzo test. Keep README.md unchanged."})
        intake = self.load("INTAKE.json")
        self.contract = {
            "schema_version": 1, "request_sha256": intake["request_sha256"],
            "objective": "Create hello.txt", "acceptance_criteria": ["Exact bytes"],
            "constraints": ["README.md unchanged"],
            "global_forbidden_paths": [".agents_tmp/", ".git/", "README.md"],
            "final_validation_commands": [self.validator()],
        }
        self.work = {
            "schema_version": 1, "node_id": "a", "goal": "Write exact bytes",
            "reuse_analysis": "Use native file tools",
            "mutable_paths": ["hello.txt"],
            "forbidden_paths": self.contract["global_forbidden_paths"],
            "acceptance_criteria": ["Exact bytes"],
            "validation_commands": [self.validator()],
        }
        self.graph = {"schema_version": 1, "current_node_id": "a",
                      "nodes": [{"id": "a", "goal": "Write exact bytes", "depends_on": []}]}
        self.write_bundle()
        self.assertEqual(self.gate("planner-qwen")["decision"], "allow")
        self.post("planner-qwen", "PLANNING_RESULT: READY")

    def validator(self):
        digest = hashlib.sha256((self.wd / "README.md").read_bytes()).hexdigest()
        return {"argv": ["python3", "-c",
                "from pathlib import Path; import hashlib; "
                "assert Path('hello.txt').read_bytes() == b'Bibliotecario Rizzo test'; "
                f"assert hashlib.sha256(Path('README.md').read_bytes()).hexdigest() == '{digest}'"],
                "expected_exit_code": 0, "timeout_seconds": 10}

    def run_hook(self, command, payload):
        result = self.helper.run_hook(command, {"working_dir": str(self.wd), **payload}, self.wd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout or "{}")

    def load(self, name):
        return json.loads((self.wd / ".agents_tmp" / name).read_text())

    def save(self, name, value):
        (self.wd / ".agents_tmp" / name).write_text(json.dumps(value))

    def write_bundle(self):
        for name, value in (("CONTRACT.json", self.contract),
                            ("WORK_GRAPH.json", self.graph),
                            ("CURRENT_WORK_ITEM.json", self.work)):
            self.save(name, value)

    def gate(self, agent):
        return self.run_hook(self.helper.task_hooks()[0]["command"],
                             {"tool_input": {"subagent_type": agent}})

    def post(self, agent, text):
        command = self.hooks["post_tool_use"][0]["hooks"][0]["command"]
        return self.run_hook(command, {"tool_input": {"subagent_type": agent},
                             "tool_response": {"status": "completed", "text": text}})

    def execute(self):
        result = self.gate("executor-spark")
        self.assertEqual(result["decision"], "allow", result)
        (self.wd / "hello.txt").write_bytes(b"Bibliotecario Rizzo test")
        self.post("executor-spark", "EXECUTION_RESULT: READY_FOR_VALIDATION")

    def validate(self):
        result = self.gate("reviewer-qwen")
        self.assertEqual(result["decision"], "allow", result)
        return self.run_hook(self.helper.task_hooks()[1]["command"],
                             {"tool_input": {"subagent_type": "reviewer-qwen"}})

    def test_invalid_contract_allows_only_one_replan_and_keeps_exact_reason(self):
        self.contract["final_validation_commands"] = []
        self.write_bundle()
        first = self.gate("executor-spark")
        reason = self.load("PLANNER_RESULT.json")["reason"]
        self.assertIn(reason, first["reason"])
        self.assertEqual(self.gate("planner-qwen")["decision"], "allow")
        self.post("planner-qwen", "PLANNING_RESULT: READY")
        second = self.gate("executor-spark")
        self.assertEqual(second["decision"], "deny")
        self.assertTrue((self.wd / ".agents_tmp/TERMINAL_FAILURE.json").is_file())
        self.assertIn(reason, self.load("TERMINAL_FAILURE.json")["reason"])
        self.assertIn("BIBLIOTECARIO_FAILURE:", second["additionalContext"])
        self.assertEqual(self.gate("planner-qwen")["decision"], "deny")
        self.assertEqual(self.load("STATE.json")["planner_calls"], 2)
        self.assertEqual(self.load("STATE.json")["executor_calls"], 0)

    def test_rejects_vacuous_success_even_when_fail_token_is_present(self):
        scripts = (
            "assert True",
            "assert 1 == 1",
            "assert len('constant')",
            "ok = True; assert ok",
            "raise SystemExit(0)",
            "import sys; sys.exit(0)",
            "# assert False\nprint('ok')",
            "from pathlib import Path; assert True or Path('hello.txt').exists()",
        )
        initial_state = self.load("STATE.json")
        for script in scripts:
            with self.subTest(script=script):
                self.save("STATE.json", initial_state)
                for name in ("TERMINAL_FAILURE.json", "EXECUTION_BASELINE.json"):
                    (self.wd / ".agents_tmp" / name).unlink(missing_ok=True)
                self.contract["final_validation_commands"] = [
                    {"argv": ["python3", "-c", script],
                     "expected_exit_code": 0, "timeout_seconds": 10}]
                self.write_bundle()
                self.save("PLANNER_RESULT.json", {"verdict": "READY"})
                result = self.gate("executor-spark")
                self.assertEqual(result["decision"], "deny", result)
                self.assertIn("validator", result["reason"])

    def test_cached_dependency_ready_items_advance_without_planner(self):
        self.graph["nodes"] += [
            {"id": "z", "goal": "Verify unchanged README", "depends_on": ["a"]},
            {"id": "b", "goal": "Verify exact bytes", "depends_on": ["a"]},
        ]
        self.graph["work_items"] = {}
        for nid in ("z", "b"):
            item = copy.deepcopy(self.work)
            item["node_id"] = nid
            self.graph["work_items"][nid] = item
        self.write_bundle()
        for expected_next in ("b", "z", None):
            self.execute()
            result = self.validate()
            if expected_next:
                self.assertEqual(result["decision"], "deny")
                self.assertIn("NEXT_WORK_ITEM_READY: " + expected_next, result["reason"])
                selected, _, source = routing.tr.choose_next(
                    [routing.user("Create hello.txt"),
                     routing.assistant_task("reviewer-qwen"),
                     routing.tool(json.dumps(result))],
                    routing.TOOLS,
                    chooser=lambda *_: self.fail("Prepared transition must not invoke Rizzo/Qwen"),
                )
                self.assertEqual(source, "deterministic")
                self.assertEqual(selected.arguments["subagent_type"], "executor-spark")
                self.assertEqual(self.load("WORK_GRAPH.json")["current_node_id"], expected_next)
                self.assertEqual(self.load("CURRENT_WORK_ITEM.json")["node_id"], expected_next)
            else:
                self.assertEqual(result["decision"], "allow", result)
        state = self.load("STATE.json")
        self.assertEqual(state["planner_calls"], 1)
        self.assertEqual(state["executor_calls"], 3)
        self.assertEqual(state["reviewer_calls"], 1)
        self.assertEqual(state["deterministic_advances"], 2)
        validation = self.load("VALIDATION.json")
        self.assertEqual(validation["overall"], "PASS")
        self.assertEqual(validation["phase"], "GLOBAL")
        self.assertTrue(validation["graph_complete"])

    def test_invalid_cached_scope_is_rejected_before_first_executor(self):
        item = copy.deepcopy(self.work)
        item["node_id"] = "b"
        item["mutable_paths"] = ["README.md"]
        self.graph["nodes"].append({"id": "b", "goal": "later", "depends_on": ["a"]})
        self.graph["work_items"] = {"b": item}
        self.write_bundle()
        result = self.gate("executor-spark")
        self.assertEqual(result["decision"], "deny", result)
        self.assertEqual(self.load("STATE.json")["executor_calls"], 0)

    def test_wrong_file_bytes_really_fail_deterministic_validation(self):
        self.execute()
        (self.wd / "hello.txt").write_bytes(b"wrong")
        result = self.validate()
        self.assertEqual(result["decision"], "deny")
        validation = self.load("VALIDATION.json")
        self.assertEqual(validation["overall"], "FAIL")
        self.assertNotEqual(validation["work_item_commands"][0]["exit_code"], 0)


    def test_node_unconditional_success_is_rejected(self):
        for script in ("process.exit(0)", "require('assert')(true); // assert(true)",
                       "assert(true)"):
            with self.subTest(script=script):
                self.contract["final_validation_commands"] = [{
                    "argv": ["node", "-e", script],
                    "expected_exit_code": 0, "timeout_seconds": 10,
                }]
                self.write_bundle()
                self.save("PLANNER_RESULT.json", {"verdict": "READY"})
                result = self.gate("executor-spark")
                self.assertEqual(result["decision"], "deny", result)
                self.assertIn("vacuous", result["reason"])


    def test_corrected_contract_executes_after_the_single_replan(self):
        self.contract["final_validation_commands"] = []
        self.write_bundle()
        self.assertEqual(self.gate("executor-spark")["decision"], "deny")
        self.assertEqual(self.gate("planner-qwen")["decision"], "allow")
        self.contract["final_validation_commands"] = [self.validator()]
        self.write_bundle()
        self.post("planner-qwen", "PLANNING_RESULT: READY")
        self.execute()
        self.assertEqual(self.validate()["decision"], "allow")
        self.assertEqual(self.load("STATE.json")["contract_replans"], 1)
        self.assertEqual(self.load("STATE.json")["planner_calls"], 2)
        self.assertFalse((self.wd / ".agents_tmp/TERMINAL_FAILURE.json").exists())

    def test_readme_change_is_authoritatively_rejected(self):
        self.execute()
        (self.wd / "README.md").write_text("unauthorized change")
        result = self.validate()
        self.assertEqual(result["decision"], "deny")
        validation = self.load("VALIDATION.json")
        self.assertEqual(validation["overall"], "FAIL")
        self.assertEqual(validation["phase"], "SCOPE")
        self.assertIn("README.md", validation["scope"]["violations"])


    def test_prepared_read_only_checkpoint_advances_and_validates(self):
        self.graph["nodes"].append({
            "id": "read-only", "goal": "Verify exact bytes", "depends_on": ["a"]})
        item = copy.deepcopy(self.work)
        item.update(node_id="read-only", mutable_paths=[])
        self.graph["work_items"] = {"read-only": item}
        self.write_bundle()
        self.execute()
        self.assertIn("NEXT_WORK_ITEM_READY", self.validate()["reason"])
        self.assertEqual(self.gate("executor-spark")["decision"], "allow")
        self.post("executor-spark", "EXECUTION_RESULT: READY_FOR_VALIDATION")
        self.assertEqual(self.validate()["decision"], "allow")
        self.assertEqual(self.load("STATE.json")["planner_calls"], 1)
        self.assertEqual(self.load("VALIDATION.json")["phase"], "GLOBAL")

    def test_read_only_checkpoint_rejects_even_previously_mutable_file_changes(self):
        self.work["mutable_paths"] = []
        self.write_bundle()
        self.assertEqual(self.gate("executor-spark")["decision"], "allow")
        (self.wd / "hello.txt").write_bytes(b"Bibliotecario Rizzo test")
        self.post("executor-spark", "EXECUTION_RESULT: READY_FOR_VALIDATION")
        self.assertEqual(self.validate()["decision"], "deny")
        self.assertIn("hello.txt", self.load("VALIDATION.json")["scope"]["violations"])

    def test_invalid_prepared_item_reason_identifies_the_node(self):
        item = copy.deepcopy(self.work)
        item.update(node_id="later", mutable_paths=["README.md"])
        self.graph["nodes"].append({
            "id": "later", "goal": "later", "depends_on": ["a"]})
        self.graph["work_items"] = {"later": item}
        self.write_bundle()
        result = self.gate("executor-spark")
        self.assertIn("WORK_GRAPH work_items[later]", result["reason"])


    def test_malformed_contract_reports_the_actual_json_error(self):
        (self.wd / ".agents_tmp/CONTRACT.json").write_text(
            '{"schema_version": 1, "broken": "unterminated}')
        result = self.gate("executor-spark")
        self.assertEqual(result["decision"], "deny")
        self.assertIn("CONTRACT.json invalid JSON at line 1 column", result["reason"])
        self.assertIn("Unterminated string", result["reason"])
        self.assertNotIn("schema_version must be", result["reason"])

    def test_planner_rejects_malformed_json_before_native_write(self):
        text = (existing.ROOT / "agents/planner-qwen.md").read_text()
        commands = [json.loads(m.group(1)) for m in re.finditer(
            r'^\s*- command: (".*")$', text, re.M)]
        self.save("PLANNER_INTAKE_READ.json", {"session_id": "repair"})
        base = {"session_id": "repair", "tool_name": "write_file"}
        bad = self.run_hook(commands[0], {**base, "tool_input": {
            "file_path": ".agents_tmp/CONTRACT.json",
            "content": '{"schema_version": 1, "broken": "unterminated}',
        }})
        self.assertEqual(bad["decision"], "deny")
        self.assertIn("invalid JSON at line 1 column", bad["reason"])
        good = self.run_hook(commands[0], {**base, "tool_input": {
            "file_path": ".agents_tmp/CONTRACT.json",
            "content": json.dumps(self.contract),
        }})
        self.assertEqual(good["decision"], "allow", good)


    def test_terminal_failure_closes_from_reason_only_sdk_observation(self):
        self.contract["final_validation_commands"] = []
        self.write_bundle()
        self.assertEqual(self.gate("executor-spark")["decision"], "deny")
        self.assertEqual(self.gate("planner-qwen")["decision"], "allow")
        self.post("planner-qwen", "PLANNING_RESULT: READY")
        denied = self.gate("executor-spark")
        expected = "BIBLIOTECARIO_FAILURE: contract: " + self.load("TERMINAL_FAILURE.json")["reason"]
        self.assertEqual(denied["reason"], expected)
        selected, _, source = routing.tr.choose_next(
            [routing.user("Create hello.txt"),
             routing.assistant_task("executor-spark"),
             routing.tool("Tool call blocked by hook: " + denied["reason"])],
            routing.TOOLS, chooser=lambda *_: self.fail("Terminal failure is deterministic"))
        self.assertEqual(selected.tool, "finish")
        self.assertEqual(selected.arguments["message"], expected)
        self.assertEqual(source, "deterministic")
        payload = {"tool_name": "finish", "tool_input": selected.arguments}
        self.assertEqual(self.run_hook(self.helper.finish_hook(), payload)["decision"], "allow")
        self.run_hook(self.helper.post_finish_hook(), payload)
        self.assertEqual(self.run_hook(self.helper.stop_hook(), {})["decision"], "allow")
        self.assertEqual(self.load("STATE.json")["planner_calls"], 2)

    def test_planner_rejects_invalid_inline_python_before_native_write(self):
        text = (existing.ROOT / "agents/planner-qwen.md").read_text()
        command = json.loads(next(re.finditer(r'^\s*- command: (".*")$', text, re.M)).group(1))
        self.save("PLANNER_INTAKE_READ.json", {"session_id": "repair"})
        self.contract["final_validation_commands"][0]["argv"][2] = "assert b'broken\n' == b'broken'"
        result = self.run_hook(command, {
            "session_id": "repair", "tool_name": "write_file",
            "tool_input": {"file_path": ".agents_tmp/CONTRACT.json",
                           "content": json.dumps(self.contract)},
        })
        self.assertEqual(result["decision"], "deny")
        self.assertIn("CONTRACT.json validator invalid Python at line 1", result["reason"])
        self.assertIn("unterminated string literal", result["reason"])
