# RizzoAgent experiment — superseded

> **Status: superseded by the standard-Agent adapter path.**
>
> The custom Python `RizzoAgent` prototype is retained only as design history and
> contract-test material. It is **not** the installation path for the P620.

## Why it was superseded

Current OpenHands plugins natively load Markdown `AgentDefinition` objects, skills,
hooks, MCP and commands. An Agent Profile also exposes an `agent` field, but the
current `OpenHandsAgentSettings.create_agent()` path constructs the standard
OpenHands `Agent`; it is not a supported plugin mechanism for importing an arbitrary
Python Agent subclass.

Using a custom `RizzoAgent` would therefore require a deeper runtime extension than
needed.

## Selected architecture

The supported experiment keeps the standard OpenHands Agent and replaces only its LLM
endpoint with a local OpenAI-compatible adapter:

```text
human request
    |
standard OpenHands Agent
    |
LLM profile: rizzo-bibliotecario-router
    |
local OpenAI-compatible adapter
    |
    +-- deterministic transition available -> choose it directly
    |
    +-- >1 legal semantic actions -> Rizzo Flow closed choice
    |
native OpenAI tool_call: task / finish
    |
normal OpenHands ActionEvent / hooks / security / executor
    |
planner-qwen / executor-spark / reviewer-qwen
```

The adapter does not execute tools and does not read the repository. It maps the normal
OpenHands transcript to a bounded workflow state and returns one native tool call.

## Authority split

- **Qwen** interprets the free-form request, plans/replans, and performs final semantic review.
- **Spark** executes one narrow `CURRENT_WORK_ITEM`.
- **Rizzo Flow** chooses only among already-legal closed alternatives when a deterministic
  transition is not sufficient.
- **OpenHands native runtime** executes the selected `task` / `finish` action.
- **Bibliotecario deterministic hooks/state** own intake, scope, budgets, tests, provenance,
  PASS/FAIL and stop.

Rizzo never establishes deterministic PASS.

## Installation

See [p620-rizzo-parent-install.md](p620-rizzo-parent-install.md).

The old `skills/rizzo-flow/scripts/rizzo_agent.py` prototype remains on this experimental
branch for comparison until the adapter E2E is complete. It must not be wired into the
installed Agent Canvas profile.
