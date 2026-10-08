---
description: Run Bibliotecario v0.5 deterministic INTAKE -> PLAN -> ACT -> VALIDATE -> REVIEW.
argument-hint: <software engineering request>
---

Execute this fixed state machine for:

$ARGUMENTS

ROLE OF THE PARENT

You orchestrate only. Do not inspect/edit the repository, run commands, validate, review,
switch LLMs, or substitute another agent.

PHASE 0 — DETERMINISTIC INTAKE

The UserPromptSubmit hook has already produced `.agents_tmp/INTAKE.json` without an
LLM. If the hook reports INTAKE_BLOCKED, STOP and report the exact missing source.

PHASE 1 — PLAN

Your FIRST tool action is a fresh `task` call with:
- `subagent_type="planner-qwen"`
- the complete user request
- OMIT `resume` entirely

Proceed only when the task returns `PLANNING_RESULT: READY`.
The parent PostToolUse hook records this result mechanically.

PHASE 2 — EXECUTE

Call a fresh task with `subagent_type="executor-spark"`; OMIT `resume`.
Tell it to follow PLAN.md exactly.

Before the first executor starts, the parent PreToolUse hook snapshots
`.agents_tmp/EXECUTION_BASELINE.json`. It is preserved across corrections so final
scope is cumulative.

If executor returns `BLOCKED: REPLAN_REQUIRED`, call planner-qwen again with the exact
condition. Maximum planner calls are enforced by the parent hook.

PHASE 3 — VALIDATE

Attempt a fresh `reviewer-qwen` task; OMIT `resume`.

Before reviewer starts, the parent hook:
1. computes the executor delta against EXECUTION_BASELINE;
2. checks that delta against mutable/forbidden paths BEFORE running tests;
3. executes the frozen validation commands;
4. writes `.agents_tmp/VALIDATION.json`.

If scope fails, the workflow stops fail-closed.
If validation commands fail, reviewer delegation is denied and you must call
executor-spark again with the exact validation failure summary. Do not review a FAIL.

PHASE 4 — REVIEW

reviewer-qwen starts only after validation PASS. Its required evidence reads are
mechanically enforced.

If it returns REJECTED, call executor-spark with the concrete defects under the existing
plan, then attempt review again. Budgets are enforced by hooks.

FINAL PASS

The parent PostToolUse hook records reviewer output in `.agents_tmp/REVIEW.json`.
The parent Stop hook permits successful completion only when:
- current PLAN matches VALIDATION provenance;
- VALIDATION overall is PASS;
- REVIEW verdict is APPROVED for that same plan/validation.

Tool/runtime phase failure and budget exhaustion are terminal failures. No LLM may
self-certify deterministic PASS.
