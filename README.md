# bibliotecario-openhands-workflow

Reusable OpenHands plugin implementing a native sequential software-engineering workflow:

```text
USER REQUEST
    |
    v
planner-qwen        (inherits the parent Qwen LLM; read + PLAN.md only)
    |
    v
PLAN.md
    |
    v
executor-spark      (LLM profile: spark2.5-4b; terminal + file_editor)
    |
    v
deterministic validation
    |
    v
reviewer-qwen       (inherits the parent Qwen LLM; read-only)
    |
    +---- APPROVED ----> STOP
    |
    +---- REJECTED ----> executor-spark fix -> reviewer-qwen
```

The workflow is exposed as the plugin command:

```text
/bibliotecario-openhands-workflow:run <request>
```

## OpenHands compatibility

Designed for the native plugin/sub-agent mechanisms available in OpenHands software-agent-sdk 1.53.x.

The plugin uses:
- plugin-provided file agents;
- `task_tool_set` delegation;
- a namespaced slash command;
- per-sub-agent iteration limits;
- native per-agent `PreToolUse` hooks;
- shared workspace state through `PLAN.md`.

No OpenHands source patch is required by the plugin itself.

## Install source

```text
github:rickinca84/bibliotecario-openhands-workflow
```

or:

```text
https://github.com/rickinca84/bibliotecario-openhands-workflow.git
```

## Parent agent requirements

For strict orchestration, the parent Agent Profile should expose only:

```text
task_tool_set
task_tracker
```

Do not expose `terminal`, `file_editor`, `switch_llm`, or Model Router tools
to the parent when using this workflow. The parent must orchestrate rather than
perform phase work itself.

## Phase capabilities

### planner-qwen

Tools:

```text
glob
grep
file_editor
```

A native `PreToolUse` hook permits `file_editor:view` everywhere but permits
write operations only when the target is exactly `PLAN.md`. Hook execution is
fail-closed: if the guard command itself fails, the tool call is blocked.

The planner has no terminal.

### executor-spark

Tools:

```text
terminal
file_editor
```

This is the only phase allowed to implement and run deterministic validation.

### reviewer-qwen

Tools:

```text
glob
grep
file_editor
```

A native `PreToolUse` hook allows only `file_editor:view`. All file writes are
denied. The reviewer has no terminal and therefore evaluates the executor's
recorded deterministic evidence plus the implementation itself.

## LLM profiles

`planner-qwen` and `reviewer-qwen` use `model: inherit`, so with the intended
parent they run on Qwen.

`executor-spark` explicitly requests:

```text
spark2.5-4b
```

The OpenHands conversation runtime must therefore be able to resolve an LLM profile
with that exact name. If the isolated Docker runtime cannot resolve the profile,
the workflow is required to stop and report the executor failure. It must not fall
back to the parent or another agent.

## Safety properties

- Existing/native/upstream/installable solutions are checked before new code.
- PLAN.md freezes requirements and deterministic validation before execution.
- Planner writes are deterministically confined to PLAN.md.
- The executor is forbidden from weakening the acceptance oracle.
- Reviewer writes are deterministically blocked.
- Tool-level phase failures are fail-closed; the parent may not substitute itself.
- PASS requires deterministic validation plus independent review.
- Replan cycles are capped at two; correction cycles are capped at three.
