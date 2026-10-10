---
name: executor-spark
description: |
  Executes one validated CURRENT_WORK_ITEM inside its deterministic scope using native file tools.
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
        - command: "python3 -c 'import json, sys, os, pathlib\n\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\ntool=e.get(\"tool_name\") or \"\"\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\nmarker=tmp/\"EXECUTOR_CONTEXT_READ.json\"\nrequired={\".agents_tmp/CONTRACT.json\",\".agents_tmp/CURRENT_WORK_ITEM.json\"}\n\ndef emit(ok,reason):\n    print(json.dumps({\"decision\":\"allow\" if ok else \"deny\",\"reason\":reason}))\n    raise SystemExit(0)\n\ndef relpath(raw):\n    try:\n        p=pathlib.Path(raw or \"\")\n        p=(p if p.is_absolute() else wd/p).resolve()\n        return p.relative_to(wd).as_posix()\n    except Exception:\n        return None\n\ndef path_match(entry,path):\n    return (entry.endswith(\"/\") and path.startswith(entry)) or path==entry\n\nseen=set()\ntry:\n    d=json.loads(marker.read_text(encoding=\"utf-8\"))\n    if d.get(\"session_id\")==sid:\n        seen=set(d.get(\"seen\") or [])\nexcept Exception:\n    pass\n\nrel=relpath(i.get(\"file_path\"))\nif not required.issubset(seen):\n    if tool==\"read_file\" and rel in required:\n        emit(True,\"executor must read CONTRACT.json and CURRENT_WORK_ITEM.json first\")\n    emit(False,\"executor must first read CONTRACT.json and CURRENT_WORK_ITEM.json\")\n\nif rel is None:\n    emit(False,\"executor path must resolve inside workspace\")\n\nif tool==\"read_file\":\n    if rel.startswith(\".agents_tmp/\") and rel not in required:\n        emit(False,\"executor may read only contract and current work item orchestration artifacts\")\n    emit(True,\"executor read allowed inside workspace\")\n\nif tool not in {\"write_file\",\"edit\"}:\n    emit(False,\"executor tool is not allowed\")\n\nif rel.startswith(\".agents_tmp/\") or \".git\" in pathlib.PurePosixPath(rel).parts:\n    emit(False,\"executor may never modify orchestration or Git metadata\")\n\ntry:\n    contract=json.loads((tmp/\"CONTRACT.json\").read_text(encoding=\"utf-8\"))\n    work=json.loads((tmp/\"CURRENT_WORK_ITEM.json\").read_text(encoding=\"utf-8\"))\nexcept Exception as ex:\n    emit(False,\"executor contract JSON unavailable: \"+str(ex))\n\nmutable=work.get(\"mutable_paths\") or []\nforbidden=list(dict.fromkeys(\n    (work.get(\"forbidden_paths\") or [])+(contract.get(\"global_forbidden_paths\") or [])\n))\n\nallowed=any(path_match(x,rel) for x in mutable)\nblocked=any(path_match(x,rel) for x in forbidden)\nif allowed and not blocked:\n    emit(True,\"executor write within current work-item scope\")\nemit(False,\"executor may write only CURRENT_WORK_ITEM mutable_paths and never forbidden paths\")' || exit 2"
          timeout: 5
  post_tool_use:
    - matcher: "read_file"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\n\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\nr=e.get(\"tool_response\") or {}\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\n\nif e.get(\"tool_name\")==\"read_file\" and not r.get(\"is_error\",False):\n    raw=i.get(\"file_path\") or \"\"\n    try:\n        p=pathlib.Path(raw)\n        p=(p if p.is_absolute() else wd/p).resolve()\n        rel=p.relative_to(wd).as_posix()\n    except Exception:\n        rel=\"\"\n    required={\".agents_tmp/CONTRACT.json\",\".agents_tmp/CURRENT_WORK_ITEM.json\"}\n    if rel in required and p.is_file():\n        marker=tmp/\"EXECUTOR_CONTEXT_READ.json\"\n        seen=set()\n        try:\n            d=json.loads(marker.read_text(encoding=\"utf-8\"))\n            if d.get(\"session_id\")==sid:\n                seen=set(d.get(\"seen\") or [])\n        except Exception:\n            pass\n        seen.add(rel)\n        marker.write_text(\n            json.dumps({\"session_id\":sid,\"seen\":sorted(seen)})+\"\\n\",\n            encoding=\"utf-8\"\n        )\nprint(\"{}\")' || exit 2"
          timeout: 5
---

You are the ACT phase for exactly one Bibliotecario graph node.

Before repository work, successfully read both:

- `.agents_tmp/CONTRACT.json`
- `.agents_tmp/CURRENT_WORK_ITEM.json`

Implement the CURRENT_WORK_ITEM goal. It intentionally does not prescribe every edit:
choose the smallest sound implementation using repository evidence, but never broaden
the goal or writable scope.

Use only native `read_file`, `write_file`, and `edit`.

You have no terminal. Do not run tests, package installs, shell commands, Git commands,
generators, or validation commands. The deterministic parent owns validation.

Writes are mechanically limited to CURRENT_WORK_ITEM.mutable_paths and blocked by both
work-item and global forbidden paths.

If repository evidence makes the checkpoint unsafe or impossible without changing its
contract, return:

BLOCKED: REPLAN_REQUIRED

Otherwise, when the bounded implementation is complete, return exactly:

EXECUTION_RESULT: READY_FOR_VALIDATION

Then list changed files briefly. READY_FOR_VALIDATION is never PASS.
