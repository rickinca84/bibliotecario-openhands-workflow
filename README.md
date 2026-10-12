# bibliotecario-openhands-workflow

## v0.6.3 — Dynamic Verified Work Graph

v0.6.3 keeps the machine-validated dynamic work graph and fixes a terminal-failure
Stop loop discovered by forced-failure E2E. The graph remains suitable for both small
fixes and larger repositories, with no authoritative Markdown PLAN.

```text
USER REQUEST
    |
deterministic INTAKE.json
    |
planner-qwen
    +--> CONTRACT.json
    +--> WORK_GRAPH.json
    +--> CURRENT_WORK_ITEM.json
    |
deterministic preflight BEFORE Spark
    |
executor-spark
    |
scope validation
    |
work-item argv validation
    |
    +-- FAIL ----------------------> retry same work item
    |
deterministic completed_nodes
    |
    +-- graph incomplete ----------> planner selects/refines next node
    |
    +-- graph complete
            |
      immutable global argv validation
            |
            +-- FAIL -------------> planner adds corrective node
            |
            v
      reviewer-qwen
            |
       APPROVED / REJECTED
            |
       deterministic Stop gate
```

### Authority split

- `INTAKE.json`: deterministic repository and environment evidence.
- `CONTRACT.json`: locked global objective, acceptance criteria, constraints, forbidden
  paths, and final integration validation.
- `WORK_GRAPH.json`: coarse dynamic DAG. Nodes contain only `id`, `goal`, `depends_on`.
- `CURRENT_WORK_ITEM.json`: just-in-time bounded checkpoint for the current node.
- `STATE.json`: authoritative budgets and completed-node fingerprints.
- `VALIDATION.json`: deterministic scope/test evidence.
- `REVIEW.json`: final semantic verdict with provenance.

There is no authoritative `PLAN.md`.

### Why the graph is deliberately incomplete

Large repositories cannot be truthfully planned edit-by-edit before implementation.
The planner maps intermediate goals and dependencies, then creates a concrete work item
only for the next dependency-ready checkpoint.

Future nodes may be changed as repository evidence is discovered. Completed nodes cannot:
their `id`, `goal`, and `depends_on` are fingerprinted in deterministic STATE.

### Work item versus graph node

A graph node is a coarse project goal. A work item is the executable contract for one
checkpoint and contains:

- local goal;
- existing/native/installable reuse analysis;
- mutable paths;
- forbidden paths;
- acceptance criteria;
- frozen argv validation.

It intentionally does not prescribe every edit. Spark chooses the smallest sound
implementation inside those bounds.

### Multi-level deterministic validation

Every work item has local validation. After every current graph node is completed,
`CONTRACT.final_validation_commands` run as global/integration validation before the
semantic reviewer.

Validation uses argv arrays, `shell=False`, bounded timeouts, deterministic cwd, and
scope comparison against a per-work-item baseline.

Missing validator executables are `ENVIRONMENT_ERROR`. Scope escape is terminal.

### Native OpenHands capabilities reused

The workflow reuses native:

- plugin/file-based subagents;
- `task` delegation and native structured `TaskObservation` completion status;
- `read_file`;
- `write_file` including native parent-directory creation;
- `edit`;
- `glob`;
- `grep`;
- UserPromptSubmit / PreToolUse / PostToolUse / Stop hooks;
- saved model profiles;
- Git-backed workspace behavior.

No OpenHands/Agent Canvas source patch is required.

`planning_file_editor` is no longer used because the authoritative planning artifacts
are structured JSON rather than a human Markdown plan.

### Models

- planner/reviewer: parent Qwen model (`inherit`);
- executor: saved local profile `spark2.5-4b`.

Spark receives only `read_file`, `write_file`, and `edit`; it has no terminal.

### Experimental Rizzo parent on this branch

The `experiment/rizzo-action-router` branch also contains an **unmerged** top-level
Rizzo selector experiment. It does not patch Agent Canvas or replace OpenHands' Agent.

It uses a standard Agent Profile whose LLM points to a local OpenAI-compatible adapter.
The adapter may emit only native `task` / `finish` calls. Deterministic workflow
transitions are taken directly; Rizzo Flow is consulted only when more than one legal
semantic action remains.

The custom Python `RizzoAgent` prototype is superseded and is not the installation path.

P620 setup and native `/validate` + `/materialize` preflight are documented in
`docs/p620-rizzo-parent-install.md`. The stable installed profile must remain unchanged
until both success-path and forced-failure E2E tests pass.

### Parent profile

The parent remains orchestration-only. Because explicit OpenHands parent tool lists
scope delegated agents, the saved parent profile must expose the union of native tools
needed by delegated agents, while plugin hooks deny direct parent use.

No Model Router or `switch_llm`.

### Fail-closed guarantees

- malformed JSON/graph/work item is denied before Spark starts;
- the locked global contract cannot change after first valid executor preflight;
- only deterministic validation writes completed-node state;
- completed graph nodes cannot be removed or mutated;
- same work item retries only after deterministic local validation failure;
- semantic reviewer never runs on deterministic failure;
- executor text is not authoritative: normal native task completion only hands work to deterministic validation;
- parent `finish` cannot claim success before final deterministic PASS + reviewer provenance;
- terminal failures are owned by deterministic `TERMINAL_FAILURE.json`, never by an LLM claim;
- native `finish` remains accepted when its exact deterministic failure message is used, with `PostToolUse(finish)` producing a hash-bound proof;
- ordinary OpenHands final text is also accepted only when the latest persisted agent `MessageEvent` exactly equals the canonical deterministic failure message;
- the Stop hook binds that exact message to the terminal-failure hash and event id in `FAILURE_REPORT.json` (`Stop:verified-agent-message`);
- pseudo-calls such as textual `finish(message=...)`, direct success claims, and stale failure proofs are rejected;
- final stop requires current contract + graph + validation + review hashes to agree.

### Install source

```text
github:rickinca84/bibliotecario-openhands-workflow
```
