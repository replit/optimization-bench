"""Build/engine contracts for verifier workers (argv, not shell strings).

Adapters supply typed public-API harnesses. No plan links both comparison arms.
All paths are worker-local; the controller applies resource/transport policies.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    language: str
    instrumentation: str
    environment: dict
    flags: tuple
    coverage_export: tuple
    limitations: tuple = ()

    def to_dict(self):
        return asdict(self)


def build_plan(language, instrumentation):
    if language in ("c", "cpp"):
        common = ("-g", "-O1", "-fno-omit-frame-pointer")
        flags = {
            "coverage": ("-fprofile-instr-generate", "-fcoverage-mapping"),
            "asan-ubsan": ("-fsanitize=address,undefined", "-fno-sanitize-recover=all"),
            "tsan": ("-fsanitize=thread",),
            "msan": ("-fsanitize=memory", "-fsanitize-memory-track-origins=2"),
            "libfuzzer": ("-fsanitize=fuzzer,address,undefined", "-fno-sanitize-recover=all"),
            "afl": (),
        }
        if instrumentation not in flags:
            raise ValueError("unknown native instrumentation")
        cc = "afl-clang-fast" if instrumentation == "afl" else "clang-16"
        cxx = "afl-clang-fast++" if instrumentation == "afl" else "clang++-16"
        limitations = ("MSan requires every dependency and C++ runtime instrumented; fixture success alone cannot qualify task closure",) if instrumentation == "msan" else ()
        return Plan(language, instrumentation, {
            "CC": cc, "CXX": cxx, "LLVM_PROFILE_FILE": "/scratch/coverage/%p-%m.profraw",
            "ASAN_OPTIONS": "abort_on_error=1:detect_leaks=1",
            "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
            "TSAN_OPTIONS": "halt_on_error=1",
        }, common + flags[instrumentation],
            (("llvm-profdata-16", "merge", "-sparse", "<all-child-profraw>", "-o", "<merged.profdata>"),
             ("llvm-cov-16", "export", "<binary>", "-instr-profile=<merged.profdata>")), limitations)
    if language == "rust":
        flags = {
            "coverage": ("-Cinstrument-coverage", "-Clink-dead-code"),
            "branch-coverage": ("-Cinstrument-coverage", "-Clink-dead-code", "-Zcoverage-options=branch"),
            "asan": ("-Zsanitizer=address",),
            "tsan": ("-Zsanitizer=thread",),
            "libfuzzer": (),
            "miri": (),
        }
        if instrumentation not in flags:
            raise ValueError("unknown Rust instrumentation")
        environment = {
            "LLVM_PROFILE_FILE": "/scratch/coverage/%p-%m.profraw",
            "RUST_BACKTRACE": "1",
        }
        if instrumentation != "coverage":
            environment["RUSTUP_TOOLCHAIN"] = "nightly-2026-10-01"
        return Plan(language, instrumentation, environment, flags[instrumentation],
            (("<toolchain-matched-llvm-profdata>", "merge", "<all-child-profraw>"),
             ("<toolchain-matched-llvm-cov>", "export", "<binary>")),
            ("Rust sanitizer/cargo-fuzz requires explicitly selected pinned nightly; rebuild std/dependency closure when required",
             "Miri requires a separately installed matching nightly component and task feasibility receipt",
             "Rust branch coverage requires supported nightly -Zcoverage-options=branch; stable line coverage is not branch evidence"))
    if language == "go":
        flags = {"coverage": ("-covermode=atomic", "-coverpkg=./..."),
                 "race": ("-race",), "checkptr": ("-gcflags=all=-d=checkptr=2",)}
        if instrumentation not in flags:
            raise ValueError("unknown Go instrumentation")
        return Plan(language, instrumentation, {"GOTOOLCHAIN": "local", "GOCOVERDIR": "/scratch/coverage"},
                    flags[instrumentation], (("go", "tool", "covdata", "textfmt", "-i=<all-child-coverdirs>", "-o=<coverprofile>"),),
                    ("Go standard coverage has no branch outcomes: explicit source-bound branch inventory/counters required",))
    if language == "python" and instrumentation == "coverage":
        return Plan(language, instrumentation,
                    {"COVERAGE_PROCESS_START": "/opt/fuzz-runners/coverage.rc"},
                    ("-m", "coverage", "run", "--branch", "--parallel-mode"),
                    (("python", "-m", "coverage", "combine"), ("python", "-m", "coverage", "json")),
                    ("Child Python interpreters must call coverage.process_startup() before task imports; native modules require separate LLVM exports",))
    if language == "javascript" and instrumentation == "coverage":
        return Plan(language, instrumentation, {"NODE_V8_COVERAGE": ""},
                    ("<istanbul-instrument-before-task-import>",),
                    (("node", "/opt/fuzz-runners/remap.cjs", "<coverage.json>", "<remapped.json>"),),
                    ("Instrument task code, not just engine/harness; merge every worker/child map and require original source mapping",))
    raise ValueError("unsupported language/instrumentation combination")


def engine_argv(engine, harness, corpus, *, seed=1729, seconds=60, runs=100):
    if seed < 0 or seconds <= 0 or runs <= 0:
        raise ValueError("seed/budgets invalid")
    if engine in ("libfuzzer", "atheris"):
        prefix = [harness] if engine == "libfuzzer" else ["python", harness]
        return prefix + [corpus, f"-seed={seed}", f"-max_total_time={seconds}", "-timeout=30"]
    if engine == "afl":
        return ["afl-fuzz", "-V", str(seconds), "-s", str(seed), "-i", corpus, "-o", "/scratch/afl",
                "--", harness, "@@"]
    if engine == "cargo-fuzz":
        return ["cargo", "+nightly-2026-10-01", "fuzz", "run", harness, corpus, "--",
                f"-seed={seed}", f"-max_total_time={seconds}", "-timeout=30"]
    if engine == "go":
        return ["go", "test", "-run=^$", "-fuzz=^" + harness + "$", f"-fuzztime={seconds}s", "-parallel=1"]
    if engine == "jazzer":
        return ["/opt/fuzz-runners/node_modules/.bin/jazzer", harness, "--", corpus,
                f"-seed={seed}", f"-max_total_time={seconds}", "-timeout=30"]
    if engine == "hypothesis":
        return ["python", harness, "--seed", str(seed), "--examples", str(runs)]
    if engine == "proptest":
        return ["cargo", "test", "--offline", "--locked", harness, "--", "--nocapture"]
    if engine == "rapid":
        return ["go", "test", "-run=^" + harness + "$", f"-rapid.seed={seed}", f"-rapid.checks={runs}"]
    if engine == "fast-check":
        return ["node", harness, "--seed", str(seed), "--runs", str(runs)]
    raise ValueError("unknown fuzz/property engine")


def engine_spec(engine, harness, corpus, *, seed=1729, seconds=60, runs=100):
    """Explicit seed environment and limitations alongside bounded engine argv."""
    argv = engine_argv(engine, harness, corpus, seed=seed, seconds=seconds, runs=runs)
    environment, limitations = {}, []
    if engine == "proptest":
        environment = {"PROPTEST_RNG_SEED": str(seed), "PROPTEST_CASES": str(runs)}
    if engine == "afl":
        environment = {"AFL_NO_AFFINITY": "1", "AFL_SKIP_CPUFREQ": "1",
                       "ASAN_OPTIONS": "abort_on_error=1:detect_leaks=0:symbolize=0"}
    if engine == "go":
        limitations.append("Go native fuzz has no stable user RNG-seed flag; freeze/replay actual generated corpus, not a claimed bitwise seed replay")
    if engine in ("hypothesis", "fast-check"):
        limitations.append("Task-owned driver must consume the declared --seed/--examples or --runs CLI and retain generated corpus")
    return {"argv": argv, "environment": environment, "timeout_seconds": seconds + 30,
            "seed": seed, "limitations": limitations,
            "corpus_requirement": "record actual ordered content-addressed cases; seed alone is not deterministic campaign evidence"}
