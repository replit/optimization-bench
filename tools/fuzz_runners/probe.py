"""Execute tool capability controls INSIDE an isolated, untimed worker.

This is deliberately not a task adapter or a qualification campaign. A successful
fixture proves only that a tool works in this container/runtime. Failure emits
unsupported plus raw evidence, never candidate regression or a silently skipped
pass. Invoke via smoke.py to use the repository's shared DockerExecutor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from coverage import normalize


class Probe:
    def __init__(self, language, out, extended_rust=False):
        self.language, self.out = language, Path(out)
        self.extended_rust = extended_rust
        self.runtime_inputs = self._runtime_inputs()
        self.out.mkdir(parents=True, exist_ok=True)
        self.work = self.out / "work"
        shutil.copytree(HERE / "fixtures", self.work, dirs_exist_ok=True)
        (self.work / "corpus").mkdir(exist_ok=True)
        (self.work / "tmp").mkdir(exist_ok=True)
        (self.work / "corpus/zero").write_bytes(b"\0")
        (self.work / "corpus/high").write_bytes(b"\xff")
        self.steps, self.capabilities = [], {}
        self.env = {**os.environ, "HOME": str(self.work), "TMPDIR": str(self.work / "tmp"),
                    "PATH": os.environ.get("FUZZ_TOOL_PATH", "/opt/fuzz-python/bin:/usr/local/cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin"),
                    "RUSTUP_HOME": "/usr/local/rustup", "CARGO_NET_OFFLINE": "true",
                    "NODE_PATH": "/opt/fuzz-runners/node_modules",
                    "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
                    "GOPATH": "/go", "GOMODCACHE": "/go/pkg/mod",
                    "ASAN_OPTIONS": "abort_on_error=1:detect_leaks=1",
                    "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
                    "TSAN_OPTIONS": "halt_on_error=1", "MSAN_OPTIONS": "halt_on_error=1",
                    "GOCACHE": str(self.work / "go-cache"), "CARGO_TARGET_DIR": str(self.work / "target"),
                    "PROPTEST_RNG_SEED": "1729", "PROPTEST_CASES": "100"}

    def _runtime_inputs(self):
        result = {}
        for name in ("probe.py", "coverage.py", "remap.cjs", "fixtures", "go", "rust/checks"):
            path = HERE / name
            paths = path.rglob("*") if path.is_dir() else [path]
            for item in paths:
                if item.is_file() and not {"target", "__pycache__"}.intersection(item.parts):
                    result[item.relative_to(HERE).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
        return result

    def run(self, argv, *, env=None, timeout=180):
        number = len(self.steps)
        started = time.monotonic()
        try:
            completed = subprocess.run(argv, cwd=self.work, env={**self.env, **(env or {})},
                                       capture_output=True, timeout=timeout)
            code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
            error = None
        except (subprocess.TimeoutExpired, OSError) as exc:
            code, stdout, stderr = None, getattr(exc, "stdout", b"") or b"", getattr(exc, "stderr", b"") or b""
            error = str(exc)
        paths = []
        for name, raw in (("stdout", stdout), ("stderr", stderr)):
            path = self.out / f"step-{number}.{name}"
            path.write_bytes(raw)
            paths.append({"path": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        record = {"argv": argv, "environment": env or {}, "exit_code": code,
                  "elapsed_seconds": time.monotonic() - started, "timeout_seconds": timeout,
                  "artifacts": paths}
        if error:
            record["infrastructure_error"] = error
        self.steps.append(record)
        return code == 0, (stdout + stderr).decode(errors="replace")

    def capability(self, name, action):
        start = len(self.steps)
        reason = None
        try:
            success, reason = action()
        except Exception as exc:
            success, reason = False, f"{type(exc).__name__}: {exc}"
        self.capabilities[name] = {
            "status": "validated" if success else "unsupported",
            "reason": reason or ("fixture control succeeded; not task qualification" if success else "fixture failed; inspect raw step logs"),
            "evidence": list(range(start, len(self.steps))),
        }

    def command(self, argv, **kwargs):
        ok, _ = self.run(argv, **kwargs)
        return ok, None

    def native(self):
        cc = ["clang-16", "-g", "-O1", "-fno-omit-frame-pointer"]
        self.capability("compiler", lambda: self.command(["clang-16", "--version"]))
        self.capability("libfuzzer", lambda: self._chain([
            cc + ["-DFUZZ", "-fsanitize=fuzzer,address,undefined", "native.c", "-o", "fuzzer"],
            ["./fuzzer", "corpus", "-seed=1729", "-runs=100", "-timeout=5"]]))
        self.capability("afl++", lambda: self._chain([
            ["afl-clang-fast", "-g", "-O1", "native.c", "-o", "afl-target"],
            ["afl-fuzz", "-V", "2", "-s", "1729", "-i", "corpus", "-o", "afl-output", "--", "./afl-target", "@@"]],
            env={"AFL_SKIP_CPUFREQ": "1", "AFL_NO_AFFINITY": "1", "AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES": "1",
                 "ASAN_OPTIONS": "abort_on_error=1:detect_leaks=0:symbolize=0",
                 "MSAN_OPTIONS": "halt_on_error=1:exit_code=86:symbolize=0"}))
        for name, flag, define, pattern in [
            ("asan", "address", "ASAN_BAD", "AddressSanitizer: heap-buffer-overflow"),
            ("ubsan", "undefined", "UBSAN_BAD", "runtime error: signed integer overflow"),
            ("tsan", "thread", "THREAD", "ThreadSanitizer: data race"),
            ("msan-fixture", "memory", "MSAN_BAD", "MemorySanitizer: use-of-uninitialized-value"),
        ]:
            self.capability(name, lambda flag=flag, define=define, pattern=pattern:
                            self._sanitizer(cc, flag, define, pattern))
        self.capabilities["msan-dependency-closure"] = {
            "status": "unsupported", "reason": "only C fixture checked; task dependency/C++ runtime closure requires separate instrumented build",
            "evidence": self.capabilities["msan-fixture"]["evidence"]}
        self.capability("llvm-coverage", lambda: self._native_coverage(cc))

    def _chain(self, commands, **kwargs):
        for command in commands:
            ok, _ = self.run(command, **kwargs)
            if not ok:
                return False, "compile/engine invocation failed; raw logs retained"
        return True, None

    def _sanitizer(self, cc, flag, define, pattern):
        base = cc + [f"-fsanitize={flag}", "-fno-sanitize-recover=all", "-fPIE", "-pie", "-pthread"]
        # Always run a safe control before accepting a negative control report.
        positive = ["-DTHREAD"] if flag == "thread" else []
        ok, _ = self.run(base + positive + ["native.c", "-o", "safe-" + flag])
        if not ok:
            return False, "sanitizer safe-control build failed"
        ok, _ = self.run(["./safe-" + flag])
        if not ok:
            return False, "sanitizer cannot run its safe control in this worker"
        negative = ["-D" + define] + (["-DNEGATIVE"] if flag == "thread" else [])
        ok, _ = self.run(base + negative + ["native.c", "-o", "bad-" + flag])
        if not ok:
            return False, "sanitizer negative-control build failed"
        ok, output = self.run(["./bad-" + flag])
        return (not ok and pattern in output), "safe and known-bad fixture report bound in raw logs"

    def _native_coverage(self, cc):
        ok, reason = self._chain([
            cc + ["-fprofile-instr-generate", "-fcoverage-mapping", "native.c", "-o", "covered"],
            ["./covered"]], env={"LLVM_PROFILE_FILE": str(self.work / "%p-%m.profraw")})
        if not ok:
            return ok, reason
        profiles = sorted(str(p) for p in self.work.glob("*.profraw"))
        ok, _ = self.run(["llvm-profdata-16", "merge", "-sparse", *profiles, "-o", "coverage.profdata"])
        if not ok:
            return False, "profile merge failed"
        ok, raw = self.run(["llvm-cov-16", "export", "./covered", "-instr-profile=coverage.profdata"])
        if not ok:
            return False, "LLVM export failed"
        (self.work / "llvm.json").write_text(raw)
        data = normalize("llvm", json.loads(raw), self.work)
        (self.work / "coverage-normalized.json").write_text(json.dumps(data, indent=2))
        branches = [c for record in data["files"].values() for key, c in record["branches"].items()
                    if int(key.split(":")[0]) in (8, 9)]
        return data["status"] == "available" and len(branches) >= 4 and all(c > 0 for c in branches), "all fixture classification branch outcomes must be covered"

    def python(self):
        self.capability("interpreter", lambda: self.command(["python", "--version"]))
        self.capability("hypothesis", lambda: self.command(["python", "python_property.py"]))
        self.capability("atheris", lambda: self.command(["python", "python_fuzz.py", "corpus", "-seed=1729", "-runs=100", "-timeout=5"]))
        self.capability("coverage.py-branch", self._python_coverage)
        self.capability("coverage.py-child-process", self._python_children)
        self.capabilities["native-extension-sanitizers"] = {
            "status": "unsupported", "reason": "requires task-specific native module build plus complete instrumented dependency closure",
            "evidence": []}

    def _python_coverage(self):
        ok, reason = self._chain([
            ["python", "-m", "coverage", "run", "--branch", "python_property.py"],
            ["python", "-m", "coverage", "json", "-o", "python-coverage.json"]])
        if not ok:
            return ok, reason
        data = normalize("python", json.loads((self.work / "python-coverage.json").read_text()), self.work)
        (self.work / "coverage-normalized.json").write_text(json.dumps(data, indent=2))
        record = data["files"]["python_target.py"]
        return len(record["branches"]) >= 4 and all(record["branches"].values()), "task fixture branch export checked"

    def _python_children(self):
        cfg = self.work / "child-coverage.rc"
        database = self.work / ".coverage-children"
        cfg.write_text("[run]\nbranch=True\nparallel=True\ndata_file=" + str(database) +
                       "\ninclude=" + str(self.work / "python_target.py") + "\n")
        code = ("import subprocess,sys; subprocess.run([sys.executable,'-c',"
                "'from python_target import classify; classify(-1)'],check=True);"
                "from python_target import classify; classify(1)")
        ok, _ = self.run(["python", "-c", code], env={"COVERAGE_PROCESS_START": str(cfg)})
        profiles = sorted(p.name for p in self.work.glob(".coverage-children.*"))
        if not ok or len(profiles) < 2:
            return False, "separate parent/child coverage exports unavailable; require startup hook in every interpreter"
        (self.work / "child-profile-inventory.json").write_text(json.dumps(profiles))
        ok, reason = self._chain([
            ["python", "-m", "coverage", "combine", "--data-file", str(database)],
            ["python", "-m", "coverage", "json", "--data-file", str(database), "-o", "python-child-coverage.json"]])
        if not ok:
            return ok, reason
        data = normalize("python", json.loads((self.work / "python-child-coverage.json").read_text()), self.work)
        (self.work / "child-coverage-normalized.json").write_text(json.dumps(data, indent=2))
        lines = data["files"]["python_target.py"]["lines"]
        return data["status"] == "available" and lines.get("3") == 1 and lines.get("6") == 1, "both parent positive and child negative branch bodies retained after combine"

    def javascript(self):
        self.capability("interpreter", lambda: self.command(["node", "--version"]))
        self.capability("fast-check", lambda: self.command(["node", "js_property.cjs"]))
        self.capability("jazzer.js", lambda: self.command([
            "/opt/fuzz-runners/node_modules/.bin/jazzer", "js_fuzz.cjs", "--", "corpus", "-seed=1729", "-runs=100", "-timeout=5"]))
        self.capability("source-mapped-istanbul", self._js_coverage)
        self.capabilities["native-addon-sanitizers"] = {
            "status": "unsupported", "reason": "task addon applicability and instrumented native dependency closure not established by managed-JS fixture",
            "evidence": []}

    def _js_coverage(self):
        ok, reason = self._chain([
            ["node", "js_coverage.cjs"],
            ["node", str(HERE / "remap.cjs"), "istanbul-raw.json", "istanbul-remapped.json"]])
        if not ok:
            return ok, reason
        data = normalize("istanbul", json.loads((self.work / "istanbul-remapped.json").read_text()), self.work)
        (self.work / "coverage-normalized.json").write_text(json.dumps(data, indent=2))
        record = data["files"]["js_target.cjs"]
        return len(record["branches"]) >= 4 and all(record["branches"].values()), "generated offset was remapped to original js_target.cjs"

    def go(self):
        (self.work / "native.c").unlink()
        shutil.copy(HERE / "go/go.mod", self.work / "go.mod")
        shutil.copy(HERE / "go/go.sum", self.work / "go.sum")
        # Negative race fixture is excluded from every positive run.
        (self.work / "go_race_test.go").rename(self.work / "go_race.txt")
        self.capability("compiler", lambda: self.command(["go", "version"]))
        self.capability("rapid", lambda: self.command(["go", "test", "-run=^TestProperty$", "-rapid.seed=1729", "-rapid.checks=100"]))
        self.capability("go-fuzz", lambda: self.command(["go", "test", "-run=^$", "-fuzz=^FuzzClassify$", "-fuzztime=2s", "-parallel=1"]))
        self.capability("go-checkptr", lambda: self.command(["go", "test", "-gcflags=all=-d=checkptr=2", "-run=^TestProperty$"]))
        self.capability("go-race", self._go_race)
        self.capability("go-coverage", self._go_coverage)
        shutil.copytree(HERE / "go", self.work / "branch-hooks")
        self.capability("go-explicit-edge-counter-runtime", lambda: self.command([
            "go", "-C", str(self.work / "branch-hooks"), "test", "-race", "./..."]))
        self.capabilities["go-branch-coverage"] = {
            "status": "unsupported", "reason": "native Go profiles lack branch counts; explicit source-bound edge instrumentation is required",
            "evidence": self.capabilities["go-coverage"]["evidence"]}

    def _go_race(self):
        ok, _ = self.run(["go", "test", "-race", "-run=^TestProperty$"])
        if not ok:
            return False, "safe Go race control failed"
        shutil.copy(self.work / "go_race.txt", self.work / "go_race_test.go")
        try:
            ok, text = self.run(["go", "test", "-race", "-run=^TestRace$"])
        finally:
            (self.work / "go_race_test.go").unlink()
        return (not ok and "WARNING: DATA RACE" in text), "safe and known-racy fixture logs retained"

    def _go_coverage(self):
        ok, _ = self.run(["go", "test", "-run=^TestProperty$", "-covermode=atomic", "-coverprofile=go-cover.txt"])
        if not ok:
            return False, "Go profile generation failed"
        data = normalize("go", (self.work / "go-cover.txt").read_text(), self.work,
                         path_mappings={"optimbench.invalid/fuzz-runners": str(self.work)})
        (self.work / "coverage-normalized.json").write_text(json.dumps(data, indent=2))
        return bool(data["files"]), "block coverage only; branch/executable-line support remains explicitly unsupported"

    def rust(self):
        # The immutable image cache is read-only to UID10000. Seed a disposable
        # writable Cargo cache; never chmod or mutate the trusted image cache.
        shutil.copytree("/usr/local/cargo/registry", self.work / "cargo-home/registry")
        self.env["CARGO_HOME"] = str(self.work / "cargo-home")
        self.capability("compiler", lambda: self.command(["rustc", "--version", "--verbose"]))
        self.capability("nightly", lambda: self.command(["rustc", "+nightly-2026-10-01", "--version", "--verbose"]))
        self.capability("proptest", self._rust_property)
        self.capability("cargo-fuzz", self._rust_fuzz)
        self.capability("rust-asan", self._rust_asan)
        self.capability("rust-tsan", lambda: self._chain([
            ["rustc", "+nightly-2026-10-01", "-Zsanitizer=thread", "-g", "rust_tsan.rs", "-o", "rust-tsan"],
            ["./rust-tsan"]]))
        self.capability("rust-llvm-coverage", self._rust_coverage)
        self.capabilities["miri"] = {
            "status": "unsupported", "reason": "Miri component and task-specific supported unsafe API subset not installed/validated",
            "evidence": []}
        self.capabilities["rust-tsan-negative-control"] = {
            "status": "unsupported", "reason": "safe fixture alone does not validate detecting a Rust race or instrument std/dependency closure",
            "evidence": self.capabilities["rust-tsan"]["evidence"]}
        if self.extended_rust:
            shutil.copytree(HERE / "rust/checks", self.work / "checks",
                            ignore=shutil.ignore_patterns("target"))
            self.capability("rust-tsan-rebuilt-std", self._rust_tsan_std)
            self.capability("miri", self._miri)

    def _rust_tsan_std(self):
        ok, _ = self.run([
            "cargo", "+nightly-2026-10-01", "build", "--offline", "--locked", "-Zbuild-std",
            "--target", "x86_64-unknown-linux-gnu", "--manifest-path", str(self.work / "checks/Cargo.toml")],
            env={"RUSTFLAGS": "-Zsanitizer=thread"}, timeout=420)
        if not ok:
            return False, "matching nightly standard-library TSan build failed; no ABI-mismatch bypass permitted"
        directory = self.work / "target/x86_64-unknown-linux-gnu/debug"
        ok, _ = self.run([str(directory / "safe")])
        if not ok:
            return False, "rebuilt-std TSan cannot execute safe fixture under standard worker isolation"
        ok, text = self.run([str(directory / "race")])
        return not ok and "ThreadSanitizer: data race" in text, "full-std safe and known-racy controls bound in raw logs; task closure still requires its own build"

    def _miri(self):
        command = ["cargo", "+nightly-2026-10-01", "miri", "run", "--offline", "--locked",
                   "--manifest-path", str(self.work / "checks/Cargo.toml")]
        env = {"MIRI_SYSROOT": "/opt/fuzz-miri-sysroot", "MIRIFLAGS": "-Zmiri-seed=1729"}
        ok, _ = self.run(command + ["--bin", "miri-safe"], env=env)
        if not ok:
            return False, "Miri safe control failed or component/sysroot unavailable"
        ok, text = self.run(command + ["--bin", "miri-bad"], env=env)
        return not ok and "Undefined Behavior" in text, "safe and one-past-end UB controls retained; task APIs may remain unsupported"

    def _rust_property(self):
        shutil.copy(HERE / "rust/Cargo.toml", self.work / "Cargo.toml")
        shutil.copy(HERE / "rust/Cargo.lock", self.work / "Cargo.lock")
        (self.work / "src").mkdir(exist_ok=True)
        shutil.copy(self.work / "rust_property.rs", self.work / "src/lib.rs")
        return self.command(["cargo", "test", "--offline", "--locked", "--lib"])

    def _rust_fuzz(self):
        # The same locked dependency cache as the property fixture. Adding a bin
        # does not change dependencies or the lock's package identity.
        manifest = self.work / "Cargo.toml"
        manifest.write_text(manifest.read_text() + """
[package.metadata]
cargo-fuzz = true

[[bin]]
name = "control"
path = "rust_fuzz.rs"
test = false
doc = false
bench = false
""")
        return self.command(["cargo", "+nightly-2026-10-01", "fuzz", "run", "--fuzz-dir", ".", "control", "corpus",
                             "--", "-seed=1729", "-runs=100", "-timeout=5"], timeout=420)

    def _rust_asan(self):
        ok, reason = self._chain([
            ["rustc", "+nightly-2026-10-01", "-Zsanitizer=address", "-g", "rust.rs", "-o", "rust-safe"],
            ["./rust-safe"],
            ["rustc", "+nightly-2026-10-01", "-Zsanitizer=address", "-g", "rust_asan.rs", "-o", "rust-bad"]])
        if not ok:
            return ok, reason
        ok, output = self.run(["./rust-bad"])
        return not ok and "AddressSanitizer: heap-buffer-overflow" in output, "safe and known-bad Rust heap control retained"

    def _rust_coverage(self):
        # Nightly enables branch coverage; matching LLVM tools are mandatory.
        nightly = "+nightly-2026-10-01"
        ok, root = self.run(["rustc", nightly, "--print", "sysroot"])
        if not ok:
            return False, "cannot locate matching Rust LLVM tools"
        tools = Path(root.strip()) / "lib/rustlib/x86_64-unknown-linux-gnu/bin"
        ok, reason = self._chain([
            ["rustc", nightly, "-Cinstrument-coverage", "-Zcoverage-options=branch", "-g", "rust.rs", "-o", "rust-covered"],
            ["./rust-covered"]], env={"LLVM_PROFILE_FILE": str(self.work / "rust-%p-%m.profraw")})
        if not ok:
            return ok, reason
        profiles = sorted(str(p) for p in self.work.glob("rust-*.profraw"))
        ok, _ = self.run([str(tools / "llvm-profdata"), "merge", "-sparse", *profiles, "-o", "rust.profdata"])
        if not ok:
            return False, "matching Rust LLVM profile merge failed"
        ok, raw = self.run([str(tools / "llvm-cov"), "export", "./rust-covered", "-instr-profile=rust.profdata"])
        if not ok:
            return False, "Rust LLVM export failed"
        (self.work / "llvm.json").write_text(raw)
        data = normalize("llvm", json.loads(raw), self.work)
        (self.work / "coverage-normalized.json").write_text(json.dumps(data, indent=2))
        branches = [c for r in data["files"].values() for c in r["branches"].values()]
        return len(branches) >= 4 and all(c > 0 for c in branches), "actual nightly LLVM branch outcomes checked, not stable line-only credit"

    def finish(self):
        getattr(self, self.language)()
        if self.runtime_inputs != self._runtime_inputs():
            raise RuntimeError("verifier runner inputs changed during probe")
        evidence = {"schema": "optimbench-runner-capabilities-v1", "language": self.language,
                    "scope": "tool-runtime-fixture-only", "candidate_qualification": False,
                    "uid": os.getuid(), "affinity": sorted(os.sched_getaffinity(0)),
                    "runtime_input_sha256": self.runtime_inputs,
                    "capabilities": self.capabilities, "steps": self.steps}
        (self.out / "capabilities.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps({key: record["status"] for key, record in self.capabilities.items()}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["native", "python", "javascript", "go", "rust"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--extended-rust", action="store_true")
    args = parser.parse_args()
    Probe(args.language, args.out, args.extended_rust).finish()


if __name__ == "__main__":
    main()
