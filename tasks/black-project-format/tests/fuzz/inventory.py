#!/usr/bin/env python3
"""Inventory the authenticated PUBLIC upstream archive; never private task data."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

PIN = "87928e6d6761a4a6d22250e1fee5601b3998086e"
ARCHIVE_SHA = "a4bba75d9fe04c23a5b0935e3cca7b5155dcbde11154fd4ecef69c3845dafd5a"
OUT = Path(__file__).resolve().parent
SURFACES = {
    "black.format_str": ("src/black/__init__.py", "format_str", "format"),
    "black.format_file_contents": ("src/black/__init__.py", "format_file_contents", "format"),
    "black.format_cell": ("src/black/__init__.py", "format_cell", "cell"),
    "black.format_ipynb_string": ("src/black/__init__.py", "format_ipynb_string", "notebook"),
    "black.format_file_in_place": ("src/black/__init__.py", "format_file_in_place", "filesystem"),
    "black.format_stdin_to_stdout": ("src/black/__init__.py", "format_stdin_to_stdout", "stdio"),
    "black.Mode": ("src/black/mode.py", "Mode", "mode"),
    "black.TargetVersion": ("src/black/mode.py", "TargetVersion", "target"),
    "black.Preview": ("src/black/mode.py", "Preview", "feature"),
    "black.cli": ("src/black/__init__.py", "main", "cli"),
    "blackd.make_app": ("src/blackd/__init__.py", "make_app", "http"),
    "blackd.client.BlackDClient": ("src/blackd/client.py", "BlackDClient", "http-client"),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def annotation(value):
    return ast.dump(value, include_attributes=False) if value is not None else None


def signature(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        positional = [*node.args.posonlyargs, *node.args.args]
        optional_at = len(positional) - len(node.args.defaults)
        result = []
        for index, arg in enumerate(positional):
            result.append({"name": arg.arg, "kind": "positional-only" if index < len(node.args.posonlyargs)
                           else "positional-or-keyword", "required": index < optional_at,
                           "annotation_ast": annotation(arg.annotation)})
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            result.append({"name": arg.arg, "kind": "keyword-only", "required": default is None,
                           "annotation_ast": annotation(arg.annotation)})
        return {"kind": "async-callable" if isinstance(node, ast.AsyncFunctionDef) else "callable",
                "parameters": result, "return_annotation_ast": annotation(node.returns)}
    return {"kind": "class", "fields": [
        {"name": field.target.id, "annotation_ast": annotation(field.annotation)}
        for field in node.body if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
    ], "public_methods": [
        method.name for method in node.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_")
    ]}


def header_review(file_count, inventory_sha256):
    review = json.loads((OUT / "header-scope.json").read_text())["independent_review"]
    fields = {"reviewer_role", "audited_at_utc", "method", "verified_support_documents",
              "independently_verified_archive_sha256",
              "independently_verified_files", "independently_verified_file_inventory_sha256",
              "decision", "limitations"}
    if (set(review) != fields
            or review["independently_verified_archive_sha256"] != ARCHIVE_SHA
            or review["independently_verified_files"] != file_count
            or review["independently_verified_file_inventory_sha256"] != inventory_sha256):
        raise ValueError("Refresh the independent public-source header review before regenerating")
    return review


def main(source, archive):
    assert sha(archive) == ARCHIVE_SHA, "Only the exact pinned public archive is admitted"
    docs = [
        "LICENSE", "pyproject.toml", "README.md", "docs/usage_and_configuration/the_basics.md",
        "docs/usage_and_configuration/black_as_a_server.md",
        "docs/usage_and_configuration/file_collection_and_discovery.md",
        "docs/guides/using_black_with_jupyter_notebooks.md",
    ]
    evidence = [{"path": path, "sha256": sha(source / path),
                 "url": f"https://github.com/psf/black/blob/{PIN}/{path}"} for path in docs]
    rows = []
    for symbol, (relative, name, family) in SURFACES.items():
        tree = ast.parse((source / relative).read_text())
        node = next(item for item in tree.body if isinstance(
            item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == name)
        origin = symbol
        if symbol in ("black.Mode", "black.TargetVersion", "black.Preview"):
            origin = "black.mode." + name
        rows.append({
            "id": symbol, "visibility": "public",
            "contract_id": "black-public-v1", "precondition_id": "black-typed-bounded-v1",
            "generators": (["typed-grammar-v1", "explicit-public-corpus-mutation-v1"]
                           if family not in ("http", "http-client", "mode", "target", "feature") else []),
            "oracle_policy": "black-library-values-error-classes-and-exact-cli-v1",
            "oracle": {"required_error_fields": {"class": "nonempty_string", "message": "string"}},
            "surface_id": symbol, "symbol_path": symbol,
            "signature_version": "python-structural-ast-v1", "structural_signature": signature(node),
            "reexport_origin": origin, "reexport_chain": [origin] if origin == symbol else [origin, symbol],
            "option_family_ids": [family], "precondition_refs": ["black-typed-bounded-v1"],
            "generator_ids": ["typed-grammar-v1"] if family not in ("http", "http-client") else [],
            "sequence_applicability": "reused caller Mode A/B/A and clear-target sets; complete reset coverage pending",
            "fault_applicability": "documented syntax errors and readonly/missing file faults; complete I/O faults pending",
            "oracle_policy_id": "black-library-values-error-classes-and-exact-cli-v1",
            "public_evidence": {"path": relative, "sha256": sha(source / relative),
                                "line": node.lineno, "documentation_review": "pending-validation2"},
            "coverage_status": "not-exercised",
        })
    manifest = {
        "task": "black-project-format", "format": "optimbench-fuzz-surface-v1",
        "source": {"commit": PIN, "archive_sha256": ARCHIVE_SHA, "public": True},
        "extractor": {"status": "incomplete-corrected-extractor-dependency", "version": None, "report_sha256": None},
        "build_features": {"runtime": "CPython 3.14.6", "native": "mypyc", "notebook": "pinned image dependencies",
                           "blackd": "aiohttp dependency present; HTTP worker pending"},
        "surfaces": rows, "unresolved": [
            "Full corrected public export/re-export denominator is not yet bound",
            "CLI option families beyond initial basic format/check/write/stdin",
            "blackd server/client lifecycle and documented HTTP header families",
            "Preview enabled_features, notebook magics, all supported target variants and exception constructors",
            "Path discovery/config/report/cache APIs and their true supported-public scope",
            "Diff timestamp/environment control requires predeclared reviewed contract",
        ],
        "complete": False, "contract_evidence": evidence,
    }
    files = sorted(path for path in source.rglob("*") if path.is_file())
    headers = [str(path.relative_to(source)) for path in files
               if path.suffix.lower() in (".h", ".hh", ".hpp", ".hxx")]
    c_sources = [str(path.relative_to(source)) for path in files
                 if path.suffix.lower() in (".c", ".cc", ".cpp", ".cxx")]
    inventory = [{"path": str(path.relative_to(source)), "sha256": sha(path)} for path in files]
    inventory_sha256 = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    review = header_review(len(files), inventory_sha256)
    header_report = {
        "format": "black-public-header-scope-v1", "task": "black-project-format",
        "source_commit": PIN, "archive_sha256": ARCHIVE_SHA, "files_scanned": len(files),
        "header_paths": headers, "c_cpp_source_paths": c_sources,
        "file_inventory_sha256": inventory_sha256,
        "support_evidence": evidence,
        "finding": "No C/C++ headers or C/C++ sources in the pinned upstream archive" if not headers and not c_sources
                   else "Public-vs-private header scope requires further review",
        "generated_mypyc_headers": "Build/dependency internals, not asserted as a documented C consumer API",
        "standards_claimed": [], "legal_include_contexts_claimed": [],
        "matrix_applicability": "not-applicable-to-pinned-Python-public-API-feature",
        "independent_review": review,
        "qualification": "independently reviewed header scope only; not full surface or native qualification",
    }
    (OUT / "surface.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (OUT / "header-scope.json").write_text(json.dumps(header_report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"provisional_surfaces": len(rows), "complete": False,
                      "headers": headers, "c_cpp_sources": c_sources, "files_scanned": len(files)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    main(args.source, args.archive)
