"""Verifier-owned v1.1 contract primitives, not an opt-in performance score.

This module is intentionally independent of interface declaration spelling,
language engines and memory caps. Trusted controllers supply source-bound
inventories; adapters cannot grant themselves coverage or remove public APIs.
"""
import hashlib
import json
import math
import re

VERDICT_ORDER = ("contract_regression", "sanitizer_error", "nondeterministic",
                 "resource_exceeded", "undertested")
IDENTITY_FIELDS = ("task", "candidate_source_sha256", "baseline_build_sha256",
                   "candidate_build_sha256", "adapter_sha256", "corpus_sha256",
                   "surface_authority_sha256")
REQUIRED_OBLIGATIONS = ("surface", "grammar_corpus", "property", "coverage_guided",
                        "faults_state", "cpu_determinism", "sanitizers",
                        "changed_coverage", "mutations", "reproducibility",
                        "header_consumers")


class ContractError(ValueError):
    """Invalid verifier input/evidence; never reinterpret as a candidate pass."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ContractError(message)


def case_digest(case):
    """Optional root self-hash is metadata, never part of its own commitment."""
    value = {key: item for key, item in case.items() if key != "case_sha256"}
    actual = digest(value)
    if "case_sha256" in case:
        require(case["case_sha256"] == actual, "Case self-hash mismatch")
    return actual


def validate_identity(observed, expected):
    require(isinstance(observed, dict) and isinstance(expected, dict), "Malformed evidence identity")
    for field in IDENTITY_FIELDS:
        require(field in expected and observed.get(field) == expected[field],
                "Missing or mismatched evidence identity: " + field)
        if field.endswith("_sha256"):
            require(isinstance(expected[field], str) and
                    re.fullmatch("[0-9a-f]{64}", expected[field]) is not None,
                    "Malformed SHA-256: " + field)


def public_case(case, surface, precondition):
    """Reject private/invalid states BEFORE invoking either library or oracle."""
    require(isinstance(case, dict) and isinstance(surface, dict), "Invalid case/surface")
    if surface.get("visibility") != "public":
        return {"valid": False, "reason": "private or unresolved surface"}
    for name in ("surface_id", "contract_id", "precondition_id"):
        key = "id" if name == "surface_id" else name
        require(isinstance(surface.get(key), str) and bool(surface[key]), "Missing surface " + key)
        require(case.get(name) == surface[key], "Case/surface identity mismatch: " + name)
    require(isinstance(case.get("task"), str) and bool(case["task"]), "Missing case task")
    case_digest(case)
    result = precondition(case, surface)
    require(isinstance(result, dict) and type(result.get("valid")) is bool
            and isinstance(result.get("reason"), str), "Malformed precondition result")
    return result


def validate_error_fields(outcome, required):
    """A missing error class/message cannot become equal-to-another-missing field."""
    require(isinstance(required, dict) and bool(required),
            "Error observation needs a trusted per-surface required_error_fields contract")
    checks = {
        "string": lambda value: type(value) is str,
        "nonempty_string": lambda value: type(value) is str and bool(value),
        "integer": lambda value: type(value) is int,
        "boolean": lambda value: type(value) is bool,
        "object": lambda value: type(value) is dict,
        "array": lambda value: type(value) is list,
        "null": lambda value: value is None,
    }
    for field, specification in required.items():
        kinds = specification if isinstance(specification, list) else [specification]
        require(isinstance(field, str) and bool(field) and bool(kinds)
                and all(isinstance(kind, str) and kind in checks for kind in kinds),
                "Malformed error-field schema")
        require(field in outcome and any(checks[kind](outcome[field]) for kind in kinds),
                "Missing or malformed contractual error field: " + field)


def validate_observation(value, case, build_sha256, required_error_fields=None):
    require(isinstance(build_sha256, str) and re.fullmatch("[0-9a-f]{64}", build_sha256) is not None,
            "Malformed observed build SHA-256")
    require(isinstance(value, dict) and value.get("format") == "optimbench-fuzz-observation-v1",
            "Malformed observation envelope")
    identity = value.get("identity", {})
    require(isinstance(identity, dict), "Malformed observation identity")
    for field, expected in {"task": case["task"], "surface_id": case["surface_id"],
                            "case_sha256": case_digest(case), "build_sha256": build_sha256}.items():
        require(identity.get(field) == expected, "Observation identity mismatch: " + field)
    termination = value.get("termination", {})
    require(isinstance(termination, dict) and "exit_code" in termination, "Missing termination record")
    require(termination.get("kind") in ("return", "exception", "panic", "signal", "timeout"),
            "Unknown termination kind")
    require(termination.get("exit_code") is None or type(termination["exit_code"]) is int,
            "Malformed exit code")
    require(isinstance(value.get("outcome"), dict) and
            value["outcome"].get("kind") in ("ok", "error", "unavailable"), "Missing typed outcome")
    if value["outcome"]["kind"] == "error":
        validate_error_fields(value["outcome"], required_error_fields)
    require(isinstance(value.get("effects"), dict), "Missing explicit effects")
    require(isinstance(value.get("diagnostics", {}), dict), "Malformed diagnostics")
    return value


def typed_equal(left, right):
    """JSON/Python scalar categories must not inherit bool/int coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        # Transport JSON keys are strings. Retain type distinctions even when
        # this helper is called directly with Python-valued fixture mappings.
        if {(type(key), key) for key in left} != {(type(key), key) for key in right}:
            return False
        return all(typed_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return left.hex() == right.hex()  # Also preserve signed zero.
    return left == right


def exact_contract_compare(left, right, case):
    """No blanket exception-message/partial-byte normalization.

    Diagnostic metadata and arm identity are out-of-band. Task-specific numeric
    or unspecified-buffer policies require an explicit, reviewed comparator.
    """
    differences = [field for field in ("termination", "outcome", "effects")
                   if not typed_equal(left.get(field), right.get(field))]
    return {"equal": not differences, "policy_id": "exact-contract-v1",
            "differences": differences}


def compare(compare_hook, left, right, case):
    result = compare_hook(left, right, case)
    require(isinstance(result, dict) and type(result.get("equal")) is bool
            and isinstance(result.get("policy_id"), str) and bool(result["policy_id"])
            and isinstance(result.get("differences"), list), "Malformed oracle result")
    require(result["equal"] == (len(result["differences"]) == 0), "Inconsistent oracle result")
    return result


def evaluate_case(case, surface, precondition, observe, builds,
                  compare_hook=exact_contract_compare, repetitions=2):
    """Replay one admitted case in fresh workers via observe(case, arm, index).

    The callback owns process/container isolation. This primitive neither
    claims that five-repeat/CPU-matrix obligations are complete nor runs timing.
    """
    admitted = public_case(case, surface, precondition)
    if not admitted["valid"]:
        return {"status": "out_of_contract", "admitted": False, "candidate_verdict": None,
                "reason": admitted["reason"], "case_sha256": case_digest(case)}
    require(type(repetitions) is int and repetitions >= 2, "At least two failure replays required")
    oracle = surface.get("oracle", {})
    observations = {arm: [] for arm in ("baseline", "candidate")}
    for index in range(repetitions):
        for arm in observations:
            require(arm in builds, "Missing build identity")
            observations[arm].append(validate_observation(
                observe(case, arm, index), case, builds[arm], oracle.get("required_error_fields")))
    allowed_terminations = oracle.get("baseline_terminations", ["return", "exception"])
    allowed_exits = oracle.get("baseline_exit_codes", [0])
    for value in observations["baseline"]:
        require(value["termination"]["kind"] in allowed_terminations
                and value["termination"]["exit_code"] in allowed_exits,
                "Unexpected pristine termination; invalid harness or unqualified public case")
        require(value["outcome"]["kind"] != "unavailable", "Pristine outcome unavailable")
    baseline = observations["baseline"]
    require(all(compare(compare_hook, baseline[0], item, case)["equal"] for item in baseline[1:]),
            "Pristine contractual observations are nondeterministic")
    candidate = observations["candidate"]
    findings = []
    if any(value["outcome"]["kind"] == "unavailable" for value in candidate):
        return {"status": "observed", "admitted": True, "case_sha256": case_digest(case),
                "findings": [{"kind": "undertested", "reason": "Candidate outcome unavailable"}],
                "observations": observations, "comparisons": []}
    if not all(compare(compare_hook, candidate[0], item, case)["equal"] for item in candidate[1:]):
        findings.append({"kind": "nondeterministic", "reproduced": True})
    comparisons = [compare(compare_hook, a, b, case) for a, b in zip(baseline, candidate)]
    if all(not item["equal"] for item in comparisons):
        findings.append({"kind": "contract_regression", "reproduced": True})
    elif any(not item["equal"] for item in comparisons):
        findings.append({"kind": "undertested", "reason": "Unstable mismatch needs additional replay"})
    return {"status": "observed", "admitted": True, "case_sha256": case_digest(case),
            "findings": findings, "observations": observations, "comparisons": comparisons}


def ratio_report(pairs):
    """Purely informational peaks. Missing/high memory never creates a finding."""
    rows = []
    for pair in pairs:
        a, b = pair.get("baseline"), pair.get("candidate")
        valid = all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in (a, b))
        rows.append({**pair, "candidate_baseline_ratio": b / a if valid else None,
                     "available": valid, "verdict_impact": False})
    return {"mode": "report-only", "verdict_impact": False, "pairs": rows}


def assess_surface(manifest, authority):
    """Authority is emitted by the corrected extractor, not self-declared by an adapter."""
    if authority.get("complete") is not True or authority.get("transitive_reexports") is not True:
        return {"complete": False, "reason": "Corrected complete transitive surface authority unavailable"}
    expected = set(authority.get("public_surface_ids", []))
    require(bool(expected) and all(isinstance(name, str) and name for name in expected),
            "Empty/malformed public surface authority")
    entries = manifest.get("surfaces", [])
    require(isinstance(entries, list), "Malformed surface map")
    ids = [entry.get("id") for entry in entries]
    require(all(isinstance(name, str) for name in ids) and len(ids) == len(set(ids)),
            "Missing/duplicate public surface IDs")
    require(not (set(ids) - expected), "Adapter maps a private/unresolved surface as public")
    incomplete = []
    for entry in entries:
        if entry.get("visibility") != "public" or any(not entry.get(key) for key in
                ("contract_id", "precondition_id", "generators", "oracle_policy")):
            incomplete.append(entry["id"])
    missing = sorted(expected - set(ids))
    return {"complete": not missing and not incomplete, "missing": missing,
            "unmapped": incomplete, "expected": len(expected), "mapped": len(ids)}


def assess_coverage(receipt, expected_identity, targets, thresholds=None):
    """Intersect observed IDs with trusted changed-code targets; never trust counts."""
    validate_identity(receipt.get("identity", {}), expected_identity)
    limits = thresholds or {"lines": .95, "branches": .90, "critical_branches": 1.0}
    require(all(type(limits.get(name)) in (float, int) and 0 < limits[name] <= 1
                for name in ("lines", "branches", "critical_branches")), "Invalid coverage thresholds")
    require(receipt.get("targets_sha256") == digest(targets), "Coverage inventory identity mismatch")
    missing = receipt.get("unmapped_changes", [])
    require(isinstance(missing, list), "Malformed unmapped-change inventory")
    rows = {}
    for name in ("lines", "branches", "critical_branches"):
        required = targets.get(name)
        observed = receipt.get("covered_" + name)
        require(isinstance(required, list) and all(isinstance(item, str) for item in required)
                and len(required) == len(set(required)),
                "Malformed coverage targets: " + name)
        require(isinstance(observed, list) and all(isinstance(item, str) for item in observed),
                "Missing coverage observations: " + name)
        denominator, numerator = len(required), len(set(required) & set(observed))
        zero_valid = bool(targets.get("zero_denominator_reason")) if not denominator else True
        rows[name] = {"covered": numerator, "total": denominator,
                      "fraction": numerator / denominator if denominator else None,
                      "passed": zero_valid and (not denominator or numerator / denominator >= limits[name])}
    return {"complete": not missing and all(row["passed"] for row in rows.values()),
            "metrics": rows, "unmapped_changes": missing}


def assess_mutations(receipt, expected_identity, required_controls, minimum=20, kill_rate=None):
    validate_identity(receipt.get("identity", {}), expected_identity)
    if kill_rate is None:
        kill_rate = .90 if expected_identity["task"] in (
            "serde-json-production-crate-index-ingestion", "xz-lzma2-compress") else .85
    require(type(minimum) is int and minimum >= 20 and 0 < kill_rate <= 1,
            "Invalid mutation qualification thresholds")
    mutants = receipt.get("mutants", [])
    require(isinstance(mutants, list), "Malformed mutation receipt")
    ids = [m.get("id") for m in mutants]
    require(all(isinstance(name, str) and name for name in ids) and len(ids) == len(set(ids)),
            "Missing/duplicate mutant identity")
    valid, killed, invalid, equivalent = [], [], [], []
    for mutant in mutants:
        require(type(mutant.get("compiled")) is bool, "Missing compiled-mutant evidence")
        require(type(mutant.get("killed", False)) is bool and
                type(mutant.get("equivalent", False)) is bool, "Malformed mutant outcome")
        if not mutant.get("compiled"):
            invalid.append(mutant["id"])
            continue
        if mutant.get("equivalent"):
            require(bool(mutant.get("equivalence_review")), "Unreviewed equivalent-mutant exclusion")
            equivalent.append(mutant["id"])
            continue
        valid.append(mutant["id"])
        if mutant.get("killed"):
            require(isinstance(mutant.get("reproducer_sha256"), str) and
                    re.fullmatch("[0-9a-f]{64}", mutant["reproducer_sha256"]) is not None
                    and type(mutant.get("fresh_replays")) is int and mutant["fresh_replays"] >= 2,
                    "Unreproduced mutant kill")
            killed.append(mutant["id"])
    controls = receipt.get("controls", {})
    require(isinstance(controls, dict), "Malformed mandatory controls")
    for name in required_controls:
        value = controls.get(name, {})
        require(isinstance(value, dict) and type(value.get("fresh_replays", 0)) is int,
                "Malformed mandatory control: " + name)
    missing_controls = sorted(name for name in required_controls if
                              controls.get(name, {}).get("passed") is not True or
                              not re.fullmatch("[0-9a-f]{64}",
                                               str(controls.get(name, {}).get("reproducer_sha256", ""))) or
                              controls.get(name, {}).get("fresh_replays", 0) < 2)
    rate = len(killed) / len(valid) if valid else None
    return {"complete": len(valid) >= minimum and rate >= kill_rate and not missing_controls,
            "compiled_non_equivalent": len(valid), "killed": len(killed), "rate": rate,
            "invalid": invalid, "equivalent": equivalent,
            "survived": sorted(set(valid) - set(killed)), "missing_controls": missing_controls}


def decide(findings, obligations, memory_pairs=(), approved_nonmemory_resources=()):
    """Missing obligations block admission; memory reporting never participates."""
    require(isinstance(obligations, dict) and bool(obligations), "Empty qualification obligations")
    require(not (set(obligations) - set(REQUIRED_OBLIGATIONS)), "Unknown qualification obligation")
    values = list(findings)
    for value in values:
        require(value.get("kind") in VERDICT_ORDER, "Unknown candidate finding")
        if value["kind"] != "undertested":
            require(value.get("reproduced") is True, "Unreproduced candidate finding")
        if value["kind"] == "resource_exceeded":
            name = value.get("resource", "")
            require(isinstance(name, str) and name in approved_nonmemory_resources
                    and "memory" not in name.lower()
                    and "rss" not in name.lower(), "Unapproved resource limit; memory is report-only")
    missing = [name for name in REQUIRED_OBLIGATIONS if obligations.get(name) is not True]
    if missing:
        values.append({"kind": "undertested", "missing": missing})
    status = next((kind for kind in VERDICT_ORDER if any(v["kind"] == kind for v in values)), "passed")
    return {"format": "optimbench-fuzz-decision-v1", "status": status, "passed": status == "passed",
            "claimable": False, "timing_performed": False, "findings": values,
            "missing_obligations": missing, "peak_memory": ratio_report(memory_pairs)}
