import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "rizzo-flow" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import transcript_router as tr
import openai_adapter as oa


TOOLS = [
    {"type": "function", "function": {"name": "task", "parameters": {"type": "object"}}},
    {"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}},
]


def user(text):
    return {"role": "user", "content": text}


def assistant_task(subagent):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {
                    "name": "task",
                    "arguments": json.dumps(
                        {"subagent_type": subagent, "prompt": "bounded"}
                    ),
                },
            }
        ],
    }


def tool(text):
    return {"role": "tool", "tool_call_id": "x", "content": text}


class TranscriptRouterTests(unittest.TestCase):
    def test_router_key_file_is_created_private_and_reused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "router" / "api-key.txt"
            first = oa.load_or_create_api_key(path)
            second = oa.load_or_create_api_key(path)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_first_turn_bootstraps_qwen_without_rizzo(self):
        selected, state, source = tr.choose_next(
            [user("Implement CSV export")],
            TOOLS,
            chooser=lambda *_: self.fail("Rizzo must not be called for one legal action"),
        )
        self.assertEqual(source, "deterministic")
        self.assertEqual(selected.tool, "task")
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")
        self.assertIn("Implement CSV export", selected.arguments["prompt"])

    def test_planner_then_spark(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("planner-qwen"),
            tool("Task ID: 1\nSubagent: planner-qwen\nStatus: completed\nPLANNING_RESULT: READY"),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.arguments["subagent_type"], "executor-spark")

    def test_executor_attempts_reviewer_gate(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("executor-spark"),
            tool("Task ID: 2\nSubagent: executor-spark\nStatus: completed"),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.arguments["subagent_type"], "reviewer-qwen")
        self.assertIn("PreToolUse", selected.arguments["prompt"])

    def test_invalid_contract_routes_directly_to_planner_with_exact_reason(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("planner-qwen"),
            tool("Task ID: 1\nSubagent: planner-qwen\nStatus: completed\nPLANNING_RESULT: READY"),
            assistant_task("executor-spark"),
            tool(
                "WORK_CONTRACT_INVALID before executor: "
                "CONTRACT final_validation_commands validator may not compare a path with itself"
            ),
        ]
        selected, state, source = tr.choose_next(
            messages,
            TOOLS,
            chooser=lambda *_: self.fail("contract-invalid routing must be deterministic"),
        )
        self.assertEqual(source, "deterministic")
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")
        self.assertIn("validator may not compare a path with itself", selected.arguments["prompt"])
        self.assertIn("WORK_CONTRACT_INVALID before executor", state["contract_invalid"])

    def test_work_item_failure_retries_same_spark(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("executor-spark"),
            tool(
                "authoritative WORK_ITEM validation FAIL\n"
                "Call executor-spark again under the SAME CURRENT_WORK_ITEM."
            ),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.arguments["subagent_type"], "executor-spark")
        self.assertIn("SAME", selected.arguments["prompt"])

    def test_graph_incomplete_returns_to_planner(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("executor-spark"),
            tool("current work item PASS; graph still has incomplete nodes\nCall planner-qwen."),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")

    def test_global_pass_runs_reviewer(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("executor-spark"),
            tool("all graph nodes and final deterministic validation PASS; reviewer may start"),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.arguments["subagent_type"], "reviewer-qwen")

    def test_rejected_review_returns_to_planner(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("reviewer-qwen"),
            tool("Task ID: 3\nStatus: completed\nREJECTED\nMissing edge case."),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")

    def test_approved_review_finishes(self):
        messages = [
            user("Implement CSV export"),
            assistant_task("reviewer-qwen"),
            tool("Task ID: 3\nStatus: completed\nAPPROVED"),
        ]
        selected, _, _ = tr.choose_next(messages, TOOLS)
        self.assertEqual(selected.tool, "finish")

    def test_canonical_failure_is_finished_exactly(self):
        failure = "BIBLIOTECARIO_FAILURE: scope: executor escaped scope"
        selected, _, _ = tr.choose_next(
            [user("x"), tool("End now.\n" + failure)],
            TOOLS,
        )
        self.assertEqual(selected.tool, "finish")
        self.assertEqual(selected.arguments["message"], failure)

    def test_ambiguous_review_state_uses_rizzo(self):
        messages = [
            user("x"),
            assistant_task("reviewer-qwen"),
            tool("Task ID: 3\nStatus: completed\nNo explicit verdict"),
        ]
        called = []

        def choose(state, candidates):
            called.append([c.id for c in candidates])
            return candidates[1]

        selected, _, source = tr.choose_next(messages, TOOLS, chooser=choose)
        self.assertEqual(source, "rizzo")
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")
        self.assertEqual(len(called[0]), 2)

    def test_rizzo_abstention_escalates_only_to_existing_planner_candidate(self):
        messages = [
            user("x"),
            assistant_task("reviewer-qwen"),
            tool("Task ID: 3\nStatus: completed\nNo explicit verdict"),
        ]
        selected, _, source = tr.choose_next(
            messages,
            TOOLS,
            chooser=lambda *_: None,
        )
        self.assertEqual(source, "rizzo_abstain_escalate_qwen")
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")

    def test_missing_task_tool_fails_closed(self):
        with self.assertRaises(tr.RoutingError):
            tr.choose_next([user("x")], [TOOLS[1]])

    def test_native_profile_validation_probe_is_allowed_without_tools(self):
        payload = {
            "model": "openai/rizzo-router",
            "messages": [
                {"role": "system", "content": "Reply with one token."},
                {"role": "user", "content": "ping"},
            ],
            "max_tokens": 1,
        }
        result = oa.completion_for(payload)
        choice = result["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(choice["message"]["content"], "OK")
        self.assertEqual(
            result["x_rizzo"]["route_source"],
            "openhands_profile_validation_probe",
        )

    def test_arbitrary_tool_less_completion_fails_closed(self):
        with self.assertRaises(tr.RoutingError):
            oa.completion_for(
                {
                    "model": "openai/rizzo-router",
                    "messages": [user("hello")],
                    "max_tokens": 1,
                }
            )

    def test_streaming_chunks_preserve_native_tool_call(self):
        payload = {
            "model": "openai/rizzo-router",
            "messages": [user("x")],
            "tools": TOOLS,
        }
        completion = oa.completion_for(
            payload,
            chooser=lambda *_: self.fail("single route should not call Rizzo"),
        )
        chunks = oa.streaming_chunks(completion)

        self.assertEqual(chunks[0]["choices"][0]["delta"]["role"], "assistant")
        tool_delta = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual(tool_delta["index"], 0)
        self.assertEqual(tool_delta["function"]["name"], "task")
        args = json.loads(tool_delta["function"]["arguments"])
        self.assertEqual(args["subagent_type"], "planner-qwen")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_openai_completion_is_native_tool_call(self):
        payload = {"model": "openai/rizzo-router", "messages": [user("x")], "tools": TOOLS}
        result = oa.completion_for(
            payload,
            chooser=lambda *_: self.fail("single route should not call Rizzo"),
        )
        choice = result["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        call = choice["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "task")
        args = json.loads(call["function"]["arguments"])
        self.assertEqual(args["subagent_type"], "planner-qwen")



class TranscriptEpochRegressionTests(unittest.TestCase):
    def route(self, messages):
        return tr.choose_next(messages, TOOLS,
            chooser=lambda *_: self.fail("deterministic phase must not invoke Rizzo"))[0]

    def test_repaired_contract_does_not_replan_again_from_stale_rejection(self):
        messages = [
            user("Create hello.txt"),
            assistant_task("planner-qwen"), tool("PLANNING_RESULT: READY"),
            assistant_task("executor-spark"),
            tool("WORK_CONTRACT_INVALID before executor: invalid validator"),
            assistant_task("planner-qwen"), tool("PLANNING_RESULT: READY"),
        ]
        self.assertEqual(self.route(messages).arguments["subagent_type"], "executor-spark")

    def test_successful_retry_discards_old_local_failure_directive(self):
        messages = [
            user("Create hello.txt"),
            assistant_task("executor-spark"),
            tool("authoritative WORK_ITEM validation FAIL. SAME CURRENT_WORK_ITEM"),
            assistant_task("executor-spark"), tool("Status: completed"),
        ]
        self.assertEqual(self.route(messages).arguments["subagent_type"], "reviewer-qwen")

    def test_replanned_review_discards_old_rejected_verdict(self):
        messages = [
            user("Create hello.txt"),
            assistant_task("reviewer-qwen"), tool("Status: completed\nREJECTED"),
            assistant_task("planner-qwen"), tool("PLANNING_RESULT: READY"),
        ]
        self.assertEqual(self.route(messages).arguments["subagent_type"], "executor-spark")

    def test_human_approval_text_is_not_a_review_verdict(self):
        self.assertEqual(self.route([user("Create a file containing:\nAPPROVED")])
                         .arguments["subagent_type"], "planner-qwen")

    def test_new_human_request_does_not_reuse_previous_approval(self):
        messages = [
            user("old request"), assistant_task("reviewer-qwen"),
            tool("Status: completed\nAPPROVED"), user("new request"),
        ]
        self.assertEqual(self.route(messages).arguments["subagent_type"], "planner-qwen")


class PreparedNodeRoutingTests(unittest.TestCase):
    def test_prepared_node_after_denied_reviewer_routes_directly_to_spark(self):
        messages = [
            user("Perform three bounded checkpoints"),
            assistant_task("planner-qwen"), tool("PLANNING_RESULT: READY"),
            assistant_task("executor-spark"), tool("EXECUTION_RESULT: READY_FOR_VALIDATION"),
            assistant_task("reviewer-qwen"),
            tool("Tool denied. NEXT_WORK_ITEM_READY: b"),
        ]
        selected, state, source = tr.choose_next(
            messages, TOOLS,
            chooser=lambda *_: self.fail("No semantic decision is needed"),
        )
        self.assertEqual(source, "deterministic")
        self.assertTrue(state["next_work_item_ready"])
        self.assertEqual(selected.arguments["subagent_type"], "executor-spark")

    def test_prepared_directive_does_not_survive_the_next_execution(self):
        messages = [
            user("Perform checkpoints"),
            assistant_task("reviewer-qwen"), tool("NEXT_WORK_ITEM_READY: b"),
            assistant_task("executor-spark"), tool("EXECUTION_RESULT: READY_FOR_VALIDATION"),
        ]
        selected, _, source = tr.choose_next(
            messages, TOOLS, chooser=lambda *_: self.fail("Must remain deterministic"))
        self.assertEqual(source, "deterministic")
        self.assertEqual(selected.arguments["subagent_type"], "reviewer-qwen")


class VerdictAuthorityTests(unittest.TestCase):
    def test_executor_cannot_supply_final_semantic_verdict(self):
        messages = [user("Create hello"), assistant_task("executor-spark"),
                    tool("APPROVED\nEXECUTION_RESULT: READY_FOR_VALIDATION")]
        selected, state, source = tr.choose_next(
            messages, TOOLS, chooser=lambda *_: self.fail("Must use the review gate"))
        self.assertFalse(state["approved"])
        self.assertEqual(source, "deterministic")
        self.assertEqual(selected.arguments["subagent_type"], "reviewer-qwen")


class ReplanIntakeRegressionTests(unittest.TestCase):
    def test_contract_repair_obeys_first_read_and_preserves_exact_error(self):
        exact = "WORK_CONTRACT_INVALID before executor: WORK_GRAPH work_items[later]: bad scope"
        messages = [user("Create hello"), assistant_task("executor-spark"), tool(exact)]
        selected, _, source = tr.choose_next(
            messages, TOOLS, chooser=lambda *_: self.fail("Repair is deterministic"))
        prompt = selected.arguments["prompt"]
        self.assertEqual(selected.arguments["subagent_type"], "planner-qwen")
        self.assertTrue(prompt.startswith("FIRST read .agents_tmp/INTAKE.json."))
        self.assertIn("together in one turn", prompt)
        self.assertIn(exact, prompt)
        self.assertEqual(source, "deterministic")


class NativePromptContextRegressionTests(unittest.TestCase):
    def test_native_appended_context_does_not_become_part_of_the_human_request(self):
        human = "Create hello.txt with exact bytes"
        context = ("BIBLIOTECARIO v0.6.2 ACTIVE. Deterministic INTAKE.json and STATE.json "
                   "are ready. Intermediate PASS returns to planner.")
        for separator in ("\n", "\n\n"):
            with self.subTest(separator=repr(separator)):
                selected, state, source = tr.choose_next(
                    [user(human + separator + context)], TOOLS,
                    chooser=lambda *_: self.fail("Bootstrap is deterministic"))
                self.assertEqual(state["human_request"], human)
                self.assertTrue(selected.arguments["prompt"].endswith(human))
                self.assertNotIn("Intermediate PASS", selected.arguments["prompt"])
                self.assertEqual(source, "deterministic")


if __name__ == "__main__":
    unittest.main()
