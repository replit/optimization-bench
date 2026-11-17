"""Recorded baseline count guards supplement, never replace, exact outcome ledgers."""
from collections import Counter
import json


def summaries(task, suites):
    if task == "babel-project-compile":
        return {name: {**suite["outcomes"]["counts"],
                       "checker_exit_code": suite["outcomes"]["checker_exit_code"]}
                for name, suite in suites.items()}
    value = suites["upstream"]["outcomes"]
    if task == "ruff-project-lint":
        counts = Counter(test["status"] for test in value["tests"])
        return {"upstream": {"passed": counts["ok"], "failed": counts["FAILED"],
                             "ignored": counts["ignored"], "summaries": len(value["summaries"])}}
    if task == "sqlglot-mozilla-transpile":
        counts = Counter(v if isinstance(v, str) else "skipped" for v in value["outcomes"].values())
        return {"upstream": {"passed": counts["passed"], "skipped": counts["skipped"],
                             "failed": counts["failed"] + counts["error"],
                             "collected": value["collected"], "subtests": len(value.get("subtests", []))}}
    if task == "sqlfluff-project-lint":
        counts = Counter("failed" if "failed" in phases.values() else
                         "skipped" if "skipped" in phases.values() else
                         "passed" if phases.get("call") == "passed" else "incomplete"
                         for phases in value["outcomes"].values())
        return {"upstream": {**dict(counts), "collected": value["collected"]}}
    return {}


def profile_baseline(task, task_path, expected, suites):
    """Authenticate exact frozen profile identities, not just passing counts."""
    configuration = json.loads((task_path / "tests/upstream-config.json").read_text())
    names = [profile["id"] for profile in configuration["profiles"]]
    profiles = expected["profiles"]
    if (expected.get("task") != task or len(names) != len(set(names))
            or not names or set(profiles) != set(names)):
        raise ValueError("Frozen upstream profile inventory mismatch")
    identities, counts = {}, {}
    for name, rows in profiles.items():
        if not rows or any(not identity.startswith(name + "::") for identity in rows):
            raise ValueError("Missing or incorrectly namespaced frozen test identities")
        if identities.keys() & rows.keys():
            raise ValueError("Duplicate frozen upstream identities")
        identities.update(rows)
        counts[name] = dict(Counter(rows.values()))
    actual = suites["upstream"]
    if actual.get("passed") is not True or actual.get("outcomes") != identities:
        raise ValueError("Pristine upstream identities/outcomes differ from frozen profiles")
    if any(step["exit_code"] != 0 for step in actual.get("steps", [])):
        raise ValueError("Pristine upstream step did not exit successfully")
    return counts


def require_baseline(task, task_path, suites):
    path = task_path / "tests/upstream-expectations.json"
    if not path.exists():
        return summaries(task, suites)
    expected = json.loads(path.read_text())
    if "suites" not in expected and "profiles" in expected:
        return profile_baseline(task, task_path, expected, suites)
    actual = summaries(task, suites)
    for name, fields in expected["suites"].items():
        for field, value in fields.items():
            if actual[name].get(field, 0) != value:
                raise ValueError(f"Recorded baseline differs: {name}.{field}: {actual[name].get(field, 0)} != {value}")
    if task == "sqlfluff-project-lint":
        skipped = sorted(name for name, phases in suites["upstream"]["outcomes"]["outcomes"].items()
                         if "skipped" in phases.values())
        if skipped != sorted(expected["skipped_nodeids"]):
            raise ValueError("SQLFluff skipped identity set differs from protected expectations")
    return actual
