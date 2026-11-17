"""Protected RC requests and raw library facts; verdicts remain shared."""
import json
from pathlib import Path
import shutil
import tempfile

from .adapter import precondition as PRECONDITION, compare as COMPARE, TASK

ROOT = Path(__file__).resolve().parent
PIN = "5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76"


def HEADER_CONSUMERS(context):
    return {
        "format": "judge-generic-header-request-v1", "applicable": False,
        "reason": "Authenticated pinned pure-Python feature has no C/C++ headers/native units.",
        "applicability_evidence": json.loads((ROOT / "header-native-scope.json").read_text()),
    }


def CLI_PRECONDITION(case, surface):
    valid = (
        surface.get("visibility") == "public"
        and case.get("task") == TASK
        and case.get("surface_id") == surface.get("id") == "sqlglot.__main__"
        and case.get("contract_id") == surface.get("contract_id") == "sqlglot-cli-v1"
        and case.get("precondition_id") == surface.get("precondition_id") == "public-cli-sql-stdin-v1"
        and isinstance(case.get("input", {}).get("sql"), str)
        and 0 < len(case["input"]["sql"].encode("utf-8")) <= 131072
        and case.get("options") == {"read": "bigquery", "write": "duckdb", "pretty": False}
        and case.get("faults") == []
    )
    return {"valid": valid, "reason": "Documented SQLGlot CLI stdin transpilation" if valid
            else "Not a declared public CLI request"}


def CLI_CASES(context):
    folder = Path(context["out"]) / "sqlglot-cli-inputs"
    folder.mkdir(parents=True, exist_ok=False)
    cases = []
    # SQL is from the public grammar, not candidate-derived or oracle output.
    for size, sql in (("small", "SELECT 1 AS a;\n"),
                      ("large", "SELECT 1 AS a;\n" * 6000)):
        inputs = folder / size
        inputs.mkdir()
        (inputs / "input.sql").write_text(sql)
        case = {
            "task": TASK, "surface_id": "sqlglot.__main__", "contract_id": "sqlglot-cli-v1",
            "precondition_id": "public-cli-sql-stdin-v1", "input": {"sql": sql},
            "options": {"read": "bigquery", "write": "duckdb", "pretty": False},
            "sequence": ["invoke"], "faults": [],
        }
        surface = {
            "id": case["surface_id"], "contract_id": case["contract_id"],
            "precondition_id": case["precondition_id"], "visibility": "public",
            "authority": f"https://github.com/tobymao/sqlglot/blob/{PIN}/sqlglot/__main__.py",
            "oracle": {"comparator": "exact-cli-effects-v1",
                       "required_error_fields": {"class": "nonempty_string", "message": "string"}},
        }
        cases.append({
            "case": case, "surface": surface, "inputs": str(inputs),
            "step": {
                "argv": ["/usr/local/bin/python", "-m", "sqlglot", "-", "--read", "bigquery",
                         "--write", "duckdb", "--no-pretty", "--identify", "false"],
                "env": {"PYTHONPATH": "{build}/runtime", "PYTHONHASHSEED": "0",
                        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONSAFEPATH": "1"},
                "cwd": ".", "stdin_file": "input.sql", "timeout": 30,
            },
            "watch_inputs": ["input.sql"], "payload_class": size,
            "faults": ["healthy", "dev_full", "closed_reader", "closed_stdout"],
        })
    return {
        "format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases,
        "readonly_applicability_evidence": {
            "authority": f"https://github.com/tobymao/sqlglot/blob/{PIN}/sqlglot/__main__.py",
            "scope": "CLI exposes SQL/stdin input and stdout only; argparse declares no output-file mode.",
        },
    }


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    output = Path(context["out"]) / "sqlglot-public-library"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["image"], context["cpuset"],
           context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_sqlglot_rc_facts", {})
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="sqlglot-rc-") as directory:
            scratch = Path(directory)
            task = Path(context["task"]).resolve()
            ctx = Context(scratch, Path(context["candidate"]),
                          task / "environment/workload/inputs", scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[
                    *HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro",
                ], tmpfs="/tmp:rw,nosuid,nodev,size=4g")
            argv = ["/usr/local/bin/python", "/verifier-fuzz/rc_driver.py",
                    "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                    "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"],
                    "--out", "/scratch/facts"]
            results = executor.run([Step(argv, timeout=180)], ctx, {
                "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONSAFEPATH": "1"})
            for index, result in enumerate(results):
                (output / f"step-{index}.stdout").write_bytes(result.stdout)
                (output / f"step-{index}.stderr").write_bytes(result.stderr)
            if (scratch / "facts").is_dir():
                shutil.copytree(scratch / "facts", output / "raw", dirs_exist_ok=True)
            if len(results) != 1 or any(result.exit_code != 0 or result.leftovers for result in results):
                raise ValueError("SQLGlot isolated library worker failed; retained raw streams")
            for name in ("alias-facts.json", "repeat-facts.json", "stream-facts.json"):
                shutil.copyfile(scratch / "facts" / name, output / name)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "requested_cpuset": context["cpuset"],
                "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results],
            }, indent=2, sort_keys=True) + "\n")
        cache["key"] = key
    return json.loads((output / filename).read_text())


def aliasing(context):
    return _facts(context, "alias-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def stream_faults(context):
    return _facts(context, "stream-facts.json")


GATES = {"aliasing": aliasing, "repeat_in_process": repeat_in_process,
         "stream_faults": stream_faults}
