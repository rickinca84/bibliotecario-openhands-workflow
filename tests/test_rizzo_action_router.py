import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "rizzo-flow" / "scripts" / "action_router.py"
spec = importlib.util.spec_from_file_location("action_router", SCRIPT)
router = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = router
assert spec.loader is not None
spec.loader.exec_module(router)


class RizzoActionRouterTests(unittest.TestCase):
    def candidates(self):
        return [
            {
                "id": "read_contract",
                "tool": "read_file",
                "arguments": {"file_path": ".agents_tmp/CONTRACT.json"},
                "description": "Read the locked contract because implementation requirements are needed.",
            },
            {
                "id": "run_targeted_tests",
                "tool": "terminal",
                "arguments": {"command": "python -m unittest tests.test_example"},
                "description": "Run the already-authorized targeted validator because code changes are ready to check.",
            },
        ]

    def test_rejects_single_candidate(self):
        with self.assertRaises(router.RouterError):
            router.normalize_candidates(self.candidates()[:1])

    def test_rejects_more_than_26_candidates(self):
        items = [
            {
                "id": f"a{i}",
                "tool": "read_file",
                "arguments": {"file_path": f"f{i}"},
                "description": f"Read permitted file {i}.",
            }
            for i in range(27)
        ]
        with self.assertRaises(router.RouterError):
            router.normalize_candidates(items)

    def test_build_request_preserves_only_candidate_ids_and_descriptions(self):
        candidates = router.normalize_candidates(self.candidates())
        req = router.build_request({"phase": "execute"}, candidates)
        options = req["questions"]["next_action"]["options"]
        self.assertEqual(
            options,
            [
                {"id": "read_contract", "description": self.candidates()[0]["description"]},
                {"id": "run_targeted_tests", "description": self.candidates()[1]["description"]},
            ],
        )
        self.assertNotIn("tool", options[0])

    def test_choose_action_maps_typed_choice_to_exact_candidate(self):
        def fake_caller(url, body, key, timeout):
            self.assertTrue(url.endswith("/v1/decisions"))
            self.assertEqual(len(body["questions"]["next_action"]["options"]), 2)
            return {
                "answers": {
                    "next_action": {
                        "status": "ok",
                        "choice": "run_targeted_tests",
                        "probabilities": {
                            "read_contract": 0.1,
                            "run_targeted_tests": 0.9,
                        },
                        "uncertainty": {"top_probability": 0.9},
                    }
                }
            }

        result = router.choose_action(
            {"phase": "validate"},
            self.candidates(),
            caller=fake_caller,
        )
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected"], self.candidates()[1])

    def test_uncertain_or_insufficient_evidence_never_executes(self):
        def fake_caller(url, body, key, timeout):
            return {
                "answers": {
                    "next_action": {
                        "status": "uncertain",
                        "choice": None,
                        "probabilities": {},
                        "uncertainty": {"top_probability": 0.51},
                    }
                }
            }

        decision = router.choose_action(
            {"phase": "unknown"},
            self.candidates(),
            caller=fake_caller,
        )
        calls = []

        result = router.dispatch(
            decision,
            lambda tool, arguments: calls.append((tool, arguments)),
        )
        self.assertEqual(result["status"], "not_executed")
        self.assertEqual(calls, [])

    def test_dispatch_executes_exact_selected_tool_and_arguments(self):
        decision = {
            "status": "selected",
            "reason": "rizzo_choice",
            "selected": self.candidates()[0],
            "rizzo": {"status": "ok"},
        }
        calls = []

        def executor(tool, arguments):
            calls.append((tool, arguments))
            return {"ok": True}

        result = router.dispatch(decision, executor)
        self.assertEqual(
            calls,
            [("read_file", {"file_path": ".agents_tmp/CONTRACT.json"})],
        )
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["result"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
