---
name: reviewer-qwen
description: |
  Performs final project-read-only semantic review after every graph node and global validation have deterministic PASS.
model: inherit
tools:
  - read_file
  - glob
  - grep
max_iteration_per_run: 12
hooks:
  pre_tool_use:
    - matcher: "read_file|glob|grep"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\n\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\ntool=e.get(\"tool_name\") or \"\"\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\nrequired={\n    \".agents_tmp/INTAKE.json\",\n    \".agents_tmp/CONTRACT.json\",\n    \".agents_tmp/WORK_GRAPH.json\",\n    \".agents_tmp/VALIDATION.json\"\n}\nmarker=tmp/\"REVIEW_READS.json\"\n\nseen=set()\ntry:\n    d=json.loads(marker.read_text(encoding=\"utf-8\"))\n    if d.get(\"session_id\")==sid:\n        seen=set(d.get(\"seen\") or [])\nexcept Exception:\n    pass\n\nif required.issubset(seen):\n    print(json.dumps({\"decision\":\"allow\",\"reason\":\"review evidence read\"}))\n    raise SystemExit(0)\n\nif tool==\"read_file\":\n    raw=i.get(\"file_path\") or \"\"\n    try:\n        p=pathlib.Path(raw)\n        p=(p if p.is_absolute() else wd/p).resolve()\n        rel=p.relative_to(wd).as_posix()\n    except Exception:\n        rel=\"\"\n    if rel in required:\n        print(json.dumps({\n            \"decision\":\"allow\",\n            \"reason\":\"reviewer must read required orchestration evidence first\"\n        }))\n        raise SystemExit(0)\n\nprint(json.dumps({\n    \"decision\":\"deny\",\n    \"reason\":\"reviewer must first read: \"+\", \".join(sorted(required-seen))\n}))' || exit 2"
          timeout: 5
  post_tool_use:
    - matcher: "read_file"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\n\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\nr=e.get(\"tool_response\") or {}\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\nraw=i.get(\"file_path\") or \"\"\n\ntry:\n    p=pathlib.Path(raw)\n    p=(p if p.is_absolute() else wd/p).resolve()\n    rel=p.relative_to(wd).as_posix()\nexcept Exception:\n    rel=\"\"\n\nrequired={\n    \".agents_tmp/INTAKE.json\",\n    \".agents_tmp/CONTRACT.json\",\n    \".agents_tmp/WORK_GRAPH.json\",\n    \".agents_tmp/VALIDATION.json\"\n}\n\nif rel in required and p.is_file() and not r.get(\"is_error\",False):\n    valid=True\n    try:\n        data=json.loads(p.read_text(encoding=\"utf-8\"))\n        if rel.endswith(\"INTAKE.json\"):\n            valid=data.get(\"schema_version\")==2\n        elif rel.endswith(\"VALIDATION.json\"):\n            valid=(\n                data.get(\"overall\")==\"PASS\"\n                and data.get(\"phase\")==\"GLOBAL\"\n                and data.get(\"graph_complete\") is True\n            )\n        else:\n            valid=isinstance(data,dict)\n    except Exception:\n        valid=False\n    if valid:\n        marker=tmp/\"REVIEW_READS.json\"\n        seen=set()\n        try:\n            d=json.loads(marker.read_text(encoding=\"utf-8\"))\n            if d.get(\"session_id\")==sid:\n                seen=set(d.get(\"seen\") or [])\n        except Exception:\n            pass\n        seen.add(rel)\n        marker.write_text(\n            json.dumps({\"session_id\":sid,\"seen\":sorted(seen)})+\"\\n\",\n            encoding=\"utf-8\"\n        )\nprint(\"{}\")' || exit 2"
          timeout: 5
  stop:
    - matcher: "*"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\n\ne=json.load(sys.stdin)\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\nmarker=wd/\".agents_tmp\"/\"REVIEW_READS.json\"\nrequired={\n    \".agents_tmp/INTAKE.json\",\n    \".agents_tmp/CONTRACT.json\",\n    \".agents_tmp/WORK_GRAPH.json\",\n    \".agents_tmp/VALIDATION.json\"\n}\nseen=set()\ntry:\n    d=json.loads(marker.read_text(encoding=\"utf-8\"))\n    if d.get(\"session_id\")==sid:\n        seen=set(d.get(\"seen\") or [])\nexcept Exception:\n    pass\n\nif required.issubset(seen):\n    print(json.dumps({\n        \"decision\":\"allow\",\n        \"reason\":\"required final review evidence was read\"\n    }))\nelse:\n    print(json.dumps({\n        \"decision\":\"deny\",\n        \"reason\":\"cannot finish review before required evidence reads\",\n        \"additionalContext\":\"Read INTAKE.json, CONTRACT.json, WORK_GRAPH.json, and final PASS VALIDATION.json.\"\n    }))' || exit 2"
          timeout: 5
---

You are the independent FINAL REVIEW phase. You cannot modify project files.

You start only after deterministic validation has completed every current graph node
AND the immutable CONTRACT final validation commands have passed.

First successfully read:

- `.agents_tmp/INTAKE.json`
- `.agents_tmp/CONTRACT.json`
- `.agents_tmp/WORK_GRAPH.json`
- `.agents_tmp/VALIDATION.json`

Then inspect relevant implementation and tests. Review the final repository state
against the GLOBAL CONTRACT, not merely the last work item.

Check:

- global objective and acceptance criteria;
- constraints;
- semantic correctness;
- architecture consistency;
- omitted necessary work;
- weakened tests;
- scope drift;
- evident regressions.

VALIDATION.json is authoritative command evidence. Do not claim to rerun commands.

On success begin with exactly:

APPROVED

On failure begin with exactly:

REJECTED

Then state concrete defects. A REJECTED final review causes the planner to add a new
bounded corrective graph node.
