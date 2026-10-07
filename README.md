# bibliotecario-openhands-workflow

Version 0.3.0 capability-contract update.

Reusable OpenHands plugin implementing a native, fail-closed sequential workflow:

```text
USER REQUEST
    |
    v
planner-qwen        (inherits parent Qwen; native planning tools)
    |
    v
.agents_tmp/PLAN.md + READY
    |
    v
executor-spark      (spark2.5-4b; terminal + file_editor)
    |
    v
deterministic validation
    |
    v
reviewer-qwen       (inherits parent Qwen; native read-only tools)
    |
    +---- APPROVED ----> STOP
    |
    +---- REJECTED ----> executor-spark fix -> reviewer-qwen
```

The workflow is exposed as:

```text
/bibliotecario-openhands-workflow:run <request>
```

While the plugin is attached, a native UserPromptSubmit hook injects the orchestration rule into each user turn. This makes ordinary software-engineering requests follow the same PLAN -> ACT -> REVIEW discipline even when the slash command is omitted.

## OpenHands compatibility

Designed for the native plugin/sub-agent/hook mechanisms in OpenHands
software-agent-sdk 1.53.x.

No OpenHands source patch is required.

## Install source

```text
github:rickinca84/bibliotecario-openhands-workflow
```

## Parent Agent Profile: important OpenHands 1.53 scope behavior

OpenHands 1.53 automatically scopes delegated sub-agent tools to the tools selected
on an Agent Profile when that profile uses an explicit custom tool list.

Therefore the parent profile must FORMALLY include the union of tools required by
its delegates:

```text
task_tool_set
task_tracker
terminal
file_editor
glob
grep
planning_file_editor
read_file
```

Do not enable `switch_llm` or Model Router for this workflow.

Although the delegate tool union must be present on the parent profile for native
sub-agent scoping, plugin-level PreToolUse hooks deterministically DENY the parent
from invoking repository tools directly. `planning_file_editor` and `read_file`
are native internal OpenHands tools; they may not appear in the normal Canvas
tool picker but can be named in the stored Agent Profile. The same
hook layer also blocks delegation to agent types outside `planner-qwen`,
`executor-spark`, and `reviewer-qwen`. These plugin hooks apply to the parent
conversation; delegated phase agents use their own per-agent hook configuration.

The effective capability split is therefore:

```text
PARENT
  effective: task delegation + task tracking only

PLANNER
  glob + grep + planning_file_editor
  native tool writes only .agents_tmp/PLAN.md

EXECUTOR
  terminal + file_editor
  reads .agents_tmp/PLAN.md and implements the contract

REVIEWER
  glob + grep + read_file
  read-only by construction
```

## Automatic Spark profile bootstrap

The isolated Docker conversation runtime has its own LLM profile store. A profile
saved in the outer Canvas server is not automatically available there.

On SessionStart this plugin uses the native OpenHands `LLMProfileStore` API to
create the local runtime profile `spark2.5-4b` if it is absent:

```text
model:    openai/spark2.5-4b
base_url: http://host.docker.internal:30000/v1
api_key:  local
```

The key is a non-secret placeholder for the local OpenAI-compatible endpoint.
An existing runtime profile with the same name is left untouched.

Before an `executor-spark` task starts, a native PreToolUse hook verifies that
the profile exists. If it does not, the executor delegation is denied and the
workflow fails closed instead of falling back to Qwen.

## Planner bounds

The planner:
- uses the native OpenHands planning toolset: `glob`, `grep`, and
  `planning_file_editor`;
- has no terminal;
- may read the repository;
- may write only the native plan file `.agents_tmp/PLAN.md`;
- has a ten-iteration run budget;
- must return either `PLANNING_RESULT: READY` or
  `PLANNING_RESULT: NEEDS_DECOMPOSITION`;
- must not create auxiliary planning documents.

## Executor bounds

The Spark executor is the only phase allowed to modify implementation files and
run deterministic validation. It has an 18-iteration run budget and may not alter
`.agents_tmp/PLAN.md` or weaken the acceptance oracle.

## Reviewer bounds

The reviewer is read-only by construction. It uses native `glob`, `grep`, and
`read_file`, independently inspecting `.agents_tmp/PLAN.md`, implementation,
tests, and executor validation evidence. It has no write-capable file tool and no
terminal, so it cannot modify files or claim to rerun commands.

## Fail-closed behavior

A final PASS requires:
1. deterministic validation success under .agents_tmp/PLAN.md; and
2. reviewer-qwen returning APPROVED.

Missing agent/model/profile, runtime errors, run-limit termination, and task
infrastructure failures stop the workflow. The parent is never allowed to
substitute itself for a failed phase.
