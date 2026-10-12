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


if __name__ == "__main__":
    unittest.main()
