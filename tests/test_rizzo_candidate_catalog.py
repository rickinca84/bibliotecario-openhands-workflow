import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "rizzo-flow" / "scripts" / "candidate_catalog.py"
spec = importlib.util.spec_from_file_location("candidate_catalog", SCRIPT)
catalog = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(catalog)


class CandidateCatalogTests(unittest.TestCase):
    def test_uses_actual_available_tools(self):
        state = {
            "has_contract": True,
            "has_current_work_item": True,
            "phase": "execute",
        }
        candidates = catalog.build_candidates(state, {"read_file"})
        self.assertEqual([c["id"] for c in candidates], ["read_contract", "read_current_work_item"])
        self.assertTrue(all(c["tool"] == "read_file" for c in candidates))

    def test_spark_is_default_bounded_executor_when_task_exists(self):
        state = {"has_current_work_item": True}
        candidates = catalog.build_candidates(state, {"task"})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "delegate_spark")
        self.assertEqual(candidates[0]["arguments"]["agent"], "executor-spark")

    def test_retry_only_after_local_deterministic_failure_and_budget(self):
        state = {
            "has_current_work_item": True,
            "validation": {"scope": "local", "status": "FAIL"},
        }
        ids = [c["id"] for c in catalog.build_candidates(state, {"task"})]
        self.assertNotIn("retry_spark", ids)

        state["retry_allowed"] = True
        ids = [c["id"] for c in catalog.build_candidates(state, {"task"})]
        self.assertIn("retry_spark", ids)

    def test_qwen_escalation_only_when_needed(self):
        state = {"routing_uncertain": True}
        candidates = catalog.build_candidates(state, {"task"})
        self.assertEqual(candidates[0]["id"], "escalate_qwen")
        self.assertEqual(candidates[0]["arguments"]["agent"], "planner-qwen")

    def test_mutations_require_complete_prepared_call(self):
        state = {
            "prepared_calls": [
                {
                    "id": "edit_ready",
                    "tool": "edit",
                    "arguments": {
                        "path": "src/a.py",
                        "old_str": "x = 1",
                        "new_str": "x = 2",
                    },
                    "description": "Apply the already-prepared bounded replacement.",
                },
                {
                    "id": "bad_write",
                    "tool": "write_file",
                    "description": "Missing arguments and must not be exposed.",
                },
            ]
        }
        candidates = catalog.build_candidates(state, {"edit", "write_file"})
        self.assertEqual([c["id"] for c in candidates], ["edit_ready"])

    def test_no_finish_or_pass_candidate_is_generated(self):
        state = {
            "has_contract": True,
            "has_current_work_item": True,
            "has_validation": True,
            "validation": {"scope": "local", "status": "PASS"},
        }
        candidates = catalog.build_candidates(
            state, {"read_file", "task", "finish", "terminal"}
        )
        tools = {c["tool"] for c in candidates}
        ids = {c["id"] for c in candidates}
        self.assertNotIn("finish", tools)
        self.assertFalse(any("pass" in cid for cid in ids))

    def test_frozen_validation_is_not_synthesized(self):
        state = {"frozen_validation_command": {"argv": ["python", "-m", "unittest"]}}
        candidates = catalog.build_candidates(state, {"terminal"})
        self.assertEqual(candidates[0]["arguments"], {"argv": ["python", "-m", "unittest"]})

    def test_catalog_rejects_more_than_26_after_build(self):
        candidates = [
            {
                "id": f"x{i}",
                "tool": "read_file",
                "arguments": {"file_path": f"{i}.txt"},
                "description": "Read one prepared file.",
            }
            for i in range(27)
        ]
        with self.assertRaises(catalog.CatalogError):
            catalog.validate_candidate_set(candidates)


if __name__ == "__main__":
    unittest.main()
