# Reviewed host registry — schema v1

The registry records the reference scoring configuration used for claimable
results: an Intel x86-64 Linux machine with 8 dedicated judge CPUs, 16 GiB and
SMT off. You do not need a registry entry to score. On your own machine matching
the documented scoring configuration, `judge.score` runs every gate and the full
timing protocol and reports `claimable:false` unless the live fingerprint matches
a registry entry. `judge/hosts/registry.json` lists no individual machines; it
holds one approved configuration, the reference Intel `c4-standard-32`, which
any fresh machine of that configuration matches.

Registry entries, approved configurations and qualification evidence change
only through a reviewed pull request. There is no runtime approval/force-claimable flag.
A session can qualify in one of two ways:

- **Exact host:** the live fingerprint equals a reviewed `hosts` entry that holds
  a stored qualification receipt for the task. The released registry has no
  `hosts` entries, so this path is unused.
- **Approved configuration:** otherwise, the live fingerprint matches a reviewed
  `configurations` entry, and the session supplies this boot's passing pristine
  control (see [Approved configurations](#approved-configurations-and-same-session-control)).
  This lets a fresh machine of an approved configuration be scored as
  claimable without a per-host receipt.

The pinned schemas are `judge/hosts/registry.schema.json` and
`judge/hosts/qualification.schema.json`. A registry entry has:

- `host_id`, a stable machine identifier (for example
  `<provider>:<project>:<zone>:<instance>`).
- `role: scoring` for the Intel scoring hosts, or `portability` for ARM.
- Exact live `fingerprint` fields from the registry schema, including actual
  visible CPU sets (do not infer core count from the nominal machine size).
- optionally, `review.pull_request` linking the pull request that reviewed it.
- `qualifications`, mapping task IDs to immutable qualification receipt
  paths/hashes under `judge/hosts/qualifications/`.

## Approved configurations and same-session control

A `configurations` entry has `configuration_id`, `role: scoring`, an optional
`review`, and a `fingerprint` holding only the configuration fields: `provider`,
`machine_type`, `cpu_model`, `architecture`, `threads_per_core`, `judge_cpus`
and `system_cpus`. A live fingerprint matches when all seven fields are equal.
`instance_id`, `zone`, `project_id`, `kernel`, `docker_version`,
`containerd_version` and `cpu_flags_sha256` are not matched: they differ
between fresh VMs, and `cpu_flags_sha256` differs between otherwise identical
Intel `c4-standard-32` machines. The registry approves only Intel `c4-standard-32`
(INTEL(R) XEON(R) PLATINUM 8581C, judge CPUs 0-7, system CPUs 8-15) with one
thread per core. Scoring is Intel-only.

An exact host whose receipt covers the task always uses that receipt. Otherwise
a configuration match needs `judge.score --session-control <result.json>`: the
unchanged-code (pristine) control run with `judge.score` on the same machine,
in the same boot, before scoring. It replaces the stored receipt only if all of these hold:

- the control's `task` is the session's task;
- the control's recorded registration links have the same
  `/proc/sys/kernel/random/boot_id`, live `fingerprint_sha256`, configuration
  `entry_sha256`, `judge_code_sha256` and `task_policy_sha256` as this session;
- it passes the canonical `tools.judge_control` validator (correct, held-out,
  inconclusive, current gate profile, clean A/A and host quality, raw and
  reduced statistics consistent) against the task's expected held-out units.
  Those come from `tests/judge.json` `performance_units.heldout` when declared;
  otherwise the protected `task.py` lists the held-out performance cases from
  the authenticated held-out set, which exists on the scoring machine where this
  check runs. Session control therefore works for every task.

The control's file SHA-256 is linked as `session_control_sha256`, and `boot_id`
is linked too, so a changed control or a reboot between preparation and final
evaluation is denied. The qualified scope and unit set come from the control.
The configuration's `cpu_class` is shared by all its machines:
`registered:<machine_type>:<sha256>` over `{"configuration_id", "fingerprint"}`.
Every other requirement is unchanged: scoring role, all mandatory gates passed,
clean tagged release, Docker locality, and the current session's A/A and host
quality. The control run itself has no session control, so it stays provisional
with `qualification_missing`.

## Canonical identity

Canonical JSON is UTF-8 `json.dumps(value, sort_keys=True,
separators=(",", ":"), ensure_ascii=True)`, without a trailing newline.
An entry hash is SHA-256 of that complete host entry; the fingerprint hash is
SHA-256 of its `fingerprint` object. CPU flags are sorted, deduplicated tokens,
joined by `\n` without a trailing newline, then SHA-256 hashed. CPU model
whitespace is collapsed; kernel is `uname -r`. Architecture is `x86_64` or
`aarch64`, not a provider marketing label. CPU lists are sorted.
`machine_type` is the final short component, e.g. `c4-standard-32`, not the
full metadata URL. Instance IDs remain decimal strings, not lossy floating
point numbers.

Qualification receipts record each A/A unit's CI, count and full width in
percentage points; each unit must be centred (its family-wise interval
contains 1, or its 95% CI lies inside the ±2.5% band; see
[statistics-policy.md](statistics-policy.md)) and have width at most 5 pp.
Keep actual before/after noise counters and the canonical quality result.
Reference the original full A/A evidence by its byte SHA-256; do not rewrite
its observations. Retained receipt file hashes are hashes of file bytes,
including compression if `.gz`; embedded code/fingerprint hashes are canonical
content identities.

`judge.host_registry.judge_code_sha256()` identifies production judge/runner
code, not mutable registration data. Its canonical path-to-file-byte-hash
inventory includes production files under `judge/`, `tools/runner/` and
top-level Python tools. It excludes judge/tools tests, Python caches, and
`judge/hosts/` registration/qualification data; the two pinned schemas remain
code inputs. This avoids requiring A/A evidence to include its own registry
commit. `task_policy_sha256(task_id)` separately binds protected task
configuration/driver policy: `task.json`, `release.json`, direct files in
`environment/`, `environment/workload/` and `tests/`, plus Python/shell/JSON/JS
differential gate implementations. Dataset/project trees are bound by their
lock manifests rather than rehashed as measurement data. Neither hash is a
replacement for image/reference-cache identities.

## Eligibility contract

Performance may be claimable only when the live fingerprint matches a reviewed
scoring entry or approved configuration, the executing judge is a clean tagged
release, the task's qualification evidence (stored receipt or same-session
control) and hashes validate, current A/A/host quality is clean,
and no mandatory gate is pending or failed. The session receipt must link the
registry file hash, entry hash, qualified evidence hash, live fingerprint hash,
release tag and executing judge commit.

Otherwise it remains `claimable: false` with explicit reasons. ARM portability
hosts never receive claimable performance scores. Logical recipe/source
identity supplements, rather than replaces, the actual image/runtime identity
used to protect reference caches and reproduce an execution.

## Pre-measurement API

Install the judge-only `judge/requirements-host-registry.txt` dependency. The
integration API is:

```python
prepare_registered_session(task_id, registry_path, *, mandatory_gates, gates, session_control=None)
evaluate_claimability(session_receipt, registry, judge_identity)
collect_execution_host(live_fingerprint)
```

`registry_path`/`registry` is the checkout's `judge/hosts/registry.json` path,
not an in-memory registry override. `mandatory_gates` is a nonempty list/tuple
of required prerequisite gate names; `gates` maps names to completed status
strings. Every mandatory gate must exist, and **every** supplied gate must be
`"passed"` (including additional gates). Missing, skipped, unknown, pending,
and failed results deny registration. The caller must provide its complete
prerequisite inventory, not a hand-picked subset. A/A is not a prerequisite
gate: the current session has not yet run.

Call `prepare_registered_session` **after prerequisite correctness/admission
gates and before any timed observation**. It re-observes the actual host and
judge checkout, requires a matching scoring entry and clean exact
`optimbench-vMAJOR.MINOR[.PATCH]` release tag, and verifies the task's historical
qualification receipt, original evidence byte hashes, code/policy/fingerprint
bindings, clean quality, and centered A/A intervals of at most 5 pp. It also
verifies Docker locality as described below. It does **not** require, run, or
fabricate current-session A/A. Failure raises `RegistrationError`, with
machine-readable `.reasons` (tuple of codes) and `.links` (immutable observed
identities).

Success returns a deeply frozen `RegistrationContext` with:

- `task_id`, `host_id`, `registry_entry_sha256`, `live_fingerprint`;
- `judge_tag`, `judge_commit`, `judge_code_sha256`, `task_policy_sha256`;
- `qualification_receipt_sha256`, `scope`, `qualified_units`;
- `cpu_class`, `execution_host`, `links`, `judge_identity`;
- the accepted `mandatory_gates` and `gates`.

Nested mappings are read-only and CPU/unit lists are tuples. `context.as_dict()`
returns independent JSON-serializable copies. `context.links` includes
`registry_sha256` (registry file bytes), `entry_sha256` (complete canonical
entry), `fingerprint_sha256`, `qualification_sha256`, `evidence_sha256`,
`release_tag`, `judge_commit`, `judge_code_sha256`, `task_policy_sha256`, and
`execution_host_sha256`. The qualification hash is of retained file bytes,
including compression, never a reserialization.

`cpu_class` is `registered:<machine_type>:<sha256>`, with the final component
being the canonical hash of `{"host_id": ..., "fingerprint": ...}`. It is stable
across registry ordering/formatting, qualification evidence updates, review
metadata changes, and release commits. Different registered hosts/fingerprints
remain distinct; a generic CPU-vendor label or requested CPU count is not a
class identity.

### Registered verifier integration and legacy calibration

For a non-provisional run, prepare the registration context first, propagate
`context.cpu_class` instead of `"unregistered"`, and use
`context.live_fingerprint["judge_cpus"]` as the **observed** host affinity.
Preparation replaces the legacy `calibration.require(file, host, image, ...)`
prerequisite: reviewed historical qualification plus exact live identity is the
pre-measurement authorization, not an operator-supplied recent calibration file.
Do not synthesize a legacy calibration receipt to satisfy that path. A prepared
context is **not** a claimability grant and must not bypass the session's own
interleaved A/A, noise checks, correctness/gate results, or image/reference-cache
checks. This module does not itself change score/verifier dispatch.

Copy `context.as_dict()["links"]` into the session receipt before timing.
After measurement, provide the session's `task_id`, `scope`, `correct`,
`aa_passed`, `quality`, scheduler-style `cases[*].aa`, complete `mandatory_gates`
and `gates`, and those original `links` to:

```python
decision = evaluate_claimability(session_receipt, registry_path,
                                context.judge_identity)
```

The evaluator independently re-runs registration checks, requires the exact
qualified scope/unit set, checks every current A/A CI/count/width/resolution and
host quality, and compares all pre-measurement links. Changed fingerprints,
release identity, qualifications, Docker daemon identity, or gates deny
claimability. No context boolean, requested reference cpuset, or override
parameter can approve a score.

## Docker locality and affinity

The collector must execute on the actual host under the judge's intended
affinity, not merely on an arbitrary Docker client. `judge_cpus` is the current
process's actual `sched_getaffinity(0)` result. `system_cpus` is the set system
services can actually run on: the systemd manager's (PID 1) scheduler affinity
intersected with the effective `system.slice` cgroup CPU set, using whichever
is observable. Reserving CPUs with systemd `CPUAffinity` (rather than cgroup
`AllowedCPUs`) leaves the cgroup set spanning every CPU, so the manager affinity
carries the reservation. A reference probe's
`requested_cores`, `cpuset`, or `pinning_took_effect` is **never** evidence that
the fingerprinted judge host is registered.

Registration and post-measurement eligibility inspect the selected Docker
context and `DOCKER_HOST`/`DOCKER_CONTEXT`. Only local Unix endpoints are
supported; TCP (including loopback), SSH, named pipes, and unknown endpoints
are denied. Context overrides cannot hide a non-local `DOCKER_HOST`.
Read-only `docker info` observations from the selected endpoint are compared
with a separate explicit `--host unix:///var/run/docker.sock` observation.
Both must expose the same nonempty daemon ID, hostname, server version,
OS/architecture and kernel. Hostname must equal local `uname -n`, OS must equal
local `uname -s`, and architecture/kernel/server version must match the live
fingerprint. Docker architecture aliases `amd64`/`arm64` are normalized to
`x86_64`/`aarch64`. Client version alone is not evidence.

This intentionally requires the local rootful Docker socket and host UTS
identity. Rootless-only, Docker Desktop/VM, container-client, remote-daemon and
inaccessible/partial-server-identity configurations remain unclaimable; there
is no locality attestation or override. No container is started by preparation.
The verified daemon identity and effective endpoint/context are retained in
`execution_host` and bound by `execution_host_sha256`, so a daemon change
between preparation and final evaluation is denied even if its version matches.

### Denial codes

Registration codes include `invalid_task_id`, `gate_not_passed`,
`schema_validator_unavailable`, `registry_invalid`,
`fingerprint_unregistered`, `fingerprint_ambiguous`, `portability_only`,
`judge_untagged`, `judge_dirty`, `judge_identity_unknown`,
`qualification_missing`, `qualification_invalid`,
`qualification_hash_mismatch`, `qualification_identity_mismatch`,
`qualification_quality_unclean`, and for configuration matches
`session_control_invalid` (missing or unreadable), `session_control_identity_mismatch`
(other task, boot, fingerprint, entry, judge code or task policy) and
`session_control_failed` (fails `tools.judge_control`).

Docker codes are `docker_endpoint_nonlocal`, `docker_endpoint_unknown`,
`docker_server_identity_unknown`, `docker_server_id_mismatch`,
`docker_server_hostname_mismatch`, `docker_server_version_mismatch`,
`docker_server_os_mismatch`, `docker_server_architecture_mismatch`, and
`docker_server_kernel_mismatch`.

Final evaluation additionally uses `invalid_input`, `judge_identity_mismatch`,
`session_quality_unclean`, and `session_links_mismatch`. Failure returns
`{"claimable": false, "reasons": [...], "links": {...}}`; partial observed
links are diagnostic only, not an authorization.
