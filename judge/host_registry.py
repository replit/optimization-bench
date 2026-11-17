"""Read-only host registration and fail-closed performance eligibility.

Unknown observations are None, not plausible defaults. Such a fingerprint has
the pinned schema's field names but intentionally cannot validate as a registered
host. Run the collector on the executing judge host, not on a Docker client.
Install requirements-host-registry.txt for pinned-schema validation.
"""
import gzip
import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
import urllib.error
import urllib.request

from .telemetry import cpu_list
from .image_recipe import agent_only_context_path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = Path(__file__).with_name("hosts")


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class RegistrationError(ValueError):
    """A denied preflight; reasons are stable machine-readable codes."""

    def __init__(self, reasons, links=None):
        self.reasons = tuple(dict.fromkeys(reasons))
        self.links = _freeze(links or {})
        super().__init__(", ".join(self.reasons))


@dataclass(frozen=True)
class RegistrationContext:
    """Deeply immutable pre-measurement observations, never a claimability grant."""

    task_id: str
    host_id: str
    registry_entry_sha256: str
    live_fingerprint: Mapping
    judge_tag: str
    judge_commit: str
    judge_code_sha256: str
    task_policy_sha256: str
    qualification_receipt_sha256: str
    scope: str
    qualified_units: tuple
    cpu_class: str
    links: Mapping
    execution_host: Mapping
    judge_identity: Mapping
    mandatory_gates: tuple
    gates: Mapping
    session_control: str = None

    def __post_init__(self):
        for name in ("live_fingerprint", "links", "execution_host", "judge_identity", "gates",
                     "qualified_units", "mandatory_gates"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))

    def as_dict(self):
        """Return independent JSON-serializable copies for receipt persistence."""
        return {name: _thaw(getattr(self, name)) for name in self.__dataclass_fields__}


def canonical_sha256(value):
    """The canonical content identity specified in docs/host-registry.md."""
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False).encode("utf-8"))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _command(*args):
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                                timeout=5, check=True,
                                env={**os.environ, "LC_ALL": "C"})
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _text(path):
    try:
        return Path(path).read_text().strip() or None
    except (OSError, UnicodeError):
        return None


def _metadata(name):
    # Do not send metadata requests through HTTP_PROXY, or trust arbitrary HTTP
    # responses as evidence that this is a GCE instance.
    request = urllib.request.Request(
        "http://169.254.169.254/computeMetadata/v1/" + name,
        headers={"Metadata-Flavor": "Google"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=.3) as response:
            if response.headers.get("Metadata-Flavor") != "Google":
                return None
            return response.read(4096).decode("utf-8").strip() or None
    except (OSError, UnicodeError, urllib.error.URLError):
        return None


def collect_fingerprint():
    """Return live schema-shaped observations; unavailable fields are None.

    judge_cpus is this process's effective scheduler affinity. system_cpus is
    the set system services can actually run on: the systemd manager's (PID 1)
    scheduler affinity intersected with system.slice's effective cgroup CPU set
    (v2, then v1), using whichever is observable. provision-host.sh confines
    system services with systemd CPUAffinity, not cgroup AllowedCPUs (so the
    judge's explicit --cpuset-cpus can still reach every CPU); there the cgroup
    set is all CPUs and the manager affinity is the reserved set. It is never
    inferred from nominal machine size, online CPUs, or the complement of
    judge_cpus.
    """
    metadata = {key: _metadata(endpoint) for key, endpoint in {
        "project_id": "project/project-id", "zone": "instance/zone",
        "instance_id": "instance/id", "machine_type": "instance/machine-type",
    }.items()}
    for key in ("zone", "machine_type"):
        if metadata[key]:
            metadata[key] = metadata[key].rsplit("/", 1)[-1]
    raw = _command("lscpu", "--json")
    try:
        fields = {row["field"].rstrip(":"): row["data"]
                  for row in json.loads(raw)["lscpu"]}
    except (TypeError, ValueError, KeyError):
        fields = {}
    flags = fields.get("Flags") or fields.get("Features")
    model = fields.get("Model name")
    try:
        threads = int(fields.get("Thread(s) per core"))
    except (TypeError, ValueError):
        threads = None
    try:
        judge_cpus = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        judge_cpus = None
    sliced = None
    for path in ("/sys/fs/cgroup/system.slice/cpuset.cpus.effective",
                 "/sys/fs/cgroup/cpuset/system.slice/cpuset.effective_cpus",
                 "/sys/fs/cgroup/cpuset/system.slice/cpuset.cpus"):
        value = _text(path)
        if value:
            try:
                sliced = cpu_list(value)
            except ValueError:
                pass
            break
    try:
        manager = sorted(os.sched_getaffinity(1))
    except (AttributeError, OSError):
        manager = None
    observed = [set(cpus) for cpus in (manager, sliced) if cpus]
    system_cpus = sorted(set.intersection(*observed)) if observed else None
    return {
        "provider": "gcp" if any(metadata.values()) else None,
        **metadata,
        "architecture": _command("uname", "-m") or None,
        "threads_per_core": threads,
        "cpu_model": " ".join(model.split()) if model else None,
        "cpu_flags_sha256": _sha("\n".join(sorted(set(flags.split()))).encode())
                            if flags else None,
        "kernel": _command("uname", "-r") or None,
        "docker_version": _command("docker", "version", "--format", "{{.Server.Version}}") or None,
        "containerd_version": _command("containerd", "--version") or None,
        "judge_cpus": judge_cpus,
        "system_cpus": system_cpus,
    }


def _production(name):
    parts = Path(name).parts
    if not parts or parts[0] not in ("judge", "tools"):
        return False
    if any(p in ("tests", "__pycache__", ".pytest_cache") for p in parts):
        return False
    if name.startswith("judge/hosts/"):
        return name in ("judge/hosts/registry.schema.json",
                        "judge/hosts/qualification.schema.json")
    return (parts[0] == "judge" or name.startswith("tools/runner/") or
            len(parts) == 2 and name.endswith(".py"))


def _inventory(paths):
    result = {}
    for path in sorted(set(paths)):
        if any(p.is_symlink() for p in (path, *path.parents) if p != ROOT):
            raise ValueError("identity_symlink")
        if path.is_file():
            result[path.relative_to(ROOT).as_posix()] = _sha(path.read_bytes())
    if not result:
        raise ValueError("identity_empty")
    return result


def _code_inventory():
    return _inventory(p for directory in ("judge", "tools")
                      for p in (ROOT / directory).rglob("*")
                      if _production(p.relative_to(ROOT).as_posix()))


def judge_code_sha256():
    """Hash production judge/tools runner files, excluding tests and host data.

    The two pinned schemas ARE code inputs. Registration JSON, qualification
    receipts/evidence, and judge/tools tests are NOT production code inputs.
    Paths and byte SHA-256s are bound using canonical JSON.
    """
    return canonical_sha256(_code_inventory())


def _task_inventory(task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", task_id):
        raise ValueError("invalid_task_id")
    task = ROOT / "tasks" / task_id
    required = [task / name for name in
                ("task.json", "release.json", "tests/judge.json",
                 "environment/workload/task.py")]
    if not all(p.is_file() for p in required):
        raise ValueError("task_policy_missing")
    # Protected configuration, image/lock manifests, top-level workload drivers,
    # and differential gate implementations. Dataset/project trees and archived
    # measurement receipts are bound by their lock manifests, not rehashed here.
    paths = list(required)
    for directory in ("environment", "environment/workload", "tests"):
        paths.extend(p for p in (task / directory).glob("*") if p.is_file()
                     and not (directory == "environment" and
                              agent_only_context_path(p.relative_to(task / "environment"))))
    paths.extend(p for p in (task / "tests/differential").rglob("*")
                 if p.suffix in (".py", ".sh", ".json", ".js", ".cjs") and
                 "__pycache__" not in p.parts)
    return _inventory(paths)


def task_policy_sha256(task_id):
    """Canonical path/byte-hash identity of protected task config and drivers."""
    return canonical_sha256(_task_inventory(task_id))


def collect_judge_identity(task_id):
    """Observe HEAD, an exact release tag, production cleanliness and code hashes.

    A registry/evidence or test-only commit does not change production identity.
    Registry updates cannot make a dirty production tree into a clean release.
    """
    commit = _command("git", "rev-parse", "HEAD")
    tags = (_command("git", "tag", "--points-at", "HEAD") or "").splitlines()
    tags = sorted(t for t in tags if re.fullmatch(
        r"optimbench-v[0-9]+\.[0-9]+(?:\.[0-9]+)?", t))
    code = _code_inventory()
    policy = _task_inventory(task_id)
    changed = _command("git", "diff", "--name-only", "HEAD", "--")
    untracked = _command("git", "ls-files", "--others", "--exclude-standard")
    # Empty output is normal, so verify repository availability independently.
    available = _command("git", "rev-parse", "--is-inside-work-tree") == "true"
    names = set((changed or "").splitlines() + (untracked or "").splitlines())
    task_prefix = "tasks/" + task_id + "/"
    dirty = any(_production(n) or n in policy or
                n.startswith(task_prefix) and _task_policy_path(n[len(task_prefix):])
                for n in names)
    return {"commit": commit, "release_tag": tags[0] if tags else None,
            "clean": available and changed is not None and untracked is not None and not dirty,
            "judge_code_sha256": canonical_sha256(code),
            "task_policy_sha256": canonical_sha256(policy)}


def _task_policy_path(name):
    parts = Path(name).parts
    if parts and parts[0] == "environment" and agent_only_context_path(Path(*parts[1:])):
        return False
    return (name in ("task.json", "release.json") or
            len(parts) == 2 and parts[0] in ("environment", "tests") or
            len(parts) == 3 and parts[:2] == ("environment", "workload") or
            parts[:2] == ("tests", "differential") and
            Path(name).suffix in (".py", ".sh", ".json", ".js", ".cjs"))


def _validate(value, schema_name):
    # Lazy import: observation works without optional schema dependencies.
    from jsonschema import Draft202012Validator
    schema = json.loads((SCHEMAS / schema_name).read_text())
    Draft202012Validator(schema).validate(value)


def _json(raw):
    def reject_constant(value):
        raise ValueError("nonfinite_json")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique)


def _receipt(ref):
    name = ref["path"]
    if not re.fullmatch(r"judge/hosts/qualifications/[a-zA-Z0-9_./-]+\.json(?:\.gz)?", name):
        raise ValueError("qualification_path_invalid")
    path = ROOT / name
    base = ROOT / "judge/hosts/qualifications"
    if (".." in Path(name).parts or not path.resolve().is_relative_to(base.resolve()) or
            any(p.is_symlink() for p in (path, *path.parents) if p != ROOT)):
        raise ValueError("qualification_path_invalid")
    raw = path.read_bytes()
    if _sha(raw) != ref["sha256"]:
        raise ValueError("qualification_hash_mismatch")
    return gzip.decompress(raw) if name.endswith(".gz") else raw


def _unit_clean(unit, width_key="full_width_pp", family_size=1):
    """Precise, adequately sampled and centred on 1 (judge.score_result.aa_centred)."""
    from .score_result import aa_centred
    try:
        low, high = unit["ci95"]
        width = unit[width_key]
        return (type(unit["sample_count"]) is int and unit["sample_count"] >= 5 and
                all(type(v) in (float, int) and math.isfinite(v) for v in (low, high, width)) and
                0 < low <= high and aa_centred(unit, family_size) and 0 <= width <= 5 and
                math.isclose(width, 100 * (high - low), rel_tol=1e-9, abs_tol=1e-9))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _quality_clean(quality):
    return (isinstance(quality, dict) and quality.get("noisy") is False and
            quality.get("missing_cpu_counters", False) is False)


def _gates_passed(mandatory, gates):
    return (isinstance(mandatory, (list, tuple)) and bool(mandatory) and
            all(isinstance(n, str) and n for n in mandatory) and
            isinstance(gates, Mapping) and bool(gates) and
            all(n in gates for n in mandatory) and
            all(status == "passed" for status in gates.values()))


def _architecture(value):
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(value, value)


def collect_execution_host(live_fingerprint):
    """Verify the selected Docker daemon against the independently local daemon.

    Only Unix endpoints are supported. The selected context is inspected even
    when DOCKER_HOST is set; TCP/SSH contexts and unknown endpoints fail closed.
    Compare server ID/name/version/OS/architecture/kernel with the daemon on
    /var/run/docker.sock and with local uname, not client version or requested
    container affinity. No container is started by these read-only commands.
    """
    env_host = os.environ.get("DOCKER_HOST")
    if env_host and not env_host.startswith("unix:///"):
        raise RegistrationError(["docker_endpoint_nonlocal"])
    context = _command("docker", "context", "show")
    if not context:
        raise RegistrationError(["docker_endpoint_unknown"])
    raw = _command("docker", "context", "inspect", context,
                   "--format", "{{json .Endpoints.docker}}")
    try:
        endpoint = _json(raw)["Host"]
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("endpoint_missing")
    except (ValueError, TypeError, KeyError):
        raise RegistrationError(["docker_endpoint_unknown"]) from None
    if not endpoint.startswith("unix:///"):
        raise RegistrationError(["docker_endpoint_nonlocal"])
    effective_endpoint = (endpoint if os.environ.get("DOCKER_CONTEXT") else
                          env_host or endpoint)
    try:
        selected = _json(_command("docker", "info", "--format", "{{json .}}"))
        local = _json(_command("docker", "--host", "unix:///var/run/docker.sock",
                               "info", "--format", "{{json .}}"))
        keys = ("ID", "Name", "ServerVersion", "OSType", "Architecture", "KernelVersion")
        if not all(isinstance(info, dict) and all(isinstance(info.get(key), str) and
                                                 info[key].strip() for key in keys)
                   for info in (selected, local)):
            raise ValueError("server_identity_missing")
    except (ValueError, TypeError):
        raise RegistrationError(["docker_server_identity_unknown"]) from None
    hostname, operating_system = _command("uname", "-n"), _command("uname", "-s")
    if not hostname or not operating_system:
        raise RegistrationError(["docker_server_identity_unknown"])
    reasons = []
    if selected["ID"] != local["ID"]:
        reasons.append("docker_server_id_mismatch")
    if selected["Name"] != local["Name"] or selected["Name"] != hostname:
        reasons.append("docker_server_hostname_mismatch")
    if (selected["ServerVersion"] != local["ServerVersion"] or
            selected["ServerVersion"] != live_fingerprint.get("docker_version")):
        reasons.append("docker_server_version_mismatch")
    if (selected["OSType"].lower() != local["OSType"].lower() or
            selected["OSType"].lower() != operating_system.lower()):
        reasons.append("docker_server_os_mismatch")
    if (_architecture(selected["Architecture"]) != _architecture(local["Architecture"]) or
            _architecture(selected["Architecture"]) != live_fingerprint.get("architecture")):
        reasons.append("docker_server_architecture_mismatch")
    if (selected["KernelVersion"] != local["KernelVersion"] or
            selected["KernelVersion"] != live_fingerprint.get("kernel")):
        reasons.append("docker_server_kernel_mismatch")
    if reasons:
        raise RegistrationError(reasons)
    return {
        "endpoint": effective_endpoint, "context": context,
        "server_id": selected["ID"], "server_name": selected["Name"],
        "server_version": selected["ServerVersion"],
        "server_os": selected["OSType"].lower(),
        "server_architecture": _architecture(selected["Architecture"]),
        "server_kernel": selected["KernelVersion"], "local_hostname": hostname,
    }


# A configuration entry matches these live fingerprint fields only. Instance,
# zone, project, kernel and container runtime versions differ between fresh VMs;
# cpu_flags_sha256 also differs between VMs of one machine type (see registry).
CONFIGURATION_FIELDS = ("provider", "machine_type", "cpu_model", "architecture",
                        "threads_per_core", "judge_cpus", "system_cpus")


def _boot_id():
    return _text("/proc/sys/kernel/random/boot_id")


def _session_control(path, task_id, links):
    """Verify a same-boot pristine control as this configuration's qualification.

    The control is the kit's unchanged-code judge.score run on this VM. It must
    be for this task, recorded on this boot with this live fingerprint, entry,
    judge code and task policy, and pass the canonical tools.judge_control check.
    Returns (control, file sha256) or raises ValueError(denial code).
    """
    try:
        raw = Path(path).read_bytes()
        control = _json(raw)
        attempt = control.get("registration_attempt") or control.get("registration") or {}
        recorded = attempt.get("links")
        if not isinstance(recorded, dict):
            raise ValueError("no recorded links")
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError("session_control_invalid") from None
    keys = ("boot_id", "fingerprint_sha256", "entry_sha256", "judge_code_sha256", "task_policy_sha256")
    if control.get("task") != task_id or any(not links.get(k) or recorded.get(k) != links[k] for k in keys):
        raise ValueError("session_control_identity_mismatch")
    try:
        from tools.judge_control import validate_files
        errors = validate_files(path, task_id)
    except (ImportError, ValueError, OSError, KeyError, TypeError):
        errors = ["unreadable or unsupported control"]
    if errors:
        raise ValueError("session_control_failed")
    return control, _sha(raw)


def prepare_registered_session(task_id, registry_path, *, mandatory_gates, gates, session_control=None):
    """Validate registration BEFORE timing; return a frozen RegistrationContext.

    Raises RegistrationError with .reasons and .links on any denial. Callers must
    supply their complete mandatory gate inventory and completed gate results.
    No current-session A/A observation is accepted, required, or fabricated.
    An exact host with a stored receipt for the task uses that receipt. Otherwise
    a matching approved configuration requires session_control: the path of this
    boot's passing pristine control result.json for the task.
    """
    reasons, links = [], {}

    def deny(code):
        if code not in reasons:
            reasons.append(code)

    def reject():
        raise RegistrationError(reasons, links)

    if not isinstance(task_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", task_id):
        deny("invalid_task_id")
        reject()
    if not _gates_passed(mandatory_gates, gates):
        deny("gate_not_passed")
    try:
        path = Path(registry_path)
        if path.resolve() != (ROOT / "judge/hosts/registry.json").resolve() or path.is_symlink():
            raise ValueError("registry_path_invalid")
        raw = path.read_bytes()
        data = _json(raw)
        _validate(data, "registry.schema.json")
        ids = [host["host_id"] for host in data["hosts"]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_host_id")
        links["registry_sha256"] = _sha(raw)
    except ImportError:
        deny("schema_validator_unavailable")
        reject()
    except Exception:
        deny("registry_invalid")
        reject()
    live = collect_fingerprint()
    links["fingerprint_sha256"] = canonical_sha256(live)
    matches = [h for h in data["hosts"] if h["fingerprint"] == live]
    if len(matches) > 1:
        deny("fingerprint_ambiguous")
        reject()
    host = matches[0] if matches else None
    configuration = None
    if host is None or task_id not in host["qualifications"]:
        configurations = [c for c in data.get("configurations", [])
                          if all(live.get(k) == c["fingerprint"][k] for k in CONFIGURATION_FIELDS)]
        if len(configurations) > 1:
            deny("fingerprint_ambiguous")
            reject()
        configuration = configurations[0] if configurations else None
        if host is None and configuration is None:
            deny("fingerprint_unregistered")
            reject()
    if configuration is not None:
        links["entry_sha256"] = canonical_sha256(configuration)
        links["boot_id"] = _boot_id()
    else:
        links["entry_sha256"] = canonical_sha256(host)
        if host["role"] != "scoring":
            deny("portability_only")
    try:
        identity = collect_judge_identity(task_id)
        links.update(judge_commit=identity["commit"], release_tag=identity["release_tag"],
                     judge_code_sha256=identity["judge_code_sha256"],
                     task_policy_sha256=identity["task_policy_sha256"])
        if not identity["release_tag"] or not identity["commit"]:
            deny("judge_untagged")
        if identity["clean"] is not True:
            deny("judge_dirty")
    except (OSError, ValueError):
        deny("judge_identity_unknown")
        identity = {}
    execution_host = {}
    try:
        execution_host = collect_execution_host(live)
        links["execution_host_sha256"] = canonical_sha256(execution_host)
    except RegistrationError as error:
        for code in error.reasons:
            deny(code)
    ref = None if configuration is not None else host["qualifications"].get(task_id)
    if configuration is not None:
        if session_control is None:
            deny("qualification_missing")
        else:
            try:
                control, control_sha256 = _session_control(session_control, task_id, links)
                links["session_control_sha256"] = control_sha256
            except ValueError as error:
                deny(str(error))
    elif ref is None:
        deny("qualification_missing")
    else:
        try:
            qualification = _json(_receipt(ref))
            links["qualification_sha256"] = ref["sha256"]
            _validate(qualification, "qualification.schema.json")
            _receipt(qualification["evidence"])
            links["evidence_sha256"] = qualification["evidence"]["sha256"]
            bindings = {
                "host_id": host["host_id"], "task_id": task_id,
                "fingerprint_sha256": links["fingerprint_sha256"],
                "judge_code_sha256": identity.get("judge_code_sha256"),
                "task_policy_sha256": identity.get("task_policy_sha256"),
            }
            if any(qualification[key] != value for key, value in bindings.items()):
                deny("qualification_identity_mismatch")
            if (not _quality_clean(qualification["quality"]) or
                    not all(_unit_clean(unit) for unit in qualification["units"].values())):
                deny("qualification_quality_unclean")
        except Exception as error:
            deny("qualification_hash_mismatch" if str(error) == "qualification_hash_mismatch"
                 else "qualification_invalid")
    if reasons:
        reject()
    if configuration is not None:
        # One CPU class per approved configuration, shared by its fresh VMs.
        host_id = "configuration:" + configuration["configuration_id"]
        cpu_class = "registered:" + configuration["fingerprint"]["machine_type"] + ":" + canonical_sha256({
            "configuration_id": configuration["configuration_id"],
            "fingerprint": configuration["fingerprint"]})
        receipt_sha256, units = control_sha256, control["cases"]
        scope = "public" if control.get("public_only") else "heldout"
    else:
        host_id = host["host_id"]
        cpu_class = "registered:" + host["fingerprint"]["machine_type"] + ":" + canonical_sha256({
            "host_id": host["host_id"], "fingerprint": host["fingerprint"]})
        receipt_sha256, units, scope = ref["sha256"], qualification["units"], qualification["scope"]
    return RegistrationContext(
        task_id=task_id, host_id=host_id, registry_entry_sha256=links["entry_sha256"],
        live_fingerprint=live, judge_tag=identity["release_tag"], judge_commit=identity["commit"],
        judge_code_sha256=identity["judge_code_sha256"],
        task_policy_sha256=identity["task_policy_sha256"],
        qualification_receipt_sha256=receipt_sha256, scope=scope,
        qualified_units=tuple(sorted(units)), cpu_class=cpu_class, links=links,
        execution_host=execution_host, judge_identity=identity,
        mandatory_gates=tuple(mandatory_gates), gates=gates,
        session_control=None if configuration is None else str(session_control))


def evaluate_claimability(session_receipt, registry, judge_identity):
    """Return {claimable: bool, reasons: [codes], links: {verified identities}}.

    registry is the repository's judge/hosts/registry.json path, never an
    in-memory approval override. judge_identity is context.judge_identity or
    collect_judge_identity(task) output; it is re-observed, not trusted.

    The session must include task_id, scope (public/heldout), correct=True,
    aa_passed=True, quality, scheduler-style cases[*].aa, mandatory_gates (a
    nonempty name list), gates (name -> "passed"), and links matching the result's
    links: all context.links identities, including the Docker execution host.
    Missing, malformed, unknown, pending, or failed inputs always deny eligibility.
    """
    reasons, links = [], {}

    def deny(code):
        if code not in reasons:
            reasons.append(code)

    def result():
        return {"claimable": not reasons, "reasons": reasons, "links": links}

    if not isinstance(session_receipt, dict) or not isinstance(judge_identity, Mapping):
        deny("invalid_input")
        return result()
    session = session_receipt
    try:
        context = prepare_registered_session(
            session.get("task_id"), registry, mandatory_gates=session.get("mandatory_gates"),
            gates=session.get("gates"), session_control=session.get("session_control"))
        links.update(context.links)
    except RegistrationError as error:
        links.update(error.links)
        reasons.extend(error.reasons)
        return result()
    if context.judge_identity != judge_identity:
        deny("judge_identity_mismatch")
    cases = session.get("cases")
    # Each unit's A/A must be resolved, precise and centred under the judge's
    # family-wise rule (or inside the ±2.5% equivalence band); the scheduler's
    # aa_passed summary is not required separately because it omits the band.
    if (session.get("correct") is not True or not isinstance(session.get("aa_passed"), bool) or
            not _quality_clean(session.get("quality")) or not isinstance(cases, dict) or
            not cases or not all(isinstance(case, dict) and
                                isinstance(case.get("aa"), dict) and
                                case["aa"].get("status") == "resolved" and
                                _unit_clean(case["aa"], "ci95_full_width_pp", len(cases))
                                for case in cases.values())):
        deny("session_quality_unclean")
    if context.scope != session.get("scope"):
        deny("qualification_identity_mismatch")
    if not isinstance(cases, dict) or set(cases) != set(context.qualified_units):
        deny("qualification_quality_unclean")
    recorded = session.get("links")
    if not isinstance(recorded, dict) or any(recorded.get(k) != v for k, v in links.items()):
        deny("session_links_mismatch")
    return result()
