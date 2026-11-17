"""Preserve original test outcomes while admitting successful new identities."""
from collections import Counter
import json
import gzip
import re


def pytest_outcomes(report):
    if report["exit_status"] != 0 or not report["collected"] or not report["outcomes"]:
        raise ValueError("Missing or failing upstream test inventory")
    return {"collected": report["collected"], "exit_status": report["exit_status"],
            "outcomes": report["outcomes"]}


def terser_outcomes(stdout):
    result = {}
    for key, pattern in {
        "compression_passed": r"Passed (\d+) test cases\.",
        "mocha_passed": r"^\s+(\d+) passing\b",
        "mocha_pending": r"^\s+(\d+) pending\b",
    }.items():
        matches = re.findall(pattern, stdout, re.M)
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous Terser upstream inventory")
        result[key] = int(matches[0])
    if not result["compression_passed"] or not result["mocha_passed"]:
        raise ValueError("Empty upstream inventory")
    tests = [json.loads(row) for row in re.findall(r"JUDGE_TEST (\{[^\n]+\})", stdout)]
    if (not tests or len(tests) != sum(result.values()) or
            len({json.dumps([row["target"], row["name"]]) for row in tests}) != len(tests)):
        raise ValueError("Missing or duplicate Terser test identity ledger")
    return {"tests": tests, "summaries": result}


def compare(baseline, candidate, task):
    required = {"upstream"}
    if task == "black-project-format":
        required.add("pure_incompatible_with_mypyc")
    if task == "sqlfluff-project-lint":
        required.add("example_plugin")
    if task == "babel-project-compile":
        required |= {"polyfills", "preset_modules", "archived_historical120", "archived_target117"}
    if task == "ruff-project-lint":
        required.add("all_features")
    if not required <= baseline.keys() or not required <= candidate.keys():
        raise ValueError("Required upstream gates missing: " + ", ".join(sorted(required)))
    for name in required:
        a, b = baseline[name], candidate[name]
        if not a.get("passed") or not b.get("passed") or not a.get("outcomes"):
            raise ValueError("Upstream suite did not pass with a nonempty inventory: " + name)
        left, right = a["outcomes"], b.get("outcomes")
        if task == "sqlglot-mozilla-transpile" and name == "upstream":
            left, right = unittest_contract(left), unittest_contract(right)
        compare_ledgers(left, right, name)
    return True


def identity_ledger(value):
    """Project supported report formats without comparing mutable suite totals."""
    rows = []
    def add(identity, status):
        rows.append((json.dumps(identity, sort_keys=True, ensure_ascii=False),
                     json.dumps(status, sort_keys=True, ensure_ascii=False)))
    if not isinstance(value, dict):
        if not isinstance(value, list):
            raise ValueError("Missing upstream identity ledger")
        for row in value:
            if "tests" in row:
                for test in row["tests"]:
                    add([row["file"], test["name"]], test["status"])
            else:
                add([row.get("file"), row.get("identity", row.get("name"))], row["status"])
    elif "outcomes" in value:
        nested = identity_ledger(value["outcomes"])
        rows.extend(nested.elements())
        for row in value.get("subtests", []):
            identity = re.sub(r"(<[\w.<>]+ object at )0x[0-9a-fA-F]+(>)",
                              r"\1<address>\2", row["identity"])
            add(["subtest", identity], row["status"])
    elif "tests" in value:
        for row in value["tests"]:
            add([row.get("target"), row.get("identity", row.get("name"))], row["status"])
    else:
        for identity, status in value.items():
            if not isinstance(status, (str, dict, list)):
                raise ValueError("Upstream counts are not a test identity ledger")
            add(identity, status)
    if not rows:
        raise ValueError("Missing upstream identity ledger")
    return Counter(rows)


def successful_added_status(encoded):
    status = json.loads(encoded)
    allowed = {"passed", "pass", "ok", "skipped", "skip", "ignored",
               "xfail", "xfailed", "expected_failure", "pending", "todo"}
    if isinstance(status, str):
        return status.lower() in allowed
    if isinstance(status, dict):
        if set(status) == {"skip"} and isinstance(status["skip"], str):
            return True
        return bool(status) and all(
            isinstance(v, str) if k.endswith("_wasxfail") else successful_added_status(json.dumps(v))
            for k, v in status.items())
    if isinstance(status, list):
        return bool(status) and all(successful_added_status(json.dumps(v)) for v in status)
    return False


def compare_ledgers(baseline, candidate, suite="upstream"):
    left, right = identity_ledger(baseline), identity_ledger(candidate)
    originals = {identity for identity, status in left}
    if any(right[row] != count for row, count in left.items()) or any(
            identity in originals and row not in left
            for row in right for identity, status in [row]):
        raise ValueError("Upstream test identities/outcomes changed: " + suite)
    for (identity, status), count in right.items():
        if identity not in originals and not successful_added_status(status):
            raise ValueError("Added upstream test failed or errored: " + suite + ": " + identity)
    return True


def unittest_contract(value):
    """Only strip process-local pointers in Python's default object repr.

    Keep raw observations/order in receipts. Compare an identity/outcome
    multiset (some upstream tests shuffle fixtures), retaining multiplicity
    and leaving literal hexadecimal SQL/parameter values untouched.
    """
    if not isinstance(value, dict):
        return value
    result = dict(value)
    result["subtests"] = sorted([
        {**row, "identity": re.sub(r"(<[\w.<>]+ object at )0x[0-9a-fA-F]+(>)",
                                  r"\1<address>\2", row["identity"])}
        for row in value.get("subtests", [])], key=lambda row: (row["identity"], row["status"]))
    return result


def validate_receipt(path, task, image, source_digest, build_digest, allow_rejected=False):
    raw = path.read_bytes()
    value = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)
    if (value.get("task") != task or value.get("image") != image or
            value.get("source_sha256") != source_digest or value.get("build_sha256") != build_digest):
        raise ValueError("Upstream receipt is not bound to this task/image/source/build")
    if allow_rejected and value.get("rejected"):
        # Score-through: the binding is still checked; the recorded rejection is a flag.
        return value
    compare(value["baseline"], value["candidate"], task)
    return value
