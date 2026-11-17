"""Inventory pinned documented APIs/options; unresolved extraction stays explicit."""
import argparse
import hashlib
import json
from pathlib import Path
import re

PIN = "317f449862b8d48756818cdcaf67c22c31ec852c"

def inventory(source):
    source = Path(source)
    readme = (source / "README.md").read_text()
    declarations = (source / "tools/terser.d.ts").read_text()
    files = ["README.md", "tools/terser.d.ts", "package.json", "main.js", "LICENSE"]
    evidence = [{"path": p, "sha256": hashlib.sha256((source / p).read_bytes()).hexdigest()}
                for p in files]
    groups = {}
    interfaces = {
        "MinifyOptions": "minify", "ParseOptions": "parse",
        "CompressOptions": "compress", "MangleOptions": "mangle",
        "ManglePropertiesOptions": "mangle.properties", "FormatOptions": "format",
        "SourceMapOptions": "sourceMap",
    }
    for interface, family in interfaces.items():
        body = re.search(r"export interface " + interface + r" \{(.*?)^\}", declarations, re.M | re.S)
        assert body, interface
        names = re.findall(r"^\s+([A-Za-z_]\w*)\?:", body.group(1), re.M)
        groups[family] = {"typed_options": sorted(set(names)), "source": f"tools/terser.d.ts:{interface}"}
    # README documents additional options/signatures not present in the d.ts.
    extras = {
        "parse": ["spidermonkey", "expression", "filename"],
        "compress": ["top_retain:function", "toplevel:funcs|vars", "unused:keep_assign",
                     "unsafe_methods:RegExp"],
        "mangle.properties": ["undeclared", "debug:string"],
        "format": ["spidermonkey"],
    }
    for family, options in extras.items():
        groups[family]["documented_additional_forms"] = options
    header_files = sorted(str(p.relative_to(source)) for p in source.rglob("*")
                          if p.is_file() and p.suffix.lower() in [".h", ".hh", ".hpp", ".hxx"])
    package = json.loads((source / "package.json").read_text())
    entries = []
    for name in ["minify", "minify_sync"]:
        for mode in ["import", "require"]:
            entries.append({
                "id": f"terser.package.{mode}.{name}.v1", "symbol_path": f"terser.{name}",
                "signature": {"parameters": ["files:string|string[]|Record<string,string>",
                                             "options?:MinifyOptions"],
                              "returns": "Promise<MinifyOutput>" if name == "minify" else "MinifyOutput"},
                "reexport": {"package_entry": ".", "condition": mode,
                             "origin": "lib/minify.js" if mode == "import" else "dist/bundle.min.js"},
                "contract_refs": ["README.md:API Reference", f"tools/terser.d.ts:{name}"],
                "visibility": "public", "contract_id": "exact-public-result-error-and-mutable-cache-v1",
                "option_family_ids": list(groups), "generator_ids": ["grammar-options-v1", "public-corpus-v1"],
                "generators": ["grammar-options-v1", "public-corpus-v1"],
                "precondition_id": "public-source-and-options-v1",
                "oracle": {"baseline_terminations": ["return", "exception"], "baseline_exit_codes": [0],
                           "required_error_fields": {"class": "nonempty_string", "message": "string"}},
                "sequence_applicability": ["fresh", "repeat", "interleaved", "name-cache-reuse", "error-recovery"],
                "fault_applicability": ["syntax-error", "invalid-source-map", "callback-error-pending-review"],
                "oracle_policy": "exact-public-result-error-and-mutable-cache-v1",
                "status": "mapped-documented-not-corrected-extractor-qualified",
            })
    for name in ["_default_options", "_run_cli"]:
        entries.append({
            "id": f"terser.package.{name}.v1", "symbol_path": f"terser.{name}",
            "reexport": {"package_entry": ".", "origin": "main.js"},
            "status": "unresolved-supported-contract-and-generator",
            "reason": "Exported by main.js but not declared in types; do not silently exclude",
        })
    entries.append({
        "id": "terser.bin.cli.v1", "symbol_path": "terser/bin/terser",
        "status": "generic-CLI-IO-probe-mapped; full-option-generator-and-error-attribution-review-pending",
        "contract_refs": ["package.json:bin and exports", "README.md:Command line options"],
        "visibility": "public", "generator_ids": ["cli-io-v1"],
        "generic_probe": "cli_probe.py",
        "exercised_IO": ["stdin-success-nonempty", "file-output-success", "dev-full",
                         "closed-reader-EPIPE", "closed-stdout", "readonly-output",
                         "missing-input", "syntax-error"],
        "oracle_policy": "retain complete raw stdout/stderr/exits/files; no automatic stack normalization",
    })
    return {
        "format": "optimbench-fuzz-surface-v1", "task": "terser-minify", "upstream_commit": PIN,
        "evidence": evidence, "option_families": groups, "entries": entries,
        "extractor": {"version": None, "report_sha256": None,
                      "status": "unresolved-corrected-extractor-dependency"},
        "public_headers": {
            "status": "not-applicable-reviewed-for-pinned-JS-feature-only",
            "files": header_files, "package_files_allowlist": package["files"],
            "package_exports": package["exports"], "declaration_entry": package["types"],
            "supported_C_Cpp_dialects": [], "legal_include_contexts": [],
            "evidence": "Complete pinned tar archive scan plus package export/files and TypeScript declaration inventory",
            "matrix_applicability": "not-applicable-to-this-pinned-JS-feature",
            "independent_review": {
                "reviewer": "Validation2",
                "conversation": "c04e9c07-100d-43af-8f55-978b00001e1d",
                "scope": "Pinned 317f449 archive has zero header/native files; package exports JS/TS, no gypfile",
                "not_waived": ["full public surface authority", "native-addon applicability for other feature sets"],
            },
        },
        "internal_surface_review": {
            "parse/TreeWalker": "Existing differential imports internal paths. No main/package re-export found; retained original cases, public status unresolved, not blanket excluded.",
            "AST_inputs": "Only documented parser-produced SpiderMonkey forms; malformed private ASTs not generated",
        },
        "qualification": "incomplete; full CLI/export/corrected-extractor and deep campaign evidence unresolved",
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(json.dumps(inventory(args.source), indent=2) + "\n")
