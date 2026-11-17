#!/usr/bin/env python3
"""Independent JSON observation transport. One library arm per process."""
import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

from adapter import case_sha256, precondition


class VerifierInfrastructureError(RuntimeError):
    pass


def b64(data):
    return base64.b64encode(data).decode("ascii")


def lifecycle():
    result = {}
    for key, path in (("threads", "/proc/self/task"), ("fds", "/proc/self/fd")):
        try:
            result[key] = sorted(os.listdir(path))
        except OSError as error:
            result[key] = {"unavailable": type(error).__name__}
    return result


def mode(black, options, alternate=False, clear_targets=False):
    values = {key: options[key] for key in (
        "line_length", "string_normalization", "is_pyi", "skip_source_first_line",
        "magic_trailing_comma", "preview", "unstable") if key in options}
    values["target_versions"] = {
        getattr(black.TargetVersion, name) for name in options.get("target_versions", [])
    }
    if alternate:
        values["line_length"] = 63
        values["string_normalization"] = not values.get("string_normalization", True)
        values["preview"] = not values.get("preview", False)
    instance = black.Mode(**values)
    if clear_targets:
        instance.target_versions.clear()
    return instance


def file_effects(root):
    result = {}
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            result[name] = {"symlink": str(path.readlink())}
        elif path.is_file():
            data = path.read_bytes()
            result[name] = {"bytes_base64": b64(data), "sha256": hashlib.sha256(data).hexdigest(),
                            "mode": path.stat().st_mode & 0o777}
    return result


def prepare_file(case, root):
    path = root / case["input"]["filename"]
    if path.exists():
        path.chmod(0o644)
        path.unlink()
    path.write_text(case["input"]["text"], encoding="utf-8", newline="")
    path.chmod(0o644)
    os.utime(path, (946684800, 946684800))
    for fault in case["faults"]:
        if fault["kind"] == "readonly-input-file":
            path.chmod(0o444)
        elif fault["kind"] == "missing-input-file":
            path.unlink()
    return path


def mode_effect(instance):
    return {
        "target_versions": sorted(target.name for target in instance.target_versions),
        "line_length": instance.line_length,
        "string_normalization": instance.string_normalization,
        "preview": instance.preview, "unstable": instance.unstable,
    }


def invoke(black, case, options, instance, root, runtime):
    text = case["input"]["text"]
    surface = case["surface_id"]
    fast = options.get("fast", False)
    ranges = [tuple(pair) for pair in options.get("ranges", [])]
    if surface == "black.format_str":
        return black.format_str(text, mode=instance, lines=ranges)
    if surface == "black.format_file_contents":
        return black.format_file_contents(text, fast=fast, mode=instance)
    if surface == "black.format_cell":
        return black.format_cell(text, fast=fast, mode=instance)
    if surface == "black.format_ipynb_string":
        return black.format_ipynb_string(text, fast=fast, mode=instance)
    if surface == "black.format_file_in_place":
        path = prepare_file(case, root)
        return black.format_file_in_place(
            path, fast=fast, mode=instance,
            write_back=getattr(black.WriteBack, options.get("write_back", "NO")))
    if surface == "black.format_stdin_to_stdout":
        return black.format_stdin_to_stdout(
            fast, content=text, mode=instance,
            write_back=getattr(black.WriteBack, options.get("write_back", "NO")))
    if surface == "black.cli":
        path = prepare_file(case, root)
        args = [sys.executable, "-m", "black", "--no-cache", "--line-length", str(instance.line_length)]
        for target in sorted(instance.target_versions, key=lambda target: target.value):
            args += ["--target-version", target.name.lower()]
        flags = (
            (not instance.string_normalization, "--skip-string-normalization"),
            (not instance.magic_trailing_comma, "--skip-magic-trailing-comma"),
            (instance.preview, "--preview"), (instance.unstable, "--unstable"),
            (instance.is_pyi, "--pyi"), (fast, "--fast"),
        )
        args.extend(flag for enabled, flag in flags if enabled)
        cli_mode = options.get("cli_mode", "check")
        if cli_mode == "check":
            args.append("--check")
        args.append("-" if cli_mode == "stdin" else path.name)
        env = os.environ.copy()
        env.update(PYTHONPATH=runtime, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
                   BLACK_NUM_WORKERS="8", HOME=str(root / ".home"),
                   XDG_CACHE_HOME=str(root / ".cache"))
        result = subprocess.run(
            args, cwd=root, input=text.encode() if cli_mode == "stdin" else b"",
            capture_output=True, env=env, timeout=18)
        return {"cli_exit_code": result.returncode, "stdout_base64": b64(result.stdout),
                "stderr_sorted_lines_base64": [b64(line) for line in sorted(result.stderr.splitlines())]}
    raise VerifierInfrastructureError("Dispatch missing for an admitted surface")


def load_monitor(path):
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("_black_verified_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observe(case, runtime, build_sha, monitor=None):
    check = precondition(case, {})
    if not check["valid"]:
        raise ValueError("Invalid/unimplemented case: " + check["reason"])
    if case["faults"] and os.geteuid() == 0:
        raise VerifierInfrastructureError("Filesystem fault evidence requires unprivileged UID")
    # Exactly one arm is imported; no source/candidate inspection or oracle data.
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import black
    root = Path("/tmp/optimbench-black-fuzz-case")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(mode=0o700)
    results, effects, diagnostics = [], [], []
    primary = mode(black, case["options"])
    alternate = mode(black, case["options"], alternate=True)
    try:
        for index, step in enumerate(case["sequence"]):
            before = monitor.snapshot("before") if monitor else lifecycle()
            output, error_output = io.BytesIO(), io.BytesIO()
            out = io.TextIOWrapper(output, encoding="utf-8", write_through=True)
            err = io.TextIOWrapper(error_output, encoding="utf-8", write_through=True)
            exception = None
            if step["op"] == "clear_targets":
                primary.target_versions.clear()
            instance = alternate if step["op"] == "alternate_mode" else primary
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    value = invoke(black, case, case["options"], instance, root, runtime)
                result = {"kind": "ok", "value": value}
            except subprocess.TimeoutExpired:
                raise
            except VerifierInfrastructureError:
                raise
            except Exception as error:
                result = {"kind": "error", "class": type(error).__name__,
                          "module": type(error).__module__, "message": str(error)}
                exception = {"message": str(error), "traceback": traceback.format_exc()}
            out.flush()
            err.flush()
            stdout_bytes, stderr_bytes = output.getvalue(), error_output.getvalue()
            after = monitor.snapshot("after") if monitor else lifecycle()
            results.append(result)
            effects.append({
                "step": index, "stdout_encoding": "base64-bytes",
                "stdout_base64": b64(stdout_bytes),
                "stderr_encoding": "base64-bytes-sorted-lines",
                "stderr_sorted_lines_base64": [b64(line) for line in sorted(stderr_bytes.splitlines())],
                "files": file_effects(root),
                "caller_owned_modes": {"primary": mode_effect(primary), "independent_alternate": mode_effect(alternate)},
            })
            diagnostics.append({"step": index, "before": before, "after": after,
                                "exception_diagnostics": exception})
            out.detach()
            err.detach()
        return {
            "format": "optimbench-fuzz-observation-v1",
            "identity": {"task": case["task"], "surface_id": case["surface_id"],
                         "case_sha256": case_sha256(case), "build_sha256": build_sha},
            "termination": {"kind": "return", "exit_code": 0},
            "outcome": {"kind": "ok", "value": results},
            "effects": {"sequence": effects},
            "diagnostics": {"calls": diagnostics, "library_origin": black.__file__,
                            "compiled": black.COMPILED,
                            "execution_affinity": sorted(os.sched_getaffinity(0)),
                            "worker_instance_id": os.environ.get("OPTIMBENCH_FUZZ_WORKER_ID"),
                            "exception_policy": "library class/category and message exact; implementation traceback diagnostic; CLI streams exact"},
        }
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--build-sha256", required=True)
    parser.add_argument("--monitor", type=Path)
    args = parser.parse_args()
    if len(args.build_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.build_sha256):
        parser.error("--build-sha256 must be the actual authenticated build digest")
    case = json.loads(args.case.read_text())
    # Reporter uses the independent stdlib JSON encoder imported before black.
    print(json.dumps(observe(case, args.runtime, args.build_sha256, load_monitor(args.monitor)),
                     ensure_ascii=False, sort_keys=True))
