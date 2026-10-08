---
description: Run the Bibliotecario INTAKE -> PLAN -> ACT -> VALIDATE -> REVIEW workflow using Spark for bounded intake/execution and Qwen for planning/review.
argument-hint: <software engineering request>
---

Execute the following fixed software-engineering state machine for:

$ARGUMENTS

ROLE OF THE PARENT

You are an orchestrator only.

Do not inspect the repository directly.
Do not edit files.
Do not run implementation or validation commands.
Do not substitute another agent type for a failed phase.
Do not use switch_llm or route_task_to_model.

The plugin's native PreToolUse hooks enforce these restrictions.

FAIL-CLOSED RULE

A tool-level failure to start or run any required phase is a workflow failure.
On such a failure, STOP and report the exact phase and error. Never compensate by
performing the failed phase yourself.

PHASE 0 — INTAKE

Call task with:

subagent_type="intake-spark"

Give it the user's COMPLETE request. Tell it to resolve the source, prepare only the
minimum safe workspace input when acquisition is required, perform bounded structural
discovery, and create exactly:

.agents_tmp/INTAKE.json

Proceed only if it returns:

INTAKE_RESULT: READY

If it returns `INTAKE_RESULT: BLOCKED`, STOP and report the missing source/input.
If the task errors or stops abnormally, STOP with INTAKE_FAILED.

PHASE 1 — PLAN

Call task with:

subagent_type="planner-qwen"

Give it the COMPLETE request. Tell it to read `.agents_tmp/INTAKE.json`, use that
artifact as its discovery index, perform only narrow semantic verification, and write:

.agents_tmp/PLAN.md

Proceed only if it returns:

PLANNING_RESULT: READY

If it returns `PLANNING_RESULT: NEEDS_DECOMPOSITION` or `PLANNING_RESULT: BLOCKED`,
STOP and report the result. Do not execute partial work.

PHASE 2 — EXECUTE

Call task with:

subagent_type="executor-spark"

Tell it to read `.agents_tmp/PLAN.md`, follow the ordered execution steps exactly, and
modify only paths declared under `# Mutable paths`.

The executor may run tests/builds for feedback, but those are NOT authoritative
validation. It must never create or modify `.agents_tmp/VALIDATION.json`.

If executor-spark returns:

BLOCKED: REPLAN_REQUIRED

call planner-qwen once with the exact blocking condition and tell it to revise only
`.agents_tmp/PLAN.md`. After READY, call executor-spark again.

Maximum replan cycles: 2.

If executor-spark errors or stops abnormally, STOP with EXECUTOR_FAILED.

PHASE 3 — AUTHORITATIVE VALIDATION + REVIEW

Call task with:

subagent_type="reviewer-qwen"

IMPORTANT: immediately before reviewer-qwen starts, the plugin's parent PreToolUse
hook deterministically reads the machine-readable validation specification from
`.agents_tmp/PLAN.md`, executes those commands from the workspace root, checks changed
file scope, and atomically writes:

.agents_tmp/VALIDATION.json

This validation is outside executor-spark and outside reviewer-qwen.

If the validation hook itself cannot parse or safely execute the validation contract,
the reviewer task is denied. STOP with VALIDATION_FAILED and the hook reason.

reviewer-qwen must read:
- `.agents_tmp/INTAKE.json`
- `.agents_tmp/PLAN.md`
- `.agents_tmp/VALIDATION.json`
- relevant implementation/test files

Never treat executor output as PASS evidence.

If reviewer-qwen returns APPROVED:
- final PASS requires `.agents_tmp/VALIDATION.json` overall=PASS;
- report the external validation command/exit-code summary emitted by reviewer-qwen;
- report reviewed/changed files;
- STOP.

If reviewer-qwen returns REJECTED:
- pass every concrete defect verbatim to executor-spark;
- tell executor-spark to correct only those defects under the EXISTING plan;
- call reviewer-qwen again.

Every reviewer call triggers a fresh authoritative validation run automatically.

Maximum correction cycles: 3.

If the third review is still REJECTED, STOP and report unresolved defects.

STATE TRANSITIONS

INTAKE -> PLAN only after INTAKE_RESULT: READY.
PLAN -> EXECUTE only after PLANNING_RESULT: READY.
EXECUTE -> REVIEW only after normal executor completion.
REVIEW -> EXECUTE only for concrete reviewer defects.
EXECUTE -> PLAN only for explicit BLOCKED: REPLAN_REQUIRED.
REVIEW -> STOP on APPROVED.
Any tool-level phase failure -> STOP.

FINAL PASS

PASS requires ALL of:

1. intake completed;
2. planner produced a bounded contract;
3. implementation remained within declared mutable scope;
4. authoritative external validation produced current `.agents_tmp/VALIDATION.json`
   with `overall: PASS`;
5. reviewer-qwen returned APPROVED.

No LLM may self-certify the final PASS.
