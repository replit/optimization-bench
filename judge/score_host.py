"""Probe the actual Docker execution host; no registered scoring machine needed."""
import json
from pathlib import Path
import re
import tempfile

from .verifier import HARDENING
from .telemetry import cpu_list
from runner import Context, Step, HarnessError
from docker_executor import DockerExecutor

PROBE = """
import importlib.util,json,os
s=importlib.util.spec_from_file_location("host_telemetry","/judge-host.py")
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
cores=sorted(os.sched_getaffinity(0))
print(json.dumps({"affinity":cores,"host":m.fingerprint(cores,"unregistered"),
                  "counters":m.counters(cores),"cpu_max":m.text("/sys/fs/cgroup/cpu.max")}))
"""


def probe(image, cpuset=None):
    with tempfile.TemporaryDirectory(prefix="judge-host-") as temporary:
        root = Path(temporary)
        build = root / "unused"
        build.mkdir()
        ctx = Context(root, build, root, root, root, docker=True)
        executor = DockerExecutor(image, cpuset, memory="16g", extra_docker_flags=[
            *HARDENING, "-v", f"{Path(__file__).with_name('telemetry.py')}:/judge-host.py:ro"])
        result = executor.run([Step(["python3", "-c", PROBE], timeout=30)], ctx, {})
        if result[0].exit_code != 0:
            raise HarnessError("Docker host probe failed: " + result[0].stderr.decode(errors="replace"))
        value = json.loads(result[0].stdout)
        value["counters"]["cpu"] = {int(k): v for k, v in value["counters"]["cpu"].items()}
        return value


def select(image, requested=None):
    initial = probe(image)
    available = initial["affinity"]
    if len(available) < 8:
        raise HarnessError("Provisional scoring requires at least eight available logical CPUs")
    cores = cpu_list(requested) if requested else available[:8]
    if len(cores) != 8 or not set(cores) <= set(available):
        raise HarnessError("Select exactly eight CPUs available to the Docker host")
    cpuset = ",".join(map(str, cores))
    try:
        effective = probe(image, cpuset)
    except HarnessError as error:
        # Diagnose cpuset support only. Do not hide arbitrary Docker failures.
        message = str(error).lower()
        if not any(word in message for word in ("cpuset", "cpus", "not supported", "not available")):
            raise
        effective = initial
        cpuset = None
        failure = str(error)
    else:
        failure = None
    took_effect = effective["affinity"] == cores
    if cpuset and not took_effect:
        # Some kernels/daemons warn and ignore an unsupported cpuset flag.
        # Use the explicitly unbound probe, not a false claim of pinning.
        failure = "Docker accepted cpuset but did not constrain CPU affinity"
        cpuset = None
        effective = initial
    effective["host"]["cpu_max"] = effective["cpu_max"]
    return {"cpuset": cpuset, "requested_cores": cores, "available_cores": available,
            "effective_cores": effective["affinity"], "pinning_took_effect": took_effect,
            "pinning_error": failure, "host": effective["host"], "probe": effective}


def require_separate(execution, agent_host=None, attested=False):
    current = execution["host"].get("boot_id_sha256")
    if agent_host is not None:
        if not isinstance(agent_host, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", agent_host):
            raise HarnessError("agent_host_fingerprint must be a SHA-256 kernel identity")
        agent_host = agent_host.lower()
    if agent_host and current and agent_host == current:
        raise HarnessError("Scoring must use a different sandbox/kernel than the agent")
    if not agent_host and not attested:
        raise HarnessError("Supply agent_host_fingerprint in submission.json or attest --separate-sandbox")
    if agent_host and not current and not attested:
        raise HarnessError("Host identity unavailable; operator must attest --separate-sandbox")
    return {"agent_host_fingerprint": agent_host,
            "separate_kernel_verified": bool(agent_host and current and agent_host != current),
            "operator_attested": bool(attested)}
