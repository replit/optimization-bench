#!/usr/bin/env python3
"""Real verifier path, explicitly author-host diagnostic qualification only."""
import datetime
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path("/work/git-factory")
OUT = Path("/work/git/pristine-scoring-repro")
sys.path[:0] = [str(ROOT), str(ROOT / "tools/runner")]
from judge import heldout, policy, score, score_host, verifier

TASK = ROOT / "tasks/git-history-change-summary"
IMAGE = (OUT / "image-id.txt").read_text().strip()
CACHE = OUT / "score-cache"
OUTPUT = OUT / "qualification-measurements.json"
WORK = OUT / "independent"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def cpu():
    return list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:]))


# The coordinator's clear is conditional on all these prerequisites.
assert not OUTPUT.exists()
assert json.loads((OUT / "validation/untimed-completion.json").read_text())["passed"]
assert json.loads((OUT / "validation/whole-git-reproducibility.json").read_text())["byte_identical"]
assert json.loads((OUT / "public-admission.json").read_text())["upstream"] == "passed"
assert subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip() == "4d65bfcb28829c1c5196a7bc1cc23706ebb5d19a"
assert IMAGE == "sha256:6579f4c57a83909459eddd44c168b37629ec2107603d01441d9fb4cf5f675c63"
assert not subprocess.check_output(["docker", "ps", "-q"], text=True).strip()
a = cpu()
time.sleep(2)
b = cpu()
d = [y - x for x, y in zip(a, b)]
busy = 1 - (d[3] + d[4]) / sum(d)
assert busy < .02, busy

started = now()
start = time.monotonic()
receipt = {
    "started": started, "image": IMAGE, "quiet_busy_fraction": busy,
    "task_commit": subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
    "coordinator": "71fa24c5-8bc1-482a-a146-45ef6c4a1676",
    "approval": "HOST-CLEAR GRANTED now; the audit worker has confirmed idle. Run source4d65bfc/image6579f4c5, verifier.evaluate provisional author-host,30AA+30ABpairs per family,1warmup,seed1729,native bootstrap,unchanged criteria bothlanes. Release clear immediately when done.",
    "mode": "qualification", "author_host": True, "claimable": False,
    "calibration_eligible": False,
}
with (OUT / "qualification-host-clear.json").open("x") as handle:
    json.dump(receipt, handle, indent=2)

result = {}
try:
    image = verifier.image_id(IMAGE)
    execution = score_host.select(image, "0-7")
    with heldout.materialized_task(TASK) as active:
        trusted = score.assets(active, image, CACHE)
        rules = json.loads((active / "release.json").read_text())["source_policy"]
        admission = policy.envelope(WORK / "source", trusted / "pristine", rules)
        public_only = heldout.check(active / "tests")["status"] != "available"
        assert public_only, "No held-out task construction authorized"
        projects = score.project_assets(active, trusted, public_only)
        result = verifier.evaluate(
            active, trusted / "baseline", WORK / "build", projects, image,
            execution["cpuset"], "author-host-diagnostic", seed=1729,
            minimum=30, maximum=30, warmups=1,
            upstream_receipt=OUT / "admission/upstream.json",
            source_sha256=policy.source_digest(admission), provisional=True,
            execution_host=execution, public_inputs=trusted / "public-workload/inputs",
            review_findings=admission["runtime_configuration_review"],
            gate_output=OUT / "qualification-behavior")
except Exception as error:
    result = {"status": "invalid run", "error": type(error).__name__ + ": " + str(error)}
    raise
finally:
    result.update(mode="qualification", author_host=True, diagnostic_only=True,
                  claimable=False, calibration_eligible=False, started=started,
                  ended=now(), wall_seconds=time.monotonic() - start)
    with OUTPUT.open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    (OUT / "qualification-ended.txt").write_text(result["ended"] + "\n")

summary = {"image": IMAGE, "passed": False, "units": {}, "wall_seconds": result["wall_seconds"]}
for name, value in result.get("cases", {}).items():
    unit = {}
    for arm in ("aa", "ab"):
        estimate = value[arm]
        records = [r for r in result["records"]
                   if r["case"] == name and r["arm"] == arm]
        times = [r["ns"] / 1e9 for r in records]
        low, high = estimate["ci95"]
        unit[arm] = {
            "ratio": estimate["speedup"], "ci95": [low, high],
            "full_width_percentage_points": 100 * (high - low),
            "sample_count": estimate["sample_count"],
            "minimum_seconds": min(times),
            "median_seconds": {lane: statistics.median(
                r["ns"] / 1e9 for r in records if r["lane"] == lane and r["phase"] == "timed")
                for lane in ("baseline", "candidate")},
            "passed": min(times) >= 1 and low <= 1 <= high and 100 * (high - low) <= 3,
        }
    summary["units"][name] = unit
summary["passed"] = bool(summary["units"]) and all(
    lane["passed"] for unit in summary["units"].values() for lane in unit.values()
) and not result.get("quality", {}).get("noisy", True)
with (OUT / "qualification-summary.json").open("x") as handle:
    json.dump(summary, handle, indent=2, sort_keys=True)
    handle.write("\n")
print(json.dumps(summary), flush=True)
