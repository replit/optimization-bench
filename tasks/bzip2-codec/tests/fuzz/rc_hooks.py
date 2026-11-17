"""Verifier-owned bzip2 RC registrations; the central controller assigns every verdict.

Registered:
* GATES["stream_faults"]: public bzlib streaming (BZ2_bzCompress/BZ2_bzDecompress with
  legal 1-byte..64 KiB short/partial chunks), buffer (BZ2_bzBuffToBuff*) and stdio
  (BZ2_bzWrite*/BZ2_bzRead*) cases: truncated/corrupt/bad-magic streams, parameter
  validation, output-buffer limits and a real OS failure (/dev/full, errno ENOSPC).
* GATES["repeat_in_process"]: the same cases twice in one persistent process per arm,
  compared with the fresh-process oracle, with per-call fd/thread/child snapshots.
* CLI_CASES: the upstream ``bzip2`` executable (source/bzip2, a declared public
  interface component): small and large stdout cases plus a file-output mode with
  a read-only output-directory fault.
* GATES["aliasing"]: source-supported library-owned pointer observations.
* HEADER_CONSUMERS: two-TU C and C++ consumers of bzlib.h linked against
  source/libbz2.a, compiler-default dialects bound to upstream flags and the
  public extern-C guard. The task image includes both gcc and g++.

Every library observation records ``diagnostics.execution_affinity`` read by the probe.
COMPARE is the central exact typed contract comparator; OS errors are compared by
bz error code, name, stage, errno and operation.
"""
import bz2
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = "bzip2-codec"
OPERATIONS = {"stream", "stream_decompress", "buffer_compress", "buffer_decompress",
              "compress_init", "stdio_write", "stdio_read"}
CLI_SURFACE = {"id": "bzip2.cli.upstream", "visibility": "public",
               "contract_id": "bzip2-1.0.8-cli-v1", "precondition_id": "bzip2-rc-cli-public-v1"}


def PRECONDITION(case, surface):
    """Bounded public bzlib cases; library error codes are valid expected outcomes."""
    if case.get("surface_id") == CLI_SURFACE["id"]:
        return CLI_PRECONDITION(case, surface)
    if case.get("surface_id") == "bzip2.c.bzlib.library-owned-pointers":
        valid = case.get("operation") in ("read_get_unused_chain", "bzerror_string")
        return {"valid": valid, "reason": "reviewed library-owned pointer surface" if valid else "unregistered alias case"}
    if case.get("surface_id") != "bzip2.c.bzlib.stream+buffer+stdio":
        return {"valid": False, "reason": "unknown surface"}
    if case.get("operation") not in OPERATIONS:
        return {"valid": False, "reason": "unknown public operation"}
    options = case.get("options", {})
    if not isinstance(options, dict):
        return {"valid": False, "reason": "malformed options"}
    for key in ("in_chunk", "out_chunk"):
        if key in options and not (isinstance(options[key], int) and 1 <= options[key] <= 1 << 20):
            return {"valid": False, "reason": "chunk outside legal bounded stream sizes"}
    if case["operation"] == "compress_init":
        # Out-of-range parameters are valid public inputs with a documented BZ_PARAM_ERROR result.
        if not all(isinstance(options.get(k), int) and -8 <= options[k] <= 300
                   for k in ("blocksize", "verbosity", "workfactor")):
            return {"valid": False, "reason": "outside bounded parameter matrix"}
    elif "blocksize" in options and not 1 <= options["blocksize"] <= 9:
        return {"valid": False, "reason": "invalid blocksize for a data operation"}
    if options.get("verbosity", 0) not in (0, 5) or ("small" in options and options["small"] not in (0, 1)):
        return {"valid": False, "reason": "outside bounded option matrix"}
    source = case.get("input")
    if source is not None and not (isinstance(source, dict) and isinstance(source.get("sha256"), str)
                                   and isinstance(source.get("bytes"), int) and source["bytes"] <= 1 << 22):
        return {"valid": False, "reason": "unbounded or unauthenticated input"}
    return {"valid": True, "reason": "bounded public bzlib case"}


def _public_inputs(context):
    return Path(context.get("public_inputs") or Path(context["task"]) / "environment/workload/inputs")


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "bzip2-public-library"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["cpuset"], context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_bzip2_rc_facts", {})
    if cache.get("key") != key:
        inputs = _public_inputs(context).resolve()
        with tempfile.TemporaryDirectory(prefix="bzip2-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]), inputs, scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"], extra_docker_flags=[
                *HARDENING,
                "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro",
                "-v", f"{inputs}:/verifier-public:ro",
            ], tmpfs="/tmp:rw,nosuid,nodev,size=2g")
            argv = ["python3", "/verifier-fuzz/rc_driver.py",
                    "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                    "--public", "/verifier-public",
                    "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"], "--out", "/scratch/facts"]
            results = executor.run([Step(argv, timeout=1800)], ctx, {})
            for index, result in enumerate(results):
                (output / f"step-{index}.stdout").write_bytes(result.stdout)
                (output / f"step-{index}.stderr").write_bytes(result.stderr)
            if any(result.exit_code != 0 for result in results):
                raise ValueError("bzip2 RC library probe infrastructure failure; inspect retained streams")
            for name in ("stream-facts.json", "repeat-facts.json", "alias-facts.json"):
                shutil.copy2(scratch / "facts" / name, output / name)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "cpuset": context["cpuset"], "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers,
                           "telemetry": r.telemetry} for r in results]}, indent=2, sort_keys=True) + "\n")
        cache["key"] = key
    return json.loads((output / filename).read_text())


def stream_faults(context):
    return _facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process, "aliasing": aliasing}


def CLI_PRECONDITION(case, surface):
    if case.get("surface_id") != CLI_SURFACE["id"]:
        return {"valid": False, "reason": "unknown CLI surface"}
    if case.get("mode") not in ("compress_stdout", "decompress_stdout", "compress_keep_file"):
        return {"valid": False, "reason": "unregistered CLI mode"}
    if case.get("options", {}).get("level") not in (1, 9, None):
        return {"valid": False, "reason": "outside bounded CLI option matrix"}
    return {"valid": True, "reason": "documented bzip2 1.0.8 command-line mode on a natural public file"}


def _cli_case(mode, fixture, body, options):
    case = {"task": TASK, "surface_id": CLI_SURFACE["id"], "contract_id": CLI_SURFACE["contract_id"],
            "precondition_id": CLI_SURFACE["precondition_id"], "mode": mode, "options": options,
            "input": {"fixture": fixture, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}}
    from judge.fuzzing_contract import case_digest
    case["case_sha256"] = case_digest(case)
    return case


def CLI_CASES(context):
    public = _public_inputs(context) / "data/dev"
    natural = sorted((public / "inputs").glob("*.raw"), key=lambda p: p.stat().st_size)
    small_source = natural[0].read_bytes()[:3000]
    large_name = next(p.stem for p in natural if p.stat().st_size >= 65536)
    large_stream = (public / "reference" / f"{large_name}-9.bz2").read_bytes()
    if len(bz2.decompress(large_stream)) < 65536:
        raise ValueError("Large CLI fixture does not produce >=64 KiB stdout")
    root = Path(context["out"]) / "bzip2-cli-inputs"
    trees = {}
    for name, files in (("small", {"small.txt": small_source}), ("large", {"large.bz2": large_stream}),
                        ("file", {"out/data.txt": natural[0].read_bytes()})):
        tree = root / name
        if tree.exists():
            shutil.rmtree(tree)
        for relative, body in files.items():
            (tree / relative).parent.mkdir(parents=True, exist_ok=True)
            (tree / relative).write_bytes(body)
        trees[name] = tree
    binary = "{build}/source/bzip2"
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": [
        {"case": _cli_case("compress_stdout", "small.txt", small_source, {"level": 9}),
         "surface": CLI_SURFACE, "payload_class": "small",
         "step": {"argv": [binary, "-c", "-9", "small.txt"], "cwd": ".", "timeout": 60},
         "inputs": str(trees["small"]), "watch_inputs": ["small.txt"]},
        {"case": _cli_case("decompress_stdout", "large.bz2", large_stream, {"level": None}),
         "surface": CLI_SURFACE, "payload_class": "large",
         "step": {"argv": [binary, "-d", "-c", "large.bz2"], "cwd": ".", "timeout": 120},
         "inputs": str(trees["large"]), "watch_inputs": ["large.bz2"]},
        {"case": _cli_case("compress_keep_file", "out/data.txt", natural[0].read_bytes(), {"level": 1}),
         "surface": CLI_SURFACE, "payload_class": "small",
         "step": {"argv": [binary, "-k", "-f", "-1", "out/data.txt"], "cwd": ".", "timeout": 120},
         "inputs": str(trees["file"]), "watch_inputs": ["out/data.txt"], "watch_outputs": ["out"],
         "output_dirs": ["out"], "required_outputs": ["out/data.txt.bz2"]},
    ]}


def HEADER_CONSUMERS(context):
    return {"format": "judge-generic-header-request-v1", "applicable": True,
            "manifest": str(HERE / "header-manifest.json")}
