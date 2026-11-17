"""Normalize *exported* compiler coverage, without manufacturing denominators.

Run exporters inside the instrumented worker first; this module performs no
candidate execution. File names outside the declared source root are excluded
explicitly. Counts are never inferred from function names or process success.
"""
from __future__ import annotations

import argparse
import hashlib
import gzip
import json
from pathlib import Path


class CoverageError(ValueError):
    pass


def _count(value):
    if type(value) is not int or value < 0:
        raise CoverageError("coverage counters must be nonnegative integers")
    return value


def _path(name, root):
    root = Path(root).resolve()
    path = Path(name)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def normalize(kind, data, root, *, go_branches=None, executable_lines=None, path_mappings=None):
    result = {
        "schema": "optimbench-coverage-v1", "format": kind,
        "source_root": str(Path(root).resolve()), "files": {},
        "excluded_files": [], "limitations": [], "status": "available",
    }

    def mapped(name):
        for prefix, replacement in sorted((path_mappings or {}).items(), key=lambda pair: -len(pair[0])):
            if name == prefix or name.startswith(prefix.rstrip("/") + "/"):
                name = replacement.rstrip("/") + name[len(prefix):]
                break
        return name

    def file(name):
        original = name
        name = mapped(name)
        if "://" in name:
            result["limitations"].append("unresolved source URL: " + name)
            return None
        name = _path(name, root)
        if name is None:
            result["excluded_files"].append(str(original))
            return None
        return result["files"].setdefault(name, {"lines": {}, "branches": {}})

    def line(out, number, count):
        if type(number) is not int or number <= 0:
            raise CoverageError("invalid executable line")
        key = str(number)
        out["lines"][key] = max(out["lines"].get(key, 0), _count(count))

    def branch(out, key, count):
        out["branches"][key] = out["branches"].get(key, 0) + _count(count)

    if kind == "llvm":
        if data.get("type") != "llvm.coverage.json.export":
            raise CoverageError("expected llvm-cov export JSON")
        for unit in data["data"]:
            for record in unit["files"]:
                out = file(record["filename"])
                if out is None:
                    continue
                segments = record["segments"]
                for current, end in zip(segments, segments[1:]):
                    # LLVM segments: line, col, count, hasCount, regionEntry, gap.
                    if not current[3] or (len(current) > 5 and current[5]):
                        continue
                    last = end[0] - (1 if end[1] == 1 else 0)
                    for number in range(current[0], last + 1):
                        line(out, number, current[2])
                for entry in record.get("branches", []):
                    if len(entry) < 6:
                        raise CoverageError("invalid LLVM branch record")
                    key = ":".join(str(x) for x in entry[:4])
                    branch(out, key + ":true", entry[4])
                    branch(out, key + ":false", entry[5])
                if "branches" not in record:
                    result["limitations"].append("LLVM export has no branch inventory: " + record["filename"])
                # Macro expansion targets must be exported too. Their absence
                # is visible rather than being treated as covered call sites.
                for expansion in record.get("expansions", []):
                    names = expansion.get("filenames", [])
                    exported = {item["filename"] for item in unit["files"]}
                    if any(_path(n, root) is not None and n not in exported for n in names):
                        result["limitations"].append("unexported macro expansion: " + record["filename"])
    elif kind == "gcov":
        if str(data.get("format_version")) not in ("1", "2"):
            raise CoverageError("unsupported gcov JSON format version")
        result["compiler"] = {"gcc_version": data.get("gcc_version"),
                              "gcov_format_version": data["format_version"]}
        for record in data["files"]:
            out = file(record["file"])
            if out is None:
                continue
            for entry in record["lines"]:
                number = entry["line_number"]
                line(out, number, entry["count"])
                if entry.get("unexecuted_block"):
                    out.setdefault("partial_lines", []).append(number)
                if "branches" not in entry:
                    result["limitations"].append("gcov branch inventory missing; export with --branch-probabilities --branch-counts: " + record["file"])
                for index, edge in enumerate(entry.get("branches", [])):
                    identity = f'{number}:{index}:{edge.get("source_block_id", "unknown")}:{edge.get("destination_block_id", "unknown")}:fallthrough={str(edge.get("fallthrough")).lower()}:throw={str(edge.get("throw")).lower()}'
                    branch(out, identity, edge["count"])
    elif kind == "python":
        if not data.get("meta", {}).get("branch_coverage"):
            raise CoverageError("coverage.py must run with --branch")
        for name, record in data["files"].items():
            out = file(name)
            if out is None:
                continue
            if "executed_branches" not in record or "missing_branches" not in record:
                result["limitations"].append("coverage.py per-file branch inventory missing: " + name)
            for n in record["executed_lines"]:
                line(out, n, 1)
            for n in record["missing_lines"]:
                line(out, n, 0)
            for label, hit in (("executed_branches", 1), ("missing_branches", 0)):
                for source, destination in record.get(label, []):
                    branch(out, f"{source}:{destination}", hit)
    elif kind == "istanbul":
        for name, record in data.items():
            if record.get("inputSourceMap"):
                raise CoverageError("Istanbul input requires remap.cjs first")
            out = file(record.get("path", name))
            if out is None:
                continue
            for key, location in record["statementMap"].items():
                # Istanbul line coverage uses statement *start* locations,
                # never every textual line in a multiline statement.
                line(out, location["start"]["line"], record["s"][key])
            for key, location in record["branchMap"].items():
                counts = record["b"][key]
                if len(counts) != len(location["locations"]):
                    raise CoverageError("branch map/count cardinality mismatch")
                for index, (loc, count) in enumerate(zip(location["locations"], counts)):
                    start, end = loc["start"], loc["end"]
                    implicit = ""
                    if "line" not in start or "line" not in end:
                        # Istanbul represents the false outcome of an if with
                        # no explicit else as empty locations. Anchor that
                        # *recorded counter* at the source-mapped condition;
                        # never drop this edge or infer its hit count.
                        if location["type"] == "if" and index == 1 and len(counts) == 2:
                            start, end = location["loc"]["start"], location["loc"]["end"]
                            implicit = ":implicit-else"
                        else:
                            result["limitations"].append("unmapped Istanbul branch outcome: " + name + ":" + str(key))
                            continue
                    identity = f'{start["line"]}:{start["column"]}:{end["line"]}:{end["column"]}:{location["type"]}:{index}{implicit}'
                    branch(out, identity, count)
    elif kind == "go":
        if not isinstance(data, str) or not data.startswith(("mode: set\n", "mode: count\n", "mode: atomic\n")):
            raise CoverageError("expected Go text coverage profile")
        for record in data.splitlines()[1:]:
            position, statements, count = record.rsplit(" ", 2)
            name, span = position.rsplit(":", 1)
            start, end = span.split(",")
            first, last = int(start.split(".")[0]), int(end.split(".")[0])
            out = file(name)
            if out is None:
                continue
            out.setdefault("blocks", {})[span] = {
                "count": _count(int(count)), "statements": _count(int(statements))}
            # Standard Go blocks include braces/comments/non-executable lines.
            # A task-owned, source-bound executable-line inventory is mandatory
            # for a changed-line denominator. Never expand blocks blindly.
            for n in (executable_lines or {}).get(_path(mapped(name), root), []):
                if first <= n <= last:
                    line(out, n, int(count))
        if executable_lines is None:
            result["limitations"].append("Go executable-line inventory unavailable; blocks are not a line denominator")
        if go_branches is None:
            result["limitations"].append("Go standard profiles have no branch outcomes; explicit branch instrumentation required")
        else:
            if go_branches.get("schema") != "optimbench-go-branches-v1":
                raise CoverageError("invalid Go branch schema")
            seen = set()
            for record in go_branches["branches"]:
                identity = (record["file"], record["id"])
                if identity in seen:
                    raise CoverageError("duplicate Go branch identity")
                seen.add(identity)
                out = file(record["file"])
                if out is not None:
                    branch(out, f'{record["line"]}:{record["id"]}', record["count"])
    else:
        raise CoverageError("unknown coverage format")
    if not result["files"]:
        result["limitations"].append("no task source files in coverage export")
    if result["limitations"]:
        result["status"] = "unsupported"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["llvm", "gcov", "python", "istanbul", "go"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--go-branches", type=Path)
    parser.add_argument("--executable-lines", type=Path)
    parser.add_argument("--path-map", action="append", default=[], metavar="EXPORTED=LOCAL")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    decoded = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
    result = normalize(
        args.format, decoded.decode() if args.format == "go" else json.loads(decoded),
        args.source_root,
        go_branches=json.loads(args.go_branches.read_text()) if args.go_branches else None,
        executable_lines=json.loads(args.executable_lines.read_text()) if args.executable_lines else None,
        path_mappings=dict(item.split("=", 1) for item in args.path_map),
    )
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    if decoded is not raw:
        result["input_compression"] = "gzip"
        result["decoded_sha256"] = hashlib.sha256(decoded).hexdigest()
    for key, path in (("go_branches", args.go_branches), ("executable_lines", args.executable_lines)):
        if path:
            result[key + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
