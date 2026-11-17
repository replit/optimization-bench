#!/usr/bin/env python3
"""Replay canonical SQLGlot controls using real isolated one-arm workers."""
import argparse
import importlib.util
import json
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile

from adapter import ROOT, digest, make_case, precondition, compare


def inventory(root):
    import hashlib
    return digest({
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts})


def load_contract(path):
    spec = importlib.util.spec_from_file_location("trusted_fuzzing_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--shared-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract = load_contract(args.shared_contract)
    surface_map = json.loads((ROOT / "surface.json").read_text())
    surfaces = {s["id"]: s for s in surface_map["surfaces"]}
    baseline_hash = inventory(args.runtime)
    counts = {"precondition": 0, "baseline": 0, "candidate": 0, "oracle": 0}

    def counted_precondition(case, surface):
        counts["precondition"] += 1
        return precondition(case, surface)

    def forbidden_observe(*unused):
        raise AssertionError("Private invalid AST must never invoke an arm")

    def forbidden_compare(*unused):
        counts["oracle"] += 1
        raise AssertionError("Private invalid AST must never invoke comparison")

    private = make_case("sqlglot.transpile", "SELECT 1 AS a")
    private["surface_id"] = "sqlglot.parser.Parser._implicit_unnests_to_explicit"
    private["precondition_id"] = "exclude-private-helper-and-missing-join-target"
    private["input"] = {"origin": "manual-ast", "root": "Select", "join_this": None}
    private_surface = {
        **surfaces["sqlglot.transpile"], "id": private["surface_id"],
        "visibility": "private", "precondition_id": private["precondition_id"],
    }
    rejected = contract.evaluate_case(
        private, private_surface, counted_precondition, forbidden_observe,
        {"baseline": baseline_hash, "candidate": baseline_hash},
        compare_hook=forbidden_compare, repetitions=2)
    assert not rejected["admitted"] and rejected["candidate_verdict"] is None
    assert counts == {"precondition": 0, "baseline": 0, "candidate": 0, "oracle": 0}
    results = {
        "sqlglot-private-malformed-join": {
            "passed": True, "result": rejected, "invocations": counts,
            "control": "Private surface rejected by shared controller before task hook or arms"},
    }
    with tempfile.TemporaryDirectory(prefix="sqlglot-public-controls-") as temp:
        scratch = Path(temp)
        for name, symbol, sql, source_change in (
            ("sqlglot-public-valid-ast", "sqlglot.transpile", "SELECT 1 AS a",
             "\n_original_control_transpile = transpile\n"
             "def transpile(*args, **kwargs):\n"
             "    return [value.replace('1', '2') "
             "for value in _original_control_transpile(*args, **kwargs)]\n"),
            ("sqlglot-public-parser-error", "sqlglot.parse_one", "SELECT (",
             "\ndef parse_one(*args, **kwargs):\n"
             "    raise ParseError('Disposable control changes exact error message')\n"),
            ("sqlglot-public-copy-aliasing", "sqlglot.expressions.Expression.copy", "SELECT 1 AS a",
             "\ndef _control_aliasing_copy(self):\n"
             "    return self\n"
             "exp.Expression.copy = _control_aliasing_copy\n"),
        ):
            work = scratch / name
            mutant = work / "runtime"
            shutil.copytree(args.runtime, mutant, ignore=shutil.ignore_patterns("__pycache__"))
            init = mutant / "sqlglot/__init__.py"
            original = init.read_text()
            init.write_text(original + source_change)
            py_compile.compile(str(init), cfile=str(work / "mutant.pyc"), doraise=True)
            build_hash = inventory(mutant)
            assert build_hash != baseline_hash
            case = make_case(symbol, sql)
            case["sequence"] = ["invoke"]
            case_path = work / "case.json"
            case_path.write_text(json.dumps(case))
            worker_receipts = []

            def observe(current, arm, replay_index):
                runtime = args.runtime if arm == "baseline" else mutant
                output = work / f"{arm}-{replay_index}.json"
                command = [
                    sys.executable, str(ROOT / "driver.py"),
                    "--runtime", str(runtime), "--case", str(case_path),
                    "--build-sha256", baseline_hash if arm == "baseline" else build_hash,
                    "--output", str(output),
                ]
                run = subprocess.run(command, capture_output=True, text=True, timeout=20)
                assert run.returncode == 0, run.stderr
                assert not run.stdout and not run.stderr, "Worker import/report side effects"
                observed = json.loads(output.read_text())
                worker_receipts.append({
                    "arm": arm, "fresh_replay": replay_index, "exit_code": run.returncode,
                    "observation": observed,
                })
                return observed

            evaluated = contract.evaluate_case(
                case, surfaces[symbol], precondition, observe,
                {"baseline": baseline_hash, "candidate": build_hash},
                compare_hook=compare, repetitions=2)
            assert evaluated["admitted"]
            assert any(f["kind"] == "contract_regression" and f["reproduced"]
                       for f in evaluated["findings"]), evaluated
            results[name] = {
                "passed": True, "compiled": True, "case": case, "case_sha256": digest(case),
                "baseline_build_sha256": baseline_hash, "mutant_build_sha256": build_hash,
                "disposable_source_change": source_change, "fresh_replays_per_arm": 2,
                "worker_receipts": worker_receipts, "shared_controller_result": evaluated,
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "format": "optimbench-fuzz-v1", "task": "sqlglot-mozilla-transpile",
        "scope": "Three mandatory controls plus public copy-aliasing control; "
                 "not full surface/campaign qualification",
        "adapter_sha256": __import__("hashlib").sha256(
            (ROOT / "adapter.py").read_bytes()).hexdigest(),
        "adapter_hash_scope": "adapter.py; complete adapter tree separately bound by git commit",
        "shared_contract_sha256": __import__("hashlib").sha256(
            args.shared_contract.read_bytes()).hexdigest(),
        "source_pin": "5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76",
        "runtime_python": sys.version, "controls": results,
        "claimable": False, "timing_performed": False,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"controls": {name: row["passed"] for name, row in results.items()}}))


if __name__ == "__main__":
    main()
