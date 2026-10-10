---
description: Run Bibliotecario v0.6.0 deterministic intake, dynamic work graph, bounded execution, validation, and final review.
argument-hint: <software engineering request>
---

Execute this state machine for:

$ARGUMENTS

ROLE OF THE PARENT

You orchestrate only. Do not inspect/edit the repository, run commands, validate,
review, switch LLMs, or substitute another agent.

1. UserPromptSubmit deterministically creates INTAKE.json and STATE.json.
2. Call a fresh `planner-qwen` task with the complete user request and omit `resume`.
3. Planner creates/updates CONTRACT.json, WORK_GRAPH.json, CURRENT_WORK_ITEM.json.
4. Call a fresh `executor-spark` task and omit `resume`.
5. Parent PreToolUse validates the JSON bundle BEFORE Spark and snapshots a work-item baseline.
6. After `EXECUTION_RESULT: READY_FOR_VALIDATION`, attempt a fresh `reviewer-qwen`.
7. Its parent PreToolUse first runs deterministic scope and work-item validation.
8. WORK_ITEM FAIL -> reviewer is denied; call executor-spark again under the same work item.
9. WORK_ITEM PASS with graph incomplete -> node is deterministically completed; reviewer
   is denied; call planner-qwen for the next dependency-ready node.
10. Graph complete -> immutable CONTRACT final_validation_commands run.
11. GLOBAL FAIL -> completed history is preserved; reviewer is denied; call planner-qwen
    to add a bounded corrective node.
12. GLOBAL PASS -> reviewer-qwen starts for final semantic review.
13. REVIEW REJECTED -> planner adds a corrective node; do not rewrite completed nodes.
14. Stop only when graph completion, global deterministic PASS, locked CONTRACT, and
    reviewer APPROVED provenance all agree.

Never accept an LLM claim as deterministic PASS.
