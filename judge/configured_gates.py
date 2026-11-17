"""Data-configured gates for migrated tasks; existing task paths are untouched."""
import json
from pathlib import Path
import shutil
import tempfile


def execute(task, source, build, image, cpuset, output_dir=None):
    from .verifier import HARDENING
    from runner import Context, HarnessError, Step
    from docker_executor import DockerExecutor
    settings = json.loads((task / "tests/judge.json").read_text())
    definition = settings["upstream_gate"]
    if definition.get("blocked"):
        raise HarnessError(definition["blocked"])
    with tempfile.TemporaryDirectory(prefix="judge-configured-upstream-") as temporary:
        scratch = Path(temporary)
        shutil.copytree(source, scratch / "source",
                        ignore=shutil.ignore_patterns(".git", "__pycache__"), symlinks=True)
        ctx = Context(scratch, build, task / "environment/workload/inputs",
                      scratch, task / "environment/workload", docker=True)
        executor = DockerExecutor(image, cpuset, extra_docker_flags=[
            *HARDENING, "-v", f"{task / 'tests'}:/judge-task-tests:ro",
            "-v", f"{Path(__file__).with_name('controls')}:/judge-controls:ro"],
            tmpfs="/tmp:rw,nosuid,nodev,size=4g")
        env = {settings["build_source_env"]: "/scratch/source",
               **settings.get("build_environment", {}),
               **definition.get("environment", {})}
        results = executor.run([Step(definition["command"],
                                     timeout=definition.get("timeout", 7200))], ctx, env)
        if output_dir is not None:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            for index, result in enumerate(results):
                (output_dir / f"step-{index}.stdout").write_bytes(result.stdout)
                (output_dir / f"step-{index}.stderr").write_bytes(result.stderr)
            report_path = scratch / definition["report"]
            if report_path.is_file():
                shutil.copyfile(report_path, output_dir / "upstream.json")
            for pattern in ("*.stdout", "*.stderr"):
                for path in scratch.glob(pattern):
                    if path.is_file() and not path.is_symlink():
                        shutil.copyfile(path, output_dir / path.name)
        if any(r.exit_code != 0 for r in results):
            raise ValueError("Configured upstream gate failed: " +
                             results[-1].stderr.decode(errors="replace")[-4000:])
        report = json.loads((scratch / definition["report"]).read_text())
        if report.get("passed") is False:
            raise ValueError("Configured upstream report rejected its identity inventory")
        kind = definition.get("format", "outcomes")
        if kind == "pytest":
            from .upstream import pytest_outcomes
            outcomes = pytest_outcomes(report)
        elif kind == "identity-tests":
            from .upstream import successful_added_status
            tests = report.get("tests", [])
            if (not tests or len({x["identity"] for x in tests}) != len(tests) or
                    any(not successful_added_status(json.dumps(x["status"])) for x in tests)):
                raise ValueError("Missing, duplicate or failing upstream identities")
            outcomes = report
        elif kind == "outcomes":
            if not report.get("passed") or not report.get("outcomes"):
                raise ValueError("Missing or failing upstream identity inventory")
            outcomes = report["outcomes"]
        else:
            raise HarnessError("Unknown configured upstream report format")
        return {"upstream": {"passed": True, "outcomes": outcomes,
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results]}}
