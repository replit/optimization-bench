"""Execute the upstream bin/terser; shared observer injects all OS faults."""
from pathlib import Path

SURFACE = {"id": "terser.bin.cli.v1", "visibility": "public",
           "contract_id": "terser-cli-raw-effects-v1", "precondition_id": "terser-cli-fixtures-v1",
           "authority": "package.json:bin, README.md:Command line options"}


def body(mode):
    return "console.log('λ🙂');\n" * (8192 if mode == "large_stdout" else 1)


def CLI_PRECONDITION(case, surface):
    mode = case.get("options", {}).get("mode")
    valid = (surface == SURFACE and case.get("task") == "terser-minify"
             and mode in ("small_stdout", "large_stdout", "file_output")
             and case.get("options") == {"mode": mode}
             and case.get("input") == {"text": body(mode)}
             and case.get("surface_id") == SURFACE["id"]
             and case.get("contract_id") == SURFACE["contract_id"]
             and case.get("precondition_id") == SURFACE["precondition_id"]
             and case.get("sequence") == ["invoke"] and case.get("faults") == [])
    return {"valid": bool(valid), "reason": "Frozen synthetic public JS via upstream CLI"}


def CLI_CASES(context):
    cases = []
    for mode in ("small_stdout", "large_stdout", "file_output"):
        root = Path(context["out"]) / "terser-cli-inputs" / mode
        root.mkdir(parents=True, exist_ok=True)
        (root / "input.js").write_text(body(mode))
        file_output = mode == "file_output"
        if file_output:
            (root / "output").mkdir(exist_ok=True)
        argv = ["/usr/local/bin/node", "{build}/project/bin/terser", "input.js"]
        if file_output:
            argv += ["--output", "output/result.js"]
        cases.append({
            "case": {"task": "terser-minify", "surface_id": SURFACE["id"],
                     "contract_id": SURFACE["contract_id"], "precondition_id": SURFACE["precondition_id"],
                     "input": {"text": body(mode)}, "options": {"mode": mode},
                     "sequence": ["invoke"], "faults": []},
            "surface": SURFACE, "inputs": str(root),
            "step": {"argv": argv, "cwd": ".", "timeout": 30},
            "watch_inputs": ["input.js"], "watch_outputs": ["output"] if file_output else [],
            "output_dirs": ["output"] if file_output else [],
            "required_outputs": ["output/result.js"] if file_output else [],
            "payload_class": "large" if mode == "large_stdout" else "small",
            "faults": ["healthy", "dev_full", "closed_reader", "closed_stdout"]
                      + (["readonly_dir"] if file_output else []),
        })
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases,
            "scope": "Actual upstream bin/terser, exact raw stdout/stderr/exits/files. "
                     "No assumed nonzero exit for stdout faults."}
