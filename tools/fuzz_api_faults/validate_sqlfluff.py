"""Run pristine and generic mutable-cache / safe-frozen-interning controls.

Only disposable copies of verified pristine source are modified. No production
candidate or task environment is edited. Local reference, not a full campaign.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .factories import sequence_request
from .worker import semantic

ROOT = Path(__file__).resolve().parents[2]
BLOCK_APPEND = '''

# Verifier source control: shared mutable factory return, intentionally unsafe.
_verifier_original_get = ReflowConfig.get_block_config
_verifier_factory_cache = {}
def _verifier_cached_get(self, block_class_types, depth_info=None):
    key = (id(self), frozenset(block_class_types))
    if key not in _verifier_factory_cache:
        _verifier_factory_cache[key] = (self, _verifier_original_get(self, block_class_types, depth_info))
    return _verifier_factory_cache[key][1]
ReflowConfig.get_block_config = _verifier_cached_get
'''
MATCH_APPEND = '''

# Verifier must-not-flag control: documented frozen return safely interned.
from functools import lru_cache as _verifier_lru_cache
MatchResult.empty_at = classmethod(_verifier_lru_cache(maxsize=32)(MatchResult.empty_at.__func__))
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_worker(python, runtime, out, cases):
    env = {"PATH": os.defpath, "HOME": str(out), "PYTHONPATH": str(ROOT),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "TZ": "UTC", "LC_ALL": "C.UTF-8"}
    data = "".join(json.dumps(c) + "\n" for c in cases).encode()
    result = subprocess.run([python, "-m", "tools.fuzz_api_faults.worker",
                             "--runtime", str(runtime)], cwd=ROOT, input=data,
                            capture_output=True, env=env, timeout=120)
    out.mkdir()
    (out / "stdout.jsonl").write_bytes(result.stdout)
    (out / "stderr").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError("worker failed, inspect retained exit/stderr before candidate attribution")
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != len(cases) or any(r.get("status") != "observed" for r in rows):
        raise RuntimeError("worker/import/precondition failed")
    if any(step["outcome"]["kind"] != "ok" for row in rows
           for sequence in row["sequence_results"] for step in sequence["observations"]):
        raise RuntimeError("unexpected public error in valid reference schedule")
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out.exists() or out.is_relative_to(source) or source.is_relative_to(out):
        p.error("fresh output outside source required")
    # Bind the actual factory files to the task's pinned archive, not a candidate.
    lock = json.loads((ROOT / "tasks/sqlfluff-project-lint/environment/source.lock.json").read_text())
    target = source / "src/sqlfluff/utils/reflow/config.py"
    match = source / "src/sqlfluff/core/parser/match_result.py"
    pristine_hashes = {str(path.relative_to(source)): digest(path) for path in (target, match)}
    # These exact file pins were derived from the hash-verified archive.
    expected = json.loads((Path(__file__).parent / "sqlfluff-source-pins.json").read_text())
    if pristine_hashes != expected["files"]:
        raise RuntimeError("reference source files differ from pinned pristine")
    out.mkdir(parents=True)
    cases = [sequence_request(), sequence_request(frozen=True)]
    report = {"schema": "optimbench-api-state-reference-v1", "source_lock": lock,
              "source_files": pristine_hashes, "cases": cases, "arms": {},
              "scope": "local fresh-process control validation, not pinned-container campaign",
              "qualification_claimed": False, "memory_cap_enforced": False}
    for name, extra in (("pristine", None), ("shared-mutable", BLOCK_APPEND), ("frozen-interned", MATCH_APPEND)):
        arm = out / name
        arm.mkdir()
        if extra is None:
            runtime = source / "src"
        else:
            clone = arm / "source"
            shutil.copytree(source, clone, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info"))
            path = clone / ("src/sqlfluff/utils/reflow/config.py" if name == "shared-mutable"
                            else "src/sqlfluff/core/parser/match_result.py")
            path.write_text(path.read_text() + extra)
            runtime = clone / "src"
        observations = [run_worker(args.python, runtime, arm / f"replay-{n}", cases) for n in range(2)]
        report["arms"][name] = {
            "observations": [[semantic(row) for row in rows] for rows in observations],
            "raw": [{"path": f"{name}/replay-{n}/stdout.jsonl",
                     "sha256": digest(arm / f"replay-{n}/stdout.jsonl")} for n in range(2)],
            "lifecycle_per_call": all(step["diagnostics"]["lifecycle"][side]["available"]
                                      for rows in observations for row in rows
                                      for sequence in row["sequence_results"]
                                      for step in sequence["observations"] for side in ("before", "after")),
        }
    stable = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    baseline = report["arms"]["pristine"]["observations"]
    if stable(baseline[0]) != stable(baseline[1]):
        raise RuntimeError("pristine reference not repeatable")
    report["shared_mutable_detected"] = all(
        stable(row) != stable(baseline[n])
        for n, row in enumerate(report["arms"]["shared-mutable"]["observations"]))
    report["frozen_interning_must_not_flag"] = all(
        stable(row) == stable(baseline[n])
        for n, row in enumerate(report["arms"]["frozen-interned"]["observations"]))
    if not report["shared_mutable_detected"] or not report["frozen_interning_must_not_flag"]:
        raise RuntimeError("generic control expectation not established")
    report["worker_source_sha256"] = digest(Path(__file__).parent / "worker.py")
    report["factory_source_sha256"] = digest(Path(__file__).parent / "factories.py")
    report["lifecycle_source_sha256"] = digest(Path(__file__).parent / "lifecycle.py")
    (out / "validation.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"shared_mutable_detected": True, "frozen_interning_must_not_flag": True,
                      "receipt_sha256": digest(out / "validation.json")}))


if __name__ == "__main__":
    main()
