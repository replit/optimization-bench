"""Public one-container-per-case prototype; infrastructure errors are invalid runs."""
import json
from pathlib import Path
import re
import subprocess
import uuid
import tempfile
from preparation import metadata, validate_receipt
from runner import HarnessError, StepResult, base_env, contained


class DockerExecutor:
    def __init__(self, image, cpuset=None, memory="16g", pids_limit=512,
                 extra_docker_flags=None, tmpfs="/tmp", task=None):
        if not image:
            raise HarnessError("Docker executor requires an image")
        self.image, self.cpuset, self.memory, self.pids_limit = image, cpuset, memory, pids_limit
        self.extra_docker_flags, self.tmpfs = list(extra_docker_flags or []), tmpfs
        self.case_time_ns = None
        self.task, self.container_prepare = task, None

    def run(self, steps, ctx, environment):
        task = self.task
        if task is None and ctx.workload.name == "workload" and ctx.workload.parent.name == "environment":
            task = ctx.workload.parent.parent
        try:
            preparation = metadata(task)
        except (ValueError, OSError, KeyError) as error:
            raise HarnessError("Invalid container preparation: " + str(error)) from error
        self.container_prepare = None
        request = []
        python_shutdown = False
        for index, step in enumerate(steps):
            env = {**environment, **step.env, **base_env(ctx.scratch, index)}
            if step.home_cwd:
                env["HOME"] = str(contained(ctx.scratch, step.cwd))
            if not step.resident and re.fullmatch(
                    r"python(?:\d+(?:\.\d+)*)?", Path(step.argv[0]).name):
                application_path = ctx.target(env.get("PYTHONPATH", ""))
                env["PYTHONPATH"] = "/judge-python" + (
                    ":" + application_path if application_path else "")
                python_shutdown = True
            request.append({"argv": [ctx.target(x) for x in step.argv],
                            "cwd": ctx.target(contained(ctx.scratch, step.cwd)),
                            "env": {k: ctx.target(v) for k, v in env.items()}, "timeout": step.timeout,
                            "resident": step.resident,
                            "stdin_file": None if step.stdin_file is None else
                                ctx.target(contained(ctx.scratch, step.stdin_file))})
        if preparation is None:
            (ctx.scratch / "steps.json").write_text(json.dumps(request))
        # No chown requirement on the host: the isolated uid may write the case's scratch only.
        for path in [ctx.scratch, *ctx.scratch.rglob("*")]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | (0o777 if path.is_dir() else 0o666))
        shim = Path(__file__).with_name("shim.py").resolve()
        name = "optimbench-case-" + uuid.uuid4().hex
        command = ["docker", "run", "--name", name, "--rm", "--network", "none", "--ipc", "private",
                   "--read-only", "--tmpfs", self.tmpfs, "--memory", self.memory,
                   "--pids-limit", str(self.pids_limit), "--user", "10000:10000",
                   "-v", f"{ctx.build}:/app/build:ro", "-v", f"{ctx.scratch}:/scratch",
                   "-v", f"{shim}:/shim.py:ro",
                   "-v", f"{Path(__file__).with_name('runner.py').resolve()}:/runner.py:ro",
                   "--entrypoint", "python3"]
        if self.cpuset:
            command += ["--cpuset-cpus", self.cpuset]
        command += self.extra_docker_flags
        if python_shutdown:
            startup = Path(__file__).with_name("python_startup") / "sitecustomize.py"
            command += ["-v", f"{startup.resolve()}:/judge-python/sitecustomize.py:ro"]
        protected = None
        request_path = "/scratch/steps.json"
        if preparation is not None:
            protected = tempfile.TemporaryDirectory(prefix="judge-container-prepare-", dir="/tmp")
            request_file = Path(protected.name) / "steps.json"
            request_path = "/judge-steps.json"
            substitutions = {"{steps}": request_path, "{inputs}": "/app/workload/inputs"}
            preparation = {**preparation,
                "argv": [substitutions.get(arg, arg) for arg in preparation["argv"]],
                "cwd": "/scratch",
                # A candidate PYTHONPATH/LD_PRELOAD must not redirect a
                # protected hook through editable runtime code.
                "env": {k: ctx.target(v) for k, v in base_env(ctx.scratch, "prepare").items()}}
            request_file.write_text(json.dumps({"steps": request, "container_prepare": preparation,
                                                "output": "/scratch"}))
            command += ["-v", f"{request_file}:{request_path}:ro",
                        "-v", f"{ctx.inputs}:/app/workload/inputs:ro"]
        command += [self.image, "/shim.py", request_path]
        try:
            run = subprocess.run(command, capture_output=True,
                timeout=sum(s.timeout for s in steps) + 120 + (600 if preparation is not None else 0))
        except (subprocess.TimeoutExpired, OSError) as error:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            raise HarnessError("Docker/shim infrastructure failed: " + str(error)) from error
        except BaseException as cancellation:
            # Controller cancellation (including the untimed CPU subset deadline)
            # must not strand a named --rm container after its client is killed.
            try:
                cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
                if cleanup.returncode:
                    cancellation.add_note("Docker cancellation cleanup failed: " +
                                          cleanup.stderr.decode(errors="replace"))
            except (subprocess.TimeoutExpired, OSError) as error:
                cancellation.add_note("Docker cancellation cleanup failed: " + str(error))
            raise
        finally:
            if protected is not None:
                protected.cleanup()
        if preparation is not None:
            try:
                self.container_prepare = json.loads((ctx.scratch / "container-prepare.json").read_text())
                try:
                    validate_receipt(self.container_prepare, preparation)
                except ValueError as error:
                    raise HarnessError("Container preparation failed: " + json.dumps(self.container_prepare)) from error
            except (OSError, ValueError) as error:
                raise HarnessError("Missing container preparation receipt: " +
                                   run.stderr.decode(errors="replace")) from error
        results = []
        for index in range(len(steps)):
            try:
                record = json.loads((ctx.scratch / f"step-{index}.json").read_text())
                if "harness_error" in record:
                    raise HarnessError(record["harness_error"])
                results.append(StepResult(stdout=(ctx.scratch / f"step-{index}.stdout").read_bytes(),
                                          stderr=(ctx.scratch / f"step-{index}.stderr").read_bytes(), **record))
            except (OSError, ValueError) as error:
                raise HarnessError("Docker shim failed: " + run.stderr.decode(errors="replace")) from error
        if run.returncode:
            raise HarnessError("Docker shim/container failed: " + run.stderr.decode(errors="replace"))
        try:
            self.case_time_ns = json.loads((ctx.scratch / "case-result.json").read_text())["case_time_ns"]
        except (OSError, ValueError, KeyError) as error:
            raise HarnessError("Missing shim case timing summary") from error
        if self.case_time_ns != sum(r.t1_ns - r.t0_ns for r in results):
            raise HarnessError("Shim case timing summary differs from its step intervals")
        if self.container_prepare is not None and results:
            results[0].telemetry["container_prepare"] = self.container_prepare
        return results
