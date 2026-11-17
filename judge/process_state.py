"""Compare fresh post-call process state and Rule D leftovers outside timing."""
import json
from pathlib import Path
import tempfile
import time
import sys
import re
from . import execution_coverage, public_packet
from .leftovers import resident_workers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/runner"))
from runner import Context, Step, fingerprint, references
from docker_executor import DockerExecutor

GO = r'''package main
import ("bytes";"encoding/json";"os";"runtime";"runtime/debug";"github.com/yuin/goldmark/v2/parser";"github.com/yuin/goldmark/v2/renderer/html")
func main(){var out bytes.Buffer;src:=[]byte("# hello\n");p:=parser.New();r:=html.New();r.Render(&out,src,p.Parse(src));old:=debug.SetGCPercent(-1);debug.SetGCPercent(old);json.NewEncoder(os.Stdout).Encode(map[string]any{"gc_percent":old,"goroutines":runtime.NumGoroutine(),"environment":os.Environ()})}
'''
NODE = r'''const fs=require("node:fs"),v8=require("node:v8");
(async()=>{const m=await import("/app/build/project/main.js");await m.minify("function f(a){return a+1}f(2)");
const h=process._getActiveHandles().filter(x=>x!==process.stdin&&x!==process.stdout&&x!==process.stderr).map(x=>x.constructor.name).sort();
fs.writeFileSync("/scratch/process-state.json",JSON.stringify({environment:process.env,active_handles:h,exec_flags:process.execArgv.filter(x=>x!=="-e"),heap_limit:v8.getHeapStatistics().heap_size_limit}));})().catch(e=>{console.error(e);process.exit(1)});'''

NODE_CALL = 'const m=await import("/app/build/project/main.js");await m.minify("function f(a){return a+1}f(2)");'
BABEL_CALL = 'const m=require("/app/build/runtime/node_modules/@babel/core");m.transformSync("const x=1;", {configFile:false,babelrc:false});'
PYTHON_TASKS = {"sqlfluff-project-lint", "black-project-format",
                "sqlglot-mozilla-transpile", "numpy-quantile"}


def not_configured(reason):
    return {"status": "not_configured", "reason": reason}


class ObservedState(dict):
    """Keep observational metadata out of the runtime-state comparison."""
    def __init__(self, value, coverage, run_id):
        super().__init__(value)
        self.execution_coverage, self.probe_id = coverage, run_id


class NativeOutputMismatch(ValueError):
    def __init__(self, expected, observed):
        super().__init__("Process-state workload differs from protected reference")
        self.expected, self.observed = dict(expected), dict(observed)


class CandidateOutputRejected(ValueError):
    def __init__(self, mismatch):
        super().__init__(str(mismatch))
        self.report = {"arm": "candidate", "check": "representative-native-output",
                       "baseline_output_checked": True,
                       "expected": mismatch.expected, "observed": mismatch.observed}


def representative(task, build, settings):
    """Resolve only verifier-owned scripts and files in the read-only build."""
    kind = settings.get("kind")
    if kind not in {"python", "node", "native", "goldmark", "go_sqlfmt"}:
        return not_configured("Unknown process-state kind")
    if "representative_operation" not in settings:
        if kind == "node" and task.name not in {"terser-minify", "babel-project-compile"}:
            return not_configured("No representative Node operation")
        if kind == "python" and task.name not in PYTHON_TASKS:
            return not_configured("No representative Python operation")
        return None
    operation = settings["representative_operation"]
    if kind not in {"node", "python"} or not isinstance(operation, dict):
        return not_configured("Representative operation requires Node or Python and module/script paths")
    module, script = operation.get("module"), operation.get("script")
    if not isinstance(module, str) or not isinstance(script, str):
        return not_configured("Representative operation requires module/script paths")
    script_path = Path(script)
    root = operation.get("root")
    if root is not None:
        if (kind != "python" or not isinstance(root, str)
                or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module)):
            return not_configured("A module import root requires a Python dotted module name")
        module_path = Path(root)
    else:
        module_path = Path(module)
    if (not module_path.is_relative_to("/app/build") or ".." in module_path.parts
            or script_path.is_absolute() or ".." in script_path.parts):
        return not_configured("Representative paths must stay inside build and task tests")
    tests = (task / "tests").resolve()
    source = tests / script_path
    built_module = Path(build).resolve() / module_path.relative_to("/app/build")
    if (not source.resolve().is_relative_to(tests) or not source.is_file()
            or source.suffix not in ((".js", ".mjs", ".cjs") if kind == "node" else (".py",))):
        return not_configured("Representative script is missing, escaping tests, or has an unsupported suffix")
    if (not built_module.resolve().is_relative_to(Path(build).resolve())
            or not (built_module.is_dir() if root is not None else built_module.is_file())):
        return not_configured("Representative module is missing or escaping build")
    if root is not None:
        imported = built_module.joinpath(*module.split("."))
        locations = [imported.with_suffix(".py"), imported / "__init__.py"]
        if not any(path.is_file() and path.resolve().is_relative_to(Path(build).resolve())
                   for path in locations):
            return not_configured("Representative Python package/module is missing or escaping build")
    result = {"module": module, "script": "/verifier-operation/" + script_path.as_posix(),
              "tests": tests}
    if root is not None:
        result["root"] = root
    return result


def node_program(task_name, operation=None):
    if operation:
        call = (
            'const {pathToFileURL}=require("node:url");'
            f'const op=await import(pathToFileURL({json.dumps(operation["script"])}).href);'
            'const run=op.run??op.default;'
            'if(typeof run!=="function"){fs.writeFileSync("/scratch/process-state.json",'
            'JSON.stringify({status:"not_configured",reason:"Representative script exports no run/default function"}));return;}'
            f'const m=await import(pathToFileURL({json.dumps(operation["module"])}).href);await run(m);'
        )
        return NODE.replace(NODE_CALL, call)
    return NODE.replace(NODE_CALL, BABEL_CALL) if task_name == "babel-project-compile" else NODE


def native_reference(module, case, cases, inputs):
    """Select the full protected output contract for the representative case."""
    rows = list(references(module, inputs))
    expected = {row["path"]: (row["expect_len"], row["expect_sha"]) for row in rows}
    if len(expected) != len(rows) or not expected:
        raise ValueError("Process-state workload has missing or duplicate protected references")
    if len(cases) == 1:
        names = set(expected)
    elif case.id in expected:
        names = {case.id}
    elif "docs" in case.data:
        # Mined tasks group several individually authenticated documents per case.
        names = {doc["path"] for doc in case.data["docs"]}
    else:
        raise ValueError("Process-state case has no protected reference scope")
    if not names or not names <= expected.keys():
        raise ValueError("Process-state case has missing protected references")
    return {name: expected[name] for name in names}


def dynamic_representative(original_steps, ctx, settings):
    """Optional trusted dynamic counterpart of a static target; never used for timing."""
    recipe = settings.get("representative_dynamic")
    if recipe is None:
        return [], original_steps
    relative = Path(recipe.get("replace_executable", ""))
    command = recipe.get("compile")
    if (relative == Path(".") or relative.is_absolute() or ".." in relative.parts or
            not isinstance(command, list) or not command or
            not all(isinstance(arg, str) and arg for arg in command)):
        raise ValueError("Invalid representative dynamic recipe")
    target = ctx.build / relative
    if not target.resolve().is_relative_to(ctx.build.resolve()) or not target.is_file():
        raise ValueError("Representative static executable is missing or escaping build")
    selected = [step for step in original_steps if step.argv[0] == str(target)]
    if not selected:
        raise ValueError("Representative recipe does not match a workload executable")
    if "-static" in command or "/scratch/state-target" not in command:
        raise ValueError("Representative recipe must produce a dynamic /scratch/state-target")
    for step in selected:
        step.argv[0] = "/scratch/state-target"
    return [Step(command, timeout=120)], original_steps


def validate_native(module, case, ctx, steps, results, expected, setup_count=1):
    # The first step compiles the observer, not part of the trusted adapter result.
    if (len(results) != len(steps) or len(results) <= setup_count or
            any(result.exit_code != 0 for result in results[:setup_count])):
        raise ValueError("Process-state observer setup or workload result count failed")
    workload = results[setup_count:]
    if any(result.exit_code is None or result.exit_code < 0 or
           result.telemetry.get("execution_protocol") not in (None, "process", "complete")
           for result in workload):
        raise ValueError("Process-state workload crashed or did not complete")
    outputs = module.collect(case, ctx, workload)
    observed = {name: fingerprint(data, getattr(module, "RESULT_ENCODING", None))
                for name, data in outputs.items()}
    if observed != expected:
        raise NativeOutputMismatch(expected, observed)


def probe(task, build, image, cpuset, public_inputs=None, projects=None):
    task = Path(task)
    settings = json.loads((task / "tests/judge.json").read_text()).get("process_state", {})
    if not settings.get("enabled"):
        return not_configured("Process-state probe is disabled or missing")
    kind = settings.get("kind")
    operation = representative(task, build, settings)
    if operation and operation.get("status") == "not_configured":
        return operation
    with tempfile.TemporaryDirectory(prefix="process-state-") as temporary:
        scratch = Path(temporary)
        run_id = scratch.name
        (scratch / "probe-id").write_text(run_id)
        ctx = Context(scratch, Path(build).resolve(),
                      Path(public_inputs) if public_inputs else task / "environment/workload/inputs",
                      Path(projects) if projects else scratch, task / "environment/workload", docker=True)
        from .probe_build import mounts as probe_build_mounts
        flags = [
            *probe_build_mounts(build),
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "-v", f"{ROOT / 'judge'}:/verifier-process:ro"]
        if operation:
            flags += ["-v", f"{operation['tests']}:/verifier-operation:ro"]
        if public_packet.load(task) is not None:
            # Input-free images receive their declared public inputs read-only at run
            # time, exactly as an agent container does; the probe sees the same root.
            flags += ["-v", f"{ctx.inputs.resolve()}:/app/workload/inputs:ro"]
        executor = DockerExecutor(image, cpuset, extra_docker_flags=flags,
                                  tmpfs="/tmp:rw,nosuid,nodev,size=2g")
        env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "HOME": "/scratch",
               "TZ": "UTC", "LC_ALL": "C.UTF-8"}
        if kind == "python":
            argv = [settings.get("interpreter", "python3"), "/verifier-process/process_probe.py", task.name]
            if operation:
                argv += [operation["module"], operation["script"]]
                if "root" in operation:
                    argv.append(operation["root"])
            steps = [Step(argv, timeout=120)]
        elif kind == "node":
            (scratch / "v8-coverage").mkdir()
            env["NODE_V8_COVERAGE"] = "/scratch/v8-coverage"
            program = node_program(task.name, operation)
            (scratch / "probe.cjs").write_text(program)
            steps = [Step(["node", "/scratch/probe.cjs"], timeout=60)]
        elif kind in ("goldmark", "go_sqlfmt"):
            program = GO
            module, version = "github.com/yuin/goldmark/v2", "v2.0.0"
            if kind == "go_sqlfmt":
                module, version = "github.com/dimitri/sqlfmt", "v0.0.0"
                program = program.replace('"bytes";', '"strings";').replace(
                    '"github.com/yuin/goldmark/v2/parser";"github.com/yuin/goldmark/v2/renderer/html"',
                    '"github.com/dimitri/sqlfmt/format"').replace(
                    'var out bytes.Buffer;src:=[]byte("# hello\\n");p:=parser.New();r:=html.New();r.Render(&out,src,p.Parse(src));',
                    'format.Format(strings.NewReader("SELECT 1;"));')
            (scratch / "probe.go").write_text(program)
            go_version = re.search(r"(?m)^go (\S+)", (Path(build) / "project/go.mod").read_text())[1]
            (scratch / "go.mod").write_text(f"module probe\n\ngo {go_version}\nrequire {module} {version}\nreplace {module} => /app/build/project\n")
            env.update(GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local", GOCACHE="/scratch/go-cache")
            (scratch / "go-coverage").mkdir()
            env["GOCOVERDIR"] = "/scratch/go-coverage"
            steps = [
                Step(["/usr/local/go/bin/go", "build", "-cover", "-covermode=count",
                      "-coverpkg=command-line-arguments," + module + "/...",
                      "-mod=mod", "-o", "/scratch/state-probe", "/scratch/probe.go"], timeout=180),
                Step(["/scratch/state-probe"], timeout=120),
                Step(["/usr/local/go/bin/go", "tool", "covdata", "textfmt", "-i=/scratch/go-coverage",
                      "-o=/scratch/go-cover.txt"], timeout=60)]
        else:
            # Run a representative real case with an in-process exit-state observer.
            from .verifier import load_task, public_input_path
            if ctx.inputs.resolve() != (task / "tests/heldout").resolve():
                # A staged input root may not replace frozen reference metadata.
                ctx.inputs = public_input_path(task, ctx.inputs)
            module = load_task(task)
            cases = list(module.cases(ctx.inputs))
            if not cases:
                raise ValueError("Process-state workload has no representative case")
            case = next(iter(cases))
            expected = native_reference(module, case, cases, ctx.inputs)
            original_steps = module.prepare(case, ctx)
            steps = [Step(["cc", "-shared", "-fPIC", "/verifier-process/process_probe.c", "-o", "/scratch/state.so", "-lm"], timeout=60)]
            preparation, original_steps = dynamic_representative(original_steps, ctx, settings)
            steps.extend(preparation)
            setup_count = len(steps)
            for original in original_steps:
                original.env.update(LD_PRELOAD="/scratch/state.so", OPTIMBENCH_STATE_FILE="/scratch/native-state.jsonl",
                                    OPTIMBENCH_STATE_STEP=str(len(steps)))
                steps.append(original)
            env.update(module.ENV)
        results = executor.run(steps, ctx, env)
        if kind == "native":
            validate_native(module, case, ctx, steps, results, expected, setup_count)
        elif any(r.exit_code for r in results):
            raise ValueError("Process-state workload failed: " + b"\n".join(r.stderr for r in results).decode(errors="replace")[-3000:])
        if kind in ("goldmark", "go_sqlfmt"):
            value = json.loads(results[1].stdout)
        elif kind in ("python", "node"):
            value = json.loads((scratch / "process-state.json").read_text())
        else:
            file = scratch / "native-state.jsonl"
            value = {"native_processes": [json.loads(line) for line in file.read_text().splitlines()]} if file.exists() else {
                "coverage": "statically linked process; Rule D only"}
            if settings.get("representative_dynamic"):
                if not value.get("native_processes"):
                    raise ValueError("Representative dynamic probe did not emit native state")
                value["coverage"] = "representative dynamic probe of static target"
        if "environment" in value and isinstance(value["environment"], list):
            value["environment"].sort()
        native_identities = []
        for row in value.get("native_processes", []):
            identity = row.pop("thread_identity", None)
            if identity is not None:
                if row.get("threads") is None or len(identity.get("threads", [])) != row["threads"]:
                    raise ValueError("Native snapshot thread identity unavailable or contradictory")
                if row.get("children") is None:
                    raise ValueError("Native child-process metadata unavailable")
                identity["children_method"] = row.pop("children_method", None)
                native_identities.append(identity)
            row["environment"].sort()
        rule_d = [{"step_index": index,
                   "phase": ("observer-tooling" if (kind == "native" and index < setup_count)
                             or (kind in ("goldmark", "go_sqlfmt") and index != 1) else "workload"),
                   "command": steps[index].argv[0],
                   "t0_ns": getattr(result, "t0_ns", None), "t1_ns": getattr(result, "t1_ns", None),
                   "exit_observation": getattr(result, "telemetry", {}).get("exit_observation"),
                   "main_pid": getattr(result, "telemetry", {}).get("main_pid"),
                   "leftovers": result.leftovers}
                  for index, result in enumerate(results)]
        if any(p.get("threads") is None for r in results for p in r.leftovers):
            raise ValueError("Concrete Rule D live-worker identity unavailable")
        value["rule_d_leftovers"] = sum(len(r.leftovers) for r in results)
        value["rule_d_leftover_threads"] = sum(p.get("threads", 0) for r in results for p in r.leftovers)
        resident_count = sum(row["count"] for row in resident_workers(results))
        if resident_count:
            value["rule_d_resident_workers"] = resident_count
        try:
            if kind == "python":
                evidence = json.loads((scratch / "python-coverage.json").read_text())
                if evidence.get("run_id") != run_id:
                    raise ValueError("Python coverage belongs to a different probe")
            elif kind == "node":
                evidence = execution_coverage.node(scratch, build, run_id)
            elif kind in ("goldmark", "go_sqlfmt"):
                evidence = execution_coverage.go(scratch, build, module, kind, run_id)
            else:
                evidence = execution_coverage.unavailable(kind, run_id, "No separate native coverage build configured")
        except (OSError, ValueError, KeyError) as error:
            evidence = execution_coverage.unavailable(kind, run_id, str(error))
        observed = ObservedState(value, evidence, run_id)
        observed.rule_d_observations = rule_d
        observed.native_exit_identities = native_identities
        if isinstance(executor.container_prepare, dict):
            observed.container_prepare = executor.container_prepare
        return observed


def check(task, baseline, candidate, image, cpuset, public_inputs=None, projects=None):
    start = time.monotonic()
    observed_left = probe(task, baseline, image, cpuset, public_inputs, projects)
    try:
        observed_right = probe(task, candidate, image, cpuset, public_inputs, projects)
    except NativeOutputMismatch as error:
        if observed_left.get("status") == "not_configured":
            raise
        raise CandidateOutputRejected(error) from error
    coverage = {side: getattr(value, "execution_coverage", {}) for side, value in
                (("baseline", observed_left), ("candidate", observed_right))}
    rule_d = {side: getattr(value, "rule_d_observations", []) for side, value in
              (("baseline", observed_left), ("candidate", observed_right))}
    native_identities = {side: getattr(value, "native_exit_identities", []) for side, value in
                         (("baseline", observed_left), ("candidate", observed_right))}
    ids = {side + "_probe_id": getattr(value, "probe_id", None) for side, value in
           (("baseline", observed_left), ("candidate", observed_right))}
    left, right = dict(observed_left), dict(observed_right)
    if any(value.get("status") == "not_configured" for value in (left, right)):
        return {"passed": False, "status": "not_configured", "claimable": False,
                "speedup": None, "baseline": left, "candidate": right,
                "elapsed_seconds": time.monotonic() - start}
    # These snapshots are post-operation, not peak concurrency. Joined worker
    # threads are allowed; additional live post-call workers are still a leak.
    differences = {key: {"baseline": left.get(key), "candidate": right.get(key)}
                   for key in left.keys() | right.keys() if left.get(key) != right.get(key)
                   and not (key in {"threads", "python_threads", "goroutines"}
                            and isinstance(left.get(key), int) and isinstance(right.get(key), int)
                            and right[key] <= left[key])}
    preparation = {side: value.container_prepare for side, value in
                   (("baseline", observed_left), ("candidate", observed_right))
                   if hasattr(value, "container_prepare")}
    workers = {side: value.get("rule_d_resident_workers", 0)
               for side, value in (("baseline", left), ("candidate", right))}
    passed = not differences and not any(workers.values())
    return {"passed": passed, "status": "passed" if passed else "needs_review",
            "claimable": False, "speedup": None, "differences": differences, "baseline": left, "candidate": right,
            **({"resident_worker_violations": workers} if any(workers.values()) else {}),
            "execution_coverage": coverage, "rule_d_observations": rule_d,
            "native_exit_identities": native_identities, **ids,
            "elapsed_seconds": time.monotonic() - start,
            **({"container_prepare": preparation} if preparation else {})}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-inputs", type=Path)
    parser.add_argument("--projects", type=Path)
    args = parser.parse_args()
    result = check(args.task, args.baseline, args.candidate, args.image, args.cpuset,
                   args.public_inputs, args.projects)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(result["status"])
    raise SystemExit(0 if result["passed"] else 2)
