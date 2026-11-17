"""PID 1, public Docker-per-case execution shim. No input/result interpretation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import base64
import hashlib
sys.path.insert(0, "/")
from runner import execute_child


def cpu_usage_usec():
    """Container-wide CPU, including all workers, sampled at operation boundaries."""
    try:
        fields = dict(line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines())
        return int(fields["usage_usec"])
    except (OSError, KeyError, ValueError):
        return None


def cpu_delta(before, after):
    return after - before if before is not None and after is not None and after >= before else None


def thread_identities(process, exiting=None, unavailable=None):
    """Concrete live task identities; no sleeps, name exemptions or dead-task credit."""
    values = []
    for task in (process / "task").iterdir():
        try:
            raw = (task / "stat").read_text()
            fields = raw.rsplit(")", 1)[1].split()
            if fields[0] in ("Z", "X"):
                continue
            again = (task / "stat").read_text().rsplit(")", 1)[1].split()
            if again[0] in ("Z", "X") or again[19] != fields[19]:
                continue
            identity = {"tid": int(task.name), "start_ticks": int(fields[19]),
                           "tgid": int(process.name), "state": again[0],
                           "name": raw.split("(", 1)[1].rsplit(")", 1)[0][:80],
                           "flags": int(again[6]),
                           "sampled_monotonic_ns": time.monotonic_ns()}
            if identity["flags"] & 0x00000004:  # Linux PF_EXITING, not a name/allowance.
                if exiting is not None:
                    exiting.append(identity)
                continue
            values.append(identity)
        except (OSError, ValueError, IndexError) as error:
            if unavailable is not None and not isinstance(error, (FileNotFoundError, ProcessLookupError)):
                unavailable.append({"tid": task.name, "error": type(error).__name__})
            continue  # Vanished/dead tasks are not live workers.
    return values


def worker_tasks(main_pid, exiting=None):
    """Live worker identities at a resident stop, excluding PID 1 and the caller."""
    workers = {}
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == 1:
            continue
        try:
            for identity in thread_identities(process, exiting):
                tid, pid = identity["tid"], int(process.name)
                if pid == main_pid and tid == main_pid:
                    continue
                workers[(pid, tid, identity["start_ticks"])] = {
                    **identity, "pid": tid, "process_pid": pid, "threads": 1,
                    "thread_identities": [identity],
                    "kind": "thread" if tid != pid else "process",
                    "reason": "worker outlived measured operation"}
        except (OSError, ValueError, IndexError):
            continue
    return workers


def signal_all(sig):
    try:
        os.kill(-1, sig)
    except ProcessLookupError:
        pass


def leftover_snapshot(proc, exiting_witnesses=None):
    """A stopped live process, excluding an exit/zombie race during inspection."""
    try:
        state = (proc / "stat").read_text().rsplit(")", 1)[1].split()[0]
        if state in ("Z", "X"):
            return None
        raw = (proc / "cmdline").read_bytes()
        exiting, unavailable = [], []
        try:
            identities = thread_identities(proc, exiting, unavailable)
        except OSError:
            identities = None
        threads = len(identities) if identities is not None else None
        ipc_fds = []
        try:
            for descriptor in (proc / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                    if int(descriptor.name) > 2 and target.startswith(("pipe:[", "socket:[")):
                        ipc_fds.append(int(descriptor.name))
                except OSError:
                    continue
        except OSError:
            pass  # Missing FD metadata makes classification stricter, never exempt.
        # A helper can finish after the first stat read despite the freeze.
        # Apply the same dead-process exclusion again, without waiting.
        state = (proc / "stat").read_text().rsplit(")", 1)[1].split()[0]
        if state in ("Z", "X"):
            return None
        if exiting_witnesses is not None:
            exiting_witnesses.extend(exiting)
        if not identities and exiting and not unavailable:
            return None  # Every observed remaining task is in kernel exit, not user work.
        if not identities:
            # Keep an explicit live-process witness instead of fabricating
            # a concrete thread count when task metadata raced or is absent.
            threads = None
        return {"pid": int(proc.name), "threads": threads, "state": state,
                "thread_identities": identities if identities else None,
                "sampled_monotonic_ns": time.monotonic_ns(),
                "ipc_fds": sorted(ipc_fds),
                "argv": [part.decode(errors="replace") for part in
                         (raw.split(b"\0")[:-1] if raw.endswith(b"\0") else raw.split(b"\0"))] if raw else [],
                "cmdline": raw.replace(b"\0", b" ").decode(errors="replace")}
    except OSError as error:
        if isinstance(error, (FileNotFoundError, ProcessLookupError)):
            return None
        return {"pid": int(proc.name), "threads": None, "thread_identities": None,
                "state": None, "argv": [], "cmdline": "",
                "metadata_error": type(error).__name__,
                "sampled_monotonic_ns": time.monotonic_ns()}


def prepare_container(config, output):
    """Judge-owned preparation, once before the timed loop, never a sample."""
    start = time.perf_counter_ns()
    child, code, leftovers, error = None, None, [], None
    stdout, stderr = output / "container-prepare.stdout", output / "container-prepare.stderr"
    try:
        executable = Path(config["argv"][0])
        root = Path("/app/workload")
        if (not executable.resolve().is_relative_to(root.resolve())
                or executable.is_relative_to(root / "inputs")
                or any(p.is_symlink() for p in [executable, *executable.parents]
                       if p != root and p.is_relative_to(root))
                or hashlib.sha256(executable.read_bytes()).hexdigest() != config["executable_sha256"]):
            raise ValueError("Preparation executable differs from protected task code")
        with stdout.open("wb") as out, stderr.open("wb") as err:
            try:
                child = subprocess.Popen(config["argv"], cwd=config["cwd"], env=config["env"],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
                code, _, _, _ = execute_child(child, config["timeout"],
                    before_export=lambda: signal_all(signal.SIGSTOP))
            finally:
                signal_all(signal.SIGSTOP)
                for proc in Path("/proc").iterdir():
                    if proc.name.isdigit() and int(proc.name) != 1:
                        record = leftover_snapshot(proc)
                        if record is not None:
                            leftovers.append(record)
                signal_all(signal.SIGKILL)
                if child is not None:
                    child.wait()
                while True:
                    try:
                        os.waitpid(-1, 0)
                    except ChildProcessError:
                        break
        if code != 0 or leftovers:
            raise ValueError("Preparation exited unsuccessfully or left live processes")
    except Exception as failure:
        error = str(failure)
    receipt = {"argv": config["argv"], "executable_sha256": config["executable_sha256"],
               "exit_code": code, "duration_ns": time.perf_counter_ns() - start,
               "stdout_base64": base64.b64encode(stdout.read_bytes() if stdout.exists() else b"").decode(),
               "stderr_base64": base64.b64encode(stderr.read_bytes() if stderr.exists() else b"").decode(),
               "leftovers": leftovers, "timed": False}
    if error:
        receipt["harness_error"] = error
    (output / "container-prepare.json").write_text(json.dumps(receipt))
    return receipt


def finish_preparation(receipt, output):
    """Keep the original receipt in PID-1 memory, and reject candidate edits."""
    streams = {output / "container-prepare.stdout": base64.b64decode(receipt["stdout_base64"]),
               output / "container-prepare.stderr": base64.b64decode(receipt["stderr_base64"])}
    path = output / "container-prepare.json"
    expected = {**streams, path: json.dumps(receipt).encode()}
    if any(file.is_symlink() or not file.is_file() or file.read_bytes() != data
           for file, data in expected.items()):
        receipt["harness_error"] = "Container preparation receipt changed during execution"
    for file, data in streams.items():
        if file.is_symlink():
            file.unlink()
        file.write_bytes(data)
    if path.is_symlink():
        path.unlink()
    path.write_text(json.dumps(receipt))


def main():
    if os.getpid() != 1:
        raise RuntimeError("Shim must be container PID 1")
    request = Path(sys.argv[1])
    steps = json.loads(request.read_text())
    output = request.parent
    preparation = None
    if isinstance(steps, dict):
        output = Path(steps["output"])
        preparation = prepare_container(steps["container_prepare"], output)
        if preparation.get("harness_error"):
            return 2
        steps = steps["steps"]
    total_ns = 0
    for i, step in enumerate(steps):
        with (output / f"step-{i}.stdout").open("wb") as out, (
                output / f"step-{i}.stderr").open("wb") as err:
            import contextlib
            with contextlib.ExitStack() as stack:
                stdin = subprocess.DEVNULL
                if step.get("stdin_file") is not None:
                    target = Path(step["stdin_file"]).resolve()
                    if not target.is_relative_to(output.resolve()):
                        raise ValueError("stdin path escapes scratch")
                    stdin = stack.enter_context(target.open("rb"))
                # Open the protected input stream before the process-start clock.
                cpu = {"start": cpu_usage_usec()}
                t0 = time.perf_counter_ns()
                try:
                    child = subprocess.Popen(step["argv"], cwd=step["cwd"], env=step["env"],
                                             stdin=stdin, stdout=out, stderr=err,
                                             start_new_session=True)
                except OSError as error:
                    (output / f"step-{i}.json").write_text(json.dumps({"harness_error": str(error)}))
                    return 2
            workers = {}
            operation_leftovers = []
            kernel_exiting = []

            def cpu_start():
                workers.update(worker_tasks(child.pid))
                cpu["start"] = cpu_usage_usec()

            def cpu_end():
                cpu["end"] = cpu_usage_usec()

            def before_export():
                signal_all(signal.SIGSTOP)
                operation_leftovers.extend(record for identity, record in worker_tasks(child.pid, kernel_exiting).items()
                                           if identity not in workers)

            code, resident_t0, t1, protocol = execute_child(
                child, step["timeout"], step.get("resident", False),
                before_export=before_export,
                measurement_start=cpu_start, measurement_end=cpu_end)
            if not step.get("resident", False):
                cpu_end()
            if step.get("resident", False):
                t0 = resident_t0
            total_ns += t1 - t0
            signal_all(signal.SIGSTOP)
            leftovers = list(operation_leftovers)
            for proc in Path("/proc").iterdir():
                if proc.name.isdigit() and int(proc.name) != 1:
                    record = leftover_snapshot(proc, kernel_exiting)
                    if record is not None:
                        leftovers.append(record)
            signal_all(signal.SIGKILL)
            child.wait()
            while True:
                try:
                    os.waitpid(-1, 0)
                except ChildProcessError:
                    break
        for leftover in leftovers:
            leftover.update(step_index=i, observation_boundary=
                            "resident-result-stop" if step.get("resident", False) else "process-exit")
        telemetry = {"leftover_count": len(leftovers), "execution_protocol": protocol,
                     "kernel_exiting_tasks": kernel_exiting,
                     "main_pid": child.pid,
                     "exit_observation": child.optimbench_exit_observation,
                     "operation_cpu_time_usec": cpu_delta(cpu.get("start"), cpu.get("end"))}
        for name in ("memory.peak", "cpu.stat"):
            try:
                telemetry[name] = (Path("/sys/fs/cgroup") / name).read_text()
            except OSError:
                pass
        (output / f"step-{i}.json").write_text(json.dumps({
            "exit_code": code, "t0_ns": t0, "t1_ns": t1, "leftovers": leftovers,
            "telemetry": telemetry}))
    if preparation is not None:
        finish_preparation(preparation, output)
    (output / "case-result.json").write_text(json.dumps({"case_time_ns": total_ns}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
