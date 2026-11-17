#!/usr/bin/env python3
"""Bind partial profiles to corrected extraction; keep every gap explicit."""
import gzip
import json
from pathlib import Path

import adapter

HERE = Path(__file__).resolve().parent
scope = json.loads((HERE / "public-scope.json").read_text())
provenance = json.loads((HERE / "receipts/corrected-surface-provenance.json").read_text())
raw = json.loads(gzip.open(HERE / "receipts/corrected-surface.json.gz").read())
entries = []
for op in sorted(adapter.OPERATIONS):
    symbol = adapter.SURFACE[op]
    if op.startswith("api_"):
        name = op.removeprefix("api_")
        signature = scope["runtime_inventory"]["top_level_structural_symbols"][name]
        origin = {"module": signature["origin_module"], "qualname": signature["qualname"]}
        chain = ["sqlfluff", "sqlfluff.api", signature["origin_module"]]
        documentation = "https://docs.sqlfluff.com/en/stable/reference/api.html"
        status = "corrected-extraction-bound; bounded initial generator"
    elif op.startswith("cli_"):
        name = "lint" if op == "cli_stdin" else op.removeprefix("cli_")
        signature = {"command": name, "parameters": scope["runtime_inventory"]["cli_commands"][name]}
        origin = {"module": "sqlfluff.cli.commands", "command": name}
        chain = []
        documentation = "https://docs.sqlfluff.com/en/stable/reference/cli.html"
        status = "documented CLI supplement; options only partially generated"
    else:
        signature = None
        origin = {"documented_symbol": symbol}
        chain = []
        documentation = (
            "https://github.com/sqlfluff/sqlfluff/tree/"
            "0348222a3bcdb0226563f7349c5a39beb0641955/docs/source/reference/internals"
        )
        status = "documented state-control supplement; absent from raw corrected extraction"
    entries.append({
        "surface_id": symbol,
        "operation_profile": op,
        "structural_signature": signature,
        "definition_origin": origin,
        "reexport_chain": chain,
        "documentation": documentation,
        "extractor_record_present": symbol in raw,
        "mapping_status": status,
        "feature": "pinned pure-Python SQLFluff runtime",
        "option_family_ids": ["dialect", "process-count"] if op.startswith("cli_") else ["dialect"],
        "generated_dialects_initial_subset": list(adapter.DIALECTS),
        "precondition_id": "sqlfluff-constructor-valid-v1",
        "generator": "adapter.py:generate",
        "sequence": adapter.SEQUENCES.get(op, ["init", "call", "finish"]),
        "fault_applicability": "not yet mapped; initial profile rejects unimplemented fault schedules",
        "oracle_policy": adapter.contract_for(op),
        "coverage_qualification": "undertested",
    })
direct = sum(e["extractor_record_present"] for e in entries)
manifest = {
    "format": "optimbench-fuzz-surface-v1",
    "task": adapter.TASK,
    "status": "undertested",
    "extractor_revision": provenance["extractor_revision"],
    "extractor_report_sha256": provenance["report_sha256"],
    "source_commit": scope["upstream_commit"],
    "source_manifest_digest": scope["source_manifest_digest"],
    "build_image": scope["task_image"],
    "features": {"optional_sqlfluffrs_installed": scope["runtime_inventory"]["optional_sqlfluffrs_installed"]},
    "corrected_raw_records": len(raw),
    "initial_operation_profiles": len(entries),
    "directly_bound_raw_records": direct,
    "unresolved_raw_records": len(raw) - direct,
    "unresolved_record_artifact": "receipts/corrected-surface.json.gz",
    "all_raw_records_mapped": False,
    "public_dialect_inventory_count": len(scope["runtime_inventory"]["dialects"]),
    "dialects_initially_generated": list(adapter.DIALECTS),
    "CLI_inventory_commands": sorted(scope["runtime_inventory"]["cli_commands"]),
    "CLI_initial_command_subset": ["lint", "fix", "parse"],
    "native_header_scope": "public-scope.json",
    "approved_exclusions": [],
    "entries": entries,
    "limitations": [
        "No silent exclusion of unresolved raw exports, aliases, dialects or CLI option families.",
        "Documented internal extension utilities missing from corrected extraction require integrator-owned metadata/extractor review.",
        "Twelve bounded profiles do not establish full public-surface, fault, lifecycle or changed-code coverage.",
        "Frozen MatchResult is exercised only through valid immutable operations; identity and nested-dict mutation do not define a regression."
    ],
}
(HERE / "surface.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
print(json.dumps({k: manifest[k] for k in
                  ("corrected_raw_records", "initial_operation_profiles", "directly_bound_raw_records",
                   "unresolved_raw_records", "all_raw_records_mapped")}))
