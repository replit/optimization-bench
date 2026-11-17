"""Public-release data ledger: measured distribution facts plus reviewed decisions.

The frozen Task trees (payload.json, held-out locks, notices) are never edited
here. This tool derives, from those frozen files, what each Task would actually
redistribute in a public release (bytes hosted in this repository, inputs only
fetched from upstream, and whether the agent image bakes input bodies into a
layer), then checks the reviewed decisions in
``datasets/public-release-ledger.json`` against those facts.

Every item is ``verified`` (checked against cited evidence), ``blocked`` (work
remains) or ``requires-human-judgment`` (a permission or usage question only a
responsible person can decide). It is not a legal grant; a Task is ready only
when all of its items are verified.

    python3 -m tools.public_release_ledger            # validate (exit 1 on problems)
    python3 -m tools.public_release_ledger --facts    # print measured facts as JSON
    python3 -m tools.public_release_ledger --summary  # per-Task readiness
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "datasets/public-release-ledger.json"
IMAGE_POLICIES = {"ecr-after-signoff", "build-locally", "held"}
STATUSES = {"verified", "blocked", "requires-human-judgment"}


def _entry_bytes(entry: dict) -> int:
    if entry.get("kind") == "git-history":
        return int(entry.get("object_inventory", {}).get("bytes") or 0)
    return int(entry.get("bytes") or 0)


def _fetch_only(entry: dict) -> bool:
    """True when the body is acquired from upstream, never hosted by this repository."""
    if entry.get("retention") == "fetch-only" or entry.get("distribution") == "upstream":
        return True
    if entry.get("kind") == "git-history" or entry.get("fetch"):
        return True
    return any(str(value).startswith("https://") for value in (
        entry.get("url", ""), entry.get("transport", {}).get("url", "")))


def task_facts(task: Path) -> dict:
    payload = json.loads((task / "payload.json").read_text())
    facts = {scope: {"repo_hosted_files": 0, "repo_hosted_bytes": 0,
                     "fetch_only_files": 0, "fetch_only_bytes": 0}
             for scope in ("public", "heldout")}
    for entry in payload["files"]:
        bucket = facts[entry["scope"]]
        key = "fetch_only" if _fetch_only(entry) else "repo_hosted"
        bucket[f"{key}_files"] += 1
        bucket[f"{key}_bytes"] += _entry_bytes(entry)
    dockerfile = (task / "environment/Dockerfile").read_text()
    ignore = task / "environment/.dockerignore"
    facts["image_embeds_workload_inputs"] = image_embeds_inputs(
        dockerfile, ignore.read_text() if ignore.is_file() else "")
    facts["image_bind_mounts_inputs"] = "target=/app/workload/inputs" in dockerfile
    return facts


def image_embeds_inputs(dockerfile: str, dockerignore: str = "") -> bool:
    """Whether a COPY places workload input bodies into a final-image layer.

    Copies of individual metadata/code files (globs, *.json, *.py, ...) do not
    count. A directory copy into /app/workload counts unless .dockerignore
    excludes the input bodies from the build context. Copies inside earlier
    stages only matter through a later ``COPY --from`` into /app/workload,
    which this rule sees directly.
    """
    ignored = "workload/inputs/**" in dockerignore
    for raw in dockerfile.replace("\\\n", " ").splitlines():
        words = raw.split()
        if not words or words[0].upper() != "COPY":
            continue
        args = [word for word in words[1:] if not word.startswith("--")]
        from_stage = any(word.startswith("--from=") for word in words[1:])
        if len(args) < 2:
            continue
        *sources, dest = args
        if not dest.rstrip("/").startswith("/app/workload"):
            continue
        for source in sources:
            name = source.rstrip("/").rsplit("/", 1)[-1]
            if "*" in name or "." in name:
                continue  # individual code/metadata files
            if "inputs" in source or "inputs" in dest or name == "workload":
                if not from_stage and ignored:
                    continue
                return True
    return False


def all_facts(root: Path = ROOT) -> dict:
    return {task.name: task_facts(task)
            for task in sorted((root / "tasks").iterdir())
            if (task / "payload.json").is_file()}


def _item_problems(label: str, item: dict, root: Path) -> list[str]:
    problems = []
    for field in ("item", "evidence", "status"):
        if not str(item.get(field, "")).strip():
            problems.append(f"{label}: {field} is required")
    if item.get("status") not in STATUSES:
        problems.append(f"{label}: status must be one of {sorted(STATUSES)}")
    if item.get("approved_by") and item.get("status") != "verified":
        problems.append(f"{label}: approved_by is only recorded on verified items")
    for path in item.get("evidence_paths", []):
        if not (root / path.split(":")[0]).exists():
            problems.append(f"{label}: evidence path {path} does not exist")
    return problems


def readiness(ledger: dict) -> dict:
    """Per Task: counts by status and whether every item is verified."""
    summary = {}
    for name, record in sorted(ledger.get("tasks", {}).items()):
        counts = {status: 0 for status in sorted(STATUSES)}
        for item in record.get("signoffs", []):
            counts[item.get("status")] = counts.get(item.get("status"), 0) + 1
        summary[name] = {"image_policy": record.get("image_policy"), **counts,
                         "ready": record.get("image_policy") != "held" and
                         counts["blocked"] == 0 and counts["requires-human-judgment"] == 0}
    return summary


def validate(ledger: dict, facts: dict, root: Path = ROOT) -> list[str]:
    problems = []
    tasks = ledger.get("tasks", {})
    for name in sorted(set(facts) - set(tasks)):
        problems.append(f"{name}: present in tasks/ but missing from the ledger")
    for name in sorted(set(tasks) - set(facts)):
        problems.append(f"{name}: in the ledger but not a Task in tasks/")
    for name, record in sorted(tasks.items()):
        if name not in facts:
            continue
        policy = record.get("image_policy")
        if policy not in IMAGE_POLICIES:
            problems.append(f"{name}: image_policy {policy!r} not in {sorted(IMAGE_POLICIES)}")
        if not str(record.get("reason", "")).strip():
            problems.append(f"{name}: reason is required")
        signoffs = record.get("signoffs", [])
        for index, item in enumerate(signoffs):
            problems += _item_problems(f"{name}: signoffs[{index}]", item, root)
        embeds = facts[name]["image_embeds_workload_inputs"]
        if policy == "ecr-after-signoff" and embeds and not signoffs:
            problems.append(f"{name}: image bakes input bodies; ECR publication needs "
                            "at least one recorded rights item")
        if record.get("publishable_now") and any(item.get("status") != "verified" for item in signoffs):
            problems.append(f"{name}: publishable_now while items are not all verified")
        # A public export carries EXPORT.json at its root: there, omitted bodies must
        # be absent. In the staging checkout they must exist so the export can drop them.
        exported = (root / "EXPORT.json").is_file()
        for path in record.get("omit_from_export", []):
            if exported and (root / path).exists():
                problems.append(f"{name}: omit_from_export path {path} is present in the export")
            elif not exported and not (root / path).exists():
                problems.append(f"{name}: omit_from_export path {path} does not exist")
    for index, item in enumerate(ledger.get("release_checks", [])):
        problems += _item_problems(f"release_checks[{index}]", item, root)
    for name in ledger.get("release_scope", {}).get("included", []):
        if name not in tasks:
            problems.append(f"release_scope: {name} is not in the ledger")
        elif tasks[name].get("image_policy") == "held":
            problems.append(f"release_scope: {name} is included but its image_policy is held")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--facts", action="store_true", help="print measured facts and exit")
    parser.add_argument("--summary", action="store_true", help="print per-Task readiness and exit")
    args = parser.parse_args(argv)
    facts = all_facts()
    if args.facts:
        print(json.dumps(facts, indent=2, sort_keys=True))
        return 0
    ledger = json.loads(LEDGER.read_text())
    if args.summary:
        print(json.dumps(readiness(ledger), indent=2))
        return 0
    problems = validate(ledger, facts)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(json.dumps({"passed": not problems, "tasks": len(facts),
                      "problems": len(problems)}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
