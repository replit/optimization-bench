"""Fixed public CLI request inventory; shared runners inject and classify faults."""
import hashlib
import json
from pathlib import Path

from .adapter import TASK, seal

FAULTS = ["healthy", "dev_full", "closed_reader", "closed_stdout", "readonly_dir"]
DEFINITIONS = (
    ("parse-small", "parse", "SELECT 1;\n", "small"),
    ("parse-large", "parse", "SELECT 1;\n" * 512, "large"),
    ("fix-file", "fix", "select  1\n", "small"),
)


def public_cases():
    return [
        seal({
            "task": TASK, "operation": "cli_" + command,
            "surface_id": "sqlfluff.cli:" + command,
            "contract_id": "sqlfluff-cli-os-faults-v1",
            "precondition_id": "sqlfluff-fixed-public-cli-fixture-v1",
            "input": {"sql": sql, "domain": "success", "fixture_id": name},
            "options": {"dialect": "ansi", "format": "json" if command == "parse" else "human",
                        "processes": 1, "fixed_suffix": ".fixed" if command == "fix" else None},
            "sequence": ["fresh-files", command, "observe-complete-streams-and-files"],
            "faults": FAULTS if command == "fix" else FAULTS[:-1],
            "provenance": {"kind": "public-SQL-grammar", "private_inputs_used": False,
                           "source": "Pinned SQLFluff parse/fix documented CLI and ANSI SELECT grammar"},
        })
        for name, command, sql, _payload in DEFINITIONS
    ]


def precondition(case, surface):
    if not isinstance(case, dict):
        return {"valid": False, "reason": "case must be a fixed public fixture"}
    if case not in public_cases():
        return {"valid": False, "reason": "unknown or changed fixed public CLI request"}
    expected = surface_for(case)
    if not isinstance(surface, dict) or any(surface.get(k) != expected[k] for k in
                                           ("id", "contract_id", "precondition_id", "visibility")):
        return {"valid": False, "reason": "public CLI surface binding differs"}
    return {"valid": True, "reason": "fixed constructor-valid public SQL and documented CLI options"}


def surface_for(case):
    return {"id": case["surface_id"], "visibility": "public",
            "contract_id": case["contract_id"], "precondition_id": case["precondition_id"]}


def requests(context):
    root = Path(context["out"]) / "sqlfluff-cli-inputs"
    root.mkdir(parents=True, exist_ok=True)
    specifications = []
    for case, (name, command, sql, payload) in zip(public_cases(), DEFINITIONS):
        inputs = root / name
        (inputs / "project").mkdir(parents=True, exist_ok=True)
        path = inputs / "project/case.sql"
        if path.exists() and path.read_text() != sql:
            raise ValueError("protected CLI fixture changed")
        path.write_text(sql)
        argv = ["python3", "-m", "sqlfluff", command, "project/case.sql",
                "--dialect", "ansi", "--nocolor"]
        if command == "parse":
            argv += ["--format", "json"]
        else:
            argv += ["--disable-progress-bar", "--processes", "1", "--fixed-suffix", ".fixed"]
        specification = {
            "case": case, "surface": surface_for(case), "inputs": str(inputs),
            "payload_class": payload, "watch_inputs": ["project/case.sql"],
            "faults": case["faults"],
            "step": {
                "argv": argv, "cwd": ".", "timeout": 20, "stdin_file": None,
                "env": {
                    "PYTHONPATH": "{build}/runtime", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "TZ": "UTC",
                    "PYTHONWARNINGS": "ignore::UserWarning:multiprocessing.resource_tracker",
                },
            },
        }
        if command == "fix":
            specification.update(output_dirs=["project"],
                                 watch_outputs=["project/case.fixed.sql"],
                                 required_outputs=["project/case.fixed.sql"])
        specifications.append(specification)
    return {"format": "judge-generic-cli-cases-v1", "applicable": True,
            "cases": specifications}


def header_request(context):
    scope_file = Path(context["task"]) / "tests/fuzz/public-scope.json"
    scope = json.loads(scope_file.read_text())
    if (scope["header_inventory"]["count"] != 0
            or scope["native_files_in_sqlfluff_runtime"]
            or "Root-Is-Purelib: true" not in scope["wheel"]["content"].splitlines()
            or "Tag: py3-none-any" not in scope["wheel"]["content"].splitlines()):
        raise ValueError("SQLFluff public header scope evidence no longer supports exclusion")
    return {
        "format": "judge-generic-header-request-v1", "applicable": False,
        "reason": "Pinned SQLFluff source exposes no C/C++ headers; installed task wheel is pure Python",
        "applicability_evidence": {
            "scope_file": str(scope_file),
            "scope_sha256": hashlib.sha256(scope_file.read_bytes()).hexdigest(),
            "header_inventory": scope["header_inventory"],
            "runtime_native_files": scope["native_files_in_sqlfluff_runtime"],
            "wheel": scope["wheel"],
            "public_evidence": scope["public_evidence"],
            "scope_limit": "Header consumers only; not a sanitizer/race or public-Python-surface waiver",
        },
    }
