# bibliotecario-openhands-workflow

Version 0.5.2 is the hardened follow-up to the first real Agent Canvas E2E runs.

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
executor-spark (Spark X2.5 4B)
    |  native: read_file / write_file / edit
    v
scope delta check BEFORE validation commands
    |
    v
deterministic argv validation (shell=False)
    |
    +-- FAIL --> bounded executor correction
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

## What v0.5.2 fixes

The v0.4 and v0.5 E2E runs exposed concrete failure modes:

- LLM intake was expensive and could modify project files;
- fresh tasks could arrive as `resume=""`;
- the standard `file_editor` cannot create missing parent directories;
- validator-created caches could be misattributed to the executor;
- deterministic FAIL could still be followed by semantic APPROVED;
- policy-hook interpreter errors could exit 1, which OpenHands treats as non-blocking;
- shell-string validation unnecessarily widened the deterministic execution surface.

v0.5.2 addresses those problems without adding another custom filesystem layer.

## Native OpenHands functionality reused

The implementation deliberately reuses capabilities confirmed in the target Agent
Canvas / Agent Server installation:

- native plugin and file-based subagent loading;
- native `task` / `task_tool_set`;
- native `read_file`;
- native `write_file`, which creates parent directories when required;
- native `edit` for exact bounded replacements;
- native `glob`, `grep`, and `planning_file_editor`;
- native UserPromptSubmit / PreToolUse / PostToolUse / Stop hooks;
- native Git-backed workspace behavior.

There is no custom mkdir/scaffolding implementation in the executor path.

## Parent profile requirement

OpenHands scopes delegated agents to the explicit parent tool list when the parent
profile stores `tools != null`. Therefore the parent profile must contain every tool
used by its delegated agents, even when parent hooks deny direct use.

For `qwen-plan-spark-execute`, keep the existing tools and add:

```text
write_file
edit
```

Do not enable Model Router or `switch_llm`.

The parent is still orchestration-only. Plugin PreToolUse hooks deny direct repository
tool usage by the parent.

Before saving a profile change, use the native
`POST /api/agent-profiles/{name}/materialize` preview endpoint.

## Deterministic intake

There is no `intake-spark` phase. UserPromptSubmit performs bounded structural intake
and writes schema-v2 `.agents_tmp/INTAKE.json` without an LLM.

Policy hooks are fail-closed: unexpected hook runtime failures are converted to exit 2,
which is the blocking hook exit code required by OpenHands.

## Planner contract

The planner must first read INTAKE.json.

`# Mutable paths` and `# Forbidden paths` accept only backticked literal
workspace-relative path bullets. No prose, globs, absolute paths, or `..` are valid.
`.agents_tmp/` must be forbidden.

Validation is represented as argv arrays, not shell strings:

```json
{
  "commands": [
    {
      "argv": ["python", "-m", "pytest", "-q"],
      "expected_exit_code": 0,
      "timeout_seconds": 120
    }
  ]
}
```

## Spark executor

The executor has only:

```text
read_file
write_file
edit
```

It has no terminal. Native hooks require PLAN.md to be read first and mechanically
restrict every write to the plan's literal mutable scope.

`write_file` supplies the missing-parent-directory behavior natively, so no
Bibliotecario-specific directory creation code is needed.

## Validation

The execution baseline is captured immediately before the first executor call.

Scope is checked before any validation command runs. Validation commands execute as
argv with `shell=False` from the deterministic intake source root.

A missing validation executable is recorded as `ENVIRONMENT_ERROR`, not silently
treated as an implementation defect. Deterministic FAIL denies reviewer startup.

## Review and final gate

reviewer-qwen is project-read-only and must successfully read INTAKE.json, PLAN.md, and
a PASS VALIDATION.json before semantic review.

The parent Stop gate permits successful completion only when the current PLAN hash,
deterministic PASS, and reviewer APPROVED provenance all agree.

## Install

```text
github:rickinca84/bibliotecario-openhands-workflow
```

For Docker conversation runtime, explicitly attach the plugin source when creating the
conversation until Agent Canvas exposes the custom installed plugin in its launcher.
