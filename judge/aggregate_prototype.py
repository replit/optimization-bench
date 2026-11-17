"""Report-only task aggregation experiment; never imported by the default scorer."""
import argparse
import gzip
import json
import math
from pathlib import Path
import random
import statistics


def means(values):
    return {"geometric": math.exp(statistics.mean(math.log(x) for x in values)),
            "harmonic": len(values) / sum(1 / x for x in values)}


def estimate(measurement, seed=1729, samples=5000):
    """Fixed-case, stratified paired bootstrap; equal weight per benchmark case."""
    rng = random.Random(seed)
    result = {"prototype_only": True, "claimable": False, "default_changed": False,
              "method": "Fixed cases; independently resample complete timing pairs within each case; "
                        "geometric per-case paired ratio, then equal-weight geometric/harmonic task mean. "
                        "Assumes pairs within each case are exchangeable; shared-host cross-case covariance "
                        "and multiplicity are not modeled. No confidence claims beyond this fixed case set.",
              "seed": seed, "samples": samples, "case_count": len(measurement["cases"])}
    values, draws = {}, {}
    for arm in ("ab", "aa"):
        cases = [item[arm]["pairs"] for item in measurement["cases"].values()]
        if not cases or any(not rows for rows in cases):
            raise ValueError("Complete nonempty A/B and A/A pairs are required")
        logs = [[math.log(a / b) for a, b in pairs] for pairs in cases]
        values[arm] = means([math.exp(statistics.mean(row)) for row in logs])
        draws[arm] = {kind: [] for kind in ("geometric", "harmonic")}
        for _ in range(samples):
            ratios = [math.exp(statistics.mean(rng.choices(row, k=len(row)))) for row in logs]
            for kind, value in means(ratios).items():
                draws[arm][kind].append(value)
    for kind in ("geometric", "harmonic"):
        ci = {}
        for arm in ("ab", "aa"):
            ordered = sorted(draws[arm][kind])
            ci[arm] = [ordered[int(.025 * (samples - 1))], ordered[int(.975 * (samples - 1))]]
        floor = max(abs(math.log(x)) for x in ci["aa"])
        clears = ci["ab"][0] > math.exp(floor) or ci["ab"][1] < math.exp(-floor)
        aa_contains_one = ci["aa"][0] <= 1 <= ci["aa"][1]
        result[kind] = {"estimate": values["ab"][kind], "ci95": ci["ab"],
                        "aa_estimate": values["aa"][kind], "aa_ci95": ci["aa"],
                        "aa_envelope_fraction": math.expm1(floor),
                        "aa_contains_one": aa_contains_one, "clears_own_aa_envelope": clears,
                        "prototype_signal": "resolved" if clears and aa_contains_one else "inconclusive"}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurement", type=Path)
    parser.add_argument("--samples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    data = args.measurement.read_bytes()
    value = json.loads(gzip.decompress(data) if args.measurement.suffix == ".gz" else data)
    print(json.dumps(estimate(value, args.seed, args.samples), indent=2))
