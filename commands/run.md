---
description: Run the Bibliotecario PLAN -> ACT -> REVIEW workflow using Qwen planner, Spark executor, and Qwen reviewer.
argument-hint: <software engineering request>
allowed-tools:
  - task_tool_set
  - task_tracker
---

Execute the following fixed software-engineering state machine for:

$ARGUMENTS

Do not implement code yourself.
Do not edit files yourself.
Do not run implementation commands yourself.
Use only delegated sub-agents for the phases below.

PHASE 1 — PLAN

Call the task tool with:

subagent_type="planner-qwen"

Give it the user's complete request and tell it to inspect the current workspace
and create PLAN.md.

Wait for it to finish.

If planning fails or it reports an unresolved ambiguity that prevents a complete
contract, STOP and report the blocking issue.

PHASE 2 — EXECUTE

Call the task tool with:

subagent_type="executor-spark"

Tell it to read PLAN.md, implement exactly that contract, and run every
deterministic validation command in PLAN.md.

Wait for it to finish.

If it returns BLOCKED: REPLAN_REQUIRED, call planner-qwen again with the exact
blocking ambiguity and instruct it to revise PLAN.md. Then call executor-spark again.

If execution returns FAIL after straightforward fixes, proceed to REVIEW only
when there is still useful implementation to inspect; otherwise report failure.

PHASE 3 — REVIEW

Always call the task tool with:

subagent_type="reviewer-qwen"

Tell it to independently inspect PLAN.md, the repository diff, changed files,
tests, and validation evidence.

Never treat the executor's PASS declaration as sufficient.

If reviewer-qwen returns APPROVED:
- report completion;
- report files changed;
- report deterministic validation commands and observed results;
- STOP.

If reviewer-qwen returns REJECTED:
- pass the reviewer's concrete defects verbatim to executor-spark;
- tell executor-spark to correct only those defects under the existing PLAN.md;
- run reviewer-qwen again.

Maximum correction cycles: 3.

If the third review is still REJECTED:
STOP and report the unresolved defects.

STATE TRANSITION RULES

PLAN -> EXECUTE only after a complete PLAN.md exists.
EXECUTE -> REVIEW after implementation/validation.
REVIEW -> EXECUTE only for concrete reviewer defects.
EXECUTE -> PLAN only for real ambiguity or architectural uncertainty.
REVIEW -> STOP on APPROVED.
Never bounce between agents without one of these state transitions.

A final PASS requires both:
1. deterministic validation success under PLAN.md; and
2. reviewer-qwen returning APPROVED.
