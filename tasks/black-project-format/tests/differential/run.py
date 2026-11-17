"""Complete public API outcomes, including repeated independent Mode instances."""
import json
from pathlib import Path
import sys

sys.path.insert(0, "/app/build/runtime")
import black


def outcome(operation):
    try:
        return {"value": operation()}
    except Exception as error:
        return {"exception": type(error).__name__, "message": str(error)}


def mode(options):
    options = dict(options)
    target = options.pop("target")
    options["python_cell_magics"] = set(options["python_cell_magics"])
    options["enabled_features"] = {getattr(black.Preview, name) for name in options["enabled_features"]}
    return black.Mode(target_versions={getattr(black.TargetVersion, target)}, **options)


def run(case):
    source = case["source"]
    if case["real_index"] is not None:
        files = sorted(p for p in Path("/app/projects").glob("**/*.py")
                       if p.is_file() and p.stat().st_size <= 12000)
        if not files:
            raise RuntimeError("Image has no bounded real project Python inputs")
        real = files[case["real_index"] % len(files)].read_text(encoding="utf-8", errors="replace")
        if case["mutation"] == "append":
            source = real + "\n" + source
        elif case["mutation"] == "unicode_comment":
            source = "# λ🙂 seeded mutation\n" + real
        else:
            source = real + "\n  unexpected_indent = 1\n"
    options = case["mode"]
    alternate = {**options, "line_length": 63, "string_normalization": not options["string_normalization"],
                 "preview": not options["preview"], "is_pyi": not options["is_pyi"]}
    # A/B/A, separately constructed instances, within one imported native package.
    transcript = []
    for current in [options, alternate, options]:
        instance = mode(current)
        transcript.append({
            "format_str": outcome(lambda: black.format_str(
                source, mode=instance, lines=[tuple(r) for r in case["ranges"]])),
            "format_file_contents": outcome(lambda: black.format_file_contents(
                source, fast=case["fast"], mode=instance)),
        })
    # Mutate a caller-owned target set, then create a new Mode independently.
    changed = mode(options)
    changed.target_versions.clear()
    transcript.append(outcome(lambda: black.format_str(source, mode=changed)))
    transcript.append(outcome(lambda: black.format_str(source, mode=mode(options))))
    return transcript


print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text())), ensure_ascii=False, sort_keys=True))
