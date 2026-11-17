#!/usr/bin/env python3
"""Check original public plus new GeoJSON work, never open old private bodies."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import tempfile

TASK = Path(__file__).resolve().parents[2]
ROOT = TASK.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools/runner")]
from runner import Case, Context, LocalExecutor, run_case
from docker_executor import DockerExecutor

spec = importlib.util.spec_from_file_location("validate_geojson_adapter", TASK / "environment/workload/task.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
WORK = TASK / "environment/workload"


def check_observations(case, observations):
    if set(observations) != {d["path"] for d in case.data["docs"]}:
        return False
    return all(len(observations[d["path"]]) == d["expect_len"]
               and hashlib.sha256(observations[d["path"]]).hexdigest() == d["expect_sha"]
               for d in case.data["docs"])


def execute(case, inputs, build, image, out, local=False):
    with tempfile.TemporaryDirectory(prefix="jq-validation-") as temporary:
        context = Context(Path(temporary), build, inputs, WORK, WORK, docker=not local)
        executor = LocalExecutor() if local else DockerExecutor(image, cpuset="0-7")
        observations, results = run_case(adapter, case, context, executor)
        if any(r.leftovers for r in results):
            raise ValueError("A process was left behind")
        success = check_observations(case, observations)
        receipt = {"complete_observation_match": success,
                   "seconds": executor.case_time_ns / 1e9 if not local else None,
                   "statuses": [r.exit_code for r in results]}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2) + "\n")
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--controls", type=Path, help="Existing source-rebuilt wrong-result, omit-work, slow-correct trees")
    parser.add_argument("--image", default="optimbench:jq-stream")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--new-private", action="store_true")
    parser.add_argument("--local", action="store_true", help="Also check public LocalExecutor parity")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    public = adapter.cases(WORK / "inputs")
    cases = [(case, WORK / "inputs", "public-native" if
              case.data["docs"][0].get("operation") != "stream-geojson" else "public-stream")
             for case in public
             if case.data["docs"][0].get("operation", "strip-descriptions")
             in ("strip-descriptions", "stream-geojson")]
    if args.new_private:
        private = TASK / "tests/heldout"
        manifest = json.loads((private / "manifest.json").read_text())
        docs = [d for batches in manifest["splits"].values() for b in batches for d in b["docs"]
                if d.get("operation") == "stream-geojson"]
        if len(docs) != 1:
            raise ValueError("Exactly one new private streaming case required")
        # Only this new doc reaches Context.batch. No old private body is opened.
        cases.append((Case(docs[0]["batch"], {"docs": docs}), private, "new-private-stream"))
    summary = {"old_private_bodies_read": False, "qualification": False, "cases": {}, "controls": {}}
    for case, inputs, label in cases:
        first = execute(case, inputs, args.build, args.image, args.out / label / "original.json")
        repeated = execute(case, inputs, args.build, args.image, args.out / label / "self.json")
        if not first["complete_observation_match"] or not repeated["complete_observation_match"]:
            raise ValueError("Original/self complete-output mismatch. Details retained privately.")
        summary["cases"][label] = {"original": first, "self": repeated}
        if args.local and label.startswith("public"):
            local = execute(case, inputs, args.build, args.image, args.out / label / "local.json", True)
            if not local["complete_observation_match"]:
                raise ValueError("Local/Docker parity failed")
            summary["cases"][label]["local_parity"] = True
        if args.controls:
            for control in ("wrong-result", "omit-work", "slow-correct", "native-wrong-result"):
                result = execute(case, inputs, args.controls / control, args.image,
                                 args.out / label / (control + ".json"))
                is_stream = case.data["docs"][0].get("operation") == "stream-geojson"
                expected = control == "slow-correct" or not is_stream
                if control == "native-wrong-result":
                    expected = is_stream
                if result["complete_observation_match"] != expected:
                    raise ValueError("Source control did not produce the required outcome")
                summary["controls"].setdefault(label, {})[control] = result
            if case.data["docs"][0].get("operation") == "stream-geojson":
                baseline, slow = [], []
                for index in range(3):
                    b = execute(case, inputs, args.build, args.image,
                                args.out / label / f"paired-original-{index}.json")
                    s = execute(case, inputs, args.controls / "slow-correct", args.image,
                                args.out / label / f"paired-slow-{index}.json")
                    if not b["complete_observation_match"] or not s["complete_observation_match"]:
                        raise ValueError("Correct-but-slow output differs")
                    baseline.append(b["seconds"])
                    slow.append(s["seconds"])
                delta = statistics.median(slow) - statistics.median(baseline)
                summary["controls"][label]["slowdown_seconds"] = delta
                summary["controls"][label]["slowdown_observed"] = delta > 0
                if delta <= 0:
                    raise ValueError("Correct-but-slow did not show a measured slowdown")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"complete_original_self": "passed", "new_private_checked": args.new_private,
                      "public_local_parity_checked": args.local,
                      "source_controls": "passed" if args.controls else "not requested",
                      "old_private_bodies_read": False, "qualification": False}))


if __name__ == "__main__":
    main()
