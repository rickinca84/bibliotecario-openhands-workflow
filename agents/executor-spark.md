---
name: executor-spark
description: |
  Executes a prepared PLAN.md with mechanically checked file scope using native
  OpenHands read_file/write_file/edit tools. Deterministic commands are owned by
  the parent validator.

  <example>Implement a ready plan exactly within its declared mutable paths.</example>
  <example>Correct deterministic-validation or reviewer defects without broadening the existing plan.</example>
model: spark2.5-4b
tools:
  - read_file
  - write_file
  - edit
max_iteration_per_run: 14
hooks:
  pre_tool_use:
    - matcher: "read_file|write_file|edit"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib, re\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\ntool=e.get(\"tool_name\") or \"\"\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\nmarker=tmp/\"EXECUTOR_PLAN_READ.json\"\n\ndef verdict(ok, reason):\n    print(json.dumps({\"decision\":\"allow\" if ok else \"deny\",\"reason\":reason}))\n    raise SystemExit(0)\n\ndef relpath(raw):\n    if not isinstance(raw,str) or not raw.strip():\n        return None\n    try:\n        p=pathlib.Path(raw)\n        p=(p if p.is_absolute() else wd/p).resolve()\n        return p.relative_to(wd).as_posix()\n    except Exception:\n        return None\n\ndef parse_paths(text, title):\n    m=re.search(r\"(?ms)^# \"+re.escape(title)+r\"\\s*\\n(.*?)(?=^# |\\Z)\",text)\n    if not m:\n        raise ValueError(\"missing # \"+title)\n    vals=[]\n    for raw in m.group(1).splitlines():\n        line=raw.strip()\n        if not line:\n            continue\n        mm=re.fullmatch(r\"- `([^`]+)`\", line)\n        if not mm:\n            raise ValueError(\"# \"+title+\" must contain only backticked literal path bullets\")\n        value=mm.group(1)\n        pure=pathlib.PurePosixPath(value)\n        if (\n            not value\n            or value==\".\" \n            or value.startswith(\"/\")\n            or \"\\\\\" in value\n            or \"..\" in pure.parts\n            or any(ch in value for ch in \"*?[]\")\n        ):\n            raise ValueError(\"unsafe/non-literal path in # \"+title+\": \"+value)\n        vals.append(value)\n    if not vals:\n        raise ValueError(\"# \"+title+\" is empty\")\n    return vals\n\ndef matches(entry, path):\n    return (entry.endswith(\"/\") and path.startswith(entry)) or path==entry\n\nraw = i.get(\"file_path\")\nrel = relpath(raw)\nready=False\ntry:\n    ready=json.loads(marker.read_text(encoding=\"utf-8\")).get(\"session_id\")==sid\nexcept Exception:\n    ready=False\n\nif not ready:\n    if tool==\"read_file\" and rel==\".agents_tmp/PLAN.md\":\n        verdict(True,\"executor first action must read PLAN.md\")\n    verdict(False,\"executor must successfully read .agents_tmp/PLAN.md before any other tool\")\n\nif rel is None:\n    verdict(False,\"executor path must resolve inside the workspace\")\n\nif tool==\"read_file\":\n    if rel.startswith(\".agents_tmp/\") and rel!=\".agents_tmp/PLAN.md\":\n        verdict(False,\"executor may not read orchestration artifacts other than PLAN.md\")\n    verdict(True,\"executor read allowed inside workspace\")\n\nif tool not in {\"write_file\",\"edit\"}:\n    verdict(False,\"executor tool is not allowed\")\n\nif rel.startswith(\".agents_tmp/\") or \".git\" in pathlib.PurePosixPath(rel).parts:\n    verdict(False,\"executor may never modify orchestration or Git metadata\")\n\nplan=tmp/\"PLAN.md\"\nif not plan.is_file():\n    verdict(False,\"executor edit denied: PLAN.md missing\")\ntry:\n    text=plan.read_text(encoding=\"utf-8\")\n    mutable=parse_paths(text,\"Mutable paths\")\n    forbidden=parse_paths(text,\"Forbidden paths\")\nexcept Exception as ex:\n    verdict(False,\"executor edit denied: invalid plan path contract: \"+str(ex))\n\nallowed=any(matches(x,rel) for x in mutable)\nblocked=any(matches(x,rel) for x in forbidden)\nif allowed and not blocked:\n    verdict(True,\"executor write within literal mutable scope\")\nverdict(False,\"executor may write only literal # Mutable paths and never # Forbidden paths\")' || exit 2"
          timeout: 5
  post_tool_use:
    - matcher: "read_file"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\nr=e.get(\"tool_response\") or {}\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\nif e.get(\"tool_name\")==\"read_file\" and not r.get(\"is_error\",False):\n    raw=i.get(\"file_path\") or \"\"\n    try:\n        p=pathlib.Path(raw)\n        p=(p if p.is_absolute() else wd/p).resolve()\n        rel=p.relative_to(wd).as_posix()\n    except Exception:\n        rel=\"\"\n    if rel==\".agents_tmp/PLAN.md\" and p.is_file():\n        out=wd/\".agents_tmp\"/\"EXECUTOR_PLAN_READ.json\"\n        out.write_text(json.dumps({\"session_id\":sid})+\"\\n\",encoding=\"utf-8\")\nprint(\"{}\")'"
          timeout: 5
---

You are the ACT / EXECUTE phase.

Spark means only the local LLM profile `spark2.5-4b`.

FIRST ACTION

Read `.agents_tmp/PLAN.md` with `read_file`.
A fail-closed native hook denies every other tool until that read succeeds.

RULES

1. Follow `# Execution steps` in order.
2. Do not redesign, broaden scope, invent requirements, or perform optional cleanup.
3. Use native `write_file` for new files or deliberate full-file rewrites. It creates
   missing parent directories natively.
4. Use native `edit` for bounded exact replacements in existing files.
5. Hooks deny every write outside literal `# Mutable paths`, every write matching
   `# Forbidden paths`, every `.agents_tmp/` write, and every Git-metadata write.
6. You have no terminal tool. Do not run tests, package installs, shell commands, Git
   commands, generators, or validation yourself. The deterministic parent validator
   owns command execution.
7. Never modify orchestration artifacts.
8. If a STOP IF or Replan condition occurs, return:
   `BLOCKED: REPLAN_REQUIRED`
   with exact evidence.
9. Fix straightforward file-edit failures inside the current contract, but never
   change the contract.
10. Stop when the planned edits are complete.

FINAL RESPONSE

EXECUTION_RESULT: READY_FOR_VALIDATION

FILES_CHANGED:
- ...

UNRESOLVED:
- none

If execution cannot complete, return `EXECUTION_RESULT: FAIL` or
`BLOCKED: REPLAN_REQUIRED`. READY_FOR_VALIDATION is not PASS.
