#!/usr/bin/env python3
"""Documented public-header inventory; corrected shared extraction still required."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(source):
    headers = [source/"src/jv.h", source/"src/jq.h"]
    rows = {}
    for header in headers:
        command = ["clang", "-x", "c", "-std=c11", "-Xclang", "-ast-dump=json",
                   "-fsyntax-only", str(header)]
        parsed = json.loads(subprocess.check_output(command))
        def visit(node):
            name = node.get("name", "")
            if node.get("kind") == "FunctionDecl" and name.startswith(("jq_", "jv_")):
                rows[name] = {
                    "surface_id": "jq.c:"+name, "symbol_path": name,
                    "structural_signature": {"format": "clang-qualtype-v1",
                                             "type": node["type"]["qualType"]},
                    "public_origin": "src/jq.h" if name.startswith("jq_") else "src/jv.h",
                    "reexport_chain": [], "feature_conditions": [],
                    "option_family_ids": [], "generator_ids": [],
                    "precondition_refs": ["src/jv.h:ownership-rules", "typed-public-api-domain-required"],
                    "sequence_applicability": "unresolved",
                    "fault_applicability": "unresolved",
                    "oracle_policy": "requires-per-surface-contract-review",
                    "status": "inventoried-unmapped-unexercised",
                }
            for child in node.get("inner", []):
                visit(child)
        visit(parsed)
    macros = []
    for header in headers:
        for name in re.findall(r"^#define\s+([A-Z][A-Z0-9_]+)", header.read_text(), re.M):
            if name not in ("JV_H", "JQ_H"):
                macros.append({"surface_id": "jq.macro:"+name, "symbol_path": name,
                               "public_origin": str(header.relative_to(source)),
                               "status": "inventoried-unmapped-unexercised"})
    return {
        "format": "optimbench-jq-public-surface-worklist-v1",
        "source_commit": "49fbca1242c7f46f00b849de76565764e0254a03",
        "source_tree_sha256": None,
        "extractor": {
            "tool": subprocess.check_output(["clang","--version"],text=True).splitlines()[0],
            "version": "task-owned-header-worklist-v1",
            "corrected_shared_extractor": "pending corrected-extractor report",
            "final_denominator": False,
        },
        "features": {"platform": "pinned-linux-task-image", "oniguruma": "builtin"},
        "public_header_hashes": {str(header.relative_to(source)): sha(header) for header in headers},
        "scope_evidence": {"path": "Makefile.am", "sha256": sha(source/"Makefile.am"),
                           "statement": "include_HEADERS = src/jv.h src/jq.h"},
        "entries": [rows[name] for name in sorted(rows)] + macros + [{
            "surface_id": "jq.cli:filter", "symbol_path": "jq CLI filter execution",
            "public_origin": "docs/content/manual/dev/manual.yml",
            "generator_ids": ["jq-grammar-v1","explicit-public-document-transform-v1"],
            "precondition_refs": ["bounded-cli-byte-input-v1"],
            "option_family_ids": ["slurp","exit-status","sorted","ascii","stream","stream-errors","seq","raw-input"],
            "oracle_policy": "jq-public-complete-streams-v1", "status": "implemented-campaign-not-run",
        }],
        "unresolved": [
            "Corrected shared extractor and export/macro/options denominator",
            "Per-symbol legal domain/state/fault mapping beyond CLI subset",
            "All supported CLI option families and combinations",
            "Documented supported C/C++ dialect/include feature matrix",
            "Coverage, sanitizer, race, CPU-repeat and mutation adequacy evidence",
        ],
    }


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    result=inventory(args.source)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"entries":len(result["entries"]),"final_denominator":False}))
