# P620 experimental install: Rizzo parent

This path is deliberately isolated from the stable Bibliotecario profile.

## What is reused natively

No Agent Canvas/OpenHands source patch is required.

The experiment uses:

- native Agent Canvas plugin installation;
- native saved LLM profiles;
- native Agent Profiles;
- native `task_tool_set` delegation;
- native file/search tools for the delegated agents;
- native `/api/profiles/{name}/validate`;
- native `/api/agent-profiles/{name}/materialize`;
- existing Bibliotecario hooks for scope, budgets, validation and stop.

The OpenHands native critic is **not** used as a replacement for `reviewer-qwen`.
The API critic classifies the conversation trace; it does not independently inspect
repository files against the locked global contract. The deterministic validator and
the read-only Qwen reviewer therefore remain separate phases.

## Processes

Run Rizzo Flow on the WSL host:

```bash
cd <RIZZO_FLOW_REPOSITORY>
uv run rizzo serve --host 127.0.0.1 --port 8017
```

Run the OpenAI-compatible parent adapter from this plugin checkout:

```bash
python3 skills/rizzo-flow/scripts/openai_adapter.py --host 0.0.0.0 --port 18019
```

On first start the adapter generates a random bearer token at
`~/.openhands/rizzo-router/api-key.txt` with mode `0600`. The token is never printed.
The installer reads the same file and saves it through the native LLM-profile secret
path. Set `RIZZO_ROUTER_API_KEY` on both processes instead if you want to supply your
own token.

Only health/model discovery is unauthenticated. `POST /v1/chat/completions` requires
the bearer token because the request can contain repository/conversation context.

Health checks:

```bash
curl -fsS http://127.0.0.1:8017/health
curl -fsS http://127.0.0.1:18019/health
```

The Agent Profile's LLM uses:

```text
http://host.docker.internal:18019/v1
```

matching the existing pattern used by local model profiles from the conversation
runtime to reach services hosted in WSL.

## Native preflight — no writes

With Agent Canvas running on the P620:

```bash
python3 scripts/install_rizzo_parent.py
```

Default ingress is `http://127.0.0.1:8001`. The script obtains the existing Canvas
session key from the standard environment/file locations but never prints it.

Dry-run performs:

1. adapter health;
2. Rizzo health;
3. Agent Canvas ingress check;
4. collision check for the two new profile names;
5. native LLM profile `/validate`.

It writes nothing.

## Persist isolated profiles

```bash
python3 scripts/install_rizzo_parent.py --apply
```

This creates only:

- LLM profile `rizzo-bibliotecario-router`;
- Agent Profile `bibliotecario-rizzo-experiment`.

The stable Qwen/Spark profiles and the active profile are not overwritten.

After the LLM profile is saved, the script calls native Agent Profile
`/materialize`. The Agent Profile is saved only if `valid == true`. A failure
rolls back the two experimental names.

The parent profile exposes the union required for native subagent scoping:

```text
task_tool_set
read_file
write_file
edit
glob
grep
```

This does **not** grant the Rizzo selector permission to use those repository tools.
The adapter emits only `task` or `finish`; the Bibliotecario parent hooks also deny
direct repository-tool use.

## Activation is separate

Only after the isolated profile has materialized successfully:

```bash
python3 scripts/install_rizzo_parent.py --apply --activate
```

Do not use `--activate` for the first install attempt. Prefer saving without
activation, inspect the native profile diagnostics, then activate for the E2E.

## First E2E

Use a disposable Git repository, not Bibliotecario itself. A suitable test is:

```text
Add a function slugify(text) in src/text_utils.py and tests for spaces,
mixed case and repeated separators. Reuse an existing implementation/library
if the repository already has one. Do not modify files outside src/ and tests/.
```

Expected control flow:

```text
human request
  -> Rizzo parent: task(planner-qwen)
  -> planner Qwen: CONTRACT + WORK_GRAPH + CURRENT_WORK_ITEM
  -> Rizzo parent: task(executor-spark)
  -> Spark: bounded file edits
  -> Rizzo parent: task(reviewer-qwen)
  -> deterministic PreToolUse validation
       FAIL -> Spark/planner
       PASS -> reviewer Qwen
  -> APPROVED
  -> native finish, accepted only by deterministic Stop gate
```

Promotion requires both the E2E and a forced-failure E2E to pass before any merge
or replacement of the stable profile.
