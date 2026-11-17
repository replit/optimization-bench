"""Original Black pre-commit hook: original file filtering and pre-commit batching, one worker."""
import json
from pathlib import Path
import sys

from pre_commit.commands.run import Classifier, filter_by_include_exclude
from pre_commit.xargs import xargs
import yaml


def main():
    case = json.loads(Path(sys.argv[1]).read_text())
    config = yaml.safe_load(Path(".pre-commit-config.yaml").read_text())
    hook = next(h for repo in config["repos"] for h in repo["hooks"] if h["id"] == "black")
    classifier = Classifier.from_config(
        case["tracked_paths"], config.get("files", ""), config.get("exclude", "^$"))
    selected = filter_by_include_exclude(
        classifier.filenames, hook.get("files", ""), hook.get("exclude", "^$"))
    selected = tuple(classifier.by_types(
        selected, hook.get("types", ["file"]), hook.get("types_or", ["python", "pyi"]),
        hook.get("exclude_types", [])))
    if sorted(selected) != case["selected_paths"]:
        raise ValueError("Original hook selection differs from the frozen manifest")
    code, output = xargs(
        (sys.executable, "-m", "black", *hook.get("args", [])), selected,
        target_concurrency=1, color=False)
    sys.stdout.buffer.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
