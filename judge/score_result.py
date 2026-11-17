"""Conservative provisional interpretation: do not attribute effects below A/A noise."""
import math

from .scheduler import AA_BIAS_FAMILY_ALPHA
from .statistics import geometric_mean


def _interval(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in value)
            and value[0] <= value[1])


def aa_bias_interval(aa, family_size):
    """Interval for the per-unit A/A bias decision (shared with tools.judge_control).

    The scheduler records a Bonferroni (1 - 0.05/k) bootstrap interval as
    aa["bias_test"]. It is used only when it matches this task's k units and
    contains the unit's 95% CI; otherwise (legacy or malformed receipts) the
    stricter 95% CI decides, so a bad record can never relax the check.
    """
    test, ci = aa.get("bias_test"), aa.get("ci95")
    alpha = AA_BIAS_FAMILY_ALPHA / family_size
    if (isinstance(test, dict) and test.get("method") == "bonferroni"
            and test.get("family_size") == family_size
            and type(test.get("per_unit_alpha")) is float
            and math.isclose(test["per_unit_alpha"], alpha, rel_tol=1e-12)
            and _interval(test.get("interval")) and _interval(ci)
            and test["interval"][0] <= ci[0] and ci[1] <= test["interval"][1]):
        return list(test["interval"]), {"basis": "bonferroni", "per_unit_alpha": alpha,
                                        "interval_level": 1 - alpha}
    return (list(ci) if _interval(ci) else None), {"basis": "unadjusted 95% CI (no valid bias_test)",
                                                    "per_unit_alpha": .05, "interval_level": .95}


# Practical-equivalence band for an A/A arm (baseline timed against itself). It
# matches the 5 pp full-width precision bar (±2.5%). Offsets this small come
# from layout/placement effects between identical builds, not from the
# measurement; a claimed speedup must still clear the measured A/A envelope.
AA_EQUIVALENCE_BAND = (0.975, 1.025)


def aa_centred(aa, family_size):
    """Whether an A/A arm is centred on 1 for claimability and bias decisions.

    Centred if its family-wise (Bonferroni over the task's units) interval
    contains 1, or if its 95% CI lies entirely inside AA_EQUIVALENCE_BAND.
    Precision (CI width) and the A/A noise envelope are checked separately.
    """
    interval, _ = aa_bias_interval(aa, family_size)
    if interval is not None and interval[0] <= 1 <= interval[1]:
        return True
    ci = aa.get("ci95")
    return _interval(ci) and AA_EQUIVALENCE_BAND[0] <= ci[0] and ci[1] <= AA_EQUIVALENCE_BAND[1]


def interpret(measurement):
    cases, reasons, floors, precision_reasons = {}, [], {}, []
    reason_codes, aa_units, unit_notes = [], {}, []
    scored_through = measurement.get("score_through") and bool(measurement.get("cases"))
    if not measurement.get("correct") and not scored_through:
        return {"status": "incorrect", "correct": False, "speedup": None, "optim_score": 1.0,
                "aa_noise_floor": {}, "reasons": ["Correctness failed"]}
    # The A/A precision bar the scheduler applied (tests/judge.json aa_max_full_width_pp, default 5 pp).
    bar = (measurement.get("aa_precision") or {}).get("maximum_full_width_pp", 5.0)
    if type(bar) not in (int, float) or not math.isfinite(bar) or not 0 < bar <= 10:
        raise ValueError("A/A precision bar must be finite, positive and at most 10 pp")
    for name, arms in measurement["cases"].items():
        aa, ab = arms["aa"], arms["ab"]
        low, high = aa["ci95"]
        # Include observed A/A offset as well as uncertainty, not only CI width.
        log_floor = max(abs(math.log(low)), abs(math.log(high)))
        width_pp = 100 * (high - low)
        aa_units[name] = {"full_width_pp": width_pp, "above_bar": width_pp > bar + 1e-9}
        floors[name] = {"ci95": aa["ci95"], "relative_half_width": aa["relative_half_width"],
                        "maximum_log_distance_from_one": log_floor,
                        "spread_fraction": math.expm1(log_floor), "pairs": aa["sample_count"]}
        bias_interval, bias = aa_bias_interval(aa, len(measurement["cases"]))
        biased = not aa_centred(aa, len(measurement["cases"]))
        floors[name]["bias_test"] = {**bias, "interval": bias_interval, "biased": biased,
                                     "equivalence_band": list(AA_EQUIVALENCE_BAND)}
        if aa["status"] != "resolved" or biased:
            reasons.append(name + ": A/A arm is unresolved or biased away from 1")
            reason_codes.append("aa_unresolved_or_biased")
        if width_pp > bar + 1e-9:
            reasons.append(name + f": A/A full CI width exceeds the {bar:g} pp qualification bar")
            reason_codes.append("aa_precision_above_bar")
        if ab["status"] != "resolved":
            precision_reasons.append(name + ": paired candidate interval did not reach requested precision")
        # CI must clear the measured A/A envelope, not merely exclude exactly 1.
        detectable = ab["ci95"][1] < math.exp(-log_floor) or ab["ci95"][0] > math.exp(log_floor)
        if not detectable:
            note = name + ": candidate difference is within the A/A noise floor"
            if len(measurement["cases"]) == 1:
                reasons.append(note)
                reason_codes.append("candidate_within_aa_noise")
            else:
                # With several units only the equal-weight aggregate below gates
                # the headline, so a candidate may leave some workloads unchanged.
                unit_notes.append(note)
        cases[name] = {"candidate_ci95": ab["ci95"], "pairs": ab["sample_count"],
                       "difference_clears_aa_noise": detectable}
        for arm in ("cpu_ab", "cpu_aa"):
            if arm in arms:
                cases[name][arm] = arms[arm]
    if measurement["quality"]["noisy"]:
        reasons.append("Host noisy-neighbor thresholds were exceeded")
        reason_codes.append("host_noisy")
    if not cases:
        reasons.append("No performance unit statistics")
        reason_codes.append("missing_performance_statistics")
        aggregate_ci, aggregate_floor = [1.0, 1.0], 1.0
    else:
        # New sessions provide a stratified bootstrap of complete pairs.
        # Historical receipts without it retain an explicitly conservative
        # geometric combination of per-unit bounds, not a claimed joint CI.
        aggregate_ci = measurement.get("wall_statistics", {}).get("ci95") or [
            geometric_mean(arms["ab"]["ci95"][side] for arms in measurement["cases"].values())
            for side in (0, 1)]
        aggregate_floor = geometric_mean(
            math.exp(value["maximum_log_distance_from_one"]) for value in floors.values())
        if not (aggregate_ci[0] > aggregate_floor or aggregate_ci[1] < 1 / aggregate_floor):
            reasons.append("Aggregate candidate interval does not clear the A/A envelope")
            reason_codes.append("aggregate_within_aa_noise")
    imprecise_speedup = bool(precision_reasons) and not reasons and aggregate_ci[0] > aggregate_floor
    inconclusive = bool(reasons) or bool(precision_reasons) and not imprecise_speedup
    reasons.extend(precision_reasons)
    if precision_reasons:
        reason_codes.append("paired_precision_unmet")
    cpu = {key: measurement[key] for key in (
        "cpu_ratio", "cpu_ratio_ci95", "cpu_ratio_direction", "cpu_classification") if key in measurement}
    if inconclusive and cpu:
        cpu["cpu_classification"] = "inconclusive"
    lower_bound = aggregate_ci[0]
    return {**cpu, "status": "inconclusive" if inconclusive else
                     "speedup_imprecise" if imprecise_speedup else
                     "speedup" if measurement["speedup"] > 1 else "slowdown",
            "correct": bool(measurement.get("correct")),
            "speedup": None if inconclusive else lower_bound if imprecise_speedup else measurement["speedup"],
            "point_speedup": measurement.get("speedup"),
            "optim_score": 1.0 if inconclusive else lower_bound,
            "score_basis": "no attributable gain" if inconclusive else "lower_confidence_bound",
            "candidate_ci95": aggregate_ci,
            "aa_precision": {"maximum_full_width_pp": bar,
                             "passed": all(not row["above_bar"] for row in aa_units.values()),
                             "units": aa_units},
            "aa_bias_test": {"method": "bonferroni", "family_alpha": AA_BIAS_FAMILY_ALPHA,
                             "family_size": len(floors),
                             "per_unit_alpha": AA_BIAS_FAMILY_ALPHA / len(floors) if floors else None,
                             "biased_units": sorted(n for n, f in floors.items() if f["bias_test"]["biased"])},
            "cases": cases, "aa_noise_floor": floors, "reasons": reasons,
            "unit_notes": unit_notes, "reason_codes": sorted(set(reason_codes))}


def summary(result):
    claimable = result.get("claimable") is True
    lines = ["QUALIFIED — CLAIMABLE" if claimable else "PROVISIONAL — NOT CLAIMABLE",
             f"Task: {result.get('task', 'unknown')}",
             f"Status: {result['status']}",
             "Cases: public only" if result.get("public_only") else "Cases: held-out",
             ("Speedup: inconclusive (no attributable ratio)" if result["status"] == "inconclusive"
              else "Speedup: not reported") if result.get("speedup") is None else
             f"Speedup: {result['speedup']:.4f}x baseline/candidate",
             f"CPU: {result.get('host', {}).get('model', 'unknown')}",
             f"Cpuset pinning took effect: {result.get('host', {}).get('pinning_took_effect', False)}"]
    for name, floor in result.get("aa_noise_floor", {}).items():
        lines.append(f"A/A {name}: 95% CI [{floor['ci95'][0]:.5f}, {floor['ci95'][1]:.5f}], "
                     f"noise envelope ±{100 * floor['spread_fraction']:.2f}%")
        test = floor.get("bias_test")
        if test and test.get("interval"):
            lines.append(f"A/A {name} bias test: {100 * test['interval_level']:.2f}% interval "
                         f"[{test['interval'][0]:.5f}, {test['interval'][1]:.5f}] ({test['basis']}); "
                         + ("biased" if test["biased"] else "not biased"))
    if result.get("optim_score") is not None:
        lines.append(f"OptimScore: {result['optim_score']:.5f} ({result.get('score_basis', 'unspecified')})")
    if result.get("status") == "speedup_imprecise":
        lines.append("Significant speedup with unmet precision target; reported ratio is the conservative LCB.")
    if result.get("cpu_ratio") is not None:
        low, high = result["cpu_ratio_ci95"]
        lines.append(f"Total CPU ratio: {result['cpu_ratio']:.4f}x candidate/baseline "
                     f"(95% CI [{low:.4f}, {high:.4f}]); "
                     f"{result.get('cpu_classification', 'unclassified')}")
        for name, case in result.get("cases", {}).items():
            for arm in ("cpu_ab", "cpu_aa"):
                if arm in case:
                    totals = case[arm]["total_usec"]
                    lines.append(f"CPU {name} {arm}: baseline {totals['baseline']} us, "
                                 f"candidate {totals['candidate']} us (sum of timed trials, not warmups)")
    if result.get("flags"):
        scored = "scored with flags" if result.get("optim_score") is not None else "flagged, not timed"
        incorrect = "; correctness flagged" if any(item.get("correctness") for item in result["flags"]) else ""
        lines.append(f"NEEDS REVIEW — {scored} (not claimable{incorrect}):")
        for item in result["flags"]:
            lines.append(f"Flagged for {item['gate']}: {item['status']}"
                         + (" — " + "; ".join(item["reasons"]) if item.get("reasons") else ""))
    lines.extend("Reason: " + reason for reason in result.get("reasons", []))
    lines.extend("Unit: " + note for note in result.get("unit_notes", []))
    if claimable:
        lines.append(f"Registered scoring host: {result.get('cpu_class', 'unknown')}")
    else:
        lines.extend("Not claimable: " + reason
                     for reason in (result.get("claimability") or {}).get("reasons", []))
        lines.append("This is an unregistered Docker host, not an Intel/AMD scoring-machine result.")
    return "\n".join(lines) + "\n"
