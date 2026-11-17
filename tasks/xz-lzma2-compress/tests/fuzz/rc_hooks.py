"""Task-local native calls and real xz CLI cases for the shared RC gates."""
import hashlib
import json
import lzma
from pathlib import Path
import shutil
import tempfile

from .rc_driver import precondition

HERE = Path(__file__).resolve().parent
PRECONDITION = precondition


def _facts(context, name):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor
    out = Path(context["out"]) / "xz-native"
    out.mkdir(parents=True, exist_ok=True)
    key = (str(out.resolve()), context["image"], context.get("cpuset"),
           context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_xz_native", {})
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="xz-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]).resolve(),
                          Path(context["public_inputs"]), scratch,
                          Path(context["task"]) / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"],
                extra_docker_flags=[*HARDENING,
                    "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                    "-v", f"{HERE}:/verifier-fuzz:ro"],
                tmpfs="/tmp:rw,nosuid,nodev,size=1g")
            results = executor.run([Step([
                "python3", "/verifier-fuzz/rc_driver.py", "--baseline", "/verifier-baseline",
                "--candidate", "/app/build", "--baseline-sha", context["builds"]["baseline"],
                "--candidate-sha", context["builds"]["candidate"],
                "--out", "/scratch/facts"], timeout=600)], ctx, {})
            result = results[0]
            (out / "stdout").write_bytes(result.stdout)
            (out / "stderr").write_bytes(result.stderr)
            (out / "execution.json").write_text(json.dumps({
                "exit_code": result.exit_code, "leftovers": result.leftovers,
                "telemetry": result.telemetry}, sort_keys=True, indent=2) + "\n")
            if result.exit_code:
                raise ValueError("XZ native worker failed: " + result.stderr.decode(errors="replace"))
            shutil.copytree(scratch / "facts", out / "facts", dirs_exist_ok=True)
        cache["key"] = key
    return json.loads((out / "facts" / (name + "-facts.json")).read_text())


def stream_faults(context):
    return _facts(context, "stream")


def repeat_in_process(context):
    return _facts(context, "repeat")


def aliasing(context):
    return _facts(context, "alias")


GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process, "aliasing": aliasing}
CLI_SURFACE = {
    "id": "xz-cli-v1", "visibility": "public", "contract_id": "xz-stream-and-file-errors-v1",
    "precondition_id": "bounded-xz-cli-fixtures-v1",
    "oracle": {"required_error_fields": {"code": "integer", "category": "nonempty_string"}},
}


def CLI_PRECONDITION(case, surface):
    valid = (surface == CLI_SURFACE and case.get("task") == "xz-lzma2-compress"
             and case.get("mode") in ("small", "large", "file")
             and case.get("surface_id") == CLI_SURFACE["id"]
             and case.get("contract_id") == CLI_SURFACE["contract_id"]
             and case.get("precondition_id") == CLI_SURFACE["precondition_id"])
    return {"valid": valid, "reason": "bounded protected xz CLI invocation" if valid else "unknown case"}


def CLI_CASES(context):
    root = Path(context["out"]) / "xz-cli-inputs"
    root.mkdir(parents=True, exist_ok=True)
    small = b"liblzma CLI fault fixture\n" * 3
    large = hashlib.shake_256(b"optimbench-xz-cli-v1").digest(262144)
    for name, data in (("small", small), ("large", large)):
        (root / (name + ".xz")).write_bytes(lzma.compress(data, preset=0, check=lzma.CHECK_CRC64))
    (root / "out").mkdir(exist_ok=True)
    (root / "out/source").write_bytes(small)
    result = []
    for mode in ("small", "large", "file"):
        case = {"task": "xz-lzma2-compress", "surface_id": CLI_SURFACE["id"],
                "contract_id": CLI_SURFACE["contract_id"],
                "precondition_id": CLI_SURFACE["precondition_id"], "mode": mode}
        fixture = "small.xz" if mode == "file" else mode + ".xz"
        argv = ["{build}/cmake/xz", "--decompress", "--stdout", "{work}/" + fixture]
        row = {"case": case, "surface": CLI_SURFACE,
               "payload_class": "large" if mode == "large" else "small",
               "step": {"argv": argv, "cwd": ".", "env": {"LC_ALL": "C"}, "timeout": 60},
               "inputs": str(root), "watch_inputs": [fixture]}
        if mode == "file":
            row["step"]["argv"] = ["{build}/cmake/xz", "--compress", "--keep", "-0",
                                   "{work}/out/source"]
            row.update(watch_outputs=["out"], output_dirs=["out"],
                       required_outputs=["out/source.xz"], watch_inputs=["out/source"])
        result.append(row)
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": result}


def HEADER_CONSUMERS(context):
    return {"format": "judge-generic-header-request-v1", "applicable": True,
            "manifest": str(HERE / "header-manifest.json")}
