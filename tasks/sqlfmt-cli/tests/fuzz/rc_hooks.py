"""Minimal frozen-RC callbacks; only shared controllers classify raw facts."""
import json
from pathlib import Path
import shutil
import tempfile

from .rc_cases import precondition as PRECONDITION
from .rc_cli import requests as CLI_CASES, precondition as CLI_PRECONDITION


def HEADER_CONSUMERS(context):
    return {
        "format": "judge-generic-header-request-v1", "applicable": False,
        "reason": "Pinned sqlfmt is a pure-Go CLI/library with no public C/C++ header surface.",
        "applicability_evidence": {
            "upstream_commit": "8c676b51ea62e6ce33221e0d9d6d8a43256c942c",
            "archive_sha256": "2fb1eec6dc5cf3eea27e72c7a6d00340520df1464a15c9f406d87ff0e8af9f37",
            "archive_inventory": {"public_c_cpp_headers": [], "c_cpp_translation_units": []},
            "public_entry_points": ["cmd/sqlfmt/main.go", "format.Format", "format.Lex"],
            "source_authority": "Pinned README.md CLI and importable format package documentation; go.mod module github.com/dimitri/sqlfmt",
            "build_authority": "Protected build.sh compiles both admitted Go entry points with CGO_ENABLED=0.",
            "scope": "Header-consumer applicability only; no exemption for library, CLI or CPU correctness gates.",
        },
    }


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor
    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "sqlfmt-public-library"
    output.mkdir(parents=True, exist_ok=True)
    cache = context.setdefault("_sqlfmt_rc_facts", {})
    key = (str(output.resolve()), context["image"], context["cpuset"],
           context["builds"]["baseline"], context["builds"]["candidate"])
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="sqlfmt-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]),
                          task / "environment/workload/inputs", scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[*HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{task/'tests/fuzz'}:/verifier-fuzz:ro"],
                tmpfs="/tmp:rw,nosuid,nodev,size=4g")
            args = ["python3", "/verifier-fuzz/rc_driver.py", "--baseline", "/verifier-baseline",
                    "--candidate", "/app/build", "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"], "--out", "/scratch/facts"]
            results = executor.run([Step(args, timeout=900)], ctx, {})
            for index, result in enumerate(results):
                (output / f"step-{index}.stdout").write_bytes(result.stdout)
                (output / f"step-{index}.stderr").write_bytes(result.stderr)
            if any(result.exit_code != 0 or result.leftovers for result in results):
                raise ValueError("sqlfmt public-library observer infrastructure failure; streams retained")
            for name in ("stream-facts.json", "repeat-facts.json", "alias-facts.json", "build-receipt.json"):
                shutil.copy2(scratch / "facts" / name, output / name)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "cpuset": context["cpuset"], "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers, "telemetry": r.telemetry}
                          for r in results]}, indent=2) + "\n")
        cache["key"] = key
    return json.loads((output / filename).read_text())

def stream_faults(context):
    return _facts(context, "stream-facts.json")

def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")

def aliasing(context):
    return _facts(context, "alias-facts.json")

# The existing task tests/judge.json go_sqlfmt process_state probe remains
# authoritative for process-global runtime state. Repeat facts add native
# goroutine profiles, concrete OS identities and per-call fds/children facts.
GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process, "aliasing": aliasing}
