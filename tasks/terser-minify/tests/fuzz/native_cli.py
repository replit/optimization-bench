"""Local Linux calibration transport for the unchanged shared CLI observer.

Not a scoring executor. Requires root/unshare/mount, drops the actual observer
and CLI to UID 10000, and uses real read-only mounts. No Docker/GCP claim.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tools/runner")]
from tools.fuzz_runners import cli_faults


def mount(source, destination, readonly=False):
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["mount", "--bind", str(source), str(target)], check=True)
    if readonly:
        subprocess.run(["mount", "-o", "remount,bind,ro", str(target)], check=True)


def worker(config_path):
    config = json.loads(Path(config_path).read_text())
    subprocess.run(["mount", "--make-rprivate", "/"], check=True)
    mount(config["build"], "/app/build", readonly=True)
    mount(config["inputs"], "/cli-inputs", readonly=True)
    mount(config["scratch"], "/scratch")
    if config["fault"] == "readonly_dir":
        for name in config["output_dirs"]:
            mount(Path(config["inputs"]) / name, Path("/scratch/work") / name, readonly=True)
    os.setgroups([])
    os.setgid(10000)
    os.setuid(10000)
    cli_faults.worker("/scratch/request.json", "/scratch")


def run_pair(*, step, inputs, baseline, candidate, out, watch_inputs,
             watch_outputs=(), output_dirs=(), required_outputs=(), faults,
             case_id, payload_class, node, **unused):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    result = {"schema": "optimbench-cli-fault-pair-v1", "case_id": case_id,
              "payload_class": payload_class, "observations": {},
              "transport": "local Linux mount namespace; actual non-root shared observer"}
    for fault in faults:
        result["observations"][fault] = {}
        for arm, build in (("baseline", baseline), ("candidate", candidate)):
            scratch = out / (fault + "-" + arm)
            scratch.mkdir(mode=0o777)
            scratch.chmod(0o777)
            (scratch / "work").mkdir(mode=0o777)
            (scratch / "work").chmod(0o777)
            argv = [str(node), *[arg.replace("{build}", "/app/build") for arg in step.argv[1:]]]
            command = {"argv": argv, "cwd": step.cwd, "env": step.env,
                       "stdin_file": step.stdin_file, "timeout": step.timeout}
            request = {"fault": fault, "case_id": case_id, "payload_class": payload_class,
                       "step": command, "watch_inputs": list(watch_inputs),
                       "watch_outputs": list(watch_outputs), "output_dirs": list(output_dirs),
                       "required_outputs": list(required_outputs),
                       "max_stream_bytes": 16 * 1024 * 1024}
            (scratch / "request.json").write_text(json.dumps(request))
            config = {"build": str(Path(build).resolve()), "inputs": str(Path(inputs).resolve()),
                      "scratch": str(scratch.resolve()), "fault": fault, "output_dirs": list(output_dirs)}
            config_path = scratch / "native-config.json"
            config_path.write_text(json.dumps(config))
            run = subprocess.run(["unshare", "-m", sys.executable, str(Path(__file__).resolve()),
                                  str(config_path)], capture_output=True, timeout=60)
            (scratch / "transport.stdout").write_bytes(run.stdout)
            (scratch / "transport.stderr").write_bytes(run.stderr)
            if run.returncode:
                raise ValueError("Native CLI transport failed: " + run.stderr.decode()[:2000])
            row = json.loads((scratch / "observer.json").read_text())
            if row.get("status") != "observed":
                raise ValueError("Shared CLI observer failed: " + str(row.get("reason")))
            row.update(arm=arm)
            cli_faults._retain_streams(row, scratch, out, case_id, fault, arm, 16 * 1024 * 1024)
            result["observations"][fault][arm] = row
    (out / "cli-faults.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


if __name__ == "__main__":
    worker(sys.argv[1])
