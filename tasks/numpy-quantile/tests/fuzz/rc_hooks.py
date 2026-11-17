"""numpy-quantile verifier-owned RC hooks. Facts only; the controller assigns verdicts.

Reviewed task oracle (task-specific, not a global relaxation):
- floats: protected dtype/shape + rtol=atol=8*eps(float64), equal NaNs, so
  +0.0 == -0.0 (environment/workload/task.py compare());
- storage order is not semantic (values are compared in logical C order);
- integers/bools/objects: exact semantic values (repr), never raw .npy bytes;
- errors: exact class and message, except MemoryError (class only);
- effects (input preservation, result independence, ordered warnings): exact.
"""
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

TASK = "numpy-quantile"
PUBLIC = {"numpy.quantile", "numpy.nanquantile", "numpy.percentile", "numpy.nanpercentile",
          "numpy.median", "numpy.nanmedian"}
EPS = 8 * 2.220446049250313e-16
POLICY = "numpy-quantile/protected-8eps-v1"


def PRECONDITION(case, surface):
    if case.get("task") != TASK or case.get("surface_id") not in PUBLIC:
        return {"valid": False, "reason": "not a documented public quantile-family entry point"}
    opts = case.get("options", {})
    if case["surface_id"].endswith("median") and "method" in opts:
        return {"valid": False, "reason": "median family has no method parameter"}
    if not isinstance(case.get("input"), dict) or case.get("sequence") not in ("repeat", "alias", "protocol_fault", "protocol_healthy"):
        return {"valid": False, "reason": "unknown input/sequence model"}
    return {"valid": True, "reason": "public documented call; library errors are valid outcomes"}


def _float_equal(x, y):
    return (math.isnan(x) and math.isnan(y)) or x == y or (
        math.isfinite(x) and math.isfinite(y) and abs(x - y) <= EPS + EPS * abs(x))


def _value_diffs(a, b):
    for key in ("type", "dtype", "shape", "encoding"):
        if a.get(key) != b.get(key):
            return [{"field": "value." + key, "baseline": a.get(key), "candidate": b.get(key)}]
    if a["encoding"] == "float-hex":
        xs = [float.fromhex(v) for v in a["values"]]
        ys = [float.fromhex(v) for v in b["values"]]
        bad = [i for i, (x, y) in enumerate(zip(xs, ys)) if not _float_equal(x, y)]
        if len(xs) != len(ys) or bad:
            return [{"field": "value.values", "policy": "8eps-equal-nan", "first_index": bad[:5]}]
        return []
    return [] if a["values"] == b["values"] else [{"field": "value.values", "policy": "exact-semantic"}]


def COMPARE(left, right, case):
    diffs = []
    if left["termination"] != right["termination"]:
        diffs.append({"field": "termination", "baseline": left["termination"], "candidate": right["termination"]})
    lo, ro = left["outcome"], right["outcome"]
    if lo.get("kind") != ro.get("kind"):
        diffs.append({"field": "outcome.kind", "baseline": lo.get("kind"), "candidate": ro.get("kind")})
    elif lo["kind"] == "ok":
        diffs.extend(_value_diffs(lo["value"], ro["value"]))
    elif lo["kind"] == "error":
        if lo.get("class") != ro.get("class"):
            diffs.append({"field": "outcome.class", "baseline": lo.get("class"), "candidate": ro.get("class")})
        elif lo["class"] != "MemoryError" and lo.get("message") != ro.get("message"):
            diffs.append({"field": "outcome.message", "baseline": lo.get("message"), "candidate": ro.get("message")})
        for key in ("os_errno", "errno"):
            if lo.get(key) != ro.get(key):
                diffs.append({"field": "outcome." + key, "baseline": lo.get(key), "candidate": ro.get(key)})
    if left["effects"] != right["effects"]:
        diffs.append({"field": "effects", "baseline": left["effects"], "candidate": right["effects"]})
    return {"equal": not diffs, "policy_id": POLICY, "differences": diffs}


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    root = task.parents[1]
    output = Path(context["out"]) / "numpy-rc-facts"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_numpy_rc_facts", {})
    if cache.get("key") != key:
        with tempfile.TemporaryDirectory(prefix="numpy-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]), task / "environment/workload/inputs",
                          scratch, task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"], extra_docker_flags=[
                *HARDENING,
                "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                "-v", f"{task / 'tests/fuzz'}:/verifier-fuzz:ro",
                "-v", f"{root / 'judge'}:/verifier-lib/judge:ro",
            ], tmpfs="/tmp:rw,nosuid,nodev,size=4g")
            argv = ["python3", "/verifier-fuzz/rc_driver.py",
                    "--baseline", "/verifier-baseline", "--candidate", "/app/build",
                    "--baseline-sha", context["builds"]["baseline"],
                    "--candidate-sha", context["builds"]["candidate"], "--out", "/scratch/facts"]
            env = {"PYTHONPATH": "/verifier-lib", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
            results = executor.run([Step(argv, timeout=1800)], ctx, env)
            for index, result in enumerate(results):
                (output / f"step-{index}.stdout").write_bytes(result.stdout)
                (output / f"step-{index}.stderr").write_bytes(result.stderr)
            if any(result.exit_code != 0 for result in results):
                raise ValueError("numpy RC library probe infrastructure failure; inspect retained streams")
            for name in ("repeat-facts.json", "alias-facts.json", "stream-facts.json"):
                shutil.copy2(scratch / "facts" / name, output / name)
            (output / "execution.json").write_text(json.dumps({
                "image": context["image"], "cpuset": context["cpuset"], "builds": context["builds"],
                "steps": [{"exit_code": r.exit_code, "leftovers": r.leftovers, "telemetry": r.telemetry}
                          for r in results]}, indent=2, sort_keys=True) + "\n")
        cache["key"] = key
    return json.loads((output / filename).read_text())


def stream_faults(context):
    # Public array_like callback protocols (__array__, __array_function__) are the
    # stream-like user-callback class here; faults are raised by the callback.
    # The literal reader/writer/file-parameter part has no public surface.
    return _facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process, "aliasing": aliasing}


def HEADER_CONSUMERS(context):
    # Frozen manifest generated from pinned pristine sources by header_manifest.py
    # before any candidate result. One supported cell (C, pinned-upstream default
    # flags: numpy's own limited_api consumer build); other dialects stay pending
    # with partial evidence, never guessed. 9 of 26 installed headers are not yet
    # covered by a legal include context (listed in uncovered_headers).
    return {"format": "judge-generic-header-request-v1", "applicable": True,
            "manifest": str(Path(__file__).resolve().parent / "header-consumers.json")}


# ---------------------------------------------------------------------------
# Public CLI requests (shared Framework 2 runner; no verdicts here).
# The release source_policy makes all of numpy editable, so the installed
# console scripts f2py and numpy-config are public surfaces even though the
# quantile workload never calls them (integrator scope decision, Oct 27).
CLI_CONTRACT = "numpy/console-scripts#installed-entry-points"
CLI_SURFACES = {"numpy.cli.numpy-config", "numpy.cli.f2py"}


def CLI_PRECONDITION(case, surface):
    if case.get("task") != TASK or case.get("surface_id") not in CLI_SURFACES:
        return {"valid": False, "reason": "not an installed numpy console script"}
    if case.get("precondition_id") != "documented-cli-invocation":
        return {"valid": False, "reason": "unknown CLI precondition"}
    return {"valid": True, "reason": "documented console-script invocation on verifier-owned inputs"}


def _fortran(count):
    return "\n".join(
        f"subroutine quant{i:04d}(n, a, q, r)\n  integer, intent(in) :: n\n"
        f"  real(8), intent(in) :: a(n), q\n  real(8), intent(out) :: r\n"
        f"  r = a(1) * q\nend subroutine quant{i:04d}\n" for i in range(count))


def CLI_CASES(context):
    root = Path(context["out"]) / "numpy-cli-inputs"
    specs = [
        ("numpy-config-cflags", "numpy.cli.numpy-config", "small",
         ["{build}/site/bin/numpy-config", "--cflags"], {"README.case": "numpy-config --cflags\n"}, {}),
        ("numpy-config-version", "numpy.cli.numpy-config", "small",
         ["{build}/site/bin/numpy-config", "--version"], {"README.case": "numpy-config --version\n"}, {}),
        ("f2py-signature-stdout", "numpy.cli.f2py", "large",
         ["{build}/site/bin/f2py", "-m", "kernels", "-h", "stdout", "kernels.f90"],
         {"kernels.f90": _fortran(300)}, {}),
        ("f2py-signature-file", "numpy.cli.f2py", "small",
         ["{build}/site/bin/f2py", "-m", "kernels", "-h", "out/kernels.pyf", "kernels.f90"],
         {"kernels.f90": _fortran(100)}, {"output_dirs": ["out"], "watch_outputs": ["out"],
                                          "required_outputs": ["out/kernels.pyf"]}),
    ]
    cases = []
    for name, surface_id, payload, argv, files, extra in specs:
        inputs = root / name
        if inputs.exists():
            shutil.rmtree(inputs)
        inputs.mkdir(parents=True)
        for filename, text in files.items():
            (inputs / filename).write_text(text)
        if extra.get("output_dirs"):
            (inputs / "out").mkdir()
        case = {"task": TASK, "surface_id": surface_id, "contract_id": CLI_CONTRACT,
                "precondition_id": "documented-cli-invocation", "name": name, "argv": argv,
                "inputs_sha256": {f: hashlib.sha256(t.encode()).hexdigest() for f, t in files.items()}}
        surface = {"id": surface_id, "visibility": "public", "contract_id": CLI_CONTRACT,
                   "precondition_id": "documented-cli-invocation",
                   "oracle": {"baseline_terminations": ["return"], "baseline_exit_codes": [0, 1, 2],
                              "required_error_fields": {"exit_code": "integer"}}}
        cases.append({"case": case, "surface": surface,
                      "step": {"argv": argv, "cwd": ".", "timeout": 120,
                               "env": {"PYTHONPATH": "{build}/site", "OPENBLAS_NUM_THREADS": "1"}},
                      "inputs": str(inputs), "watch_inputs": sorted(files), "payload_class": payload,
                      **extra})
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": cases}
