"""Untimed CLI OS-fault observations using the shared DockerExecutor.

No stderr/exit compatibility policy lives here. The canonical controller compares
pristine/candidate facts, including baseline-specific SIGPIPE behavior. Adapters
provide valid nonempty input trees and separately declared small/large payloads.
Observer JSON is written outside the child's faulted stdout, after descendants
are stopped. No optimizer patch or task source is inspected/modified.
The new bounded 250ms observer shutdown barrier changes the process-observation
boundary only: semantic file/stream commitments are captured at root exit.
Post-barrier file/stream observations are diagnostic; late mutations fail closed.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # Also safe for standalone worker mount /cli_faults.py.
DEFAULT_FAULTS = ("healthy", "dev_full", "closed_reader", "closed_stdout", "readonly_dir")
MAX_HEAD = 32768
MAX_RETAINED_STREAM = 16 * 1024 * 1024
POST_EXIT_SETTLE_SECONDS = 0.25
POST_EXIT_POLL_SECONDS = 0.01
POST_EXIT_MAX_SNAPSHOTS = 32


class FaultHarnessError(ValueError):
    """Invalid setup/observation, never a candidate failure."""


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative(name):
    path = Path(name)
    if not isinstance(name, str) or not name or path.is_absolute() or ".." in path.parts:
        raise FaultHarnessError("watch/output/cwd paths must be relative to the case input tree")
    return path


def _inside(root, name):
    path = Path(root) / _relative(name)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise FaultHarnessError("input/setup path escapes case tree")
    return path


def snapshot(root, names):
    """Do not follow candidate-created symlinks when observing side effects."""
    root = Path(root)
    result = {}
    for name in names:
        relative = _relative(name)
        target = root / relative
        current = root
        for component in relative.parts[:-1]:
            current = current / component
            if current.is_symlink():
                result[name] = {name: {"kind": "ancestor_symlink",
                                      "ancestor": current.relative_to(root).as_posix(),
                                      "target": os.readlink(current)}}
                break
        if name in result:
            continue
        paths = [target]
        if target.is_dir() and not target.is_symlink():
            for folder, directories, files in os.walk(target, followlinks=False):
                paths.extend(Path(folder) / part for part in [*directories, *files])
        rows = {}
        for path in sorted(set(paths)):
            key = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
            except FileNotFoundError:
                rows[key] = {"kind": "missing"}
                continue
            row = {"mode": stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                row.update(kind="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                row.update(kind="directory")
            elif stat.S_ISREG(info.st_mode):
                row.update(kind="file", bytes=info.st_size, sha256=_sha(path))
            else:
                row.update(kind="special", device=info.st_rdev)
            rows[key] = row
        result[name] = rows
    return result


def _stream(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise FaultHarnessError("observer stream path replaced or unavailable")
    with path.open("rb") as stream:
        head = stream.read(MAX_HEAD)
    first = next((line for line in head.decode("utf-8", errors="replace").splitlines() if line.strip()), "")
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": _sha(path),
            "head_base64": base64.b64encode(head).decode(), "head_truncated": path.stat().st_size > len(head),
            "headline": first[:4096]}


def _retain_streams(row, source, out, case_id, fault, arm, limit):
    """Copy authenticated full streams out of a possibly temporary worker root.

    `out` is the caller's durable evidence directory, not its TemporaryDirectory.
    Heads remain previews only. A missing/oversized/mismatched full stream never
    becomes input to a semantic stderr comparator.
    """
    if "stdout" not in row or "stderr" not in row:
        if row.get("status") == "observed":
            raise FaultHarnessError("completed observer omitted raw stream commitments")
        row["raw_streams_retained"] = False
        return
    if type(limit) is not int or not 0 < limit <= MAX_RETAINED_STREAM:
        raise FaultHarnessError("raw stream retention limit must not exceed 16MiB")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", case_id).strip(".-")[:60] or "case"
    case_directory = "case-" + slug + "-" + hashlib.sha256(case_id.encode()).hexdigest()[:12]
    destination = Path(out) / case_directory / fault / arm
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("stdout", "stderr"):
        expected = row[name]
        if (type(expected.get("bytes")) is not int or not 0 <= expected["bytes"] <= limit
                or not re.fullmatch(r"[a-f0-9]{64}", expected.get("sha256", ""))):
            raise FaultHarnessError("raw stream missing commitment or exceeds bounded retention")
        src = Path(source) / (name + ".bin")
        dst = destination / (name + ".bin")
        temporary = destination / ("." + name + "-" + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected["bytes"]:
                raise FaultHarnessError("raw stream is not the committed regular file")
            digest, total = hashlib.sha256(), 0
            with os.fdopen(descriptor, "rb", closefd=False) as incoming:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as retained:
                    while chunk := incoming.read(min(1024 * 1024, limit - total + 1)):
                        total += len(chunk)
                        if total > limit:
                            raise FaultHarnessError("raw stream grew beyond retention bound")
                        digest.update(chunk)
                        retained.write(chunk)
                    retained.flush()
                    os.fsync(retained.fileno())
            after = os.fstat(descriptor)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or total != expected["bytes"] or digest.hexdigest() != expected["sha256"]):
                raise FaultHarnessError("raw stream changed or does not match observer SHA256")
            temporary.chmod(0o444)
            os.link(temporary, dst)  # Never overwrite another run's retained evidence.
        finally:
            os.close(descriptor)
            temporary.unlink(missing_ok=True)
        expected["artifact"] = {
            "path": str(dst.resolve()), "relative_path": dst.relative_to(Path(out)).as_posix(),
            "root": str(Path(out).resolve()), "bytes": total, "sha256": digest.hexdigest(),
            "max_bytes": limit, "complete": True,
        }
    row["raw_streams_retained"] = True


def _subreaper():
    library = ctypes.CDLL(None, use_errno=True)
    if library.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise FaultHarnessError("worker cannot adopt/stop CLI descendants safely")


def _close_stdout():
    os.close(1)  # Runs in child after Popen dup2, before exec; not in observer.


def _process_identity(pid):
    """Read Linux identity before/after cmdline; never attribute a recycled PID."""
    root = Path("/proc") / str(pid)
    try:
        raw = (root / "stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        command = (root / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        confirm = (root / "stat").read_text()
        current = confirm[confirm.rfind(")") + 2:].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    if fields[19] != current[19]:
        return None
    return {"pid": pid, "start_ticks": int(current[19]), "state": current[0],
            "ppid": int(current[1]), "pgid": int(current[2]), "cmdline": command}


def _descendants(pgid):
    rows = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            row = _process_identity(int(entry.name))
            if row is not None:
                rows[row["pid"]] = row
    owned = {os.getpid()}
    while True:
        discovered = {pid for pid, row in rows.items()
                      if row["ppid"] in owned or row["pgid"] == pgid}
        if discovered <= owned:
            break
        owned.update(discovered)
    return [rows[pid] for pid in sorted(owned - {os.getpid()}) if pid in rows]


def _settle_descendants(pgid):
    """Observe bounded natural shutdown, not a process-name exemption.

    A CLI root's wait() does not wait for its adopted grandchildren. Preserve
    every snapshot, reap only identity-checked zombies, and give all descendants
    the same short untimed shutdown bound. Anything still alive is left for the
    existing stop_leftovers and compatibility classifier; no process is ignored.
    """
    start = time.monotonic()
    snapshots, reaped = [], []
    for _ in range(POST_EXIT_MAX_SNAPSHOTS):
        rows = _descendants(pgid)
        elapsed = time.monotonic() - start
        snapshots.append({"elapsed_seconds_diagnostic_only": elapsed, "processes": rows})
        for row in rows:
            if row["state"] not in ("Z", "X"):
                continue
            identity = _process_identity(row["pid"])
            if identity is None or identity["start_ticks"] != row["start_ticks"]:
                continue
            try:
                pid, status = os.waitpid(row["pid"], os.WNOHANG)
            except ChildProcessError:
                continue  # Not adopted yet; observe again within the same bound.
            if pid:
                reaped.append({**row, "wait_status": status})
        if not rows or elapsed >= POST_EXIT_SETTLE_SECONDS:
            break
        time.sleep(min(POST_EXIT_POLL_SECONDS, POST_EXIT_SETTLE_SECONDS - elapsed))
    return {"format": "cli-post-exit-settlement-v1",
            "bound_seconds": POST_EXIT_SETTLE_SECONDS,
            "max_snapshots": POST_EXIT_MAX_SNAPSHOTS,
            "initial": snapshots[0], "transitions": snapshots[1:-1],
            "final": snapshots[-1], "reaped": reaped,
            "deadline_reached": bool(snapshots[-1]["processes"])}


def _copy_seed(source, target, readonly=(), relative="."):
    """Make normal case files UID10000-owned; never write through RO mounts."""
    if relative in readonly:
        return
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        target.mkdir(exist_ok=True)
        for child in source.iterdir():
            name = child.name if relative == "." else relative + "/" + child.name
            _copy_seed(child, target / child.name, readonly, name)
        if target.stat().st_uid == os.getuid():
            shutil.copystat(source, target)
    else:
        shutil.copy2(source, target)


def _publish(root, value):
    # Publish only after all untrusted descendants are gone. Replace a forged
    # symlink itself, never follow it; use an unpredictable O_EXCL temporary.
    temporary = Path(root) / ("observer-" + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, Path(root) / "observer.json")


def worker(request_path, output_root):
    """Trusted worker entry; the outer shared shim remains PID1."""
    request = json.loads(Path(request_path).read_text())
    out, work = Path(output_root), Path(output_root) / "work"
    report = {"schema": "optimbench-cli-fault-observation-v1", "fault": request["fault"],
              "case_id": request["case_id"], "payload_class": request["payload_class"],
              "uid": os.getuid(),
              "status": "invalid_run", "candidate_verdict": None,
              "stderr_classification": "owned by shared cli_stderr/controller, not this runner"}
    try:
        # Read the observer's effective mask inside this Docker/cgroup context.
        # This is a syscall observation, not the controller's requested cpuset.
        report["execution_affinity"] = sorted(os.sched_getaffinity(0))
        if os.getuid() == 0:
            raise FaultHarnessError("CLI worker must run non-root; otherwise readonly-dir control is invalid")
        _subreaper()
        sys.path.insert(0, "/")
        from runner import stop_leftovers
        fault = request["fault"]
        if fault not in DEFAULT_FAULTS:
            raise FaultHarnessError("unsupported CLI fault")
        setup = []
        readonly = request["output_dirs"] if fault == "readonly_dir" else []
        _copy_seed(Path("/cli-inputs"), work, readonly)
        mounts = []
        if fault == "readonly_dir":
            if not request["output_dirs"]:
                report.update(status="unsupported", reason="readonly_dir needs declared output directories")
                _publish(out, report)
                return
            for name in request["output_dirs"]:
                path = _inside(work, name)
                if not path.is_dir() or not (os.statvfs(path).f_flag & os.ST_RDONLY):
                    raise FaultHarnessError("declared readonly output is not an actual read-only mount")
                mounts.append({"path": name, "readonly_filesystem_verified": True})
        before_inputs = snapshot(work, request["watch_inputs"])
        before_outputs = snapshot(work, request["watch_outputs"])
        total = sum(item.get("bytes", 0) for rows in before_inputs.values() for item in rows.values()
                    if item["kind"] == "file")
        if total <= 0:
            raise FaultHarnessError("valid nonempty watched input is required; no empty fault probe")
        report.update(input_bytes=total, input_before=before_inputs, output_before=before_outputs,
                      setup_permission_changes=setup, setup_readonly_mounts=mounts,
                      output_dirs=request["output_dirs"])
        command = request["step"]
        cwd = _inside(work, command["cwd"])
        if not cwd.is_dir():
            raise FaultHarnessError("CLI cwd unavailable")
        stderr_path, stdout_path = out / "stderr.bin", out / "stdout.bin"
        stderr = stderr_path.open("wb")
        stdout = stdout_path.open("wb")
        stdin = None
        sink = None
        child = None
        timed_out = False
        output_budget_exceeded = False
        leftovers = []
        settlement = None
        root_exit = None
        start = time.monotonic()
        try:
            if fault == "dev_full":
                sink = os.open("/dev/full", os.O_WRONLY)
            elif fault == "closed_reader":
                reader, sink = os.pipe()
                os.close(reader)
            else:
                sink = stdout.fileno()
            stdin = (_inside(work, command["stdin_file"]).open("rb")
                     if command.get("stdin_file") else open(os.devnull, "rb"))
            environment = {**os.environ, **command["env"]}
            child = subprocess.Popen(
                command["argv"], cwd=cwd, env=environment, stdin=stdin,
                stdout=sink, stderr=stderr, close_fds=True, restore_signals=True,
                start_new_session=True, preexec_fn=_close_stdout if fault == "closed_stdout" else None)
            if fault in ("dev_full", "closed_reader"):
                os.close(sink)
                sink = None
            while child.poll() is None:
                if time.monotonic() - start > command["timeout"]:
                    timed_out = True
                    break
                if max(stderr_path.stat().st_size, stdout_path.stat().st_size) > request["max_stream_bytes"]:
                    output_budget_exceeded = True
                    break
                time.sleep(.01)
            if timed_out or output_budget_exceeded:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.wait(timeout=5)
            # Do not let a child finish outputs after the public CLI returned.
            # Freeze semantic commitments BEFORE the new shutdown barrier.
            root_exit = {
                "input_after": snapshot(work, request["watch_inputs"]),
                "output_after": snapshot(work, request["watch_outputs"]),
                "required_outputs": snapshot(work, request["required_outputs"]),
                "stdout": _stream(stdout_path), "stderr": _stream(stderr_path),
            }
            settlement = _settle_descendants(child.pid)
            leftovers = stop_leftovers(child.pid)
            # Reap adopted grandchildren before reading files/observer publication.
            while True:
                try:
                    if os.waitpid(-1, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
        finally:
            if child is not None and child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
                stop_leftovers(child.pid)
            if sink is not None and fault in ("dev_full", "closed_reader"):
                os.close(sink)
            if stdin:
                stdin.close()
            stderr.close()
            stdout.close()
        exit_code = child.returncode
        post_settle = {
            "input_after": snapshot(work, request["watch_inputs"]),
            "output_after": snapshot(work, request["watch_outputs"]),
            "required_outputs": snapshot(work, request["required_outputs"]),
            "stdout": _stream(stdout_path), "stderr": _stream(stderr_path),
        }
        late_mutations = [key for key, value in post_settle.items() if value != root_exit[key]]
        report.update(
            status="invalid_run" if timed_out or output_budget_exceeded or late_mutations else "observed",
            argv=command["argv"], cwd=command["cwd"], environment=command["env"],
            exit_code=exit_code, signal=-exit_code if exit_code is not None and exit_code < 0 else None,
            timed_out=timed_out, stream_budget_exceeded=output_budget_exceeded,
            elapsed_seconds_diagnostic_only=time.monotonic() - start,
            stdout=root_exit["stdout"], stderr=root_exit["stderr"],
            stdout_capture_available=fault not in ("dev_full", "closed_reader", "closed_stdout"),
            injected_fault_errno={"dev_full": "ENOSPC", "closed_reader": "EPIPE",
                                  "closed_stdout": "EBADF", "readonly_dir": "EROFS"}.get(fault),
            fault_errno_is_setup_not_observed_error_class=True,
            input_after=root_exit["input_after"],
            output_after=root_exit["output_after"],
            required_outputs=root_exit["required_outputs"],
            post_settle_file_effects={key: value for key, value in post_settle.items()
                                     if key not in ("stdout", "stderr")},
            post_settle_stream_effects={key: post_settle[key] for key in ("stdout", "stderr")},
            post_exit_file_mutations=[key for key in late_mutations if key not in ("stdout", "stderr")],
            post_exit_stream_mutations=[key for key in late_mutations if key in ("stdout", "stderr")],
            stopped_descendants=leftovers,
            post_exit_settlement=settlement,
            stdout_fault_triggered_by_program="not asserted; inspect healthy output/applicability",
        )
        if late_mutations:
            report["reason"] = "CLI file or stream effects changed during bounded post-exit shutdown barrier"
    except Exception as exc:
        # Do not turn a controller/transport/setup failure into a program exit.
        report.update(status="invalid_run", reason=type(exc).__name__ + ": " + str(exc)[:4096])
    _publish(out, report)


def _step_dict(step):
    value = {key: getattr(step, key) for key in ("argv", "cwd", "env", "timeout", "stdin_file")}
    if not isinstance(value["env"], dict) or any(not isinstance(key, str) or not isinstance(item, str)
                                               for key, item in value["env"].items()):
        raise FaultHarnessError("CLI environment must be string-to-string")
    for key in value["env"]:
        if re.search(r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|API_KEY|ACCESS_KEY|AUTH)", key, re.I):
            raise FaultHarnessError("credential environment keys are not accepted by offline CLI probes: " + key)
    if not value["argv"] or type(value["timeout"]) not in (int, float) or value["timeout"] <= 0:
        raise FaultHarnessError("invalid CLI step")
    return value


def _render(value, baseline, candidate):
    if not isinstance(value, str):
        return value
    for root in (str(baseline), str(candidate)):
        if value == root or value.startswith(root + "/"):
            value = "/app/build" + value[len(root):]
            break
    return value.replace("{build}", "/app/build").replace("{work}", "/scratch/work")


def run_pair(*, step, inputs, baseline, candidate, image, out, watch_inputs,
             watch_outputs=(), output_dirs=(), required_outputs=(), faults=DEFAULT_FAULTS,
             case_id="cli-fault", payload_class="small", cpuset=None, task=None,
             public_inputs=None, projects=None, max_stream_bytes=16 * 1024 * 1024,
             baseline_build_sha256=None, candidate_build_sha256=None):
    """Observe independent fresh workers for every arm/fault; no verdict policy.

    `inputs` is a verifier-owned prepared case tree, copied identically per arm.
    Step argv/env can use {build}, {work} or worker paths. cwd/stdin/watch/output
    paths are relative to that tree. Supply small+large valid cases separately.
    """
    from .receipt import image as immutable_image
    from .receipt import digest
    immutable_image(image)
    inputs, baseline, candidate, out = [Path(p).resolve() for p in (inputs, baseline, candidate, out)]
    if out.exists() or not inputs.is_dir() or not baseline.is_dir() or not candidate.is_dir():
        raise FaultHarnessError("fresh output and existing case/build directories required")
    if payload_class not in ("small", "large"):
        raise FaultHarnessError("declare small or large payload class")
    if not watch_inputs or not faults or len(set(faults)) != len(faults) or any(f not in DEFAULT_FAULTS for f in faults):
        raise FaultHarnessError("nonempty input inventory and unique known fault list required")
    if type(max_stream_bytes) is not int or not 65536 <= max_stream_bytes <= MAX_RETAINED_STREAM:
        raise FaultHarnessError("stream budget must be between 64KiB and 16MiB")
    if not isinstance(case_id, str) or not case_id or len(case_id) > 512:
        raise FaultHarnessError("case_id must be a bounded nonempty string")
    for name in [*watch_inputs, *watch_outputs, *output_dirs, *required_outputs]:
        _relative(name)
    if len(set(output_dirs)) != len(output_dirs) or any(
            a != b and Path(a) in Path(b).parents for a in output_dirs for b in output_dirs):
        raise FaultHarnessError("readonly output directories must be unique and non-overlapping")
    for path in inputs.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(inputs):
            raise FaultHarnessError("prepared input tree has an escaping symlink")
    sys.path.insert(0, str(ROOT / "tools/runner"))
    from runner import Context, Step, inventory_sha256
    from docker_executor import DockerExecutor
    template = _step_dict(step)
    runner_sha = _sha(Path(__file__))
    _relative(template["cwd"])
    if template["stdin_file"]:
        _relative(template["stdin_file"])
    original = inventory_sha256(inputs)
    builds = {"baseline": baseline, "candidate": candidate}
    supplied = {"baseline": baseline_build_sha256, "candidate": candidate_build_sha256}
    bindings = {arm: digest(supplied[arm]) if supplied[arm] else inventory_sha256(path)
                for arm, path in builds.items()}
    out.mkdir(parents=True)
    result = {"schema": "optimbench-cli-fault-pair-v1", "case_id": case_id, "payload_class": payload_class,
              "image": image, "runner_sha256": runner_sha, "input_tree_sha256": original,
              "artifacts_root": str(out),
              "observations": {}, "candidate_verdict": None,
              "build_sha256": bindings,
              "build_binding_source": {arm: "authenticated controller commitment supplied" if supplied[arm]
                                       else "runner computed inventory" for arm in builds},
              "timing_performed": False, "policy": "raw facts only; shared controller owns compatibility"}
    for fault in faults:
        result["observations"][fault] = {}
        for arm, build in (("baseline", baseline), ("candidate", candidate)):
            scratch = out / fault / arm
            scratch.mkdir(parents=True)
            (scratch / "work").mkdir()
            command = {
                **template,
                "argv": [_render(arg, baseline, candidate) for arg in template["argv"]],
                "env": {key: _render(value, baseline, candidate) for key, value in template["env"].items()},
            }
            request = {"case_id": case_id, "payload_class": payload_class, "fault": fault,
                       "step": command, "watch_inputs": list(watch_inputs), "watch_outputs": list(watch_outputs),
                       "output_dirs": list(output_dirs), "required_outputs": list(required_outputs),
                       "max_stream_bytes": max_stream_bytes}
            request_file = scratch / "request.json"
            request_file.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n")
            request_sha = _sha(request_file)
            ctx = Context(scratch, build, Path(public_inputs).resolve() if public_inputs else inputs,
                          Path(projects).resolve() if projects else inputs,
                          Path(task).resolve() / "environment/workload" if task else inputs, docker=True)
            flags = ["-v", f"{Path(__file__).resolve()}:/cli_faults.py:ro",
                     "-v", f"{inputs}:/cli-inputs:ro",
                     "-v", f"{build}:/scratch/build:ro", "-v", f"{build}:/app/baseline-build:ro",
                     "--cap-drop=ALL", "--security-opt=no-new-privileges"]
            if fault == "readonly_dir":
                for index, name in enumerate(output_dirs):
                    target = _inside(inputs, name)
                    if target.is_symlink() or (target.exists() and not target.is_dir()):
                        raise FaultHarnessError("readonly output must name a real directory")
                    seed = out / "readonly-seeds" / arm / str(index)
                    seed.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_dir():
                        shutil.copytree(target, seed, symlinks=True)
                    else:
                        seed.mkdir()
                    seed.chmod(0o777)
                    for item in seed.rglob("*"):
                        if item.is_symlink():
                            raise FaultHarnessError("readonly output seed has a symlink")
                        item.chmod(0o777 if item.is_dir() else 0o666)
                    mount_target = _inside(scratch / "work", name)
                    mount_target.mkdir(parents=True, exist_ok=True)
                    flags += ["-v", f"{seed}:{'/scratch/work' if name == '.' else '/scratch/work/' + name}:ro"]
            executor = DockerExecutor(image, cpuset=cpuset, extra_docker_flags=flags,
                                      tmpfs="/tmp:rw,nosuid,nodev,size=512m", task=task)
            try:
                observed = executor.run([
                    Step(["python3", "/cli_faults.py", "worker", "--request", "/scratch/request.json",
                          "--out", "/scratch"], timeout=template["timeout"] + 30)],
                    ctx, {"PYTHONDONTWRITEBYTECODE": "1"})[0]
                if observed.exit_code != 0:
                    raise FaultHarnessError("observer process failed")
                if _sha(request_file) != request_sha:
                    raise FaultHarnessError("CLI program changed trusted request metadata")
                row = json.loads((scratch / "observer.json").read_text())
                if (row.get("schema") != "optimbench-cli-fault-observation-v1" or
                        row.get("fault") != fault or row.get("case_id") != case_id):
                    raise FaultHarnessError("observer identity mismatch")
                row.update(arm=arm, observer_sha256=_sha(scratch / "observer.json"),
                           request_sha256=request_sha, shared_shim_leftovers=observed.leftovers)
                _retain_streams(row, scratch, out, case_id, fault, arm, max_stream_bytes)
            except Exception as exc:
                row = {"status": "invalid_run", "arm": arm, "fault": fault,
                       "reason": type(exc).__name__ + ": " + str(exc)[:4096], "candidate_verdict": None}
            result["observations"][fault][arm] = row
    if inventory_sha256(inputs) != original or _sha(Path(__file__)) != runner_sha:
        raise FaultHarnessError("verifier's prepared input tree or runner changed during observations")
    if any(not supplied[arm] and inventory_sha256(path) != bindings[arm] for arm, path in builds.items()):
        raise FaultHarnessError("protected build inventory changed during observations")
    (out / "cli-faults.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worker_args = sub.add_parser("worker")
    worker_args.add_argument("--request", type=Path, required=True)
    worker_args.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    worker(args.request, args.out)


if __name__ == "__main__":
    main()
