# Rizzo E2E reliability results — 2026-10-13

Tested production commit: c3c8ed4fdac786dd6522cdbab143a7d09172cdeb.
Base: ce4ab6823e8996978b5d8b07ce007f5ce7810eee.
Branch: codex/rizzo-e2e-reliable. PR: [#4](https://github.com/rickinca84/bibliotecario-openhands-workflow/pull/4).

## Verified outcome

The native OpenHands Docker conversation 26864a07-8a50-44b0-99f6-27539dd6e210 finished successfully.
No explicit test hooks were added to this positive run. The plugin was loaded from the
immutable GitHub commit above. Executed preflight and Stop commands matched the source exactly.

| Criterion | Observed result |
| --- | --- |
| hello.txt | Exactly 24 bytes: Bibliotecario Rizzo test; no newline |
| hello SHA-256 | 8c079f4df580187a8c245feebb70238d477f9515cdafeb9e84f9788a607004e6 |
| fixture README SHA-256 before and after | f0beec0c931d8babcaa1fdb449f406e2a2fb90abc34e1648913a463fdf5d6091 |
| deterministic validation | overall=PASS, phase=GLOBAL, graph_complete=true |
| final semantic review | verdict=APPROVED |
| native Stop | successful, not blocked, exit code 0 |
| phase delegations | planner=1, executor=1, reviewer=1 |
| contract replans | 0 |
| parent routing | 4 deterministic decisions, 0 semantic selections in event replay |
| global profiles | 7 file hashes and active profile selections unchanged |
| run wall time, excluding Docker provisioning | 178.053 seconds |

CONTRACT, WORK_GRAPH, VALIDATION and REVIEW hash bindings were checked independently.
Scope validation listed only hello.txt as changed. The workflow repository's own README.md
also remains byte-for-byte unchanged from the base commit.

The actual final validators were rerun on separate copies of the fixture:
baseline exits [0, 0], wrong hello bytes [1, 0], changed README [0, 1].
This proves the exercised validators do not succeed vacuously.

## Prompt and call measurements

These are native OpenHands stats, including repeated and cached prompt tokens.
The parent adapter performs deterministic Python routing; its HTTP requests and SDK
token counts are separated from actual Qwen/Spark inference calls.

| Component | Calls | Prompt tokens | Completion tokens | Cache-read tokens | Summed response latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Parent adapter, no model inference | 4 | 38920 | 366 | 0 | 0.239 s |
| Initial Qwen planner | 3 | 20423 | 1121 | 13704 | 88.508 s |
| Spark executor | 6 | 41061 | 638 | 39569 | 15.758 s |
| Final Qwen reviewer | 2 | 12792 | 490 | 5151 | 70.045 s |

There were 11 model inference requests: 5 Qwen and 6 Spark, with 74276 prompt
tokens in total. Qwen was used only for initial planning and final review.

## Regression coverage and recovery

All 102 tests pass locally with exit code 0 and in
[GitHub Actions](https://github.com/rickinca84/bibliotecario-openhands-workflow/actions/runs/38328646135).
The command is:

    python3 -m unittest discover -s tests -p "test_*.py" -v

The regression suite exercises stale contract/retry/reviewer evidence, new human requests,
reviewer-only approval, native appended prompt context, exact JSON diagnostics, rejection of
malformed JSON and Python before write_file, constant validators, failed content checks,
forbidden README edits, read-only checkpoints, invalid cached scope, and one bounded replan.

A three-node hook/router integration test completes a, b, z in that order despite z appearing
before b in the graph. It records planner=1, executor=3, reviewer=1 and two deterministic
advances. A chooser that raises on any semantic request is supplied to the routing checks.
Read-only prepared items are accepted, while any project-file modification under empty
mutable_paths fails the scope check. These graph checks are deterministic contract tests;
the positive native model E2E uses one node.

Invalid contracts permit one repair carrying the exact diagnostic. A second invalid contract
becomes a terminal failure. The regression drives that failure through native finish,
PostToolUse proof and Stop using only the reason field visible in SDK tool observations.
A real earlier run at 8b52ff3 also recovered after exactly one contract replan and finished
with GLOBAL PASS and APPROVED.

A separate native Docker negative test on the final production commit injected a terminal
contract failure through a per-conversation PreToolUse hook. It did not alter the positive
run or production plugin. The production hook emitted the canonical reason, and the
conversation finished after exactly two parent decisions, with zero Qwen or subagent calls,
a hash-bound FAILURE_REPORT and successful native Stop. This specifically verifies the
reason/additionalContext integration that previously caused repeated denied delegations.

## Reuse and scope audit

OpenHands already provides Docker conversation provisioning, per-conversation settings and
hook merging, profile materialization, task delegation, native file tools, and lifecycle hooks.
These existing facilities were reused. JSON parsing, Python syntax compilation and AST checks
use the Python standard library. No new scheduler service, SDK fork or package dependency was added.

Prepared work items remain under WORK_GRAPH.work_items; graph nodes retain their original
minimal schema. Scope, contract locks, completed-node fingerprints, command exit codes,
review provenance and Stop checks remain authoritative. No validator or acceptance criterion
was weakened. The existing cycle test's unconditional-success fixture was replaced with a
real file condition so it continues to reach the cycle validation under the stricter preflight.

Only the assigned worktree and codex branch were modified. No main changes or merge were made.
Global stable OpenHands profiles and existing secrets were not edited. Test artifacts,
native harnesses, raw evidence and logs remain locally under the assigned worktree's .e2e/
directory; they are not installed globally.

Full sanitized evidence is in [the JSON record](evidence/rizzo-e2e-2026-10-13.json).
