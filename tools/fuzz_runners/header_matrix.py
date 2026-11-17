"""Two-TU public-header compile AND link evidence, not a task verdict engine.

The task owner freezes required_cells, support references and legal include
contexts before candidate execution. Dialects are never inferred from this
runner's compiler accepting them. Both arms use the same immutable task runner
image and cell compiler/flags/dependencies; only matching build roots differ.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SCHEMA = "optimbench-header-matrix-v1"
LIMIT = 1024 * 1024
DIALECTS = {
    **{name: "c" for name in ("gnu89", "c89", "c90", "gnu90", "c99", "gnu99",
                               "c11", "gnu11", "c17", "gnu17", "c18", "gnu18", "c23", "gnu23")},
    **{prefix + version: "c++" for prefix in ("c++", "gnu++")
       for version in ("98", "03", "11", "14", "17", "20", "23")},
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe(root, name):
    root = Path(root).resolve()
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("matrix input path must be relative to matching build")
    path = (root / path).resolve()
    path.relative_to(root)
    return path


def validate_manifest(data):
    if data.get("schema") != SCHEMA:
        raise ValueError("unknown header matrix schema")
    required = data.get("required_cells")
    cells = data.get("cells")
    if not isinstance(required, list) or not required or len(set(required)) != len(required):
        raise ValueError("required_cells must be a nonempty frozen unique inventory")
    if not isinstance(cells, list) or {c["id"] for c in cells} != set(required) or len(cells) != len(required):
        raise ValueError("every frozen header/context/dialect/feature cell needs exactly one record")
    for cell in cells:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", cell["id"]):
            raise ValueError("invalid cell id")
        pending = cell.get("support_status") == "pending"
        if (not pending and type(cell.get("supported")) is not bool) or not cell.get("support_refs"):
            raise ValueError("each supported/unsupported cell needs predeclared upstream evidence")
        for ref in cell["support_refs"]:
            if not ref.get("quote") or not (ref.get("url") or ref.get("path")) or not re.fullmatch(r"[0-9a-f]{64}", ref.get("sha256", "")):
                raise ValueError("support evidence requires source reference, content hash and relevant quote")
        if pending:
            if cell.get("supported") is not None or not cell.get("reason") or not cell.get("headers"):
                raise ValueError("pending support requires supported:null, known headers, reason and partial source evidence")
            continue
        if not cell["supported"]:
            if not cell.get("reason"):
                raise ValueError("unsupported cell needs reason")
            continue
        default = cell.get("dialect") == "compiler-default"
        if default and (cell.get("language") not in ("c", "c++")
                        or cell.get("support_basis") != "pinned-upstream-default-flags"):
            raise ValueError("compiler-default needs C/C++ and source-bound pinned upstream default-flags authority")
        if not default and (cell.get("dialect") not in DIALECTS or cell.get("language") != DIALECTS[cell["dialect"]]):
            raise ValueError("supported dialect/language mismatch")
        if not cell.get("headers") or not cell.get("features") and cell.get("features") != []:
            raise ValueError("headers/features inventory required")
        sdk_headers = cell.get("optional_third_party_sdk_headers", [])
        if not isinstance(sdk_headers, list):
            raise ValueError("optional SDK headers must be a frozen source-bound inventory")
        seen_sdk = set()
        for sdk in sdk_headers:
            header = sdk.get("header", "")
            if (not re.fullmatch(r"[A-Za-z0-9_.+/-]+", header) or Path(header).is_absolute()
                    or ".." in Path(header).parts or header in seen_sdk):
                raise ValueError("invalid/duplicate optional SDK header")
            seen_sdk.add(header)
            refs = sdk.get("support_refs", [])
            if not refs or any(not ref.get("quote") or not (ref.get("path") or ref.get("url"))
                               or not re.fullmatch(r"[0-9a-f]{64}", ref.get("sha256", "")) for ref in refs):
                raise ValueError("optional third-party SDK designation needs upstream source references")
        if not cell.get("include_context", {}).get("includes") or not cell["include_context"].get("id"):
            raise ValueError("legal include context required")
        tus = cell.get("translation_units")
        if not isinstance(tus, list) or len(tus) != 2 or tus[0]["name"] == tus[1]["name"]:
            raise ValueError("exactly two distinct translation units required")
        if tus[0]["source"] == tus[1]["source"]:
            raise ValueError("translation units must be distinct consumers, not duplicate text")
        if not cell.get("symbol_refs"):
            raise ValueError("real public symbol reference inventory required")
        for tu in tus:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx)", tu["name"]):
                raise ValueError("invalid TU filename")
            if not all(re.search(r'#\s*include\s*["<]' + re.escape(header) + r'[">]', tu["source"])
                       for header in cell["include_context"]["includes"]):
                raise ValueError("both TUs must include the declared legal context")
            if not any(re.search(r"\b" + re.escape(symbol) + r"\b", tu["source"]) for symbol in cell["symbol_refs"]):
                raise ValueError("each consumer must reference declared public symbols")
        compiler = cell.get("compiler", "")
        if not re.fullmatch(r"(?:/[A-Za-z0-9_.+/-]+|[A-Za-z0-9_.+-]+)", compiler):
            raise ValueError("compiler must be a single pinned executable name/path")
        flags = cell.get("compile_flags", []) + cell.get("link_flags", [])
        macros = cell.get("defines", [])
        for flag in flags + macros:
            if not isinstance(flag, str):
                raise ValueError("flags/macros must be strings")
        joined = " ".join(flags + macros).lower()
        banned = ("flto", "fgnu89-inline", "fno-gnu89-inline", "allow-multiple-definition",
                  "muldefs", "gc-sections", "function-sections", "data-sections",
                  "unresolved-symbols", "allow-shlib-undefined", "relocatable",
                  "defsym", "nodefaultlibs", "nostdlib", "weak", "inline=",
                  "__gnuc_stdc_inline__", "__gnuc_gnu_inline__", "fcommon", "fuse-ld",
                  "-specs", "-wrapper", "-fplugin")
        if any(token in joined for token in banned):
            raise ValueError("link/inline/undefined-symbol bypass flags forbidden")
        if any(flag.startswith(("@", "-std=", "-O", "-o", "-ansi")) or flag in ("-r", "-shared", "-c", "-S", "-E")
               for flag in flags):
            raise ValueError("cell may not override runner dialect/output/compile-link stages")
    return data


def _command(argv, out, name, timeout=120):
    try:
        result = subprocess.run(argv, cwd=out, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                                     "HOME": str(out), "LC_ALL": "C", "TMPDIR": str(out)})
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
        error = None
    except (subprocess.TimeoutExpired, OSError) as exc:
        code, stdout, stderr = None, getattr(exc, "stdout", b"") or b"", getattr(exc, "stderr", b"") or b""
        error = str(exc)
    artifacts = {}
    for stream, raw in (("stdout", stdout), ("stderr", stderr)):
        path = out / (name + "." + stream)
        path.write_bytes(raw[:LIMIT])
        artifacts[stream] = {"path": path.name, "sha256": _sha(raw[:LIMIT]), "full_sha256": _sha(raw),
                             "bytes": len(raw), "truncated": len(raw) > LIMIT}
    record = {"argv": argv, "exit_code": code, "logs": artifacts}
    if error:
        record["infrastructure_error"] = error
    return record


def dialect_flags(cell):
    """Representation only, never infer default-mode portable numeric support."""
    return [] if cell["dialect"] == "compiler-default" else ["-std=" + cell["dialect"]]


def default_macros(text):
    names = {"__STDC__", "__STDC_VERSION__", "__cplusplus", "__STRICT_ANSI__",
             "__GNUC_STDC_INLINE__", "__GNUC_GNU_INLINE__", "__GNUC__", "__GNUC_MINOR__",
             "__GNUC_PATCHLEVEL__", "__GXX_ABI_VERSION__"}
    result = {}
    for line in text.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0] == "#define" and fields[1] in names:
            result[fields[1]] = fields[2]
    return result


def missing_sdk_evidence(cell, compiled, build, out):
    """Conservative raw proof, not an inference from arbitrary baseline failure."""
    declared = {row["header"]: row for row in cell.get("optional_third_party_sdk_headers", [])}
    log = compiled.get("logs", {}).get("stderr", {})
    if (not declared or compiled.get("infrastructure_error") or
            not isinstance(compiled.get("exit_code"), int) or compiled["exit_code"] <= 0
            or log.get("truncated")):
        return None
    try:
        raw = (Path(out) / log["path"]).read_bytes()
        if len(raw) != log["bytes"] or _sha(raw) != log["sha256"] or _sha(raw) != log["full_sha256"]:
            return None
        text = raw.decode("utf-8", errors="backslashreplace")
        diagnostics = []
        for line in text.splitlines():
            match = re.fullmatch(r"(.+?):(\d+):(?:(\d+):)?\s*(fatal error|error):\s*(.*)", line)
            if match:
                diagnostics.append((line, match))
        if len(diagnostics) != 1 or diagnostics[0][1][4] != "fatal error":
            return None  # Syntax/context/Werror failures cannot become SDK N/A.
        diagnostic, match = diagnostics[0]
        missing = re.fullmatch(r"(?:'([^'\r\n]+)' file not found|([^:\r\n]+): No such file or directory)", match[5])
        if not missing:
            return None
        header = missing[1] or missing[2]
        if header not in declared or header in cell["include_context"]["includes"]:
            return None  # Never exempt a directly requested primary/context header.
        primary = {_safe(build, name): name for name in cell["headers"]}
        if any(header == name or header == Path(name).name for name in cell["headers"]):
            return None
        origin = (Path(out) / match[1]).resolve()
        # -H proves this is a real transitive include, not a TU's missing include
        # or a diagnostic merely naming a public header via #line.
        stack, chain = [], None
        for line in text.splitlines():
            trace = re.fullmatch(r"(\.+) (.+)", line)
            if not trace:
                continue
            depth = len(trace[1])
            path = (Path(out) / trace[2]).resolve()
            if depth > len(stack) + 1:
                return None
            stack = stack[:depth - 1] + [path]
            if path == origin:
                chain = list(stack)
        if not chain or not any(path in primary for path in chain):
            return None
        source = origin.read_bytes()
        number = int(match[2])
        if number < 1 or number > len(source.splitlines()):
            return None
        source_line = source.splitlines()[number - 1]
        directive = re.fullmatch(
            r'\s*#\s*include\s*(?:<([^>]+)>|"([^"]+)")\s*(?://.*|/\*.*\*/\s*)?',
            source_line.decode("utf-8", errors="backslashreplace"))
        if not directive or (directive[1] or directive[2]) != header:
            return None
        return {
            "schema": "optimbench-missing-third-party-sdk-v1", "missing_header": header,
            "sdk_support_refs": declared[header]["support_refs"],
            "requested_public_headers": [{"path": primary[path], "sha256": _sha(path.read_bytes())}
                                         for path in chain if path in primary],
            "include_chain": [{"path": str(path), "sha256": _sha(path.read_bytes())} for path in chain],
            "diagnostic_source": str(origin), "diagnostic_source_sha256": _sha(source),
            "line": number, "column": int(match[3]) if match[3] else None,
            "source_line_hex": source_line.hex(), "diagnostic": diagnostic,
            "compiler_stderr": dict(log), "compiler_argv": list(compiled["argv"]),
            "scope": "not resolvable in the frozen legal pristine compile context; not whole-filesystem absence"}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_cell(cell, build, out):
    out = Path(out).resolve()
    out.mkdir(parents=True)
    cell_sha256 = _sha(json.dumps(cell, sort_keys=True, separators=(",", ":")).encode())
    if cell.get("support_status") == "pending":
        return {"id": cell["id"], "status": "pending", "reason": cell["reason"],
                "support_refs": cell["support_refs"], "headers": cell["headers"],
                "compile": [], "link": None, "cell_sha256": cell_sha256}
    if not cell["supported"]:
        return {"id": cell["id"], "status": "unsupported", "reason": cell["reason"],
                "support_refs": cell["support_refs"], "compile": [], "link": None,
                "cell_sha256": cell_sha256}
    record = {"id": cell["id"], "status": "completed", "language": cell["language"],
              "dialect": cell["dialect"], "features": cell["features"],
              "support_refs": cell["support_refs"], "include_context": cell["include_context"],
              "headers": [], "libraries": [], "compile": [], "link": None,
              "translation_units": [], "symbol_refs": cell["symbol_refs"], "cell_sha256": cell_sha256}
    try:
        for key in ("headers", "libraries"):
            for name in cell.get(key, []):
                path = _safe(build, name)
                record[key].append({"path": name, "sha256": _sha(path.read_bytes())})
        compiler = cell["compiler"]
        compiler_path = shutil.which(compiler)
        if compiler_path is None:
            raise ValueError("pinned compiler unavailable")
        record["compiler"] = {"path": compiler_path, "sha256": _sha(Path(compiler_path).read_bytes()),
                              "version": _command([compiler, "--version"], out, "compiler-version")}
        ld_record = _command([compiler, "-print-prog-name=ld"], out, "linker-path")
        if ld_record["exit_code"] != 0:
            raise ValueError("cannot resolve pinned compiler linker")
        linker = (out / "linker-path.stdout").read_text().strip()
        linker_path = shutil.which(linker)
        if linker_path is None:
            raise ValueError("compiler linker unavailable")
        record["linker"] = {"path": linker_path, "sha256": _sha(Path(linker_path).read_bytes()),
                            "version": _command([linker_path, "--version"], out, "linker-version")}
        flags = [*dialect_flags(cell), "-O0", "-fno-lto", *cell.get("compile_flags", [])]
        flags += ["-I" + str(_safe(build, path)) for path in cell.get("include_dirs", [])]
        flags += ["-D" + define for define in cell.get("defines", [])]
        if cell["dialect"] == "compiler-default":
            predefines = _command([compiler, *flags, "-dM", "-E", "-x", cell["language"], "-"],
                                  out, "compiler-default-predefines")
            if predefines["exit_code"] != 0:
                raise ValueError("cannot capture actual default compiler predefines")
            macros = default_macros((out / "compiler-default-predefines.stdout").read_text())
            if ("__cplusplus" if cell["language"] == "c++" else "__STDC__") not in macros:
                raise ValueError("compiler default-language predefines unavailable")
            record["compiler_default"] = {
                "support_basis": cell["support_basis"], "flags": flags,
                "predefines": predefines, "observed_macros": macros,
                "numeric_portability_or_minimum_inferred": False,
                "upstream_support_authentication": "canonical controller/review; macro values are observations only"}
        objects = []
        for index, tu in enumerate(cell["translation_units"]):
            source = out / tu["name"]
            source.write_text(tu["source"])
            obj = out / f"consumer-{index}.o"
            record["translation_units"].append({"name": tu["name"], "source": tu["source"],
                                                "sha256": _sha(source.read_bytes())})
            trace_flags = ["-H"] if cell.get("optional_third_party_sdk_headers") else []
            compiled = _command([compiler, *flags, *trace_flags, "-x", cell["language"], "-c", str(source), "-o", str(obj)], out, f"compile-{index}")
            sdk = missing_sdk_evidence(cell, compiled, build, out)
            if sdk:
                sdk.update(cell_sha256=cell_sha256, compiler_sha256=record["compiler"]["sha256"],
                           tu_sha256=record["translation_units"][-1]["sha256"])
                compiled["missing_third_party_sdk"] = sdk
            if compiled["exit_code"] == 0:
                compiled["object_sha256"] = _sha(obj.read_bytes())
                # Retain undefined symbol table so the reviewer can see actual
                # dependency references rather than a syntax-only include.
                compiled["undefined_symbols"] = _command(["nm", "-u", str(obj)], out, f"undefined-{index}")
            record["compile"].append(compiled)
            objects.append(str(obj))
        if all(item["exit_code"] == 0 for item in record["compile"]):
            argv = [compiler, *flags, *objects,
                    *(str(_safe(build, name)) for name in cell.get("libraries", [])),
                    *cell.get("link_flags", []), "-o", str(out / "consumer")]
            record["link"] = _command(argv, out, "link")
            if record["link"]["exit_code"] == 0:
                record["link"]["executable_sha256"] = _sha((out / "consumer").read_bytes())
        if any(item.get("infrastructure_error") for item in record["compile"]) or (record["link"] or {}).get("infrastructure_error"):
            record.update(status="invalid_run", reason="compiler/link process infrastructure failure")
    except (OSError, ValueError, KeyError) as exc:
        record.update(status="invalid_run", reason=str(exc))
    return record


def compare_cells(baseline, candidate):
    """Evidence attribution only; integrator maps these facts to gate verdicts."""
    if baseline["id"] != candidate["id"]:
        raise ValueError("matrix cells differ")
    if not baseline.get("cell_sha256") or baseline.get("cell_sha256") != candidate.get("cell_sha256"):
        return {"id": baseline["id"], "attribution": "invalid_run", "reason": "frozen header/dialect/features/context/TU configuration differs or missing"}
    if baseline["status"] == candidate["status"] == "pending":
        return {"id": baseline["id"], "attribution": "support_evidence_pending",
                "reason": baseline["reason"], "candidate_verdict": None}
    if baseline["status"] == candidate["status"] == "unsupported":
        return {"id": baseline["id"], "attribution": "unsupported", "reason": baseline["reason"]}
    if baseline["status"] != "completed" or candidate["status"] != "completed":
        return {"id": baseline["id"], "attribution": "invalid_run", "reason": "compile/link infrastructure unavailable"}
    for tool in ("compiler", "linker"):
        if baseline[tool]["sha256"] != candidate[tool]["sha256"]:
            return {"id": baseline["id"], "attribution": "invalid_run", "reason": "toolchain identity differs"}
    if (baseline.get("compiler_default", {}).get("observed_macros") !=
            candidate.get("compiler_default", {}).get("observed_macros")):
        return {"id": baseline["id"], "attribution": "invalid_run", "reason": "pinned default-compiler predefines differ"}

    def success(record):
        return len(record["compile"]) == 2 and all(c["exit_code"] == 0 for c in record["compile"]) and (record["link"] or {}).get("exit_code") == 0

    good_baseline, good_candidate = success(baseline), success(candidate)
    failed = [(index, row) for index, row in enumerate(baseline["compile"]) if row["exit_code"] != 0]
    if not good_baseline and len(baseline["compile"]) == 2 and failed and baseline["link"] is None:
        proofs = []
        for index, row in failed:
            proof = row.get("missing_third_party_sdk", {})
            if (proof.get("schema") != "optimbench-missing-third-party-sdk-v1"
                    or proof.get("cell_sha256") != baseline["cell_sha256"]
                    or proof.get("compiler_sha256") != baseline["compiler"]["sha256"]
                    or proof.get("tu_sha256") != baseline["translation_units"][index]["sha256"]
                    or proof.get("compiler_stderr") != row["logs"]["stderr"]):
                break
            proofs.append(proof)
        else:
            return {"id": baseline["id"], "attribution": "missing_third_party_sdk",
                    "pristine_missing_sdk_evidence": proofs, "candidate_difference": False,
                    "candidate_verdict": None, "qualification": "not-a-task-verdict"}
    return {"id": baseline["id"], "baseline_compile_succeeded": all(c["exit_code"] == 0 for c in baseline["compile"]),
            "baseline_link_succeeded": (baseline["link"] or {}).get("exit_code") == 0,
            "candidate_compile_succeeded": all(c["exit_code"] == 0 for c in candidate["compile"]),
            "candidate_link_succeeded": (candidate["link"] or {}).get("exit_code") == 0,
            "attribution": "baseline_supported_cell_failed" if not good_baseline else
                           "candidate_compatibility_difference" if not good_candidate else "same_success",
            "candidate_difference": good_baseline and not good_candidate,
            "qualification": "not-a-task-verdict"}


def worker_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = validate_manifest(json.loads(args.manifest.read_text()))
    args.out.mkdir(parents=True)
    result = {"schema": SCHEMA, "manifest_sha256": _sha(args.manifest.read_bytes()),
              "execution_affinity": sorted(os.sched_getaffinity(0)),
              "cells": [run_cell(cell, args.build, args.out / cell["id"]) for cell in manifest["cells"]]}
    (args.out / "matrix.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    worker_main()
