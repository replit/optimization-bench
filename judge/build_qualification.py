"""Two fresh pristine builds; compare packaged native ALLOC/code, never timings."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from . import build, score, policy
from .verifier import image_id


def elf(path):
    data = Path(path).read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Qualification supports little-endian ELF64 only: " + str(path))
    h = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    headers = [struct.unpack_from("<IIQQQQIIQQ", data, h[6] + i * h[11])
               for i in range(h[12])]
    sh = headers[h[13]]
    strings = data[sh[4]:sh[4] + sh[5]]
    sections = {}
    for row in headers:
        name = strings[row[0]:strings.find(b"\0", row[0])].decode(errors="strict")
        sections[name] = {
            "flags": row[2], "address": row[3], "offset": row[4], "size": row[5],
            "alignment": row[8],
            "data": b"" if row[1] == 8 else data[row[4]:row[4] + row[5]],
        }
    return {"sha256": hashlib.sha256(data).hexdigest(), "sections": sections}


def artifacts(folder):
    """Native exported programs and all packaged Python native extensions.

    Exclude compiler intermediates/tests in project/target/cmake/source trees.
    Python extensions form a conservative superset of actual per-case loads.
    Static libraries linked into the programs are covered by the final ELF.
    """
    folder = Path(folder)
    paths = list(folder.iterdir())
    for subtree in ("site", "runtime"):
        if (folder / subtree).is_dir():
            paths.extend((folder / subtree).rglob("*"))
    result = []
    for path in sorted(paths):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            header = stream.read(20)
        if header[:4] == b"\x7fELF":
            if len(header) < 20 or header[4:6] != b"\x02\x01":
                raise ValueError("Unsupported exported ELF format: " + str(path))
            if struct.unpack_from("<H", header, 16)[0] in (2, 3):
                result.append(str(path.relative_to(folder)))
    if not result:
        raise ValueError("No exported native artifacts; this is not a native qualification")
    return result


DATE = re.compile(rb"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [ 0-3][0-9] [0-9]{4}\b")
TIME = re.compile(rb"\b[0-2][0-9]:[0-5][0-9]:[0-6][0-9]\b")


def _dates(data):
    return TIME.sub(b"00:00:00", DATE.sub(b"Jan 01 2000", data))


def compare(left, right):
    a, b = elf(left), elf(right)
    changes, layout, code, meaningful = [], [], [], []
    for name in sorted(a["sections"].keys() | b["sections"].keys()):
        x, y = a["sections"].get(name), b["sections"].get(name)
        if not ((x or {}).get("flags", 0) & 2 or (y or {}).get("flags", 0) & 2):
            continue
        if x is None or y is None:
            changes.append(name)
            layout.append(name)
            meaningful.append(name)
            continue
        content = x["data"] != y["data"]
        moved = any(x[k] != y[k] for k in ("flags", "address", "offset", "size", "alignment"))
        if content:
            changes.append(name)
            if x["flags"] & 4 or y["flags"] & 4:
                code.append(name)
            if name not in (".note.gnu.build-id", ".note.go.buildid"):
                meaningful.append(name)
        if moved:
            layout.append(name)
    same = a["sha256"] == b["sha256"]
    date_only = bool(meaningful) and all(
        name in a["sections"] and name in b["sections"]
        and _dates(a["sections"][name]["data"]) == _dates(b["sections"][name]["data"])
        for name in meaningful)
    if same:
        status, cause = "identical", "Whole-file SHA-256 identical"
    elif layout or code:
        status, cause = "flagged", "Loaded layout or executable code differs"
    elif date_only:
        status, cause = "acceptable-with-disclosure", "Only same-length date/time strings and build IDs differ"
    elif not meaningful:
        status, cause = "acceptable-with-disclosure", "Only debug/non-ALLOC bytes or build-ID notes differ"
    else:
        status, cause = "flagged", "Loaded data differs beyond same-length date/time strings"
    # Bounded evidence of common path/.git causes; never print binary contents.
    paths = {}
    for side, info, other in (("first", a, b), ("second", b, a)):
        strings = set()
        for name in meaningful:
            raw = info["sections"].get(name, {}).get("data", b"")
            old = other["sections"].get(name, {}).get("data", b"")
            values = set(re.findall(rb"[\x20-\x7e]{8,}", raw))
            values -= set(re.findall(rb"[\x20-\x7e]{8,}", old))
            strings.update(value[:200].decode() for value in values
                           if b"/scratch/" in value or b"/app/" in value)
        paths[side] = sorted(strings)[:12]
    return {"first_sha256": a["sha256"], "second_sha256": b["sha256"],
            "identical": same, "status": status, "cause": cause,
            "differing_alloc_sections": changes, "layout_changes": layout,
            "code_changes": code, "path_evidence": paths}


def qualify(task, image, cpuset, out, cache):
    task, out, cache = map(lambda p: Path(p).resolve(), (task, out, cache))
    if out.exists():
        raise ValueError("Qualification output must be fresh")
    image = image_id(image)
    trusted = score.assets(task, image, cache)
    out.mkdir(parents=True)
    builds = []
    for name in ("first", "second"):
        build.build(task, trusted / "pristine", image, cpuset, out / name,
                    source=trusted / "pristine")
        builds.append(out / name / "build")
    left, right = map(artifacts, builds)
    if left != right:
        raise ValueError("Two pristine builds exported different native artifact sets")
    comparisons = {name: compare(builds[0] / name, builds[1] / name) for name in left}
    rules = json.loads((task / "release.json").read_text())["source_policy"]
    result = {
        "format": "judge-pristine-build-qualification-v1", "task": task.name,
        "image": image, "source_sha256": policy.source_digest(
            policy.envelope(trusted / "pristine", trusted / "pristine", rules)),
        "author_host": True, "diagnostic_only": True, "claimable": False,
        "timing_performed": False, "compiler_cache_seed": None,
        "scope": "Exported native ELF programs/libraries and packaged native Python extensions",
        "status": "flagged" if any(v["status"] == "flagged" for v in comparisons.values())
                  else "identical" if all(v["identical"] for v in comparisons.values())
                  else "acceptable-with-disclosure",
        "artifacts": comparisons,
    }
    (out / "qualification.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cpuset", default="0-7")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/optimbench-score-cache"))
    args = parser.parse_args()
    result = qualify(args.task, args.image, args.cpuset, args.out, args.cache)
    print(json.dumps({"task": result["task"], "status": result["status"]}))
    return 1 if result["status"] == "flagged" else 0


if __name__ == "__main__":
    raise SystemExit(main())
