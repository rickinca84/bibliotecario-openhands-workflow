# Rizzo action-router experiment

This experiment extends the existing planner-only Rizzo MCP integration without changing
the deterministic authority model.

## Key idea

Rizzo Flow cannot generate arbitrary tool arguments and does not execute tools itself.
It chooses one option from a finite set. Therefore Bibliotecario must first construct a
set of **complete, already-permitted candidate tool calls**.

Example:

```json
{
  "state": {
    "phase": "validation",
    "last_exit_code": 1,
    "changed_files": ["src/example.py"]
  },
  "candidates": [
    {
      "id": "inspect_diff",
      "tool": "git_diff",
      "arguments": {},
      "description": "Inspect the current diff before deciding how to repair the failed validation."
    },
    {
      "id": "retry_spark",
      "tool": "task",
      "arguments": {
        "agent": "executor-spark",
        "prompt_ref": ".agents_tmp/CURRENT_WORK_ITEM.json"
      },
      "description": "Ask Spark to repair the current bounded work item after deterministic validation failed."
    }
  ]
}
```

Rizzo sees the state and the candidate descriptions, then returns one candidate id. The
router maps that id back to the exact candidate object. It never accepts a tool name or
arguments invented by Rizzo.

## Safety boundary

The intended flow is:

```text
deterministic state
      |
candidate generator / policy
      |
Rizzo choice
      |
action_router
      |
PreToolUse / budget / scope gate
      |
native OpenHands tool executor
```

The controller still owns:

- allowed candidate generation;
- workspace and mutable-path rules;
- attempt and wall-clock budgets;
- deterministic test exit codes;
- diff/scope validation;
- PASS/FAIL and terminal stop.

Rizzo owns only **which permitted action to try next**.

## Current mode

The CLI is deliberately shadow-only: it prints the selected complete tool call and never
executes it.

Programmatic execution is available only through `dispatch(decision, executor)`, where
the caller must supply the executor function. That is the insertion point for the native
OpenHands tool runtime after we verify its supported programmatic interface.

This prevents the experiment from creating a second custom tool stack.

## Why shadow mode first

The bundled Rizzo documentation warns that agent-trace / observability judgment is its
weakest benchmark area. We should compare Rizzo's choices with the existing Qwen planner
before allowing autonomous dispatch.

Minimum promotion condition for the next phase:

1. no unknown / malformed tool calls;
2. no execution after `uncertain` or `insufficient_evidence`;
3. exact candidate round-trip;
4. deterministic gates still block forbidden actions;
5. measured routing quality on our own labelled Bibliotecario traces.
