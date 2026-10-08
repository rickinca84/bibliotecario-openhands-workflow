---
name: reviewer-qwen
description: |
  Performs project-read-only semantic review only after deterministic validation PASS.

  <example>Review a validated implementation against the plan and acceptance criteria.</example>
  <example>Reject a semantically incorrect implementation even when deterministic tests pass.</example>
model: inherit
tools:
  - read_file
  - glob
  - grep
max_iteration_per_run: 10
hooks:
  pre_tool_use:
    - matcher: "read_file|glob|grep"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\ntool=e.get(\"tool_name\") or \"\"\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\nrequired={\".agents_tmp/INTAKE.json\",\".agents_tmp/PLAN.md\",\".agents_tmp/VALIDATION.json\"}\nmarker=tmp/\"REVIEW_READS.json\"\nseen=set()\ntry:\n    d=json.loads(marker.read_text(encoding=\"utf-8\"))\n    if d.get(\"session_id\")==sid:\n        seen=set(d.get(\"seen\") or [])\nexcept Exception:\n    pass\nif required.issubset(seen):\n    print(json.dumps({\"decision\":\"allow\",\"reason\":\"review evidence read\"}))\n    raise SystemExit(0)\nif tool==\"read_file\":\n    raw=i.get(\"file_path\") or \"\"\n    try:\n        p=pathlib.Path(raw)\n        p=(p if p.is_absolute() else wd/p).resolve()\n        rel=p.relative_to(wd).as_posix()\n    except Exception:\n        rel=\"\"\n    if rel in required:\n        print(json.dumps({\"decision\":\"allow\",\"reason\":\"reviewer must read required orchestration evidence first\"}))\n        raise SystemExit(0)\nmissing=sorted(required-seen)\nprint(json.dumps({\"decision\":\"deny\",\"reason\":\"reviewer must first read: \"+\", \".join(missing)}))' || exit 2"
          timeout: 5
  post_tool_use:
    - matcher: "read_file"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\ne=json.load(sys.stdin)\ni=e.get(\"tool_input\") or {}\nr=e.get(\"tool_response\") or {}\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\ntmp=wd/\".agents_tmp\"\nraw=i.get(\"file_path\") or \"\"\ntry:\n    p=pathlib.Path(raw)\n    p=(p if p.is_absolute() else wd/p).resolve()\n    rel=p.relative_to(wd).as_posix()\nexcept Exception:\n    rel=\"\"\nrequired={\".agents_tmp/INTAKE.json\",\".agents_tmp/PLAN.md\",\".agents_tmp/VALIDATION.json\"}\nif rel in required and p.is_file() and not r.get(\"is_error\",False):\n    valid=True\n    try:\n        if rel.endswith(\"INTAKE.json\"):\n            valid=json.loads(p.read_text(encoding=\"utf-8\")).get(\"schema_version\")==2\n        elif rel.endswith(\"VALIDATION.json\"):\n            valid=json.loads(p.read_text(encoding=\"utf-8\")).get(\"overall\")==\"PASS\"\n        else:\n            valid=bool(p.read_text(encoding=\"utf-8\").strip())\n    except Exception:\n        valid=False\n    if valid:\n        marker=tmp/\"REVIEW_READS.json\"\n        seen=set()\n        try:\n            d=json.loads(marker.read_text(encoding=\"utf-8\"))\n            if d.get(\"session_id\")==sid:\n                seen=set(d.get(\"seen\") or [])\n        except Exception:\n            pass\n        seen.add(rel)\n        marker.write_text(json.dumps({\"session_id\":sid,\"seen\":sorted(seen)})+\"\\n\",encoding=\"utf-8\")\nprint(\"{}\")'"
          timeout: 5
  stop:
    - matcher: "*"
      hooks:
        - command: "python3 -c 'import json, sys, os, pathlib\ne=json.load(sys.stdin)\nsid=e.get(\"session_id\") or \"\"\nwd=pathlib.Path(e.get(\"working_dir\") or os.getcwd()).resolve()\nmarker=wd/\".agents_tmp\"/\"REVIEW_READS.json\"\nrequired={\".agents_tmp/INTAKE.json\",\".agents_tmp/PLAN.md\",\".agents_tmp/VALIDATION.json\"}\nseen=set()\ntry:\n    d=json.loads(marker.read_text(encoding=\"utf-8\"))\n    if d.get(\"session_id\")==sid:\n        seen=set(d.get(\"seen\") or [])\nexcept Exception:\n    pass\nif required.issubset(seen):\n    print(json.dumps({\"decision\":\"allow\",\"reason\":\"required review evidence was read\"}))\nelse:\n    print(json.dumps({\"decision\":\"deny\",\"reason\":\"cannot finish review before reading INTAKE.json, PLAN.md, and PASS VALIDATION.json\",\"additionalContext\":\"Read the three required orchestration artifacts before producing a verdict.\"}))' || exit 2"
          timeout: 5
---

You are the independent REVIEW phase. You cannot modify project files.

A parent PreToolUse hook starts you only after deterministic validation reports PASS.

FIRST EVIDENCE

Before any other inspection, successfully read all three:
- `.agents_tmp/INTAKE.json`
- `.agents_tmp/PLAN.md`
- `.agents_tmp/VALIDATION.json`

Fail-closed hooks enforce this ordering and prevent you from finishing before the
successful reads.

Then inspect relevant implementation and test files under the plan's mutable scope.
Check requirements, semantic correctness, scope drift, weakened tests, missing edge
cases, and mismatch between plan and implementation.

Do not claim to rerun commands. VALIDATION.json is the authoritative deterministic
command evidence.

RETURN

On success, begin the response with exactly:

APPROVED

Then summarize validation evidence and files reviewed.

On failure, begin with exactly:

REJECTED

Then list concrete defects and exact corrections.

Never APPROVE when the evidence is stale, mismatched, or semantically insufficient.
