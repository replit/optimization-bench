"""Restore immutable legacy verifier bytes with the image-owned pristine producer."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks/serde-json-production-crate-index-ingestion"
sys.path.insert(0, str(ROOT))

from judge import heldout, image_recipe, telemetry
from judge.verifier import HARDENING, image_id, load_task
from tools import stage_payload
from docker_executor import DockerExecutor
from runner import Context, Step

NAMES = ("bevy", "diesel", "image", "nalgebra", "opencv", "winit")
METADATA = (
    "tests/heldout/manifest.json",
    *("tests/heldout/references/" + name + ".json" for name in (
        "license-policy", "overlap-preflight", "reference-generation",
        "selection-freeze", "sources", "geojson-typed-selection")),
)
BUNDLE = ROOT / "scorer-metadata" / TASK.name


def original(path, entry):
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing original bytes: " + str(path))
    raw = path.read_bytes()
    if raw.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ValueError("Original bytes required, not a Git LFS pointer: " + str(path))
    if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("Original length/SHA-256 mismatch: " + str(path))
    return raw


def originals(task, bundle):
    payload = json.loads((task / "payload.json").read_text())
    lock = json.loads((task / "tests/heldout.lock.json").read_text())
    retained = {row["destination"]: row for row in payload["files"]
                if row["scope"] == "heldout" and row.get("retention") != "fetch-only"}
    goldens = {"tests/heldout/expected/" + name for name in NAMES}
    if set(retained) != set(METADATA) | goldens:
        raise ValueError("Expected exactly the original seven metadata files and six goldens")
    for name, row in retained.items():
        if lock["files"].get(name.removeprefix("tests/")) != row["sha256"]:
            raise ValueError("Original retained payload/lock mismatch: " + name)
    contents = {}
    for name in METADATA:
        path = stage_payload.contained(bundle, name)
        try:
            contents[name] = original(path, retained[name])
        except ValueError as error:
            raise ValueError("Restore the original ordinary-Git scorer metadata bundle: " +
                             str(error)) from error
    manifest = json.loads(contents["tests/heldout/manifest.json"])
    docs = {doc["path"]: doc for batch in manifest["splits"]["heldout"]
            if batch.get("id", "distinct-registry-corpus") == "distinct-registry-corpus"
            for doc in batch["docs"]}
    references = heldout.references(lock, task / "tests")
    heldout.reference_payload(references, payload)
    rows = []
    for name in NAMES:
        doc = docs[name]
        expected = retained["tests/heldout/expected/" + name]
        source = references["heldout/files/" + doc["sha256"]]
        if ((doc["expect_len"], doc["expect_sha"]) != (expected["bytes"], expected["sha256"])
                or any(source[key] != doc[key] for key in ("bytes", "sha256", "url"))
                or source.get("kind", "file") != "file"):
            raise ValueError("Original input/output commitments disagree: " + name)
        rows.append({"name": name, "input": source, "expected": expected})
    return retained, contents, rows


def recover(task, bundle, image, cpuset, output, inputs=None, fetch=False, cache=None):
    task, bundle, output = Path(task).resolve(), Path(bundle).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(ROOT):
        raise ValueError("Recovery output must be a fresh directory outside the source checkout")
    retained, contents, rows = originals(task, bundle)
    if fetch and (cache is None or Path(cache).resolve().is_relative_to(ROOT)):
        raise ValueError("Explicit --fetch requires an external --cache directory")
    telemetry.pinned(cpuset)
    image = image_id(image)
    identity = image_recipe.observation(task, image)
    if not identity["recipe_label_matches"]:
        raise ValueError("Recovery requires the existing canonical pristine SDK image")
    settings = json.loads((task / "tests/judge.json").read_text())
    fetcher = heldout.git_tools(task / "tests").Fetcher(cache) if fetch else None
    module = load_task(task)
    with tempfile.TemporaryDirectory(prefix="serde-legacy-recovery-") as temporary:
        temporary = Path(temporary)
        input_view, scratch, unused_build = [temporary / name for name in
                                             ("inputs", "scratch", "unused-build")]
        for path in (input_view, scratch, unused_build):
            path.mkdir(mode=0o755)
        for row in rows:
            source = row["input"]
            path = stage_payload.contained(Path(inputs), source["sha256"]) if inputs else None
            if path is None or not path.exists():
                if fetcher is None:
                    raise ValueError("Missing original input " + row["name"] +
                                     "; supply --inputs or explicit --fetch --cache")
                path = fetcher.url(source)
            raw = original(path, source)
            target = input_view / source["sha256"]
            target.write_bytes(raw)
            target.chmod(0o444)
        (scratch / "batch-list.tsv").write_text("".join(
            row["name"] + "\t/recovery-inputs/" + row["input"]["sha256"] + "\n"
            for row in rows))
        ctx = Context(scratch, unused_build, input_view, unused_build,
                      task / "environment/workload", docker=True)
        executor = DockerExecutor(image, cpuset, memory=settings["memory"],
            pids_limit=settings["pids_limit"],
            extra_docker_flags=[*HARDENING, "-v", str(input_view) + ":/recovery-inputs:ro"])
        # The SDK already built this pristine producer before any held-out admission.
        results = executor.run([Step([
            "/app/baseline-build/prog", "--check-batch",
            "/scratch/batch-list.tsv", "/scratch/outputs"])], ctx,
            {"LC_ALL": "C.UTF-8", "TZ": "UTC"})
        if (len(results) != 1 or results[0].exit_code != 0 or results[0].stdout
                or results[0].stderr or results[0].leftovers):
            raise ValueError("Image-owned pristine recovery producer failed")
        outputs = scratch / "outputs"
        if {path.name for path in outputs.iterdir()} != {name + ".json" for name in NAMES}:
            raise ValueError("Pristine recovery output set differs from the original six")
        for row in rows:
            path = outputs / (row["name"] + ".json")
            if path.is_symlink() or not path.is_file():
                raise ValueError("Pristine recovery output must be a regular file")
            raw = module.canonical_json(module.strict_loads(path.read_bytes()))
            expected = row["expected"]
            if (len(raw) != expected["bytes"] or
                    hashlib.sha256(raw).hexdigest() != expected["sha256"]):
                raise ValueError("Frozen golden length/SHA-256 mismatch: " + row["name"] +
                                 "; expected commitments must not be updated")
            contents[expected["destination"]] = raw
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="serde-originals-", dir=output.parent) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir()
        for name, raw in contents.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            if not stage_payload.authentic(destination, retained[name]):
                raise ValueError("Restored original failed final authentication: " + name)
        staging.rename(output)
    return {"image": image, "recipe_sha256": identity["recipe_sha256"],
            "output": str(output), "restored_files": len(contents), "recovered_goldens": len(rows),
            "bytes": sum(len(raw) for raw in contents.values()),
            "claimable": False, "timing_performed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Existing canonical pristine SDK image")
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, help="External original SHA-named input directory")
    parser.add_argument("--fetch", action="store_true", help="Acquire only the six pinned inputs")
    parser.add_argument("--cache", type=Path, help="External original-input URL cache")
    args = parser.parse_args(argv)
    result = recover(TASK, BUNDLE, args.image, args.cpuset, args.output,
                     args.inputs, args.fetch, args.cache)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
