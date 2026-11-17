"""Register public CLI, native library and installed-header observations."""
import base64
import hashlib
import json
from pathlib import Path
from judge.fuzzing_contract import case_digest
from . import rc_library


FILTER = 'fromstream(2 | truncate_stream(inputs | select(.[0][0] == "features")))'
TASK = "jq-strip-openapi-descriptions"
WORK = Path(__file__).resolve().parents[2] / "environment/workload"
SURFACE = {"id": "jq.cli:geojson-stream-v1", "visibility": "public",
           "contract_id": "jq-streaming-complete-output-status-v1",
           "precondition_id": "jq-streaming-public-input-v1"}


def PRECONDITION(case, surface):
    return rc_library.precondition(case, surface)


def _library(context):
    key = (context["cpuset"], str(context["out"]), tuple(sorted(context["builds"].items())))
    cache = context.setdefault("_jq_library_facts", {})
    if key not in cache:
        cache[key] = rc_library.facts(context)
    return cache[key]


def repeat_in_process(context):
    return _library(context)


def _selected(context, modes):
    facts = _library(context)
    return {**facts, "cases": [row for row in facts["cases"]
                              if row["case"]["options"]["mode"] in modes]}


def aliasing(context):
    return _selected(context, {"ownership"})


def stream_faults(context):
    return _selected(context, {"parser", "input-fault"})


GATES = {"repeat_in_process": repeat_in_process,
         "aliasing": aliasing, "stream_faults": stream_faults}


def HEADER_CONSUMERS(context):
    here = Path(__file__).resolve().parent
    inventory = json.loads((here / "headers/inventory.json").read_text())
    cells = []
    for header in inventory["headers"]:
        stem = Path(header["path"]).stem
        for language, compiler in (("c", "cc"), ("c++", "c++")):
            refs = [{"path": "Makefile.am",
                     "sha256": inventory["installed_header_evidence"]["sha256"],
                     "quote": inventory["installed_header_evidence"]["quote"]},
                    {"path": header["path"], "sha256": header["sha256"],
                     "quote": '#ifdef __cplusplus\nextern "C" {' if language == "c++"
                     else "#ifndef " + stem.upper() + "_H"},
                    {"path": "configure.ac",
                     "sha256": "5bf3b41f9bee589f30db0f32203ada104fd62d278c2e95458fff7f9eb7322fa6",
                     "quote": "AC_PROG_CC\nm4_version_prereq([2.70], [], [AC_PROG_CC_STDC])"}]
            cells.append({
                "id": stem + ("-c" if language == "c" else "-cxx") + "-default",
                "supported": True, "headers": ["project/src/" + stem + ".h"], "language": language,
                "dialect": "compiler-default", "support_basis": "pinned-upstream-default-flags",
                "features": [], "support_refs": refs,
                "include_context": {"id": stem, "includes": [stem + ".h"]},
                "compiler": compiler, "include_dirs": ["project/src"],
                "libraries": ["project/.libs/libjq.a", "project/vendor/oniguruma/src/.libs/libonig.a"],
                "defines": [], "compile_flags": [], "link_flags": ["-lm", "-pthread"],
                "symbol_refs": header["referenced_symbols"],
                "translation_units": [{"name": name.replace(".c", ".cpp") if language == "c++" else name,
                                       "source": (here / "headers" / name).read_text()}
                                      for name in header["translation_units"]],
            })
    manifest = {"schema": "optimbench-header-matrix-v1", "scope": "task:" + TASK,
                "required_cells": [cell["id"] for cell in cells], "cells": cells,
                "uncovered_headers": [],
                "cxx_scope": {"status": "pinned-compiler-default",
                              "basis": "Installed headers explicitly expose extern C linkage. No numeric dialect promise.",
                              "unmapped_contexts": []},
                "limitations": ["Pinned default compiler modes only; no numeric dialect support claim."]}
    path = Path(context["out"]) / "jq-header-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return {"format": "judge-generic-header-request-v1", "applicable": True,
            "manifest": str(path)}


def CLI_PRECONDITION(case, surface):
    try:
        raw = base64.b64decode(case["input"]["data"], validate=True)
        valid = (case["task"] == TASK and case["surface_id"] == SURFACE["id"]
                 and case["contract_id"] == SURFACE["contract_id"]
                 and case["precondition_id"] == SURFACE["precondition_id"]
                 and case["case_sha256"] == case_digest(case)
                 and case["input"]["encoding"] == "base64"
                 and case["options"] == {"program": FILTER, "argv": ["-cn", "--stream"]}
                 and case["faults"] == []
                 and case["sequence"] == [{"operation": "execute-fresh-cli"}]
                 and 0 < len(raw) <= 32 * 1024 * 1024)
        return {"valid": bool(valid), "reason": "Bounded public streaming CLI"}
    except (KeyError, ValueError, TypeError):
        return {"valid": False, "reason": "Invalid streaming request"}


def CLI_CASES(context):
    manifest = json.loads((WORK / "inputs/manifest.json").read_text())
    documents = [d for b in manifest["splits"]["dev"] for d in b["docs"]]
    row = next(d for d in documents if d.get("operation") == "stream-geojson")
    raw = (WORK / "inputs/files" / row["sha256"]).read_bytes()
    if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("Public CLI fault fixture differs")
    first = json.loads(raw)["features"][0]
    examples = [("small", json.dumps({"features": [first]}, ensure_ascii=False).encode()),
                ("large", raw)]
    cases = []
    for size, body in examples:
        seed = Path(context["out"]) / "cli-inputs" / size
        seed.mkdir(parents=True, exist_ok=False)
        (seed / "input.geojson").write_bytes(body)
        case = {
            "task": TASK, "surface_id": SURFACE["id"], "contract_id": SURFACE["contract_id"],
            "precondition_id": SURFACE["precondition_id"],
            "input": {"encoding": "base64", "data": base64.b64encode(body).decode()},
            "options": {"program": FILTER, "argv": ["-cn", "--stream"]},
            "faults": [], "sequence": [{"operation": "execute-fresh-cli"}],
            "generator": "pinned-public-toronto-stream-v1",
        }
        case["case_sha256"] = case_digest(case)
        cases.append({
            "case": case, "surface": SURFACE, "inputs": seed, "payload_class": size,
            "step": {"argv": ["{build}/project/.libs/jq", "-cn", "--stream", FILTER, "input.geojson"],
                     "timeout": 60, "env": {
                         "LD_LIBRARY_PATH": "{build}/project/.libs:{build}/project/vendor/oniguruma/src/.libs"}},
            "watch_inputs": ["input.geojson"], "watch_outputs": ["."],
            "output_dirs": [], "required_outputs": [],
            "faults": ["healthy", "dev_full", "closed_reader", "closed_stdout"],
        })
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases,
            "readonly_applicability_evidence": {
                "scope": "This operation reads input and writes stdout/stderr only.",
                "source": "Pinned main.c process() writes through jv_dump/priv_fwrite.",
                "limits": "No output-file option. No exemption for native library gates."}}
