"""Paired statistics; retain all samples and never pool CPU classes."""
import math
import random
import statistics


def geometric_mean(values):
    values = list(values)
    if not values or any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("Expected positive finite samples")
    return math.exp(statistics.mean(math.log(v) for v in values))


def bootstrap(pairs, seed=0, draws=5000):
    """Bootstrap whole pairs in log space, with no trimming."""
    logs = [math.log(a / b) for a, b in pairs if a > 0 and b > 0]
    if len(logs) != len(pairs) or not logs:
        raise ValueError("Invalid paired timings")
    rng = random.Random(seed)
    distribution = sorted(math.exp(statistics.mean(rng.choices(logs, k=len(logs))))
                          for _ in range(draws))
    low, high = distribution[int(draws * .025)], distribution[int(draws * .975)]
    return {"speedup": math.exp(statistics.mean(logs)), "ci95": [low, high],
            "relative_half_width": (high - low) / (2 * math.exp(statistics.mean(logs))),
            "pairs": [list(p) for p in pairs], "sample_count": len(pairs)}


def stopping(pairs, minimum=5, maximum=30, half_width=.02, seed=0, full_width_pp=None):
    if (minimum < 5 or maximum < minimum or half_width <= 0
            or full_width_pp is not None and (not math.isfinite(full_width_pp) or full_width_pp <= 0)):
        raise ValueError("Invalid adaptive-round settings")
    if len(pairs) < minimum:
        return {"stop": False, "status": "collecting"}
    result = bootstrap(pairs, seed)
    width = 100 * (result["ci95"][1] - result["ci95"][0])
    resolved = (result["relative_half_width"] <= half_width and
                (full_width_pp is None or width <= full_width_pp + 1e-9))
    return {**result, "stop": resolved or len(pairs) >= maximum,
            "ci95_full_width_pp": width,
            "full_width_limit_pp": full_width_pp,
            "status": "resolved" if resolved else "not resolved"}


def aggregate_wall(cases, seed=0, draws=5000):
    """Equal-weight units; bootstrap complete pairs within each unit in log space."""
    logs = []
    for pairs in cases.values():
        if not pairs or any(not math.isfinite(a) or not math.isfinite(b) or a <= 0 or b <= 0
                            for a, b in pairs):
            raise ValueError("Invalid paired wall times")
        logs.append([math.log(a / b) for a, b in pairs])
    if not logs:
        raise ValueError("No wall-time samples")
    rng = random.Random(seed)
    values = sorted(math.exp(statistics.mean(
        statistics.mean(rng.choices(unit, k=len(unit))) for unit in logs)) for _ in range(draws))
    return {"speedup": math.exp(statistics.mean(statistics.mean(unit) for unit in logs)),
            "ci95": [values[int(draws * .025)], values[int(draws * .975)]],
            "direction": "baseline/candidate", "weighting": "equal performance units",
            "bootstrap": "complete pairs within units; no CPU-class pooling"}


def cpu_ratio(cases, seed=0, draws=5000):
    """Candidate/baseline CPU cost; bootstrap whole paired trials within each case.

    Cases have equal log-space weight, like wall-time speedup. Never pool CPU
    classes or treat each worker as an independent sample.
    """
    logs = []
    for pairs in cases.values():
        if not pairs or any(not math.isfinite(a) or not math.isfinite(b) or a <= 0 or b <= 0
                            for a, b in pairs):
            raise ValueError("Invalid paired CPU times")
        logs.append([math.log(candidate / baseline) for baseline, candidate in pairs])
    if not logs:
        raise ValueError("No CPU samples")
    rng = random.Random(seed)
    estimate = math.exp(statistics.mean(statistics.mean(values) for values in logs))
    distribution = sorted(math.exp(statistics.mean(
        statistics.mean(rng.choices(values, k=len(values))) for values in logs)) for _ in range(draws))
    return {"ratio": estimate, "ci95": [distribution[int(draws * .025)], distribution[int(draws * .975)]],
            "direction": "candidate/baseline", "unit": "cgroup CPU microseconds"}


def aggregate_classes(sessions):
    sessions = list(sessions)
    require_same_mode(sessions)
    classes = {}
    for session in sessions:
        label = session["cpu_class"]
        if label in classes:
            raise ValueError("Aggregate sessions within a class explicitly; never pool classes")
        classes[label] = session
    if not classes:
        raise ValueError("No CPU classes")
    correct = all(s["correct"] for s in classes.values())
    provisional = bool(sessions[0].get("provisional", False))
    unresolved = any(s.get("correct") and s.get("speedup") is None for s in sessions)
    return {"classes": classes, "correct": correct, "claimable": False,
            "optim_score": geometric_mean(
                s.get("optim_score", s.get("speedup") or 1.0) if s["correct"] else 1.0
                for s in classes.values()),
            "provisional": provisional, "public_only": all(s.get("public_only", False) for s in sessions),
            "hosts": [s.get("host") for s in sessions],
            "aa_noise_floor": {s["cpu_class"]: s.get("aa_noise_floor", {}) for s in sessions},
            "status": "inconclusive" if unresolved else "resolved",
            "equal_weight_speedup": None if unresolved else geometric_mean(
                s.get("optim_score", s["speedup"]) if s["correct"] else 1.0 for s in classes.values())}


def aggregate_tasks(results):
    results = list(results)
    if not results:
        raise ValueError("No tasks")
    require_same_mode(results)
    unresolved = any(r.get("correct") and r.get("speedup") is None for r in results)
    return {"speedup": None if unresolved else geometric_mean(
                r.get("optim_score", r["speedup"]) if r["correct"] else 1.0 for r in results),
            "optim_score": geometric_mean(
                r.get("optim_score", r.get("speedup") or 1.0) if r["correct"] else 1.0
                for r in results),
            "correctness_rate": sum(bool(r["correct"]) for r in results) / len(results),
            "provisional": bool(results[0].get("provisional", False)),
            "public_only": all(r.get("public_only", False) for r in results),
            "hosts": [r.get("host") for r in results],
            "aa_noise_floor": [r.get("aa_noise_floor", {}) for r in results],
            "status": "inconclusive" if unresolved else "resolved",
            "setting": "default; cross-task aggregation remains open", "claimable": False}


def require_same_mode(results):
    if any(r.get("status") == "invalid run" for r in results):
        raise ValueError("Invalid infrastructure runs cannot be aggregated as candidate failures")
    if any(r.get("diagnostic_only") for r in results):
        raise ValueError("Raw measurement diagnostics cannot be aggregated as scores")
    modes = {bool(r.get("provisional", False)) for r in results}
    if len(modes) > 1 or any(r.get("provisional") and r.get("claimable") for r in results):
        raise ValueError("Never aggregate provisional and claimable/non-provisional runs together")


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(aggregate_classes(
        json.loads(p.read_text()) for p in args.sessions), indent=2, sort_keys=True) + "\n")
