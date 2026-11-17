#!/usr/bin/env python3
"""Author new diagnostic inputs without printing private membership or results.

Never reads existing evaluation bodies. Existing JSON commitments are consulted
mechanically only to exclude overlaps. Private diagnostics remain outside the
agent image. They are not a replacement for the historical held-out suite.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import tarfile
from native_provenance import fresh_original_build, verify_build

TASK = Path(__file__).resolve().parents[3]
REPO = TASK.parents[1]
PIN = "3d6a828938a5fa0444275d3d2f67b64ec3199eb7"
ARCHIVE_SHA256 = "b90e598761827cee3477a6159653914a7fbf02602767ea7122762fa59611513a"
PUBLIC = [
    ("basics", "src/tutorial/basics.source"),
    ("advanced", "src/tutorial/advanced.source"),
    ("catalogs", "src/tutorial/syscat.source"),
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def collect_hashes(value):
    if isinstance(value, str):
        return set(re.findall(r"\b[a-f0-9]{64}\b", value))
    if isinstance(value, dict):
        return set().union(*(collect_hashes(k) | collect_hashes(v) for k, v in value.items()))
    if isinstance(value, list):
        return set().union(*(collect_hashes(v) for v in value))
    return set()


def format_request(items):
    return [{"id": row["id"], "sql": base64.b64encode(row["data"]).decode()} for row in items]


def baseline(binary, items):
    verify_build(binary.parent)
    result = subprocess.run([str(binary)], input=json.dumps(format_request(items)).encode(),
                            capture_output=True, timeout=120, check=True)
    assert not result.stderr
    results = json.loads(result.stdout)
    assert [r["id"] for r in results] == [r["id"] for r in items]
    assert all(set(r) == {"id", "output_base64", "error_base64"} for r in results)
    return b"EXIT 0\nSTDOUT - " + str(len(result.stdout)).encode() + b"\n" + result.stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--archive-sha256", required=True)
    p.add_argument("--build", type=Path, required=True)
    p.add_argument("--source-archive", type=Path, required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--private", type=Path, required=True)
    args = p.parse_args()
    installed = TASK / "environment/workload/inputs/native-batch.json"
    if installed.is_file() and json.loads(installed.read_text()).get("frozen"):
        p.error("The native edition is frozen. Do not replace it with diagnostic inputs.")
    assert args.archive_sha256 == ARCHIVE_SHA256
    assert sha(args.archive.read_bytes()) == ARCHIVE_SHA256
    receipt = fresh_original_build(args.source_archive, args.build, args.image)
    assert not args.private.exists(), "Use a new private authoring directory"
    args.private.mkdir(mode=0o700, parents=True)

    files = {}
    with tarfile.open(args.archive) as archive:
        for member in archive:
            parts = Path(member.name).parts[1:]
            if member.isfile() and parts:
                path = "/".join(parts)
                if path.startswith(("src/tutorial/", "src/test/regress/sql/",
                                    "src/test/regress/expected/")) or path == "COPYRIGHT":
                    files[path] = archive.extractfile(member).read()

    # No existing held-out data bodies are opened or disclosed here.
    forbidden = set()
    unavailable_commitment_files = 0
    for task in (REPO / "tasks").iterdir():
        for relative in ("environment/workload/inputs/manifest.json",
                         "tests/heldout/manifest.json", "tests/heldout/inputs.lock.json"):
            path = task / relative
            if path.is_file():
                text = path.read_text()
                if text.startswith("version https://git-lfs.github.com/spec/"):
                    unavailable_commitment_files += 1
                    continue
                forbidden |= collect_hashes(json.loads(text))

    public = [{"id": name, "path": path, "data": files[path]} for name, path in PUBLIC]
    public_hashes = {sha(row["data"]) for row in public}
    assert len(public_hashes) == len(public)
    eligible = {}
    excluded = {"known_hash": 0, "invalid_text": 0, "outside_size": 0}
    for path, data in files.items():
        if not path.startswith("src/test/regress/sql/") or not path.endswith(".sql"):
            continue
        if sha(data) in forbidden | public_hashes:
            excluded["known_hash"] += 1
            continue
        if not 2048 <= len(data) <= 100000:
            excluded["outside_size"] += 1
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            excluded["invalid_text"] += 1
            continue
        # Whole query-template families, not fragments/alternate versions.
        family = re.sub(r"[_-]?\d+$", "", Path(path).stem)
        eligible.setdefault(family, []).append((path, data))
    groups = list(eligible.values())
    secrets.SystemRandom().shuffle(groups)
    assert len(groups) >= 3
    private = []
    for group in groups:
        path, data = sorted(group)[0]
        if sha(data) not in {sha(r["data"]) for r in private}:
            private.append({"id": f"private-{len(private)}", "path": path, "data": data})
        if len(private) == 3:
            break
    assert len(private) == 3

    # Same-query pairs from other regression-file families than the public demo.
    pattern = re.compile(
        rb"(?m)^explain \(costs off\)\n(?P<sql>[^;]{1,2000};)\n"
        rb"(?P<plan>[ \t]*QUERY PLAN[ \t]*\n-+\n(?:[^\n]*\n){1,80}?\(\d+ rows?\)\n)")
    plan_groups = []
    for path, data in files.items():
        if not path.startswith("src/test/regress/expected/") or not path.endswith(".out"):
            continue
        if Path(path).name in ("join.out", "select_parallel.out"):
            continue
        by_query = {}
        for match in pattern.finditer(data):
            # Preserve literals and whitespace exactly, not a semantic guess.
            key = match["sql"]
            by_query.setdefault(key, []).append(match)
        pairs = []
        for matches in by_query.values():
            for before, after in zip(matches, matches[1:]):
                if (before["plan"] != after["plan"]
                        and sha(before["plan"]) not in forbidden
                        and sha(after["plan"]) not in forbidden):
                    pairs.append((before, after))
        if pairs:
            plan_groups.append((path, pairs))
    secrets.SystemRandom().shuffle(plan_groups)
    assert len(plan_groups) >= 3
    plans, plan_origins = [], []
    for path, pairs in plan_groups[:3]:
        before, after = pairs[0]
        plans.append({"before": before["plan"].decode(), "after": after["plan"].decode()})
        plan_origins.append({"path": path, "query": before["sql"].decode(),
                             "before_offset": before.start("plan"),
                             "after_offset": after.start("plan")})

    inputs = TASK / "environment/workload/inputs"
    payload_path = TASK / "payload.json"
    payload = json.loads(payload_path.read_text())
    docs = []
    for row in public:
        digest = sha(row["data"])
        dest = inputs / "files" / digest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(row["data"])
        docs.append({"id": row["id"], "sha256": digest, "bytes": len(row["data"])})
        name = "native-" + row["id"]
        payload["files"] = [v for v in payload["files"] if v["name"] != name]
        url = f"https://raw.githubusercontent.com/postgres/postgres/{PIN}/{row['path']}"
        payload["files"].append({
            "name": name, "scope": "public", "destination": str(dest.relative_to(TASK)),
            "bytes": len(row["data"]), "sha256": digest, "distribution": "upstream",
            "fetch": {"kind": "url", "url": url, "bytes": len(row["data"]), "sha256": digest},
            "license": "PostgreSQL"})
    expected = baseline(args.build / "sqlfmt-native", public)
    dump(TASK / "tests/fuzz/native/native-reference-build.json", receipt)
    binding = sha((TASK / "tests/fuzz/native/native-reference-build.json").read_bytes())
    dump(inputs / "native-batch.json", {
        "format": "sqlfmt-native-batch-v1", "edition": "diagnostic-v1",
        "reference_build_sha256": binding,
        "docs": docs, "expected": {"expect_len": len(expected), "expect_sha": sha(expected)}})
    dump(payload_path, payload)
    # Preserve the existing public guard structure and its public-only projection.
    public_payload = json.loads((TASK / "environment/payload-public.json").read_text())
    public_payload["files"] = [
        {**r, "destination": r["destination"].removeprefix("environment/")}
        for r in payload["files"] if r["scope"] == "public"
    ]
    dump(TASK / "environment/payload-public.json", public_payload)
    (TASK / "environment/notices/PostgreSQL-native-COPYRIGHT.txt").write_bytes(files["COPYRIGHT"])
    (TASK / "tests/fuzz/native/native-public-reference.bin").write_bytes(expected)

    private_inputs = args.private / "inputs"
    (private_inputs / "files").mkdir(parents=True)
    private_docs = []
    for row in private:
        digest = sha(row["data"])
        (private_inputs / "files" / digest).write_bytes(row["data"])
        private_docs.append({"id": row["id"], "sha256": digest, "bytes": len(row["data"])})
    reference = baseline(args.build / "sqlfmt-native", private)
    dump(private_inputs / "native-batch.json", {
        "format": "sqlfmt-native-batch-v1", "edition": "diagnostic-v1",
        "reference_build_sha256": binding,
        "docs": private_docs, "expected": {"expect_len": len(reference), "expect_sha": sha(reference)}})
    dump(private_inputs / "manifest.json", {"splits": {}})
    (args.private / "reference.bin").write_bytes(reference)
    dump(args.private / "ownership-input.json", {"format": format_request(private), "plans": plans})
    dump(args.private / "origins.json", {
        "archive_sha256": args.archive_sha256, "commit": PIN,
        "native": [{k: v for k, v in row.items() if k != "data"} for row in private],
        "plans": plan_origins})
    dump(args.private / "public-ownership-input.json", {"format": format_request(public)})
    dump(args.private / "audit.json", {
        "new_native_public_documents": len(public), "new_native_private_documents": len(private),
        "new_private_plan_pairs": len(plans), "native_hash_overlap": 0,
        "heldout_source_family_exclusions": ["public tutorial files", "public plan regression families"],
        "exclusion_counts": excluded, "membership_printed": False,
        "unavailable_cross_task_commitment_files": unavailable_commitment_files,
        "global_existing_corpus_disjointness": "not established if commitments unavailable",
        "diagnostic_only": True, "frozen_for_qualification": False})
    print(json.dumps(json.loads((args.private / "audit.json").read_text()), indent=2))


if __name__ == "__main__":
    main()
