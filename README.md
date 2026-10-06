# bibliotecario-openhands-workflow

Reusable OpenHands plugin implementing a native sequential software-engineering workflow:

```text
USER REQUEST
    |
    v
planner-qwen        (inherits the parent Qwen LLM)
    |
    v
PLAN.md
    |
    v
executor-spark      (LLM profile: spark2.5-4b)
    |
    v
deterministic validation
    |
    v
reviewer-qwen       (inherits the parent Qwen LLM)
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

The parent Agent Profile should use the planning model and expose at least:

```text
task_tool_set
task_tracker
```

For the strictest orchestration profile, do not expose `terminal` or `file_editor`
to the parent. The phase agents own repository interaction.

## LLM profiles

`planner-qwen` and `reviewer-qwen` use `model: inherit`, so with the intended
parent they run on Qwen.

`executor-spark` explicitly requests:

```text
spark2.5-4b
```

The OpenHands conversation runtime must therefore be able to resolve an LLM profile
with that exact name. If the isolated Docker runtime cannot resolve the profile,
the executor should fail visibly rather than silently falling back to Qwen.

## Safety properties

- Existing/native/upstream/installable solutions are checked before new code.
- PLAN.md freezes requirements and deterministic validation before execution.
- The executor is forbidden from weakening the acceptance oracle.
- The reviewer has no file editing tool.
- PASS requires deterministic validation plus independent review.
- Correction loops are capped at three.
