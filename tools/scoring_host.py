"""Early scoring/agent-host dependency and Docker-network preflight.

Read-only `check` is safe before reservation, not a claim of host quietness.
`fix-network` explicitly changes bridge MTUs/MSS rules, requires a quiet-host
acknowledgement and never restarts Docker. It is intentionally rerunnable after
daemon/host restarts. No command pulls all LFS data or acquires private inputs.
Bootstrap a checkout with GIT_LFS_SKIP_SMUDGE=1 git clone first; preflight cannot
undo corpus downloads performed by an earlier clone.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BUILD_X = {
    "x86_64": ("amd64", "c41ed17ec05b6ebb50eeb02fb26cce90f16cd260b8d26ce73963428c6b2d6508"),
    "aarch64": ("arm64", "ad33819d085a635e3b4400a412bd2b4e943bfbc830366d78f50579bae48f8053"),
}
BUILD_X_VERSION = "v0.24.0"
COMMENT = "optimbench-docker-pmtu-v1"


class PreflightError(ValueError):
    def __init__(self, failures):
        self.failures = failures
        super().__init__("\n".join(
            item["check"] + ": " + item["reason"] + "\n  Run: " + "\n  Then: ".join(item["commands"])
            for item in failures))


def run(argv, *, cwd=None):
    # Never echo command output/config/environment: Docker endpoint errors can
    # contain credentials. Callers expose only safe, parsed fields or exit codes.
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=30)
        return completed.returncode, completed.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def apt(*packages):
    prefix = "" if os.geteuid() == 0 else "sudo "
    return prefix + "apt-get update && " + prefix + "apt-get install -y " + " ".join(packages)


def parser_setup(venv=None):
    venv = str(venv or os.environ.get("JUDGE_VENV", "/opt/judge"))
    return ("python3 -m venv " + shlex.quote(venv) + " && " +
            shlex.quote(str(Path(venv) / "bin/python")) +
            " -m pip install --require-hashes --only-binary=:all: -r judge/requirements-interface.lock "
            "-r judge/requirements-host-registry.lock")


def interpreter_check(root=ROOT):
    requirement = json.loads((Path(root) / "judge/host-runtime.lock.json").read_text())["python"]
    minor = f"{sys.version_info[0]}.{sys.version_info[1]}"
    state = {"implementation": platform.python_implementation(), "minor": minor,
             "release_level": getattr(sys.version_info, "releaselevel", "final"),
             "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
             "supported_minor_versions": requirement["supported_minor_versions"]}
    good = (state["implementation"] == requirement["implementation"]
            and minor in requirement["supported_minor_versions"]
            and state["release_level"] == requirement["release_level"]
            and state["gil_disabled"] == requirement["gil_disabled"])
    failures = [] if good else [{
        "check": "host-interpreter",
        "reason": "Judge runtime lock requires standard stable CPython " +
                  "/".join(requirement["supported_minor_versions"]) + "; current " +
                  state["implementation"] + " " + minor + " is not a supported runtime/ABI",
        "commands": [apt("python3.12", "python3.12-venv"),
                     "JUDGE_PYTHON=python3.12 JUDGE_VENV=/opt/judge-py312 sh tools/setup_scoring_host.sh",
                     "Alternatively use installed stable CPython 3.14 with JUDGE_PYTHON and a fresh JUDGE_VENV"],
    }]
    return state, failures


def parser_check(root=ROOT):
    failures, versions = [], {}
    locks = ("judge/requirements-interface.lock", "judge/requirements-host-registry.lock")
    locked = [item for name in locks for item in re.findall(
        r"^([a-z][a-z0-9-]+)==([^\s]+)", (Path(root) / name).read_text(), re.M)]
    reason = None
    if sys.prefix == sys.base_prefix:
        reason = "judge must run from its dedicated interface-lock venv, not system Python"
    for name, expected in locked:
        try:
            module = importlib.import_module({"rpds-py": "rpds"}.get(name, name.replace("-", "_")))
            actual = importlib.metadata.version(name)
            if actual != expected:
                raise ImportError("version does not match interface lock")
            if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
                raise ImportError("parser imported outside the active venv")
            versions[name] = actual
        except (ImportError, importlib.metadata.PackageNotFoundError, TypeError):
            reason = "missing/mismatched locked venv dependency: " + name + "==" + expected
    if not locked:
        reason = "interface dependency lock is empty"
    if reason is None:
        try:
            from judge.lexical import parse
            for suffix in (".c", ".cpp", ".rs", ".go", ".py", ".js", ".ts", ".tsx", ".java"):
                parse("", suffix)
            from jsonschema import Draft202012Validator, ValidationError
            Draft202012Validator.check_schema({"type": "object", "required": ["machine"]})
            try:
                Draft202012Validator({"type": "object", "required": ["machine"]}).validate({})
            except ValidationError:
                pass
            else:
                raise ValueError("registry validator did not enforce required fields")
        except (ImportError, ValueError, TypeError):
            reason = "locked grammar/schema dependencies cannot initialize required validators"
    if reason:
        failures.append({"check": "interface-venv", "reason": reason,
                         "commands": [apt("python3", "python3-venv"), parser_setup(),
                                      shlex.quote(str(Path(os.environ.get("JUDGE_VENV", "/opt/judge")) / "bin/python")) +
                                      " -m tools.scoring_host check"]})
    return {"python": sys.executable, "venv": sys.prefix, "locked_versions": versions}, failures


def bridge_inventory():
    code, raw = run(["docker", "network", "ls", "--filter", "driver=bridge", "--format", "{{.ID}}"])
    if code != 0:
        raise ValueError("cannot enumerate Docker bridge networks")
    identifiers = raw.split()
    if not identifiers:
        raise ValueError("Docker has no bridge network")
    code, raw = run(["docker", "network", "inspect", *identifiers])
    if code != 0:
        raise ValueError("cannot inspect Docker bridge networks")
    networks = json.loads(raw)
    result = []
    for network in networks:
        name = network.get("Options", {}).get("com.docker.network.bridge.name")
        name = name or ("docker0" if network["Name"] == "bridge" else "br-" + network["Id"][:12])
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name):
            raise ValueError("invalid Docker bridge interface identity")
        code, raw = run(["ip", "-j", "link", "show", "dev", name])
        if code != 0:
            raise ValueError("Docker bridge interface unavailable: " + name)
        links = json.loads(raw)
        if len(links) != 1 or type(links[0].get("mtu")) is not int:
            raise ValueError("invalid bridge MTU response")
        result.append({"interface": name, "mtu": links[0]["mtu"],
                       "ipv6": bool(network.get("EnableIPv6"))})
    return result


def mss_rule(interface, direction):
    if direction not in ("-i", "-o"):
        raise ValueError("invalid MSS direction")
    return ["FORWARD", direction, interface, "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
            "-m", "comment", "--comment", COMMENT, "-j", "TCPMSS", "--clamp-mss-to-pmtu"]


def local_daemon():
    # Do not report URI values: DOCKER_HOST/context endpoints may contain secrets.
    endpoint = os.environ.get("DOCKER_HOST")
    if endpoint:
        return endpoint.startswith("unix://")
    code, raw = run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"])
    if code != 0:
        return False
    try:
        return json.loads(raw).startswith("unix://")
    except (ValueError, AttributeError):
        return False


def network_check(mtu=1400):
    if type(mtu) is not int or not 1280 <= mtu <= 9000:
        raise ValueError("Docker MTU must be an explicit integer between 1280 and 9000")
    if not local_daemon():
        raise ValueError("network preflight/fix must run on the local Unix-socket Docker daemon host")
    bridges = bridge_inventory()
    problems = []
    for bridge in bridges:
        if bridge["mtu"] > mtu:
            problems.append(bridge["interface"] + " exceeds configured MTU " + str(mtu))
        for program in (["iptables", "ip6tables"] if bridge["ipv6"] else ["iptables"]):
            for direction in ("-i", "-o"):
                code, _ = run([program, "-w", "5", "-t", "mangle", "-C",
                               *mss_rule(bridge["interface"], direction)])
                if code != 0:
                    problems.append(program + " MSS rule missing or unreadable for " + bridge["interface"])
    return {"configured_max_mtu": mtu, "bridges": bridges,
            "packet_connectivity_tested": False}, problems


def fix_network(mtu=1400, *, acknowledged=False):
    if not acknowledged:
        raise ValueError("fix-network changes host networking; require --confirm-no-timing-session")
    if os.geteuid() != 0:
        raise ValueError("fix-network requires root/CAP_NET_ADMIN on the Docker daemon host")
    if type(mtu) is not int or not 1280 <= mtu <= 9000:
        raise ValueError("Docker MTU must be between 1280 and 9000")
    if not local_daemon():
        raise ValueError("refusing to change local networking for a remote Docker daemon")
    changes = []
    for bridge in bridge_inventory():
        interface = bridge["interface"]
        # Never increase an existing smaller MTU.
        if bridge["mtu"] > mtu:
            code, _ = run(["ip", "link", "set", "dev", interface, "mtu", str(mtu)])
            if code != 0:
                raise ValueError("cannot set bridge MTU; root/CAP_NET_ADMIN required")
            changes.append({"interface": interface, "mtu": mtu})
        for program in (["iptables", "ip6tables"] if bridge["ipv6"] else ["iptables"]):
            for direction in ("-i", "-o"):
                rule = mss_rule(interface, direction)
                code, _ = run([program, "-w", "5", "-t", "mangle", "-C", *rule])
                if code == 0:
                    continue
                # 1 means a rule is absent; permission/tool/runtime errors must
                # not be mistaken for absence and silently duplicated.
                if code != 1:
                    raise ValueError("cannot inspect " + program + " rules; install iptables and grant CAP_NET_ADMIN")
                code, _ = run([program, "-w", "5", "-t", "mangle", "-A", *rule])
                if code != 0:
                    raise ValueError("cannot add " + program + " MSS rule")
                changes.append({"interface": interface, "mss_direction": direction, "family": program})
    state, problems = network_check(mtu)
    if problems:
        raise ValueError("network fix did not verify: " + "; ".join(problems))
    return {"changes": changes, "verified": state,
            "persistence": "live bridge/rules only; rerun after restart/new bridge creation, no daemon restart performed"}


def install_buildx(destination=None):
    if platform.system() != "Linux" or platform.machine() not in BUILD_X:
        raise ValueError("pinned buildx installer supports Linux x86_64/aarch64 only")
    architecture, expected = BUILD_X[platform.machine()]
    destination = Path(destination or
                       (Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "cli-plugins/docker-buildx"))
    if destination.is_symlink():
        raise ValueError("refusing to overwrite a symlinked buildx plugin")
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/docker/buildx/releases/download/{BUILD_X_VERSION}/buildx-{BUILD_X_VERSION}.linux-{architecture}"
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return {"status": "already-installed", "version": BUILD_X_VERSION, "sha256": expected}
    if destination.exists():
        raise ValueError("existing nonmatching buildx plugin retained; choose --destination or remove it explicitly")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        path = Path(temporary.name)
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                shutil.copyfileobj(response, temporary)
            temporary.flush()
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("buildx download SHA256 mismatch")
            path.chmod(0o755)
            # Do not replace an operator's concurrently installed plugin.
            os.link(path, destination)
        finally:
            path.unlink(missing_ok=True)
    return {"status": "installed", "version": BUILD_X_VERSION, "sha256": expected}


def binutils_check(tools):
    failures = []
    for program in ("nm", "c++filt"):
        if program not in tools:
            continue  # The required-binary check supplies the installation command.
        code, version = run([program, "--version"])
        if code != 0 or "GNU" not in version:
            failures.append({"check": program + "-binutils",
                             "reason": "judge requires compatible GNU " + program,
                             "commands": [apt("binutils")]})
            continue
        if program == "c++filt":
            code, output = run(["c++filt", "-n", "_Z3foov", "_ZTV3Foo",
                                "_Zoptimbench_invalid_mangling", "optimbench_unmangled_symbol"])
            if code != 0 or output.splitlines() != [
                    "foo()", "vtable for Foo", "_Zoptimbench_invalid_mangling", "optimbench_unmangled_symbol"]:
                failures.append({"check": "c++filt-demangling",
                                 "reason": "GNU c++filt -n must demangle known Itanium symbols and preserve unknown names",
                                 "commands": [apt("binutils"), "c++filt -n _Z3foov _ZTV3Foo _Zoptimbench_invalid_mangling optimbench_unmangled_symbol"]})
    return failures


def scoring_preflight(*, root=ROOT, mtu=1400, require_ffmpeg=False):
    root = Path(root)
    interpreter, runtime_failures = interpreter_check(root)
    if runtime_failures:
        raise PreflightError(runtime_failures)  # Clear cause before any audit/dependency mismatch.
    parser_state, failures = parser_check(root)
    record = {"schema": "optimbench-host-preflight-v1", "interpreter": interpreter,
              "parser": parser_state, "tools": {},
              "scope": "host prerequisites only, not task qualification or quiet-host evidence"}
    from tools.judge_command_audit import check as command_audit
    try:
        record["external_commands"] = command_audit(root)
    except (ValueError, OSError):
        failures.append({"check": "external-command-audit", "reason": "judge/task dependency audit missing or changed since review",
                         "commands": ["python3 -m tools.judge_command_audit --check",
                                      "Review changed launch sites/host-versus-worker dependencies, then python3 -m tools.judge_command_audit --write --reviewed"]})
    if platform.system() != "Linux":
        failures.append({"check": "platform", "reason": "judge requires a Linux Docker/cgroup-v2 daemon host",
                         "commands": ["Use the Linux scoring/agent host and rerun sh tools/setup_scoring_host.sh"]})
    binaries = {
        "git": ("git",), "git-lfs": ("git", "git-lfs"), "nm": ("binutils",),
        "c++filt": ("binutils",),
        "docker": ("docker.io",), "ip": ("iproute2",), "iptables": ("iptables",),
        "sh": ("dash",),
    }
    if require_ffmpeg:
        binaries["ffmpeg"] = ("ffmpeg",)
    for executable, packages in binaries.items():
        path = shutil.which(executable)
        if path is None:
            failures.append({"check": executable, "reason": "required external binary is missing",
                             "commands": [apt(*packages)]})
        else:
            record["tools"][executable] = path
    failures.extend(binutils_check(record["tools"]))
    if "git" in record["tools"] and "git-lfs" in record["tools"]:
        code, _ = run(["git", "lfs", "version"], cwd=root)
        configured, value = run(["git", "config", "--local", "--get", "filter.lfs.process"], cwd=root)
        if code != 0 or configured != 0 or "git-lfs" not in value:
            failures.append({"check": "git-lfs-local", "reason": "Git LFS is not initialized locally in the verifier checkout",
                             "commands": ["git lfs install --local --skip-smudge",
                                          "Rerun preflight; use only scoped public LFS acquisition when requested"]})
    if "docker" in record["tools"]:
        code, version = run(["docker", "info", "--format", "{{.CgroupVersion}} {{.CgroupDriver}}"])
        if code != 0 or not version.strip().startswith("2 "):
            failures.append({"check": "docker-cgroup-v2", "reason": "Docker daemon unavailable or not using cgroup v2",
                             "commands": ["sudo systemctl enable --now docker",
                                          "Configure the Linux daemon host with cgroup v2 before reservation; rerun docker info"]})
        else:
            record["docker_cgroup"] = version.strip()
        code, output = run(["docker", "buildx", "version"])
        if code != 0 or "buildx" not in output:
            failures.append({"check": "docker-buildx", "reason": "Docker buildx plugin unavailable",
                             "commands": ["python3 -m tools.scoring_host install-buildx", "docker buildx version"]})
        if code == 0 and not failures:
            try:
                state, problems = network_check(mtu)
                record["network"] = state
                if problems:
                    raise ValueError("; ".join(problems))
            except (ValueError, KeyError, TypeError):
                failures.append({"check": "docker-network", "reason": "bridge MTU/MSS rules unavailable, unverified or exceed configured maximum",
                                 "commands": [apt("iproute2", "iptables"),
                                              "sudo python3 -m tools.scoring_host fix-network --mtu " + str(mtu) + " --confirm-no-timing-session",
                                              "Rerun check after daemon/host restarts or new bridge creation"]})
    try:
        controllers = set(Path("/sys/fs/cgroup/cgroup.controllers").read_text().split())
        readable = {"cpu", "memory", "pids", "cpuset"} <= controllers
    except OSError:
        readable = False
    if not readable:
        failures.append({"check": "cgroup-v2", "reason": "local cgroup-v2 controllers are not readable",
                         "commands": ["Use the Linux cgroup-v2 daemon host; do not run this check from a remote Docker client"]})
    else:
        record["local_cgroup_controllers"] = sorted(controllers)
    if failures:
        raise PreflightError(failures)
    record["ready"] = True
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--task", action="append", default=[])
    check.add_argument("--stage-public", action="store_true")
    check.add_argument("--mtu", type=int, default=1400)
    check.add_argument("--out", type=Path)
    fix = commands.add_parser("fix-network")
    fix.add_argument("--mtu", type=int, default=1400)
    fix.add_argument("--confirm-no-timing-session", action="store_true")
    install = commands.add_parser("install-buildx")
    install.add_argument("--destination", type=Path)
    commands.add_parser("runtime-check", help="Bootstrap interpreter/ABI check, before venv/package setup")
    args = parser.parse_args()
    try:
        if args.command == "runtime-check":
            result, failures = interpreter_check()
            if failures:
                raise PreflightError(failures)
        elif args.command == "check":
            from judge.public_input_preflight import ensure_public_inputs, needs_ffmpeg
            tasks = [ROOT / "tasks" / name for name in args.task]
            result = scoring_preflight(mtu=args.mtu, require_ffmpeg=any(needs_ffmpeg(t) for t in tasks))
            result["public_inputs"] = [ensure_public_inputs(t, fetch=args.stage_public) for t in tasks]
            if not tasks:
                result["public_inputs_scope"] = "not requested; task readiness not established"
            if args.out:
                args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        elif args.command == "fix-network":
            result = fix_network(args.mtu, acknowledged=args.confirm_no_timing_session)
        else:
            result = install_buildx(args.destination)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (PreflightError, ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
