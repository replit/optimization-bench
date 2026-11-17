#!/usr/bin/env python3
"""Shared public workload runner. Local execution reports results, never timings."""
import argparse
import base64
import ctypes
from dataclasses import dataclass, field, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import select
import shutil
import signal
import subprocess
import struct
import sys
import tempfile
import threading
import time

sys.modules.setdefault("runner", sys.modules[__name__])


class HarnessError(RuntimeError):
    """Invalid infrastructure/input run, not a candidate verdict."""


def canonical_json(value, ensure_ascii=True):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=ensure_ascii).encode("utf-8")


def b64(value):
    return base64.b64encode(value).decode("ascii")


def stderr_contract(raw):
    """CLI diagnostics, not implementation stack frames or process addresses.

    Ordinary diagnostics retain their text. Recognized runtime traces retain
    every distinct error/panic headline (including causes), never file contents,
    stdout, exit status or side effects. Unknown trace formats stay conservative.
    """
    text = raw.decode("utf-8", "surrogateescape") if isinstance(raw, bytes) else raw
    if not isinstance(text, str):
        raise TypeError("stderr must be bytes or text")
    text = re.sub(r"0x[0-9a-fA-F]{4,}", "0x_", text)
    lines = text.splitlines()
    nonempty = [line for line in lines if line.strip()]
    kind, headlines = ("diagnostic" if nonempty else "empty"), nonempty
    go = [re.sub(r"\s*\[(?:recovered|repanicked|recovered,\s*repanicked)\]\s*$", "", line.strip())
          for line in lines if re.match(r"^\s*(?:panic:|fatal error:)", line)]
    if go:
        kind = "fatal" if any(line.startswith("fatal error:") for line in go) else "panic"
        if any(re.match(r"^\s*goroutine \d+\s+\[", line) for line in lines):
            first = next(i for i, line in enumerate(lines)
                         if re.match(r"^\s*(?:panic:|fatal error:)", line))
            headlines = [line for line in lines[:first] if line.strip()] + go
    elif any(re.match(r"^thread .+ panicked at ", line) for line in lines):
        kind, headlines = "panic", []
        collecting = False
        for line in lines:
            match = re.match(r"^thread .+ panicked at (.+)", line)
            if match:
                tail = match[1]
                old = re.match(r"'(.*)', .+:\d+(?::\d+)?\s*$", tail)
                if old:
                    headlines.append("panic: " + old[1])
                    collecting = False
                elif re.search(r":\d+(?::\d+)?:?\s*$", tail):
                    collecting = True
                else:
                    headlines.append("panic: " + tail)
                    collecting = False
            elif collecting and line.strip() and not line.startswith(("note:", "stack backtrace:")):
                headlines.append("panic: " + line.strip())
                collecting = False
            elif line.startswith(("note:", "stack backtrace:")):
                collecting = False
        if not headlines:
            # An unrecognized panic format must not erase evidence.
            kind, headlines = "diagnostic", nonempty
    else:
        exception = re.compile(
            r"^(?:[\w.]+(?:Error|Exception|Failure|Fault)|Error|Exception|"
            r"KeyboardInterrupt|SystemExit|StopIteration)(?::|$)")
        java = [re.sub(r'^Exception in thread "[^"]*"\s+', "", line)
                for line in nonempty if re.match(r'^Exception in thread "[^"]*"\s+', line)]
        terminal = [line.strip() for line in lines
                    if exception.match(line) or line.startswith(("Caused by:", "Suppressed:"))]
        if java:
            kind, headlines = "exception", java + terminal
        elif terminal and (any("Traceback (most recent call last)" in line for line in lines)
                           or any(re.match(r"^\s+at\s+\S+", line) for line in lines)):
            kind, headlines = "exception", terminal
        elif terminal and all(exception.match(line) or line.startswith(("Caused by:", "Suppressed:"))
                              for line in nonempty):
            kind, headlines = "exception", terminal
        elif text.startswith("terminate called after throwing"):
            kind = "exception"
            headlines = [line for line in nonempty
                         if line.startswith(("terminate called", "  what():", "what():"))]
    # Duplicate recover/re-panic headers do not create a different error.
    distinct = list(dict.fromkeys(headlines))
    encoded = [line.encode("utf-8", "surrogateescape") for line in distinct]
    return {"version": "cli-stderr-contract-v1", "class": kind,
            "headlines": [line.decode("utf-8", "backslashreplace") for line in encoded],
            "headlines_base64": [b64(line) for line in encoded]}


def normalize_stderr(raw):
    """Keep ordinary bytes stable; collapse only recognized diagnostic traces."""
    as_bytes = isinstance(raw, bytes)
    text = raw.decode("utf-8", "surrogateescape") if as_bytes else raw
    contract = stderr_contract(raw)
    if contract["class"] == "empty":
        result = ""
    elif contract["class"] == "diagnostic":
        result = re.sub(r"0x[0-9a-fA-F]{4,}", "0x_", text)
    else:
        result = b"\n".join(base64.b64decode(line) for line in contract["headlines_base64"])
        result += b"\n" if text.endswith("\n") else b""
        result = result.decode("utf-8", "surrogateescape")
    return result.encode("utf-8", "surrogateescape") if as_bytes else result


def normalize_cli_observation(value):
    """Normalize protected CLI record schemas, not arbitrary library JSON data."""
    if isinstance(value, list):
        return [normalize_cli_observation(row) for row in value]
    if not isinstance(value, dict):
        return value
    result = dict(value)
    is_cli = (type(value.get("exit_code", value.get("code"))) is int and
              (any(name in value for name in ("stdout", "stdout_base64", "stdout_b64", "stdout_hex"))
               or "result" in value and "leftovers" in value))
    if is_cli:
        for name, encoding in (("stderr", "text"), ("stderr_base64", "base64"),
                               ("stderr_b64", "base64"), ("stderr_hex", "hex")):
            if name not in value:
                continue
            raw = value[name]
            if encoding == "base64":
                result[name] = b64(normalize_stderr(base64.b64decode(raw, validate=True)))
            elif encoding == "hex":
                result[name] = normalize_stderr(bytes.fromhex(raw)).hex()
            else:
                result[name] = normalize_stderr(raw)
        if "stderr_lines_base64" in value:
            raw = b"\n".join(base64.b64decode(line, validate=True)
                             for line in value["stderr_lines_base64"])
            result["stderr_lines_base64"] = [
                b64(line) for line in sorted(normalize_stderr(raw).splitlines())]
    # Known CLI result containers only. Do not recurse through files/stdout or
    # treat an application's JSON document as diagnostic metadata.
    for name in ("result", "results", "invocations"):
        if name in value:
            result[name] = normalize_cli_observation(value[name])
    return result


def contained(root, name):
    root, name = Path(root).resolve(), Path(name)
    path = (root / name).resolve()
    if name.is_absolute() or ".." in name.parts or not path.is_relative_to(root):
        raise HarnessError("Path escapes root")
    return path


def inventory_sha256(root, exclude_vcs=False):
    files, links = {}, {}
    for path in Path(root).rglob("*"):
        if exclude_vcs and ".git" in path.relative_to(root).parts:
            continue
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            contained(root, name)
            links[name] = os.readlink(path)
        elif path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(canonical_json({"files": files, "symlinks": links})).hexdigest()


def snapshot(tree, skip_git=False):
    files, links = {}, {}
    for path in sorted(Path(tree).rglob("*")):
        relative = path.relative_to(tree)
        if skip_git and ".git" in relative.parts:
            continue
        if path.is_symlink():
            links[relative.as_posix()] = os.readlink(path)
        elif path.is_file():
            files[relative.as_posix()] = b64(path.read_bytes())
    return files, links


@dataclass
class Case:
    id: str
    data: dict


@dataclass
class Step:
    argv: list
    cwd: str = "."
    env: dict = field(default_factory=dict)
    timeout: float = 600
    resident: bool = False
    stdin_file: str | None = None
    home_cwd: bool = False

    def __post_init__(self):
        if (not isinstance(self.argv, list) or not self.argv or
                any(not isinstance(arg, str) or "\0" in arg for arg in self.argv) or
                not isinstance(self.timeout, (int, float)) or self.timeout <= 0):
            raise HarnessError("Invalid step command or timeout")


@dataclass
class StepResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    t0_ns: int | None = None
    t1_ns: int | None = None
    leftovers: list = field(default_factory=list)
    telemetry: dict = field(default_factory=dict)

    def streams(self):
        return {"exit_code": self.exit_code, "stdout_base64": b64(self.stdout),
                "stderr_base64": b64(self.stderr)}


def documents(inputs):
    try:
        manifest = json.loads((Path(inputs) / "manifest.json").read_text())
        docs = [d for batches in manifest["splits"].values() for b in batches for d in b["docs"]]
        seen = set()
        for doc in docs:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", doc["path"]) or doc["path"] in seen:
                raise HarnessError("Invalid or duplicate result id")
            seen.add(doc["path"])
            if not re.fullmatch(r"[a-f0-9]{64}", doc["sha256"]):
                raise HarnessError("Invalid content address")
            directory = Path(inputs) / "files" if (Path(inputs) / "files").is_dir() else Path(inputs)
            raw = contained(directory, doc["sha256"]).read_bytes()
            if len(raw) != doc["bytes"] or hashlib.sha256(raw).hexdigest() != doc["sha256"]:
                raise HarnessError("Input checksum mismatch")
        return docs
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HarnessError("Invalid input manifest: " + str(error)) from error


def project_cases(inputs):
    directory = Path(inputs) / "files" if (Path(inputs) / "files").is_dir() else Path(inputs)
    try:
        return [Case(d["path"], json.loads((directory / d["sha256"]).read_text()))
                for d in documents(inputs)]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HarnessError("Invalid project case: " + str(error)) from error


@dataclass
class Context:
    scratch: Path
    build: Path
    inputs: Path
    projects: Path
    workload: Path
    seed: int | None = None
    docker: bool = False

    snapshot = staticmethod(snapshot)
    canonical_json = staticmethod(canonical_json)

    def target(self, path):
        text = str(path)
        if self.docker:
            for source, target in ((self.scratch, "/scratch"), (self.build, "/app/build"),
                                   (self.inputs, "/app/workload/inputs"),
                                   (self.workload, "/app/workload")):
                if text == str(source) or text.startswith(str(source) + "/"):
                    return target + text[len(str(source)):]
        return text

    def copy_project(self, name, inventory_sha256_expected, destination="project", exclude_vcs=False):
        source = contained(self.projects, name)
        if not source.is_dir() or inventory_sha256(source, exclude_vcs) != inventory_sha256_expected:
            raise HarnessError("Project inventory mismatch: " + name)
        target = contained(self.scratch, destination)
        shutil.copytree(source, target, symlinks=True)
        return target

    def batch(self, docs):
        directory = self.inputs / "files" if (self.inputs / "files").is_dir() else self.inputs
        rows = [{k: v for k, v in d.items() if k not in ("expect_len", "expect_sha", "bytes")}
                for d in docs]
        if self.seed is not None:
            random.Random(self.seed).shuffle(rows)
        for doc in docs:
            data = contained(directory, doc["sha256"]).read_bytes()
            if len(data) != doc["bytes"] or hashlib.sha256(data).hexdigest() != doc["sha256"]:
                raise HarnessError("Input changed during preparation")
            (self.scratch / doc["sha256"]).write_bytes(data)
        (self.scratch / "cases.json").write_bytes(canonical_json(rows))

    def batch_results(self, docs, results):
        result = results[0]
        abnormal = result.exit_code != 0 or result.stdout or result.stderr
        outputs = {}
        for doc in docs:
            path = self.scratch / (doc["path"] + ".out")
            data = path.read_bytes() if path.is_file() and not path.is_symlink() else None
            # Preserve historical successful bytes, but compare every process failure/stream.
            outputs[doc["path"]] = (data if not abnormal and data is not None else
                canonical_json({"format": "workload-failure-v1", **result.streams(),
                                "output_base64": None if data is None else b64(data)}))
        return outputs


def base_env(scratch, index):
    home, tmp = scratch / f"home-{index}", scratch / f"tmp-{index}"
    home.mkdir()
    tmp.mkdir()
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
            "TMPDIR": str(tmp), "LC_ALL": "C.UTF-8", "TZ": "UTC"}


def processes():
    rows = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] not in ("Z", "X"):
                rows[int(path.name)] = (int(stat[1]), int(stat[2]),
                    (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace"))
        except OSError:
            pass
    return rows


def stop_leftovers(pgid):
    """Rule D: freeze before inspecting streams; include setsid-detached descendants."""
    found = {}
    while True:
        rows, descendants = processes(), {os.getpid()}
        while True:
            new = {p for p, (parent, group, _) in rows.items()
                   if parent in descendants or group == pgid}
            if new <= descendants:
                break
            descendants.update(new)
        live = descendants - {os.getpid()}
        for pid in live:
            try:
                os.kill(pid, signal.SIGSTOP)
                found[pid] = {"pid": pid, "cmdline": rows[pid][2]}
            except ProcessLookupError:
                pass
        if live <= found.keys():
            # A second scan catches children forked during the first scan.
            again = processes()
            if all(p in found or (parent not in descendants and group != pgid)
                   for p, (parent, group, _) in again.items() if p != os.getpid()):
                break
    for pid in found:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    while True:
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            break
    return list(found.values())


class LocalExecutor:
    def run(self, steps, ctx, environment):
        if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0):
            raise HarnessError("Cannot become a subreaper")
        results = []
        for index, step in enumerate(steps):
            env = {**environment, **step.env, **base_env(ctx.scratch, index)}
            if step.home_cwd:
                env["HOME"] = str(contained(ctx.scratch, step.cwd))
            out, err = ctx.scratch / f"step-{index}.stdout", ctx.scratch / f"step-{index}.stderr"
            try:
                import contextlib
                with contextlib.ExitStack() as stack:
                    stdout = stack.enter_context(out.open("wb"))
                    stderr = stack.enter_context(err.open("wb"))
                    stdin = (stack.enter_context(contained(ctx.scratch, step.stdin_file).open("rb"))
                             if step.stdin_file is not None else subprocess.DEVNULL)
                    child = subprocess.Popen(step.argv, cwd=contained(ctx.scratch, step.cwd), env=env,
                                             stdin=stdin, stdout=stdout, stderr=stderr,
                                             start_new_session=True)
                    code, _, _, protocol = execute_child(child, step.timeout, step.resident)
                    leftovers = stop_leftovers(child.pid)
                    child.wait()
                results.append(StepResult(code, out.read_bytes(), err.read_bytes(), leftovers=leftovers,
                                          telemetry={"execution_protocol": protocol,
                                                     "exit_observation": child.optimbench_exit_observation}))
            except OSError as error:
                raise HarnessError("Cannot execute step: " + str(error)) from error
        return results


def run_case(module, case, ctx, executor):
    if not ctx.build.is_dir():
        raise HarnessError("Missing build")
    try:
        steps = module.prepare(case, ctx)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HarnessError("Case preparation failed: " + str(error)) from error
    results = executor.run(steps, ctx, module.ENV)
    normalized = []
    for result in results:
        stderr = normalize_stderr(result.stderr)
        if stderr != result.stderr:
            result = replace(result, stderr=stderr, telemetry={
                **result.telemetry, "stderr_contract": stderr_contract(result.stderr),
                "raw_stderr_base64": b64(result.stderr),
                "raw_stderr_sha256": hashlib.sha256(result.stderr).hexdigest()})
        normalized.append(result)
    results = normalized
    return module.collect(case, ctx, results), results


def bundle(files):
    """Named-file framing used by the original public complete-output commitments."""
    return b"".join(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data
                    for name, data in sorted(files.items()))


def unbundle(data):
    files = {}
    while data:
        name, size, tail = data.split(b"\0", 2)
        length = int(size)
        name = name.decode()
        if length < 0 or length > len(tail) or name in files or "/" in name:
            raise ValueError("Invalid named-file result")
        files[name], data = tail[:length], tail[length:]
    return files


def bundled_size(data):
    """Validate named-file framing without copying the payload or remaining tail."""
    offset, total = 0, 0
    names = set()
    while offset < len(data):
        name_end = data.index(b"\0", offset)
        size_end = data.index(b"\0", name_end + 1)
        name = data[offset:name_end].decode()
        length = int(data[name_end + 1:size_end])
        offset = size_end + 1
        if length < 0 or length > len(data) - offset or name in names or "/" in name:
            raise ValueError("Invalid named-file result")
        names.add(name)
        total += length
        offset += length
    return total


def fingerprint(data, encoding=None):
    """Default is exact bytes; named-file length excludes framing, as in legacy receipts."""
    try:
        size = bundled_size(data) if encoding == "named-files-v1" else len(data)
    except (ValueError, UnicodeError):
        size = len(data)  # Candidate failure envelopes remain compared outputs.
    return size, hashlib.sha256(data).hexdigest()


def references(module, inputs):
    """Optional adapter reference enumeration; default authenticates standard documents."""
    return module.references(inputs) if hasattr(module, "references") else documents(inputs)


def locked_cases(inputs):
    """Authenticate an exact staged public file allowlist, then enumerate protected cases."""
    inputs = Path(inputs)
    descriptor = json.loads((inputs / "manifest.json").read_text())
    if descriptor.get("format") != "optimbench-locked-cases-v1":
        raise HarnessError("Unsupported or unavailable input set")
    workload = inputs.parent
    manifest = json.loads(contained(workload, descriptor["cases"]).read_text())
    lock = json.loads(contained(workload, descriptor["lock"]).read_text())
    for name, record in lock["files"].items():
        original = inputs / "data" / name
        if any(p.is_symlink() for p in [original, *original.parents]
               if p == inputs or p.is_relative_to(inputs)):
            raise HarnessError("Symlink in locked input: " + name)
        path = contained(inputs / "data", name)
        if path.is_symlink() or not path.is_file():
            raise HarnessError("Missing locked input: " + name)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != record["bytes"] or digest != record["sha256"]:
            raise HarnessError("Locked input mismatch: " + name)
    cases = [Case(row["id"], row) for row in manifest["cases"]]
    if (len({case.id for case in cases}) != len(cases) or
            any(not re.fullmatch(r"[A-Za-z0-9_.-]+", case.id) for case in cases)):
        raise HarnessError("Invalid or duplicate case identity")
    return cases


def bundle_references(inputs):
    inputs = Path(inputs)
    descriptor = json.loads((inputs / "manifest.json").read_text())
    if descriptor.get("format") != "optimbench-locked-cases-v1":
        raise HarnessError("Unavailable reference set")
    rows = json.loads(contained(inputs.parent, descriptor["references"]).read_text())
    return [{"path": r["id"], "expect_len": r["bytes"], "expect_sha": r["sha256"]}
            for r in rows]


def collected_bundle(case, ctx, results, files):
    result = results[0]
    protocol = result.telemetry.get("execution_protocol")
    abnormal = (result.exit_code != 0 or result.stdout or result.stderr or
                protocol not in (None, "process", "complete"))
    if not abnormal:
        try:
            return {case.id: bundle({name: path.read_bytes() for name, path in files.items()
                                    if path.is_file() and not path.is_symlink()})} if all(
                                        p.is_file() and not p.is_symlink() for p in files.values()) else {
                                            case.id: canonical_json({"format": "workload-failure-v1",
                                                                     **result.streams(), "missing_output": True})}
        except OSError:
            abnormal = True
    return {case.id: canonical_json({"format": "workload-failure-v1", **result.streams(),
                                    "execution_protocol": protocol})}


def compare_results(module, expected, actual):
    """Trusted, untimed numeric hook; never permit missing/extra or failed results."""
    if expected.keys() != actual.keys():
        return False
    compare = getattr(module, "compare", None)
    for name in expected:
        a, b = expected[name], actual[name]
        if fingerprint(a) == fingerprint(b):
            continue
        if compare is None:
            return False
        # Failures are exact compared, never passed to a tolerant comparator.
        if b'"format":"workload-failure-v1"' in a[:100] or b'"format":"workload-failure-v1"' in b[:100]:
            return False
        try:
            if compare(a, b) is not True:
                return False
        except (ValueError, TypeError, KeyError, OverflowError, IndexError, UnicodeError,
                SyntaxError, struct.error):
            return False
    return True


def wait_event(child, timeout):
    """Wait for a main-process exit or SIGSTOP, bounded by a trusted wall-clock alarm."""
    def expired(signum, frame):
        raise subprocess.TimeoutExpired(child.args, timeout)
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        _, status = os.waitpid(child.pid, os.WUNTRACED)
        if os.WIFSTOPPED(status):
            return os.WSTOPSIG(status)
        child.returncode = os.waitstatus_to_exitcode(status)
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def freeze_for_export(main_pid):
    """Freeze descendants (including reparented/detached ones), not the trusted exporter."""
    while True:
        rows = processes()
        descendants = {os.getpid()}
        while True:
            new = {pid for pid, (parent, group, _) in rows.items()
                   if parent in descendants or group == main_pid}
            if new <= descendants:
                break
            descendants.update(new)
        live = descendants - {os.getpid(), main_pid}
        for pid in live:
            try:
                os.kill(pid, signal.SIGSTOP)
            except ProcessLookupError:
                pass
        again = processes()
        if all(pid in live or pid in (os.getpid(), main_pid) or
               (parent not in descendants and group != main_pid)
               for pid, (parent, group, _) in again.items()):
            return


def observe_exit(child, timeout):
    """Observe exit without Popen's timeout polling; leave timeout cleanup to Rule D."""
    observation = {"method": "pidfd-poll", "fallback_reason": None}
    child.optimbench_exit_observation = observation
    try:
        fd = os.pidfd_open(child.pid, 0)
    except (AttributeError, OSError) as error:
        observation.update(method="threaded-blocking-waitpid",
                           fallback_reason=f"{type(error).__name__}: {error}")
        code = child.poll()
        if code is not None:
            return code, time.perf_counter_ns()
        finished = threading.Event()
        outcome = {}
        def reap():
            try:
                outcome["code"] = child.wait()  # Blocking waitpid, never timeout polling.
                outcome["end"] = time.perf_counter_ns()
            except BaseException as exception:
                outcome["error"] = exception
            finally:
                finished.set()
        threading.Thread(target=reap, name="optimbench-exit-wait", daemon=True).start()
        if not finished.wait(timeout):
            raise subprocess.TimeoutExpired(child.args, timeout)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["code"], outcome["end"]
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            try:
                # Even a zero deadline first checks an already-exited child,
                # matching Popen.wait(timeout=0) without polling for completion.
                events = poller.poll(max(0, int(remaining * 1000 + .999)))
            except InterruptedError:
                continue
            if not events:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(child.args, timeout)
                continue
            end = time.perf_counter_ns()
            return child.wait(), end  # pidfd readability guarantees exit; no polling.
    finally:
        os.close(fd)


def execute_child(child, timeout, resident=False, before_export=None,
                  measurement_start=None, measurement_end=None):
    """Resident protocol: untimed ready stop, measured operation stop, untimed export."""
    start = time.perf_counter_ns()
    end = None
    protocol = "process"
    child.optimbench_exit_observation = {
        "method": "waitpid-sigstop" if resident else "pending", "fallback_reason": None}
    try:
        if resident:
            if wait_event(child, timeout) != signal.SIGSTOP:
                return child.returncode, start, time.perf_counter_ns(), "missing-ready-stop"
            if measurement_start:
                measurement_start()
            start = time.perf_counter_ns()
            os.kill(child.pid, signal.SIGCONT)
            if wait_event(child, timeout) != signal.SIGSTOP:
                return child.returncode, start, time.perf_counter_ns(), "missing-result-stop"
            end = time.perf_counter_ns()
            if measurement_end:
                measurement_end()
            (before_export or (lambda: freeze_for_export(child.pid)))()
            os.kill(child.pid, signal.SIGCONT)
            code = child.wait(timeout=timeout)
            return code, start, end, "complete"
        code, end = observe_exit(child, timeout)
    except subprocess.TimeoutExpired:
        code, protocol = None, "timeout"
    return code, start, end if end is not None else time.perf_counter_ns(), protocol


def summarize_outputs(data, summaries, output_directory, encoding):
    """Keep only small receipts between cases; optional raw outputs go to disk."""
    for name, value in data.items():
        if name in summaries or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise HarnessError("Invalid or duplicate collected result")
        if output_directory:
            (output_directory / (name + ".out")).write_bytes(value)
        summaries[name] = fingerprint(value, encoding)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, default=Path(__file__).with_name("task.py"))
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--projects", type=Path, default=Path("/app/projects"))
    parser.add_argument("--outputs", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--executor", choices=["local", "docker"], default="local")
    parser.add_argument("--image")
    parser.add_argument("--cpuset")
    parser.add_argument("--memory", default="16g")
    parser.add_argument("--pids-limit", type=int, default=512)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("workload_task", args.task)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workload = args.task.resolve().parent
    executor = LocalExecutor()
    if args.executor == "docker":
        try:
            from docker_executor import DockerExecutor
        except ImportError as error:
            raise HarnessError("Docker prototype is host-side tools/runner/docker_executor.py") from error
        executor = DockerExecutor(args.image, args.cpuset, args.memory, args.pids_limit)
    outputs = {}
    if args.outputs:
        args.outputs.mkdir(parents=True, exist_ok=True)
        if any(args.outputs.iterdir()):
            raise HarnessError("Output directory must be empty")
    for case in module.cases(args.inputs or workload / "inputs"):
        with tempfile.TemporaryDirectory(prefix="optimbench-case-") as temporary:
            ctx = Context(Path(temporary), args.build.resolve(), (args.inputs or workload / "inputs").resolve(),
                          args.projects.resolve(), workload, args.seed, args.executor == "docker")
            data, results = run_case(module, case, ctx, executor)
            summarize_outputs(data, outputs, args.outputs, getattr(module, "RESULT_ENCODING", None))
            if args.outputs:
                target = args.outputs / case.id
                target.mkdir()
                for index, result in enumerate(results):
                    (target / f"step-{index}.stdout").write_bytes(result.stdout)
                    (target / f"step-{index}.stderr").write_bytes(result.stderr)
                (target / "steps.json").write_bytes(canonical_json([
                    {"exit_code": r.exit_code, "t0_ns": r.t0_ns, "t1_ns": r.t1_ns,
                     "leftovers": r.leftovers, "telemetry": r.telemetry} for r in results]))
            del data, results
    for name, (size, digest) in sorted(outputs.items()):
        print(f"{name}\t{size}\t{digest}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print("HarnessError: " + str(error), file=sys.stderr)
        raise SystemExit(2)
