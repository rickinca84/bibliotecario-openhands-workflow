# bibliotecario-openhands-workflow

Version 0.5.0 simplifies Bibliotecario after the first real Canvas E2E.

```text
USER REQUEST
    |
    v
deterministic UserPromptSubmit intake
    |
    v
.agents_tmp/INTAKE.json
    |
    v
planner-qwen (Qwen)
    |
    v
.agents_tmp/PLAN.md
    |
    v
EXECUTION_BASELINE snapshot
    |
    v
executor-spark (Spark X2.5 4B; file edits only)
    |
    v
scope delta check BEFORE validation commands
    |
    v
deterministic validation
    |
    +-- FAIL --> executor correction / terminal failure
    |
    v
reviewer-qwen (Qwen; project-read-only)
    |
    v
mechanically recorded REVIEW.json
    |
    v
parent Stop gate
```

## Why v0.5

The v0.4 E2E proved plugin loading and real Spark delegation, but also exposed four
problems: an LLM intake phase wasted calls and modified the project, `resume="none"`
was misinterpreted as a task ID, validation-generated cache files polluted the scope
check, and reviewer APPROVED could override deterministic FAIL.

v0.5 removes those failure modes.

## Native OpenHands functionality reused

Before adding policy, the implementation was checked against current OpenHands SDK:
native plugin loading, file-based subagents, `task`, `UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `Stop`, `planning_file_editor`, `read_file`, and
Git-backed workspace behavior are reused directly. No OpenHands source patch is added.

## Deterministic intake

There is no `intake-spark` agent. The parent UserPromptSubmit hook performs bounded
mechanical source/workspace discovery and writes schema-v2 `.agents_tmp/INTAKE.json`.
It may clone only an explicitly supplied Git URL when the workspace has no meaningful
project content. Otherwise it never acquires arbitrary sources.

This costs zero LLM calls.

## Planner

`planner-qwen` must read INTAKE.json first; agent hooks mechanically deny every other
tool until that read succeeds. The plan remains prescriptive, with literal mutable
paths and a frozen machine-readable validation specification.

## Executor

`executor-spark` uses the real local `spark2.5-4b` profile. It has only
`file_editor`: no terminal, package installation, test execution, or shell write
bypass is available. It must view PLAN.md first and writes only paths declared mutable.

Immediately before the first executor call, the parent snapshots
`.agents_tmp/EXECUTION_BASELINE.json`. The baseline is preserved across correction
cycles.

## Validation

Before reviewer delegation, the parent hook computes the cumulative executor delta
against EXECUTION_BASELINE and checks scope before any test/build command runs. This
prevents validator-created artifacts such as `.pytest_cache` or `__pycache__` from
being attributed to Spark.

Only after scope PASS are the plan-declared commands executed. Validation FAIL denies
reviewer startup.

## Review and final gate

`reviewer-qwen` is project-read-only and must read INTAKE.json, PLAN.md, and a PASS
VALIDATION.json before other inspection or stopping.

A parent PostToolUse hook records the actual task result into orchestration markers,
including `REVIEW.json`. The parent Stop hook allows successful completion only when
current plan provenance, deterministic PASS, and reviewer APPROVED agree.

## Parent profile

Keep the existing `qwen-plan-spark-execute` profile revision with the tool union
needed by delegated agents. Do not re-enable Model Router or `switch_llm`.

## Install

```text
github:rickinca84/bibliotecario-openhands-workflow
```

For Docker conversation runtime, explicitly attach the plugin source when creating the
conversation until Agent Canvas exposes custom installed plugins in its launcher.
