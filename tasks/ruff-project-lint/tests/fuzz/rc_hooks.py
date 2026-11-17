"""Verifier-owned CLI requests; the shared judge executes and classifies faults."""
import hashlib
import json
from pathlib import Path

TASK = "ruff-project-lint"
ROOT = Path(__file__).resolve().parents[2]
SURFACE = {
    "id": "ruff.check",
    "visibility": "public",
    "contract_id": "ruff-check-diagnostics-v1",
    "precondition_id": "ruff-public-python-f401-v1",
}
OPTIONS = {"select": "F401", "isolated": True, "cache": False, "fix": False, "exit_zero": True}


def _fixture(lines):
    return "".join(f"import os as unused_{index}\n" for index in range(lines)).encode()


def CLI_PRECONDITION(case, surface):
    value = case.get("input", {})
    valid = (
        isinstance(value, dict) and surface == SURFACE and case.get("task") == TASK
        and case.get("surface_id") == SURFACE["id"]
        and all(case.get(key) == SURFACE[key] for key in ("contract_id", "precondition_id"))
        and case.get("options") == OPTIONS and case.get("faults") == []
        and value.get("fixture") == "unused-os-aliases-v1"
        and value.get("lines") in (1, 1024)
        and type(value.get("lines")) is int
        and value.get("sha256") == hashlib.sha256(_fixture(value["lines"])).hexdigest()
    )
    return {"valid": bool(valid), "reason": "Documented check command on authored Python input"}


def CLI_CASES(context):
    from judge.fuzzing_contract import case_digest

    folder = Path(context["out"]) / "ruff-cli-inputs"
    folder.mkdir(parents=True, exist_ok=False)
    cases = []
    for size, lines in (("small", 1), ("large", 1024)):
        tree = folder / size
        tree.mkdir()
        content = _fixture(lines)
        (tree / "sample.py").write_bytes(content)
        case = {
            "task": TASK,
            "surface_id": SURFACE["id"],
            "contract_id": SURFACE["contract_id"],
            "precondition_id": SURFACE["precondition_id"],
            "options": dict(OPTIONS),
            "faults": [],
            "input": {
                "fixture": "unused-os-aliases-v1",
                "lines": lines,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        }
        case["case_sha256"] = case_digest(case)
        cases.append({
            "case": case,
            "surface": dict(SURFACE),
            "payload_class": size,
            "step": {
                "argv": [
                    "{build}/ruff", "check", "--isolated", "--no-cache", "--no-fix",
                    "--output-format=json", "--exit-zero", "--select", "F401", "sample.py",
                ],
                "cwd": ".",
                "env": {"RAYON_NUM_THREADS": "8", "NO_COLOR": "1"},
                "timeout": 60,
            },
            "inputs": str(tree),
            "watch_inputs": ["sample.py"],
        })
    return {
        "format": "judge-generic-cli-cases-v1",
        "applicable": True,
        "cases": cases,
        "readonly_applicability_evidence": {
            "scope": "These check invocations emit diagnostics only to stdout/stderr.",
            "source": "--no-cache --no-fix, without --output-file; input bytes are watched.",
            "limits": "No exemption for file-writing CLI modes or upstream tests.",
        },
    }


def _binary_scope():
    settings = json.loads((ROOT / "tests/judge.json").read_text())
    release = json.loads((ROOT / "release.json").read_text())
    lock = json.loads((ROOT / "environment/source.lock.json").read_text())
    recipe = (ROOT / "environment/workload/build.sh").read_bytes()
    if (settings["public_interface"]["kind"] != "binary"
            or b"build --locked --offline --release -p ruff --bin ruff -j 8" not in recipe
            or release["source_policy"]["allowed_extensions"] != [".rs"]):
        raise ValueError("Ruff public binary scope changed; applicability must be reviewed")
    return {
        "public_surface": "The protected recipe exports the standalone Ruff CLI, not a linkable library.",
        "source_archive_sha256": lock["ruff"]["archive_sha256"],
        "upstream_commit": lock["ruff"]["commit"],
        "build_recipe_sha256": hashlib.sha256(recipe).hexdigest(),
        "artifact": "build/ruff",
        "coverage": "CLI faults execute the native binary; upstream tests retain internal Rust API coverage.",
    }


def stream_faults(context):
    return {
        "format": "judge-generic-call-evidence-v1",
        "applicable": False,
        "reason": "No caller-provided in-process stream callback is exported by the standalone CLI.",
        "applicability_evidence": _binary_scope(),
    }


def repeat_in_process(context):
    return {
        "format": "judge-generic-call-evidence-v1",
        "applicable": False,
        "reason": "Each supported CLI invocation is a fresh OS process, not a persistent library session.",
        "applicability_evidence": _binary_scope(),
    }


def aliasing(context):
    return {
        "format": "judge-generic-call-evidence-v1",
        "applicable": False,
        "reason": "The standalone CLI exposes no caller-owned in-process buffers or borrowed results.",
        "applicability_evidence": _binary_scope(),
    }


def HEADER_CONSUMERS(context):
    return {
        "format": "judge-generic-header-request-v1",
        "applicable": False,
        "reason": "The task builds a Rust command-line executable, not a C/C++ header distribution.",
        "applicability_evidence": _binary_scope(),
    }


GATES = {
    "stream_faults": stream_faults,
    "repeat_in_process": repeat_in_process,
    "aliasing": aliasing,
}
