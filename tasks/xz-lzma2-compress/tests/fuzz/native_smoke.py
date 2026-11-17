"""Untimed public component checks, not eight-CPU scorer qualification."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from judge import behavior, fuzzing_generic, gates, interfaces, policy, score
from judge.verifier import build_inventory, image_id
from runner import Context, fingerprint, references, run_case, LocalExecutor
from docker_executor import DockerExecutor
from tools.replay_tasks import load

TASK = Path(__file__).resolve().parents[2]


def public_parity(build, inputs, projects, image, cpuset):
    module = load(TASK)
    expected = {row["path"]: (row["expect_len"], row["expect_sha"])
                for row in references(module, inputs)}
    results = {}
    for mode in ("local", "docker"):
        outputs = {}
        for case in module.cases(inputs):
            with tempfile.TemporaryDirectory(prefix="xz-parity-") as temporary:
                context = Context(Path(temporary), build, inputs, projects,
                                  TASK / "environment/workload", docker=mode == "docker")
                executor = LocalExecutor() if mode == "local" else DockerExecutor(image, cpuset)
                actual, _ = run_case(module, case, context, executor)
                outputs.update({key: fingerprint(data) for key, data in actual.items()})
        results[mode] = outputs
    return {"outputs": len(expected), "local_exact": results["local"] == expected,
            "docker_exact": results["docker"] == expected,
            "executor_parity": results["local"] == results["docker"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", default=",".join(map(str, sorted(os.sched_getaffinity(0)))))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    image = image_id(args.image)
    trusted = score.assets(TASK, image, args.out / "cache")
    build, source = trusted / "baseline", trusted / "pristine"
    inputs, projects = trusted / "public-workload/inputs", trusted / "projects"
    projects.mkdir(exist_ok=True)
    rules = json.loads((TASK / "release.json").read_text())["source_policy"]
    source_sha = policy.source_digest(policy.envelope(source, source, rules))
    builds = {arm: build_inventory(build) for arm in ("baseline", "candidate")}
    report = {"task": TASK.name, "image": image, "cpuset": args.cpuset, "claimable": False,
              "timing_performed": False, "source_sha256": source_sha, "builds": builds,
              "scope": "image-pristine public component replay, not candidate reconstruction, "
                       "held-out verification, CPU-matrix or scorer qualification"}

    def record(name, value):
        report[name] = value
        (args.out / "native-smoke.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        print(name, value.get("status", "observed"), flush=True)
        return value

    parity = record("public_parity", public_parity(build, inputs, projects, image, args.cpuset))
    interface = record("interface", interfaces.check(TASK, build, build, image, args.cpuset))
    actual_behavior = record("behavior", behavior.check(
        TASK, build, build, image, args.cpuset, args.out / "behavior", inputs, projects))
    generic = record("generic", fuzzing_generic.check(
        TASK, build, build, image, args.cpuset, args.out / "generic",
        public_inputs=inputs, projects=projects, process_state=actual_behavior.get("process_state"),
        builds=builds, sources={arm: source_sha for arm in ("baseline", "candidate")}))
    record("upstream", gates.execute_suites(
        TASK, source, build, image, args.cpuset, args.out / "upstream", args.out / "compiler-cache"))
    passed = (all(parity[key] for key in ("local_exact", "docker_exact", "executor_parity"))
              and interface["passed"] and actual_behavior["differential"]["passed"]
              and actual_behavior["process_state"]["passed"] and generic["passed"])
    report["passed"] = passed
    (args.out / "native-smoke.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
