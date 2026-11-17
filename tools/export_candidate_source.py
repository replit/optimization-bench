"""Export policy-validated production source, never candidate build artifacts."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from judge import policy, score, source_policy


def export(task, candidate, pristine, output):
    task, candidate, pristine, output = map(Path, (task, candidate, pristine, output))
    if output.exists():
        raise ValueError("Candidate source export destination must be fresh")
    if output.resolve().is_relative_to(candidate.resolve()) or output.resolve().is_relative_to(pristine.resolve()):
        raise ValueError("Candidate export must be outside both source trees")
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    original = source_policy.snapshot(pristine, rules, trusted_pristine=True)
    files = source_policy.snapshot(candidate, rules, original=original)
    file_exports = (rules.get("operations") == ["modify"] and
                    rules["source_roots"] == ["."] and bool(rules.get("allowed_files")))
    names = rules["allowed_files"] if file_exports else rules["source_roots"]
    output.mkdir(parents=True)
    tree = output / "source"
    tree.mkdir()
    if file_exports:
        for name, body in files.items():
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf8")
    else:
        for name in names:
            if name == "." or Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Unsafe directory export root")
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(pristine / name, target, symlinks=True,
                            ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        source_policy.apply(tree, original, files, rules)
    descriptor = output / "submission.json"
    value = {"artifacts": {name: "source/" + name for name in names},
             "source_manifest_sha256": source_policy.digest(files)}
    descriptor.write_text(json.dumps(value, indent=2) + "\n")
    mapping, _ = score.artifacts(task, descriptor)
    with tempfile.TemporaryDirectory() as temporary:
        replay = Path(temporary) / "replay"
        policy.materialize(mapping, pristine, replay, rules)
        admitted = policy.envelope(replay, pristine, rules)
        if admitted["files"] != files:
            raise ValueError("Source-only candidate replay differs from the exported source manifest")
    return descriptor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--source", type=Path, required=True,
                        help="Complete source tree from the agent; generated build outputs are not exported")
    parser.add_argument("--pristine", type=Path, required=True,
                        help="Trusted pristine tree extracted by canonical score.assets")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    print(export(ROOT / "tasks" / args.task, args.source, args.pristine, args.out))


if __name__ == "__main__":
    main()
