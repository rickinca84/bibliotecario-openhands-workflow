# RizzoAgent experiment

## Architecture

The experiment now makes Rizzo the **top-level operational selector**, not an advisory
call inside Qwen.

```text
human request
    |
RizzoAgent
    |
    +-- no CONTRACT yet --> one deterministic task(planner-qwen)
    |                       Qwen interprets free-form intent and writes contract/work item
    |
    +-- contract exists --> candidate catalog from real OpenHands tools + state
                            |
                         Rizzo choice
                            |
                      native ActionEvent
                            |
                 OpenHands normal agent loop
                            |
              PreToolUse / security / confirmation
                            |
                     native tool executor
                            |
                        observation
                            |
                       next state
```

## Why ActionEvent instead of execute_tool()

OpenHands exposes `LocalConversation.execute_tool()`, but upstream explicitly documents
that it bypasses the normal agent loop, including confirmation policies and security
analyzer checks. Bibliotecario must not use that path.

RizzoAgent therefore only replaces next-action selection. It emits the same native
`ActionEvent` shape that an ordinary model-generated tool call produces. OpenHands then
executes it through its existing pending-action machinery.

## Authority

Rizzo may choose only complete calls from the deterministic catalog. It cannot invent
tool names, file paths, commands, replacement text, or task prompts.

Qwen owns interpretation/replanning when semantic generation is needed.
Spark owns bounded coding work.
Rizzo owns rapid selection among already-instantiated legal actions.
Deterministic hooks/state own budgets, scope, validation, PASS/FAIL, and stop.

## Promotion status

This is still an experiment. It is not wired into the installed P620 profile and does
not modify main. Static contract tests ensure the implementation does not use the
unsafe direct-tool shortcut.
