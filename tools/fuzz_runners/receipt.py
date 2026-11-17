"""Small evidence envelopes for the canonical fuzz controller to consume.

These are capability/build/coverage observations, NOT task verdicts. The shared
judge owns policy. All unsupported records carry a reason and raw evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected lowercase SHA256 hex")
    return value


def image(value):
    if not re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", value):
        raise ValueError("images must be immutable repo@sha256 or local sha256 identities")
    return value


def artifact(path, base):
    path, base = Path(path).resolve(), Path(base).resolve()
    relative = path.relative_to(base)
    if not path.is_file():
        raise ValueError("artifact is not a regular file")
    return {"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}


def build_receipt(*, language, runner_image, parent_task_image, source_sha256,
                  build_sha256, features, instrumentation, flags, tools,
                  capabilities, artifacts=()):
    for name, record in capabilities.items():
        if record.get("status") not in ("validated", "unsupported", "unvalidated"):
            raise ValueError("capability status is not a task verdict: " + name)
        if record["status"] != "validated" and not record.get("reason"):
            raise ValueError("unsupported/unvalidated capability needs a reason")
        if record["status"] == "validated" and not record.get("evidence"):
            raise ValueError("validated capability needs runtime evidence")
    if not isinstance(features, list) or any(not isinstance(x, str) for x in features):
        raise ValueError("features must be a string list")
    return {
        "schema": "optimbench-runner-build-v1",
        "language": language, "runner_image": image(runner_image),
        "parent_task_image": image(parent_task_image),
        "source_sha256": digest(source_sha256), "build_sha256": digest(build_sha256),
        "features": features, "instrumentation": instrumentation, "flags": list(flags),
        "tools": tools, "capabilities": capabilities, "artifacts": list(artifacts),
        "qualification": "not-a-task-verdict",
    }


def coverage_receipt(*, build_receipt_sha256, raw_paths, normalized_path,
                     base, expected_workers, observed_workers, source_sha256):
    expected, observed = set(expected_workers), set(observed_workers)
    if not expected or not observed <= expected:
        raise ValueError("coverage worker inventory missing or contains unknown workers")
    if not raw_paths:
        raise ValueError("raw coverage exports required")
    normalized = json.loads(Path(normalized_path).read_text())
    if normalized.get("schema") != "optimbench-coverage-v1":
        raise ValueError("not normalized runner coverage")
    missing = sorted(expected - observed)
    return {
        "schema": "optimbench-runner-coverage-v1",
        "build_receipt_sha256": digest(build_receipt_sha256),
        "source_sha256": digest(source_sha256),
        "raw": [artifact(p, base) for p in raw_paths],
        "normalized": artifact(normalized_path, base),
        "expected_workers": sorted(expected), "observed_workers": sorted(observed),
        "missing_workers": missing,
        "status": "available" if not missing and normalized["status"] == "available" else "unsupported",
        "reason": "missing worker exports" if missing else "; ".join(normalized["limitations"]),
        "qualification": "not-a-task-verdict",
    }
