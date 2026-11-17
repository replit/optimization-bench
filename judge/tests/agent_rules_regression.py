"""Rebuild and reject the actual Sonnet jq/goldmark patches on a trusted Docker host."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from judge import build, policy, reference
from judge.score import assets
from judge.verifier import evaluate, image_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=["jq-strip-openapi-descriptions", "goldmark-gfm-html"], required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--cpuset", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    task = root / "tasks" / args.task
    image = image_id(args.image)
    trusted = assets(task, image, args.cache)
    rebuilt_reference = reference.ensure(task, trusted, image, args.cpuset, args.cache)
    args.output.mkdir(parents=True)
    source = args.output / "patched"
    shutil.copytree(trusted / "pristine", source, symlinks=True)
    fixture = Path(__file__).parent / "fixtures" / (
        "sonnet55-jq.patch" if args.task.startswith("jq-") else "sonnet55-goldmark.patch")
    subprocess.run(["git", "-C", str(source), "apply", str(fixture)], check=True)
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    admission = policy.envelope(source, trusted / "pristine", rules)
    build.build(task, trusted / "pristine", image, args.cpuset, args.output / "rebuilt", source=source)
    result = evaluate(task, rebuilt_reference["build"], args.output / "rebuilt/build",
                      trusted / "projects", image, args.cpuset, "regression",
                      plumbing=True, dry_run=True,
                      public_inputs=trusted / "public-workload/inputs",
                      review_findings=admission["runtime_configuration_review"],
                      reference_info=rebuilt_reference)
    expected = "interface_broken" if args.task.startswith("jq-") else "needs_review"
    assert result["status"] == expected, result
    assert not result["claimable"] and result["speedup"] is None, result
    if args.task.startswith("jq-"):
        removed = [item for component in result["public_interface"]["components"]
                   for item in component["removed"]]
        assert any(item.endswith(":jv_copy") for item in removed), removed
        assert any(item.endswith(":jv_free") for item in removed), removed
        assert any(item.endswith(":jv_get_kind") for item in removed), removed
    else:
        assert any("SetGCPercent" in line["text"]
                   for row in result["runtime_configuration_review"] for line in row["added_lines"])
    result["fixture_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"task": args.task, "status": result["status"], "fixture": result["fixture_sha256"]}))


if __name__ == "__main__":
    main()
