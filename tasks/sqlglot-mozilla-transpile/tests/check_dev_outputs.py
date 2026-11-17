"""Task-owned development checks, not scored qualification or CPU ownership.

Use the pinned task image. Results go outside the task, with aggregate-only
console output. --private-inputs accepts only newly authored optimizer data.
No original private body is opened and no payload is fetched.
"""
import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time
import warnings
from unittest.mock import patch

TASK = Path(__file__).resolve().parents[1]
WORKLOAD = TASK / "environment/workload"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def witness(runtime, scratch, child, output):
    """Untimed observation of the actual protected child, not a substitute job."""
    sys.path.insert(0, str(runtime))
    import sqlglot
    from sqlglot.optimizer import RULES, optimize
    rule_codes = {rule.__code__: rule.__name__ for rule in RULES}
    calls = Counter()
    def trace(frame, event, arg):
        if event != "call":
            return
        if frame.f_code is optimize.__code__:
            calls["optimize"] += 1
        if frame.f_code is sqlglot.transpile.__code__:
            calls["transpile"] += 1
        if frame.f_code in rule_codes and frame.f_back.f_code is optimize.__code__:
            calls["rule:" + rule_codes[frame.f_code]] += 1
    sys.argv = [str(child), str(runtime), str(scratch)]
    sys.setprofile(trace)
    try:
        runpy.run_path(str(child), run_name="__main__")
    finally:
        sys.setprofile(None)
    modules = {}
    for name, module in sorted(sys.modules.items()):
        if name == "sqlglot" or name.startswith("sqlglot."):
            file = getattr(module, "__file__", None)
            if not file:
                continue
            path = Path(file).resolve()
            if not path.is_relative_to(runtime.resolve()):
                raise AssertionError("Imported package escaped rebuilt runtime")
            modules[path.relative_to(runtime).as_posix()] = digest(path.read_bytes())
    assert modules and "sqlglot/__init__.py" in modules
    write_json(output, {"calls": dict(calls), "loaded_module_sha256": modules,
                        "timing_evidence": False, "ownership_evidence": False})


def contract_checks(runtime):
    sys.path.insert(0, str(runtime))
    import sqlglot
    from sqlglot import exp
    optimizer = load("dev_optimizer", WORKLOAD / "driver/optimizer.py")
    case = {"sql": "SELECT x.a + 1 AS v FROM x JOIN y ON x.id = y.id WHERE y.b > 0",
            "schema": {"x": {"id": "INT", "a": "INT"}, "y": {"id": "INT", "b": "INT"}},
            "dialect": None}
    tree = optimizer.prepare(case)
    saved_tree, saved_schema = copy.deepcopy(tree.dump()), copy.deepcopy(case["schema"])
    result = optimizer.optimize_tree(tree, case)
    assert tree.dump() == saved_tree and case["schema"] == saved_schema
    assert result is not tree
    def semantic(tree):
        encoder = optimizer.Materializer()
        root = encoder.expression(tree)
        return optimizer.encode({"root": root, "expressions": encoder.expressions})
    reference = semantic(result)
    assert semantic(result.copy()) == reference
    reordered = result.copy()
    for node in reordered.walk():
        node.args = dict(reversed(list(node.args.items())))
    assert semantic(reordered) == reference
    for mutation in ("type", "comment", "metadata", "child-order"):
        changed = result.copy()
        if mutation == "type":
            changed.type = exp.DataType.build("TEXT")
        elif mutation == "comment":
            changed.add_comments(["observable contract check"])
        elif mutation == "metadata":
            changed.meta["contract_probe"] = 1
        else:
            join = changed.find(exp.EQ)
            assert join is not None
            left, right = join.this, join.expression
            join.set("this", right)
            join.set("expression", left)
        assert semantic(changed) != reference
    try:
        optimizer.Materializer().value(set())
    except TypeError:
        pass
    else:
        raise AssertionError("Unknown values must fail closed")
    bad = {**case, "sql": "SELECT ("}
    try:
        sqlglot.parse_one(bad["sql"], error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as error:
        expected_error = (type(error).__module__ + "." + type(error).__qualname__, str(error))
    observed = json.loads(optimizer.run_case(bad))
    assert observed["status"] == "error"
    assert (observed["exception"], observed["message"]) == expected_error
    assert json.loads(optimizer.run_case(case))["status"] == "ok"
    logger = logging.getLogger("sqlglot")
    handlers, filters = list(logger.handlers), list(warnings.filters)
    def diagnostic(_):
        logger.warning("contract warning")
        warnings.warn("python contract warning", RuntimeWarning)
        return {}
    with patch.object(optimizer, "operation", diagnostic):
        captured = json.loads(optimizer.run_case(case))
    assert captured["logs"] == [{"level": "WARNING", "message": "contract warning"}]
    assert captured["python_warnings"] == [
        {"category": "builtins.RuntimeWarning", "message": "python contract warning"}]
    assert logger.handlers == handlers and warnings.filters == filters
    representative = load("optimizer_representative", TASK / "tests/differential/optimizer_operation.py")
    representative.run(sqlglot)
    return {"copy_and_schema": True, "full_semantic_fields": True,
            "errors_and_recovery": True, "logs_and_warning_restoration": True}


def run_batch(runner, module, case, build, inputs, output, seed, audit=False):
    output.mkdir(parents=True)
    ctx = runner.Context(output, build, inputs, Path("/app/projects"), WORKLOAD, seed=seed)
    steps = module.prepare(case, ctx)
    assert len(steps) == 1 and not steps[0].resident
    if audit:
        step = steps[0]
        step.argv = [sys.executable, str(Path(__file__)), "--witness",
                     str(build / "runtime"), str(output), step.argv[1], str(output / "witness.json")]
    start = time.perf_counter()
    results = runner.LocalExecutor().run(steps, ctx, module.ENV)
    elapsed = time.perf_counter() - start
    assert all(result.exit_code == 0 and not result.stdout and not result.stderr
               and not result.leftovers for result in results), "Child failed its process/stream contract"
    full = module.collect(case, ctx, results)
    for identity, raw in full.items():
        (output / (identity + ".complete")).write_bytes(raw)
    # Deliberately remove one finished output and use the real collector.
    omitted = case.data["docs"][0]["path"]
    saved = (output / (omitted + ".out")).read_bytes()
    (output / (omitted + ".out")).unlink()
    assert module.collect(case, ctx, results)[omitted] != full[omitted]
    (output / (omitted + ".out")).write_bytes(saved)
    return full, elapsed


def build_controls(baseline, output):
    builds = {"baseline": baseline}
    for label, change in (
            ("wrong", "    optimized.add_comments(['deliberately wrong result'])\n    return optimized"),
            ("slow", "    _control_sum = 0\n"
                     "    for _control_index in range(4_000_000):\n"
                     "        _control_sum = (_control_sum + _control_index) % 65521\n"
                     "    return optimized"),
            ("skip", None)):
        source = output / "control-sources" / label
        shutil.copytree(baseline / "runtime/sqlglot", source / "sqlglot")
        path = source / "sqlglot/optimizer/optimizer.py"
        before = path.read_text()
        if label == "skip":
            after = before.replace("    schema = ensure_schema(schema, dialect=dialect)",
                                   "    return exp.maybe_parse(expression, dialect=dialect, copy=True)\n"
                                   "    schema = ensure_schema(schema, dialect=dialect)", 1)
        else:
            assert before.count("    return optimized") == 1
            after = before.replace("    return optimized", change)
        assert after != before
        path.write_text(after)
        build = output / "builds" / label
        subprocess.run(["sh", str(WORKLOAD / "build.sh"), str(build)], check=True,
                       env={**os.environ, "SQLGLOT_SOURCE": str(source)},
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        builds[label] = build
    return builds


def main(args):
    args.output.mkdir(parents=True)
    args.output.chmod(0o700)
    runner = load("runner", WORKLOAD / "runner.py")
    module = load("workload_task", WORKLOAD / "task.py")
    # Verify the complete immutable baseline before making disposable controls.
    author = load("optimizer_author", TASK / "public/optimizer/prepare.py")
    author.baseline(args.baseline / "runtime")
    checks = contract_checks(args.baseline / "runtime")
    builds = build_controls(args.baseline, args.output)
    public_inputs = WORKLOAD / "inputs"
    public_cases = [case for case in module.cases(public_inputs) if case.id in ("batch", "optimizer")]
    all_inputs = [("public", public_inputs, public_cases)]
    if args.private_inputs is not None:
        docs = runner.documents(args.private_inputs)
        assert docs and all(doc.get("family") == "optimizer" for doc in docs)
        all_inputs.append(("new-private-only", args.private_inputs, [
            runner.Case("optimizer", {"docs": docs, "child": "optimizer_child.py"})]))
    summary = {"scope": "development checks, not qualification",
               "contract": checks, "controls": {}, "durations": {},
               "omitted_output_rejected": True, "source_provenance": {},
               "cpu_ownership": "unmeasured", "aa_noise": "not measured",
               "original_private_inputs": "not opened"}
    for cohort, inputs, cases in all_inputs:
        reference, baseline_seconds = {}, {}
        for case in cases:
            full, seconds = run_batch(runner, module, case, args.baseline, inputs,
                                      args.output / cohort / "baseline" / case.id, 1729)
            assert set(full) == {doc["path"] for doc in case.data["docs"]}
            assert all((len(full[doc["path"]]), digest(full[doc["path"]]))
                       == (doc["expect_len"], doc["expect_sha"]) for doc in case.data["docs"])
            reference[case.id], baseline_seconds[case.id] = full, seconds
        summary["durations"][cohort] = {"baseline_seconds": baseline_seconds,
            "boundary": "Uninstrumented cold child execution plus shared executor supervision, excluding preparation and result comparison."}
        summary["controls"][cohort] = {}
        for label in ("baseline-repeat", "wrong", "skip", "slow"):
            build = args.baseline if label == "baseline-repeat" else builds[label]
            per_family = {}
            for case in cases:
                full, seconds = run_batch(runner, module, case, build, inputs,
                                          args.output / cohort / label / case.id, 2718)
                identical = full == reference[case.id]
                if case.id == "batch" or label in ("baseline-repeat", "slow"):
                    assert identical, "Correctness-preserving control changed complete output"
                else:
                    assert not identical, "Negative optimizer control was not detected"
                per_family[case.id] = {"all_complete_outputs_equal": identical, "seconds": seconds}
                summary["controls"][cohort][label] = per_family
                write_json(args.output / "partial-aggregate.json", summary)
                if label == "slow" and case.id == "optimizer":
                    assert seconds > baseline_seconds[case.id] * 1.25, "Slow control not clearly slower"
            summary["controls"][cohort][label] = per_family
        for label in ("baseline", "wrong", "skip", "slow"):
            build = builds[label]
            evidence = {}
            for case in cases:
                destination = args.output / cohort / (label + "-witness") / case.id
                full, _ = run_batch(runner, module, case, build, inputs, destination, 2718, audit=True)
                normal = args.output / cohort / label / case.id
                # Baseline normal run used a different order, but complete
                # per-input outputs must still be identical.
                assert all(raw == (normal / (identity + ".complete")).read_bytes()
                           for identity, raw in full.items())
                observed = json.loads((destination / "witness.json").read_text())
                for name, expected in observed["loaded_module_sha256"].items():
                    assert digest((build / "runtime" / name).read_bytes()) == expected
                count = len(case.data["docs"])
                if case.id == "batch":
                    assert observed["calls"].get("transpile") == count
                else:
                    assert observed["calls"].get("optimize") == count
                    rule_counts = {key: value for key, value in observed["calls"].items() if key.startswith("rule:")}
                    if label == "skip":
                        assert not rule_counts
                    else:
                        assert len(rule_counts) == 14 and all(value == count for value in rule_counts.values())
                evidence[case.id] = {"verified_loaded_modules": len(observed["loaded_module_sha256"]),
                                    "required_calls": count, "full_results_match_normal_run": True}
            summary["source_provenance"][cohort + ":" + label] = evidence
    write_json(args.output / "aggregate.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--witness":
        witness(*(Path(value) for value in sys.argv[2:]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--baseline", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--private-inputs", type=Path)
        options = parser.parse_args()
        main(options)
