---
description: Run the Bibliotecario PLAN -> ACT -> REVIEW workflow using Qwen planner, Spark executor, and Qwen reviewer.
argument-hint: <software engineering request>
allowed-tools:
  - task_tool_set
  - task_tracker
---

Execute the following fixed software-engineering state machine for:

$ARGUMENTS

ROLE OF THE PARENT

You are an orchestrator only.

Do not implement code yourself.
Do not edit files yourself.
Do not run implementation commands yourself.
Do not substitute another agent type for a failed phase.
Do not use switch_llm or route_task_to_model.
Use only the named delegated sub-agents below.

FAIL-CLOSED RULE

A tool-level failure to start or run any required sub-agent is a workflow failure.
Examples include:
- unknown agent;
- missing LLM profile;
- unavailable model;
- runtime/tool exception;
- iteration/run-limit termination;
- task infrastructure failure.

On any such failure, STOP immediately and report the exact phase and error.
Never compensate by implementing, testing, reviewing, or replanning yourself.

PHASE 1 — PLAN

Call the task tool with:

subagent_type="planner-qwen"

Give it the user's complete request and tell it to inspect the current workspace
and create PLAN.md.

Wait for it to finish.

If the planner task itself errors or stops abnormally, STOP.
Do not retry automatically.

If the planner returns a normal result but explicitly reports an unresolved ambiguity
that prevents a complete contract, STOP and report the ambiguity.

PHASE 2 — EXECUTE

Call the task tool with:

subagent_type="executor-spark"

Tell it to read PLAN.md, implement exactly that contract, and run every
deterministic validation command in PLAN.md.

Wait for it to finish.

If the executor task itself errors or stops abnormally for any reason, including
"profile not found", model unavailability, run-limit, or tool failure:
STOP and report EXECUTOR_FAILED with the original error.
Do not ask planner-qwen, reviewer-qwen, general-purpose, or yourself to implement.

If executor-spark completes normally with:
BLOCKED: REPLAN_REQUIRED
then call planner-qwen once with the exact blocking ambiguity and instruct it to
revise PLAN.md only. After a successful replan, call executor-spark again.

Maximum replan cycles: 2.

PHASE 3 — REVIEW

After a normal executor completion, always call:

subagent_type="reviewer-qwen"

Tell it to independently inspect PLAN.md, the implementation files, tests, and
the executor's deterministic validation evidence.

If the reviewer task itself errors or stops abnormally, STOP and report REVIEW_FAILED.
Never self-review as a substitute.

Never treat the executor's PASS declaration as sufficient.

If reviewer-qwen returns APPROVED:
- report completion;
- report files changed as stated by the executor;
- report deterministic validation commands, exit codes, and observed results;
- STOP.

If reviewer-qwen returns REJECTED:
- pass the reviewer's concrete defects verbatim to executor-spark;
- tell executor-spark to correct only those defects under the existing PLAN.md;
- require the executor to rerun all deterministic validation in PLAN.md;
- run reviewer-qwen again.

Maximum correction cycles: 3.

If the third review is still REJECTED:
STOP and report the unresolved defects.

STATE TRANSITION RULES

PLAN -> EXECUTE only after a complete PLAN.md exists.
EXECUTE -> REVIEW only after executor-spark completes normally.
REVIEW -> EXECUTE only for concrete reviewer defects.
EXECUTE -> PLAN only for explicit BLOCKED: REPLAN_REQUIRED.
REVIEW -> STOP on APPROVED.
Any tool-level phase failure -> STOP.
Never bounce between agents without one of these state transitions.

A final PASS requires both:
1. deterministic validation success under PLAN.md, with actual command/exit-code evidence; and
2. reviewer-qwen returning APPROVED.
