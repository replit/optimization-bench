#!/usr/bin/env python3
"""Freeze public-only seed selection and source-bound initial surface evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from adapter import POLICY, ROOT, SURFACES, TASK

UPSTREAM = "5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    source = args.upstream.resolve()
    task = ROOT.parents[1]
    inputs = task / "environment/workload/inputs"
    manifest = json.loads((inputs / "manifest.json").read_text())
    assert list(manifest["splits"]) == ["train"], "Explicit public/train split required"
    docs = sorted(
        (row for batch in manifest["splits"]["train"] for row in batch["docs"]
         if 0 < row["bytes"] <= 16000), key=lambda row: row["sha256"])[:8]
    corpus = []
    for row in docs:
        original = inputs / "files" / row["sha256"]
        assert sha(original) == row["sha256"]
        dest = ROOT / "corpus" / (row["sha256"] + ".sql")
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(original, dest)
        corpus.append({
            "path": dest.name, "sha256": row["sha256"], "bytes": row["bytes"],
            "original_public_path": row["path"],
        })
    save(ROOT / "corpus/manifest.json", {
        "format": "sqlglot-fuzz-public-corpus-v1", "split": "public/train",
        "selection": "First eight public/train documents sorted by SHA-256, 0<bytes<=16000",
        "source_manifest": "environment/workload/inputs/manifest.json",
        "source_manifest_sha256": sha(inputs / "manifest.json"),
        "source_visibility": "environment/public-workloads.lock.json: visibility=public",
        "oracle_values_read": False, "files": corpus,
        "provenance_note": "Original Mozilla public workload bytes; license/provenance remain "
                           "bound by the unchanged task public payload and notices.",
    })
    evidence_paths = [
        "README.md", "pyproject.toml", "setup.py", "MANIFEST.in", "sqlglot/__init__.py",
        "sqlglot/dialects/dialect.py", ".github/workflows/package-test.yml",
        ".github/workflows/package-publish.yml", "sqlglotc/setup.py",
    ]
    refs = [{
        "path": path, "sha256": sha(source / path),
        "url": f"https://github.com/tobymao/sqlglot/blob/{UPSTREAM}/{path}",
    } for path in evidence_paths]
    headers = [str(p.relative_to(source)) for p in sorted(source.rglob("*"))
               if p.is_file() and p.suffix.lower() in (".h", ".hpp", ".hh", ".hxx")]
    native = [str(p.relative_to(source)) for p in sorted(source.rglob("*"))
              if p.is_file() and p.suffix.lower() in (".c", ".cc", ".cpp", ".cxx")]
    save(ROOT / "header-native-scope.json", {
        "format": "sqlglot-header-native-scope-v1", "upstream_commit": UPSTREAM,
        "source_archive_sha256": "d553b6eddad6e7bdeb55b05a54e722e230b3a0cf2c3ce06cdd8558677d5dadd9",
        "scan": {"root": "entire authenticated upstream source archive",
                 "header_paths": headers, "native_source_paths": native},
        "build_feature": "Pinned task pure-Python SQLGlot, no sqlglotc/sqlglotrs optional extras",
        "package_evidence": {
            "pyproject.toml": "packages=['sqlglot','sqlglot.*']; package-data only py.typed",
            "setup.py": "C and Rust extras are optional, not installed by pinned task",
            "environment/workload/build.sh": "Copies only sqlglot Python package into runtime",
            "environment/source.lock.json": "Pinned pure-Python runtime; native extensions protected",
        },
        "support_refs": refs,
        "header_matrix_applicability": "No public C/C++ headers in authenticated pinned source; "
                                      "task builds only pure-Python package. Independently approved "
                                      "header-consumer N/A by Validation2 for this pinned feature only; "
                                      "race/lifecycle/full-surface qualification is not waived.",
        "sanitizer_applicability": "ASan/UBSan/MSan target changed native code; no editable native "
                                   "code in this task feature. Python interpreter itself unchanged.",
        "race_applicability": "Pure-Python candidates still require lifecycle/thread-creation inventory; "
                              "absence of C headers does not waive race evidence for creating workers.",
    })
    surfaces = []
    for symbol in SURFACES:
        surfaces.append({
            "id": symbol, "visibility": "public",
            "structural_signature": {"version": 1, "shape": "See pinned API docstrings/source",
                                     "authority_status": "pending-corrected-extraction"},
            "contract_id": "sqlglot-behavior-preservation-v1",
            "precondition_id": "public-sql-or-parser-produced-tree-v1",
            "generators": ["sqlglot-public-grammar-v1", "sqlglot-public-corpus-v1",
                           "sqlglot-public-corpus-comment-mutation-v1"],
            "option_family_ids": ["dialect-read-write", "generation-bools", "error-levels"],
            "sequence_applicability": ["invoke", "interleave-independent", "reinitialize", "invoke"],
            "fault_applicability": "SQL corruption/truncation through public errors; "
                                   "native allocator/I/O hooks not documented for these string APIs",
            "oracle_policy": POLICY,
            "oracle": {
                "baseline_terminations": ["return", "exception"], "baseline_exit_codes": [0],
                "required_error_fields": {"class": "nonempty_string", "message": "string"},
            },
            "reexport_status": "pending-corrected-extraction; documented symbol identity only",
        })
    save(ROOT / "surface.json", {
        "format": "sqlglot-fuzz-surface-map-v1", "task": TASK, "complete": False,
        "extractor": {"version": None, "report_sha256": None,
                      "status": "blocked-pending-corrected-transitive-public-surface-authority"},
        "upstream_commit": UPSTREAM, "feature": "pure-python",
        "contract_sources": ["../../instruction.md", *refs],
        "surfaces": surfaces,
        "unresolved": [
            "Full corrected transitive re-export inventory/denominator",
            "Every remaining documented builder, schema, optimizer, lineage, executor, diff and CLI API",
            "Complete supported dialect/custom subclass/option-family coverage",
            "Full structural signatures and re-export origin chains from corrected authority",
        ],
        "exclusions": [],
    })
    save(ROOT / "adapter.json", {
        "format": "optimbench-fuzz-adapter-v1", "task": TASK, "language": "python",
        "contract_sources": ["../../instruction.md",
                             f"https://github.com/tobymao/sqlglot/blob/{UPSTREAM}/README.md"],
        "surface_manifest": "surface.json",
        "hooks": {key: "adapter.py:" + key for key in
                  ("generate", "precondition", "shrink", "compare")},
        "seed_list": [1729, 2718, 31415],
        "budget": {"profile": "Goldmark, sqlfmt, Black, SQLFluff, SQLGlot, Terser",
                   "grammar_property_seconds": 900, "coverage_guided_seconds": 2700,
                   "cpu_state_seconds": 900, "sanitizer_seconds_per_applicable_build": 1200,
                   "per_case_seconds": 20, "large_case_seconds": 60,
                   "maximum_sql_bytes": 131072},
        "resources": {"peak_memory": {"mode": "report-only", "verdict_impact": False}},
    })
    print(json.dumps({"mapped_initial_surfaces": len(surfaces), "public_seed_files": len(corpus),
                      "headers": headers, "native_sources": native, "complete": False}))


if __name__ == "__main__":
    main()
