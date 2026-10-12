import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT = ROOT / "skills" / "rizzo-flow" / "scripts" / "rizzo_agent.py"


class RizzoAgentContractTests(unittest.TestCase):
    def text(self):
        return AGENT.read_text(encoding="utf-8")

    def test_agent_subclasses_native_openhands_agent(self):
        tree = ast.parse(self.text())
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        rizzo = next(c for c in classes if c.name == "RizzoAgent")
        self.assertEqual([ast.unparse(base) for base in rizzo.bases], ["Agent"])

    def test_never_uses_direct_execute_tool_bypass(self):
        text = self.text()
        self.assertNotIn(".execute_tool(", text)
        self.assertNotIn("_execute_action_event(", text)
        self.assertIn("self._execute_actions(", text)

    def test_emits_normal_action_event(self):
        text = self.text()
        self.assertIn("ActionEvent(", text)
        self.assertIn("MessageToolCall(", text)
        self.assertIn("tool.action_type.model_validate(arguments)", text)
        self.assertIn("on_event(action_event)", text)

    def test_catalog_uses_actual_native_tools_map(self):
        text = self.text()
        self.assertIn("build_candidates(snapshot, self.tools_map.keys())", text)

    def test_rizzo_uncertainty_can_only_escalate_to_catalogued_qwen(self):
        text = self.text()
        self.assertIn('("escalate_qwen", "bootstrap_qwen_contract")', text)
        self.assertIn("return self._pick_escalation(candidates)", text)


if __name__ == "__main__":
    unittest.main()
