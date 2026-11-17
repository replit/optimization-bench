"""Bounded existing public CLI fixtures; shared observer/comparator/verdict only."""
import hashlib
import json
from pathlib import Path

from .rc_cases import digest

HERE = Path(__file__).resolve().parent
TASK = "sqlfmt-cli"
SURFACE = "sqlfmt.public-cli"
CONTRACT = "sqlfmt.public-cli.exact-output-and-file-effects"
PRECONDITION = "sqlfmt.existing-public-cli-fixtures"
SMALL = b"select a,b from t where a=1;\n"
LARGE_PAYLOAD_SHA = "f783e5a7c220b5cd5415914dfe6a511d6b9ac1326083a6751afa334f147acb6e"
LARGE_SQL_SHA = "f9479657f8d6c21c59f26fe60670fe5388dd24e0c437335985e73c1893f3662f"
LARGE_MEMBER = "regress/core/relate.sql"

def _case(name):
    small = name == "validated-diff-write"
    value = {"task": TASK, "surface_id": SURFACE, "contract_id": CONTRACT,
             "precondition_id": PRECONDITION,
             "input": {"fixture": name, "sql_sha256": hashlib.sha256(SMALL).hexdigest() if small else LARGE_SQL_SHA},
             "options": {"argv": ["-d", "-w", "project/input.sql"] if small else [], "transport": "file" if small else "stdin"},
             "sequence": ["fresh-cli-invocation"],
             "faults": ["healthy", "dev_full", "closed_reader", "closed_stdout", "readonly_dir"] if small else
                       ["healthy", "dev_full", "closed_reader", "closed_stdout"]}
    value["case_sha256"] = digest(value)
    return value

def precondition(case, surface):
    name = case.get("input", {}).get("fixture")
    valid = name in ("validated-diff-write", "existing-public-stdin") and case == _case(name)
    return {"valid": valid, "reason": "Existing public README CLI flags and content-bound approved fixtures" if valid else
            "Not an approved public CLI fixture, argv or content commitment"}

def _surface():
    return {"id": SURFACE, "visibility": "public", "contract_id": CONTRACT, "precondition_id": PRECONDITION,
            "contract_sources": ["upstream 8c676b5 README.md CLI -d/-w and stdin examples",
                                 "task.json exact CLI outputs and file effects",
                                 "Validation 2 existing unformatted -d -w regression fixture",
                                 "environment/workload/inputs/manifest.json postgis-extension-006"],
            "oracle": {"required_error_fields": {"class": "nonempty_string"}}}

def requests(context):
    fixture = HERE / "cli-fixtures/rewrite"
    if (fixture / "project/input.sql").read_bytes() != SMALL:
        raise ValueError("Existing validated rewrite fixture commitment changed")
    task = Path(context["task"])
    public = Path(context.get("public_inputs") or task / "environment/workload/inputs")
    manifest = json.loads((public / "manifest.json").read_text())
    docs = [doc for batches in manifest["splits"].values() for batch in batches for doc in batch["docs"]]
    selected = [doc for doc in docs if doc["sha256"] == LARGE_PAYLOAD_SHA]
    if len(selected) != 1:
        raise ValueError("Existing public large-stdout fixture is unavailable in this definition")
    doc = selected[0]
    if doc["argv"] != ["sqlfmt"] or doc["input"] != "stdin" or doc["outputs"] != "-" or doc["expect_len"] < 65536:
        raise ValueError("Existing public CLI invocation contract changed")
    raw = (public / "files" / LARGE_PAYLOAD_SHA).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LARGE_PAYLOAD_SHA:
        raise ValueError("Existing public CLI payload commitment mismatch")
    header, argv, transport, outputs, sql = raw.split(b"\n", 4)
    if (header, argv, transport, outputs) != (b"CLI1", b"sqlfmt", b"stdin", b"-") or hashlib.sha256(sql).hexdigest() != LARGE_SQL_SHA:
        raise ValueError("Existing public SQL/transport identity mismatch")
    prepared = Path(context["out"]) / "sqlfmt-cli-existing-public-stdin"
    prepared.mkdir(parents=True, exist_ok=True)
    target = prepared / "input.sql"
    if target.exists() and target.read_bytes() != sql:
        raise ValueError("Verifier-owned prepared CLI fixture was modified")
    target.write_bytes(sql)
    cases = []
    for name, inputs, payload in [("validated-diff-write", fixture, "small"),
                                  ("existing-public-stdin", prepared, "large")]:
        case = _case(name)
        small = payload == "small"
        cases.append({"case": case, "surface": _surface(), "inputs": str(inputs.resolve()), "payload_class": payload,
            "step": {"argv": ["{build}/sqlfmt", *case["options"]["argv"]], "cwd": ".", "timeout": 60,
                     "stdin_file": None if small else "input.sql"},
            "watch_inputs": ["project"] if small else ["input.sql"], "watch_outputs": ["."],
            "output_dirs": ["project"] if small else [], "required_outputs": ["project/input.sql"] if small else [],
            "faults": case["faults"]})
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases}
