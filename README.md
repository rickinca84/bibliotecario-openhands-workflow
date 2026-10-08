# bibliotecario-openhands-workflow

Version 0.4.0 workspace-intake and external-validation update.

Reusable OpenHands plugin implementing a fail-closed coding workflow:

```text
USER REQUEST
    |
    v
intake-spark         (Spark; bounded source/workspace intake)
    |
    v
.agents_tmp/INTAKE.json
    |
    v
planner-qwen         (inherits parent Qwen; narrow semantic planning)
    |
    v
.agents_tmp/PLAN.md
    |
    v
executor-spark       (Spark; guarded implementation)
    |
    v
parent validation hook
    |
    +--> reruns deterministic commands
    +--> checks Git changed-file scope
    +--> writes .agents_tmp/VALIDATION.json
    |
    v
reviewer-qwen        (inherits parent Qwen; read-only semantic review)
    |
    +---- APPROVED ----> STOP
    |
    +---- REJECTED ----> executor-spark correction -> validate -> review
```

The workflow is exposed as:

```text
/bibliotecario-openhands-workflow:run <request>
```

While attached, a `UserPromptSubmit` hook also injects the orchestration requirement so
ordinary software-engineering requests use the same pipeline.

## Design rule

Use deterministic/native machinery where possible; spend LLM reasoning only where it
adds value.

This plugin deliberately reuses OpenHands-native capabilities instead of rebuilding
them:

- native workspace/runtime abstraction;
- native Git diff/change tracking and conversation worktrees when a conversation starts
  from an existing Git repository;
- native plugin/subagent registration;
- native `planning_file_editor` for plan-only writes;
- native `read_file` for read-only review;
- native agent/plugin hooks for capability enforcement.

The plugin adds only the orchestration and policy that OpenHands does not provide as
this specific workflow.

## Install source

```text
github:rickinca84/bibliotecario-openhands-workflow
```

Designed for OpenHands software-agent-sdk 1.53.x. No OpenHands source patch is
required.

## Parent Agent Profile

OpenHands 1.53 scopes delegated subagent tools to an explicit parent tool list.
Therefore the parent profile must formally contain the union required by all delegates:

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

Do not enable `switch_llm` or Model Router.

Plugin-level `PreToolUse` hooks deny the parent from directly using repository tools.
The parent effectively retains task delegation/tracking only.

## Phase 0: bounded Workspace Intake

`intake-spark` is intentionally cheap and mechanical.

It classifies input as one of:

- `git_local`
- `git_remote`
- `directory`
- `archive`
- `files`
- `greenfield`
- `unknown`

It prefers meaningful workspace content already supplied to OpenHands. OpenHands may
initialize an otherwise empty workspace as a synthetic Git repository; intake does not
mistake that control repository for user project content.

If the workspace has no meaningful project input and the user explicitly supplied a
remote Git URL, intake may acquire it. When OpenHands control files already occupy the
workspace, the remote source is cloned under `repo/` and recorded as `source_root`.

Intake does not install packages, implement features, run the project test suite, or
perform broad semantic design. It produces only:

```text
.agents_tmp/INTAKE.json
```

with source facts, Git metadata, manifests, tests, instruction files, top-level
structure, explicitly named paths, and at most 15 relevant candidate paths.

## Phase 1: prescriptive Qwen plan

`planner-qwen` starts from `INTAKE.json` rather than exploring blindly.

It may perform narrow semantic verification with:

```text
glob
grep
planning_file_editor
```

The plan must define:

- objective and intake evidence;
- existing/native/upstream/installable solution analysis;
- exact mutable paths;
- forbidden paths;
- ordered execution steps;
- explicit replan conditions;
- acceptance criteria;
- PASS conditions;
- one machine-readable deterministic validation specification.

Mutable paths are literal workspace-relative paths. Directories end in `/`; globs are
not allowed.

The machine-readable block is bounded by:

```text
<!-- VALIDATION_SPEC_BEGIN -->
...
<!-- VALIDATION_SPEC_END -->
```

and contains 1..20 deterministic commands with expected exit codes and timeouts.

## Phase 2: guarded Spark execution

`executor-spark` remains the fast implementation model.

It receives:

```text
terminal
file_editor
```

but its subagent definition now carries native OpenHands `PreToolUse` hooks.

The file-editor guard:

- allows read-only views;
- denies edits outside `# Mutable paths`;
- denies edits to `INTAKE.json`, `PLAN.md`, and `VALIDATION.json`.

The terminal guard blocks selected destructive Git/system operations, direct access to
orchestration artifacts, and common shell-write bypasses. Project edits are expected to
use `file_editor`.

The authoritative validator later checks the actual Git change set, so an executor
cannot obtain final PASS merely by claiming it stayed in scope.

The executor may run tests/builds during implementation for feedback, but reports only:

```text
EXECUTION_RESULT: READY_FOR_VALIDATION
```

not PASS.

## Phase 3: external deterministic validation

Immediately before every `reviewer-qwen` task, a parent `PreToolUse` hook:

1. parses the current plan;
2. validates the validation JSON schema/bounds;
3. rejects dangerous validation commands;
4. executes every validation command from the workspace root;
5. records actual exit codes, timeouts, and bounded stdout/stderr tails;
6. computes SHA-256 of the plan;
7. obtains the current Git changed-file set;
8. checks those changes against `# Mutable paths` and `# Forbidden paths`;
9. atomically writes `.agents_tmp/VALIDATION.json`.

The hook runs outside executor-spark and reviewer-qwen.

If no Git-backed change set can be established, scope validation fails closed.

A failing command or scope violation produces `overall: FAIL`; review still runs so the
reviewer can turn deterministic failures into concrete correction instructions.
Malformed/unsafe validation contracts deny the reviewer task and fail the workflow.

## Phase 4: read-only Qwen review

`reviewer-qwen` has:

```text
glob
grep
read_file
```

and no terminal or write tool.

It must first read:

```text
.agents_tmp/INTAKE.json
.agents_tmp/PLAN.md
.agents_tmp/VALIDATION.json
```

It verifies that `VALIDATION.json` matches the current validation specification and
scope evidence, then independently reviews the implementation and acceptance criteria.

It may return APPROVED only when external validation reports PASS.

## Replan and correction policy

- `INTAKE -> PLAN` only after `INTAKE_RESULT: READY`.
- `PLAN -> EXECUTE` only after `PLANNING_RESULT: READY`.
- `EXECUTE -> PLAN` only after `BLOCKED: REPLAN_REQUIRED`.
- maximum replan cycles: 2.
- `REVIEW -> EXECUTE` only for concrete reviewer defects.
- every new review triggers a fresh external validation run.
- maximum correction cycles: 3.
- any phase/tool infrastructure failure stops fail-closed.

## Final PASS

Final PASS requires all of:

1. successful workspace intake;
2. a bounded planner contract;
3. changed-file scope verification;
4. current `.agents_tmp/VALIDATION.json` with `overall: PASS`;
5. `reviewer-qwen` returning APPROVED.

No LLM self-certifies final PASS.

## Spark profile bootstrap

The isolated conversation runtime has its own OpenHands profile store. On
`SessionStart`, the plugin creates the runtime profile `spark2.5-4b` if absent:

```text
model:    openai/spark2.5-4b
base_url: http://host.docker.internal:30000/v1
api_key:  local
```

The key is a non-secret placeholder for the local OpenAI-compatible endpoint. Existing
profiles with the same name are left untouched.
