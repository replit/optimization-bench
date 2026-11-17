"""Admit Harbor editable-root artifacts and build with the shared Docker executor."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from . import policy, source_policy
from .telemetry import pinned
from .verifier import HARDENING, build_inventory, image_id
from runner import Context, HarnessError, Step
from docker_executor import DockerExecutor


class BuildFailed(HarnessError):
    """build.sh exited non-zero. The reference builds with the same image and recipe,
    so score-through records a candidate build failure as the candidate's outcome."""


SOURCE_ENV = {
    "terser-minify": "TERSER_SOURCE", "black-project-format": "BLACK_SOURCE",
    "sqlfluff-project-lint": "SQLFLUFF_SOURCE", "sqlglot-mozilla-transpile": "SQLGLOT_SOURCE",
    "ruff-project-lint": "RUFF_SOURCE",
}


def build(task, pristine, image, cpuset, output, artifacts=None, source=None, seed_build=None,
          execution_host=None):
    task, pristine, output = Path(task).resolve(), Path(pristine).resolve(), Path(output).resolve()
    if execution_host is None:
        pinned(cpuset)
    elif len(execution_host["requested_cores"]) != 8 or cpuset != execution_host["cpuset"]:
        raise ValueError("Build execution host must match the verified eight-CPU selection")
    image = image_id(image)
    if output.exists():
        raise ValueError("Build output must be fresh")
    if (artifacts is None) == (source is None):
        raise ValueError("Supply exactly one of artifacts or complete source")
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    with tempfile.TemporaryDirectory(prefix="judge-build-") as temporary:
        scratch = Path(temporary)
        submitted = scratch / "source"
        if artifacts is not None:
            admitted = policy.artifacts_to_envelope(artifacts, pristine, submitted, rules)
        else:
            admitted = policy.envelope(source, pristine, rules)
            shutil.copytree(source, submitted, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        dummy = scratch / "unused-build"
        dummy.mkdir()
        if seed_build is not None:
            if task.name != "ruff-project-lint":
                raise ValueError("Compiler-cache seeding is only supported for Ruff")
            seed_build = Path(seed_build).resolve()
            saved = json.loads((seed_build / "build-receipt.json").read_text())
            if saved["task"] != task.name or saved["image"] != image:
                raise ValueError("Compiler-cache seed task/image mismatch")
            previous_admission = policy.envelope(seed_build / "source", pristine, rules)
            previous = previous_admission["files"]
            if policy.source_digest(previous_admission) != saved["source_sha256"]:
                raise ValueError("Compiler-cache seed source commitment mismatch")
            if build_inventory(seed_build / "build") != saved["build_sha256"]:
                raise ValueError("Compiler-cache seed build commitment mismatch")
            (scratch / "build").mkdir()
            shutil.copytree(seed_build / "build/target", scratch / "build/target", symlinks=True)
            # Cargo uses mtimes for local-source invalidation. A negative source
            # edit may predate the seed's compile, so advance changed files only.
            for name, content in admitted["files"].items():
                if previous.get(name) != content:
                    os.utime(submitted / name, None)
        for name in ("cache", "work"):
            (scratch / name).mkdir(mode=0o777)
        flags = [*HARDENING, "-v", f"{scratch / 'cache'}:/app/.cache"]
        env = {"PATH": "/toolchain/bin:/usr/local/bin:/usr/bin:/bin",
               "CI": "1", "INSTA_UPDATE": "no", "PIP_NO_INDEX": "1",
               "RAYON_NUM_THREADS": "8", "BLACK_NUM_WORKERS": "8", "CARGO_BUILD_JOBS": "8",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
               "npm_config_offline": "true", "npm_config_audit": "false", "npm_config_fund": "false"}
        if task.name == "babel-project-compile":
            flags.extend(["-v", f"{submitted}:/app/babel:ro", "-v", f"{scratch / 'work'}:/work"])
        else:
            settings = json.loads((task / "tests/judge.json").read_text())
            source_env = settings.get("build_source_env") or SOURCE_ENV.get(task.name)
            if not source_env or not source_env.isidentifier():
                raise ValueError("Missing build source environment setting")
            env[source_env] = "/scratch/source"
            env.update(settings.get("build_environment", {}))
        ctx = Context(scratch, dummy, task / "environment/workload/inputs", scratch,
                      task / "environment/workload", docker=True)
        executor = DockerExecutor(image, cpuset, extra_docker_flags=flags,
                                  tmpfs="/tmp:rw,nosuid,nodev,size=4g")
        results = executor.run([Step(["sh", "/app/workload/build.sh", "/scratch/build"],
                                     timeout=7200)], ctx, env)
        if any(r.exit_code != 0 for r in results):
            raise BuildFailed("Candidate build failed: " + results[-1].stderr.decode(errors="replace")[-4000:])
        # Build recipes do not authorize edits to submitted source or protected tests.
        after = policy.envelope(submitted, pristine, rules)
        if admitted != after:
            raise ValueError("Build changed the admitted submission")
        built = scratch / "build"
        commitment = build_inventory(built)
        output.mkdir(parents=True)
        shutil.copytree(submitted, output / "source", symlinks=True)
        shutil.copytree(built, output / "build", symlinks=True)
        receipt = {"task": task.name, "image": image, "cpuset": cpuset,
                   "source_sha256": policy.source_digest(admitted),
                   "build_sha256": commitment, "claimable": False,
                   "executor": "shared DockerExecutor; source admission, not a timing sample",
                   "compiler_cache_seed": None if seed_build is None else {
                       "source_sha256": saved["source_sha256"], "build_sha256": saved["build_sha256"]},
                   "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                              "telemetry": r.telemetry} for r in results]}
        (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("task", "pristine", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, help="JSON: source-relative root -> exported directory")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--seed-build", type=Path, help="Ruff only: validated prior judge.build output as compiler cache")
    args = parser.parse_args()
    receipt = build(args.task, args.pristine, args.image, args.cpuset, args.output,
                    json.loads(args.artifacts.read_text()) if args.artifacts else None, args.source, args.seed_build)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
