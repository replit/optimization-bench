#!/usr/bin/env python3
"""Exclusive original/rebuilt-original A/A; shared shim clocks and statistics."""
import datetime
import base64
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path("/work/git-factory")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools/runner"))
from judge import telemetry
from judge.scheduler import orders
from judge.statistics import bootstrap
from judge.verifier import load_task, build_inventory, HARDENING
from runner import Context
from docker_executor import DockerExecutor

TASK = ROOT / "tasks/git-history-change-summary"
OUT = Path("/work/git/pristine-expanded")
IMAGE = (OUT / "image-id.txt").read_text().strip()
BASE = OUT / "image/baseline"
REBUILT = Path("/work/git/pristine-swap/do-nothing-build")
INPUTS = OUT / "image/inputs"
EXPECTED_STORE_BYTES = sum(
    p.stat().st_size for repo in (INPUTS / "histories").iterdir()
    for p in (repo / ".git/objects/pack").iterdir() if p.suffix in {".pack", ".idx"})
SEED = 20261025
RESULT = OUT / "aa-expanded-session-20261025.json"
RECEIPT = TASK / "tests/differential/validation/aa-host-clear-expanded-20261025.json"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write():
    RESULT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


class StopQualification(Exception):
    pass


approval = json.loads(RECEIPT.read_text())
assert approval["decision"] == "approve"
assert approval["image"] == IMAGE
assert subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip() == approval["task_commit"]
assert subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE], text=True).strip() == IMAGE
module = load_task(TASK)
cases = list(module.cases(INPUTS))
cores = telemetry.pinned("0-7")
validated = json.loads((TASK / "tests/differential/validation/expanded-public-controls.json").read_text())
assert validated["image"] == IMAGE
# Reuse the just-validated build inventories rather than reading Git binaries
# for hashing again; task preparation is pack/index I/O only.
builds = {"pristine": validated["reused_control_builds"]["pristine"],
          "rebuilt_pristine": validated["reused_control_builds"]["do-nothing"]}
locked = json.loads((TASK / "environment/workload/baseline-outputs.json").read_text())
receipt = sha(RECEIPT)
protocol = {name: sha(ROOT / name) for name in (
    "tools/runner/runner.py", "tools/runner/shim.py", "tools/runner/docker_executor.py",
    "tools/runner/preparation.py", "judge/scheduler.py", "judge/statistics.py")}
report = {
    "task": TASK.name, "task_commit": approval["task_commit"], "image": IMAGE,
    "started": now(), "ended": None, "seed": SEED, "cpuset": "0-7", "cores": 8,
    "memory": "16g", "pids_limit": 512, "network": "none",
    "protocol": protocol, "builds": builds,
    "host_clear_receipt": {"path": str(RECEIPT.relative_to(ROOT)), "sha256": receipt},
    "host": telemetry.fingerprint(cores, "intel-xeon-platinum-8581c"),
    "fixed_pairs_per_unit": 30, "warmup_pairs_per_unit": 1,
    "bootstrap_draws": 20000, "trimmed": False,
    "container_policy": "One fresh shared DockerExecutor container per pair; both builds read-only, four fresh Git processes per arm.",
    "case_clock": "Sum of shared shim t1_ns-t0_ns step intervals for each four-step arm; excludes container startup.",
    "minimum_seconds_every_arm": 1, "units": [], "passed": False,
    "public_only": True, "claimable": False, "optimized_or_model_runs": 0,
}
before = telemetry.counters(cores)
wall_start = time.monotonic()
rng = random.Random(SEED)
schedules = {case.id: orders(30, rng) for case in cases}
write()

try:
    for case in cases:
        expected = (OUT / "public" / case.id / "pristine/complete-observation.json").read_bytes()
        lock = next(row for row in locked if row["id"] == case.id)
        assert len(expected) == lock["expect_len"] and hashlib.sha256(expected).hexdigest() == lock["expect_sha"]
        unit_before = telemetry.counters(cores)
        unit = {"id": case.id, "schedule": schedules[case.id], "records": [], "pairs": [],
                "started": now(), "ended": None, "passed": False}
        report["units"].append(unit)

        def pair(order, phase, index):
            with tempfile.TemporaryDirectory(prefix="xdiff-aa-pair-") as directory:
                scratch = Path(directory)
                ctx = Context(scratch, BASE, INPUTS, scratch, TASK / "environment/workload", SEED, docker=True)
                all_steps = []
                spans = {}
                for lane in order:
                    lane_dir = scratch / lane
                    lane_dir.mkdir()
                    arm_ctx = Context(lane_dir, BASE, INPUTS, lane_dir, TASK / "environment/workload", SEED, docker=True)
                    steps = module.prepare(case, arm_ctx)
                    assert len(steps) == 4
                    for step in steps:
                        step.cwd = lane + "/" + step.cwd
                        step.stdin_file = lane + "/" + step.stdin_file
                        if lane == "candidate":
                            step.argv[0] = "/app/rebuilt-pristine/project/git"
                    spans[lane] = (len(all_steps), len(all_steps) + len(steps))
                    all_steps.extend(steps)
                executor = DockerExecutor(IMAGE, "0-7", memory="16g", pids_limit=512,
                    extra_docker_flags=[*HARDENING,
                        "-v", str(REBUILT) + ":/app/rebuilt-pristine:ro"],
                    tmpfs="/tmp:rw,nosuid,nodev,size=2g")
                values = executor.run(all_steps, ctx, module.ENV)
                preparation = executor.container_prepare
                assert preparation["timed"] is False and preparation["exit_code"] == 0
                assert not preparation["leftovers"] and not preparation.get("harness_error")
                counts = json.loads(base64.b64decode(preparation["stdout_base64"]))
                assert counts["selected_repositories"] == 4
                assert counts["files_read"] == 8 and counts["bytes_read"] == EXPECTED_STORE_BYTES
                assert executor.case_time_ns == sum(r.t1_ns - r.t0_ns for r in values)
                seconds = {}
                row = {"phase": phase, "round": index, "order": order, "arms": {},
                       "container_prepare": preparation, "preparation_counts": counts}
                for lane in order:
                    start, end = spans[lane]
                    results = values[start:end]
                    output = module.collect(case, ctx, results)[case.id]
                    assert output == expected, "Complete-byte mismatch"
                    assert all(r.exit_code == 0 and not r.stderr and not r.leftovers for r in results)
                    seconds[lane] = sum(r.t1_ns - r.t0_ns for r in results) / 1e9
                    row["arms"][lane] = {
                        "seconds": seconds[lane], "complete_bytes_equal": True, "bytes": len(output),
                        "invocations": [{"t0_ns": r.t0_ns, "t1_ns": r.t1_ns,
                                         "exit_code": r.exit_code, "leftovers": r.leftovers,
                                         "telemetry": r.telemetry} for r in results],
                    }
                unit["records"].append(row)
                write()
                print(case.id, phase, index, seconds, flush=True)
                for lane in order:
                    if seconds[lane] < 1:
                        raise StopQualification(f"unit_under_1s:{case.id}:{lane}:{seconds[lane]}")
                return [seconds["baseline"], seconds["candidate"]]

        pair(("baseline", "candidate"), "warmup", -1)
        for index, order in enumerate(schedules[case.id]):
            unit["pairs"].append(pair(order, "measurement", index))
            write()
        estimate = bootstrap(unit["pairs"], seed=SEED, draws=20000)
        low, high = estimate["ci95"]
        unit.update({
            "ended": now(), "statistics": estimate,
            "baseline_median_seconds": statistics.median(p[0] for p in unit["pairs"]),
            "rebuilt_median_seconds": statistics.median(p[1] for p in unit["pairs"]),
            "minimum_observed_seconds": min(a["seconds"] for row in unit["records"] for a in row["arms"].values()),
            "full_ci_width_percentage_points": 100 * (high - low),
            "quality": telemetry.quality(unit_before, telemetry.counters(cores), cores),
        })
        unit["passed"] = low <= 1 <= high and 100 * (high - low) <= 3 and not unit["quality"]["noisy"]
        positions = [[row["arms"][row["order"][0]]["seconds"],
                      row["arms"][row["order"][1]]["seconds"]]
                     for row in unit["records"] if row["phase"] == "measurement"]
        unit["first_vs_second_position"] = bootstrap(positions, seed=SEED, draws=20000)
        write()
        if not unit["passed"]:
            raise StopQualification("A/A_envelope_or_noise_failed:" + case.id)
    report["passed"] = True
    report["status"] = "qualified"
except StopQualification as error:
    report["status"] = "stopped"
    report["stop_reason"] = str(error)
except Exception as error:
    report["status"] = "invalid"
    report["error"] = type(error).__name__ + ": " + str(error)
    raise
finally:
    for unit in report["units"]:
        if unit["records"] and "minimum_observed_seconds" not in unit:
            unit["minimum_observed_seconds"] = min(
                a["seconds"] for row in unit["records"] for a in row["arms"].values())
        if unit["ended"] is None:
            unit["ended"] = now()
    report["ended"] = now()
    report["wall_seconds"] = time.monotonic() - wall_start
    report["quality"] = telemetry.quality(before, telemetry.counters(cores), cores)
    write()
    (OUT / "aa-expanded-session-20261025-ended.txt").write_text(report["ended"] + "\n")
print(json.dumps({"status": report["status"], "passed": report["passed"], "stop_reason": report.get("stop_reason")}), flush=True)
