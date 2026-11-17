"""Fail-closed admission of pristine controls; does not change judge statistics."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
# Off-VM fallbacks: patch_judge_kit fills DECLARED_UNITS from tests/judge.json
# when it builds the kit; RECORD is the VM-written validation of one control.
DECLARED_UNITS = {}
RECORD = "control-validation.json"


def _interval(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in value)
            and value[0] <= value[1])


_AA_EQUIVALENCE_BAND = (0.975, 1.025)


def _aa_centred(aa, family_size):
    """Mirrors judge.score_result.aa_centred (kept self-contained for the standalone kit)."""
    interval = _aa_bias_interval(aa, family_size)
    if interval is not None and interval[0] <= 1 <= interval[1]:
        return True
    ci = aa.get("ci95")
    return _interval(ci) and _AA_EQUIVALENCE_BAND[0] <= ci[0] and ci[1] <= _AA_EQUIVALENCE_BAND[1]


def _aa_bias_interval(aa, family_size):
    """Interval for the per-unit A/A bias decision; mirrors judge.score_result.aa_bias_interval.

    Kept self-contained because tools.patch_judge_kit copies this module's
    functions into the standalone kit. Bonferroni over the task's units at
    family-wise alpha 0.05; a missing or inconsistent record falls back to the
    stricter 95% CI. Tests bind this function to the judge's.
    """
    test, ci = aa.get("bias_test"), aa.get("ci95")
    alpha = .05 / family_size
    if (isinstance(test, dict) and test.get("method") == "bonferroni"
            and test.get("family_size") == family_size
            and type(test.get("per_unit_alpha")) is float
            and math.isclose(test["per_unit_alpha"], alpha, rel_tol=1e-12)
            and _interval(test.get("interval")) and _interval(ci)
            and test["interval"][0] <= ci[0] and ci[1] <= test["interval"][1]):
        return list(test["interval"])
    return list(ci) if _interval(ci) else None


def control_errors(result, measurement, expected_units):
    """Require actual scheduler evidence, not just the reduced 'inconclusive' label."""
    errors = []
    units = set(expected_units)
    if not units or not all(isinstance(n, str) and n for n in units):
        return ["Expected workload inventory is empty or malformed"]
    if not isinstance(result, dict) or not isinstance(measurement, dict):
        return ["Result and measurements must be JSON objects"]
    if result.get("correct") is not True or result.get("status") != "inconclusive":
        errors.append("Pristine control must be correct and inconclusive")
    if result.get("public_only") is not False:
        errors.append("Held-out scoring scope is required")
    # judge.score's CLI accepts v1.1-rc, but its result records PROFILE.
    if result.get("gate_profile") != "judge-v1.1-rc-generic":
        errors.append("Current gate profile is required; legacy is not qualification")
    for name in ("generic_gates", "cpu_correctness_gate"):
        if not isinstance(result.get(name), dict) or result[name].get("passed") is not True:
            errors.append("Required gate did not pass: " + name)
    # The judge's pristine standard is "correct and inconclusive": the aggregate
    # stays inside the A/A envelope (aggregate_within_aa_noise). A 95% interval
    # need not contain 1; a real offset beyond the envelope is reported as a
    # speedup or slowdown and fails the status check above.
    if not _interval(result.get("candidate_ci95")):
        errors.append("Pristine aggregate interval must be finite and ordered")
    codes = result.get("reason_codes")
    allowed = {"aggregate_within_aa_noise", "candidate_within_aa_noise", "paired_precision_unmet"}
    if not isinstance(codes, list) or any(not isinstance(c, str) or c not in allowed for c in codes):
        errors.append("Unreliable or unknown control reason codes")
    if measurement.get("correct") is not True:
        errors.append("Raw measurement correctness did not pass")
    quality = measurement.get("quality")
    if not isinstance(quality, dict) or quality.get("noisy") is not False:
        errors.append("Missing or noisy raw host-quality evidence")
    precision = result.get("aa_precision")
    if not isinstance(precision, dict) or precision.get("passed") is not True:
        errors.append("A/A precision did not pass")
        precision = {}
    inventories = {
        "result": result.get("cases"), "measurements": measurement.get("cases"),
        "A/A precision": precision.get("units"), "A/A intervals": result.get("aa_noise_floor"),
    }
    for name, cases in inventories.items():
        if not isinstance(cases, dict) or set(cases) != units:
            errors.append(name + ": expected every workload exactly once")
    if any(not isinstance(cases, dict) or set(cases) != units for cases in inventories.values()):
        return errors
    for name in sorted(units):
        arms = measurement["cases"][name]
        aa = arms.get("aa") if isinstance(arms, dict) else None
        ab = arms.get("ab") if isinstance(arms, dict) else None
        if not isinstance(aa, dict) or not isinstance(ab, dict):
            errors.append(name + ": missing raw A/A or A/B")
            continue
        aa_ci = aa.get("ci95")
        # Same decision as the judge: family-wise (Bonferroni over this task's
        # units) interval contains 1, or the 95% CI lies inside the ±2.5% band.
        bias_ci = _aa_bias_interval(aa, len(units))
        if (aa.get("status") != "resolved" or not _interval(aa_ci)
                or not _aa_centred(aa, len(units))):
            errors.append(name + ": A/A unresolved, biased or malformed")
            continue
        width = 100 * (aa_ci[1] - aa_ci[0])
        if width > 5.0 + 1e-9:
            errors.append(name + ": A/A full width exceeds 5 pp")
        for arm in (aa, ab):
            if type(arm.get("sample_count")) is not int or arm["sample_count"] < 5:
                errors.append(name + ": missing minimum paired observations")
        unit = result["cases"][name]
        floor = result["aa_noise_floor"][name]
        summary = precision["units"][name]
        if not all(isinstance(v, dict) for v in (unit, floor, summary)):
            errors.append(name + ": malformed reduced statistics")
            continue
        if (not _interval(ab.get("ci95")) or unit.get("candidate_ci95") != ab["ci95"]
                or unit.get("pairs") != ab.get("sample_count")
                or floor.get("ci95") != aa_ci or floor.get("pairs") != aa.get("sample_count")
                # A recorded bias test must agree with the raw one; legacy
                # results without either were decided by the 95% CI above.
                or ("bias_test" in floor or "bias_test" in aa) and (
                    not isinstance(floor.get("bias_test"), dict)
                    or floor["bias_test"].get("interval") != bias_ci
                    or floor["bias_test"].get("biased") is not False)):
            errors.append(name + ": raw/reduced statistics disagree")
        reported = summary.get("full_width_pp")
        if (type(reported) not in (int, float) or not math.isfinite(reported)
                or not math.isclose(reported, width, rel_tol=0, abs_tol=1e-9)
                or summary.get("above_bar") is not False):
            errors.append(name + ": A/A precision summary disagrees with raw interval")
    return errors


def declared_units(task, root=ROOT):
    """Held-out units declared in tests/judge.json performance_units, or None if undeclared."""
    if not isinstance(task, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", task):
        raise ValueError("Invalid task id")
    try:
        settings = json.loads((Path(root) / "tasks" / task / "tests/judge.json").read_text())
    except (OSError, ValueError):
        if task in DECLARED_UNITS:
            return list(DECLARED_UNITS[task])
        raise ValueError("Task timed-unit definition unavailable: " + task) from None
    if not isinstance(settings, dict):
        raise ValueError("Malformed task definition: " + task)
    if "performance_units" not in settings:
        return None
    groups = settings["performance_units"].get("heldout") if isinstance(settings["performance_units"], dict) else None
    if (not isinstance(groups, dict) or not groups or not all(
            isinstance(n, str) and n and isinstance(m, list) and m for n, m in groups.items())):
        raise ValueError("Malformed performance_units.heldout: " + task)
    return sorted(groups)


def task_units(task, root=ROOT):
    """Expected held-out performance units from the task's protected definition.

    Declared units come from tests/judge.json. Otherwise every held-out
    performance case is a unit, listed by the protected task.py from the
    authenticated held-out set, which exists only on the scoring VM.
    """
    units = declared_units(task, root)
    if units is not None:
        return units
    directory = Path(root) / "tasks" / task
    sys.path.insert(0, str(Path(root) / "tools/runner"))
    try:
        settings = json.loads((directory / "tests/judge.json").read_text())
        roles = settings.get("case_roles", {}).get("heldout", {})
        spec = importlib.util.spec_from_file_location(
            "control_task_" + task.replace("-", "_"), directory / "environment/workload/task.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ids = [case.id for case in module.cases(directory / "tests/heldout")]
        units = sorted(c for c in ids if roles.get(c, "performance") == "performance")
    except Exception:
        raise ValueError("Implicit held-out units need the protected task and authenticated "
                         "held-out set (scoring VM): " + task) from None
    finally:
        sys.path.remove(str(Path(root) / "tools/runner"))
    if not units or len(set(ids)) != len(ids):
        raise ValueError("No distinct held-out performance units: " + task)
    return units


def _task_policy_sha256(task, root):
    sys.path.insert(0, str(root))
    try:
        from judge.host_registry import task_policy_sha256
        return task_policy_sha256(task)
    finally:
        sys.path.remove(str(root))


def _recorded_units(path, raw, result, task):
    """Units from the VM-written validation, bound to these result bytes, task and policy."""
    try:
        record = json.loads((path.parent / RECORD).read_text())
        links = (result.get("registration_attempt") or result.get("registration") or {}).get("links") or {}
        units = record["units"]
        if (record.get("passed") is not True or record.get("task") != task
                or record.get("result_sha256") != hashlib.sha256(raw).hexdigest()
                or not record.get("task_policy_sha256")
                or record["task_policy_sha256"] != links.get("task_policy_sha256")
                or not isinstance(units, list) or not units or not all(isinstance(u, str) and u for u in units)):
            raise ValueError
        return units, record["task_policy_sha256"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("No VM-recorded validation bound to this control's bytes, task and "
                         "task policy") from None


def validation_record(result_path, task, root=ROOT):
    """Validate one control; units come from the task definition, else the VM record."""
    path = Path(result_path)
    raw = path.read_bytes()
    result = json.loads(raw)
    measurement = json.loads((path.parent / "measurements.json").read_text())
    record = {"task": task, "result_sha256": hashlib.sha256(raw).hexdigest()}
    if not isinstance(result, dict) or result.get("task") != task:
        return {**record, "passed": False, "errors": ["Result task does not match the requested task"]}
    try:
        units = task_units(task, root)
        policy = _task_policy_sha256(task, root) if (Path(root) / "judge").is_dir() else None
        source = ("task definition" if (Path(root) / "tasks" / task / "tests/judge.json").is_file()
                  else "kit inventory")
    except (ValueError, ImportError, OSError):
        units, policy = _recorded_units(path, raw, result, task)
        source = "vm record"
    errors = control_errors(result, measurement, units)
    return {**record, "passed": not errors, "errors": errors, "units": units,
            "task_policy_sha256": policy, "units_source": source}


def validate_files(result_path, task, root=ROOT):
    try:
        return validation_record(result_path, task, root)["errors"]
    except ValueError as error:
        return [str(error)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    try:
        errors = validate_files(args.result, args.task)
    except (ValueError, OSError, KeyError, TypeError) as error:
        errors = [str(error)]
    output = {"passed": not errors, "errors": errors}
    try:
        ci = json.loads(args.result.read_text()).get("candidate_ci95")
    except (ValueError, OSError, AttributeError):
        ci = None
    if _interval(ci):
        # Informational only; containing 1 is not a qualification rule.
        output.update(aggregate_ci95=ci, aggregate_contains_one=ci[0] <= 1 <= ci[1])
    print(json.dumps(output))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
