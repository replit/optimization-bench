"""Read-only registration inventory; never infer runtime qualification from hooks."""
import argparse
import json
from pathlib import Path

from .fuzzing_generic import task_runners
from .verifier import ROOT


def inventory(root=ROOT):
    rows = []
    for task in sorted((Path(root) / "tasks").iterdir()):
        if not (task / "release.json").is_file():
            continue
        row = {"task": task.name, "runtime_validation": "not_run",
               "cli_requests_registered": False, "library_callbacks": [],
               "header_provider_registered": False, "affinity_sampling_source_present": False}
        try:
            callbacks, module = task_runners(task)
            row.update(module_load="loaded" if module else "not_registered",
                       cli_requests_registered=callable(getattr(module, "CLI_CASES", None)),
                       library_callbacks=sorted(callbacks),
                       header_provider_registered=callable(getattr(module, "HEADER_CONSUMERS", None)))
        except Exception as error:
            row.update(module_load="blocked", reason=type(error).__name__ + ": " + str(error))
        files = [path for path in (task / "tests/fuzz").rglob("*")
                 if path.is_file() and path.suffix in (".py", ".rs", ".go", ".c", ".cc", ".cpp", ".cxx",
                                                       ".h", ".hpp", ".mjs", ".js")]
        row["affinity_sampling_source_present"] = any(
            any(token in path.read_text(errors="replace")
                for token in ("sched_getaffinity", "Cpus_allowed_list", "execution_affinity"))
            for path in files)
        row["gaps"] = []
        if row["module_load"] != "loaded":
            row["gaps"].append("RC module not loaded")
        if not row["cli_requests_registered"]:
            row["gaps"].append("CLI scope/requests unregistered")
        for name in ("stream_faults", "repeat_in_process", "aliasing"):
            if name not in row["library_callbacks"]:
                row["gaps"].append(name + " unregistered")
        if not row["header_provider_registered"]:
            row["gaps"].append("Header scope/consumers unregistered")
        if not row["affinity_sampling_source_present"]:
            row["gaps"].append("OS affinity sampling source not found")
        row["gaps"].append("Runtime/CPU evidence not certified by this inventory")
        rows.append(row)
    return {"format": "judge-rc-registration-inventory-v1", "tasks": rows,
            "qualification": "Registration and source inspection only; not execution or applicability approval"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(inventory(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
