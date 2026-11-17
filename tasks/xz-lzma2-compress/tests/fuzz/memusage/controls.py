"""Pinned real source mutants, never patches to the probe or a candidate.

The hidden-allocation mutant enlarges an existing owned history allocation.
The buffer is still freed normally and encoding stays valid. No memusage
calculation is changed, making the semantic inconsistency observable.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

PINNED_COMMIT = "299eb5c3f597f107fd6019bf156885ac8bfaa5ea"
PINNED_ARCHIVE_SHA256 = "c1f497513e85cbc2f3760990a6ed9cbebe6386f273481b87251d0d6eb481c51a"
PINNED_SOURCE_IDENTITY = "65438ce3eaec181df118902600a118c940931488d2f679dae5fc900f67e2ca4b"
SPECS = {
    "underreport-owned-workspace": (
        "src/liblzma/common/filter_encoder.c",
        "return lzma_raw_coder_memusage(&coder_find, filters);",
        "return lzma_raw_coder_memusage(&coder_find, filters) / 2;",
    ),
    "unreported-owned-allocation": (
        "src/liblzma/lz/lz_encoder.c",
        "mf->buffer = lzma_alloc(mf->size + LZMA_MEMCMPLEN_EXTRA,\n\t\t\t\tallocator);",
        "mf->buffer = lzma_alloc(mf->size + LZMA_MEMCMPLEN_EXTRA + 2097152,\n\t\t\t\tallocator);",
    ),
}


def recipes(source):
    root = Path(source)
    result = []
    for name, (path, before, after) in SPECS.items():
        data = (root / path).read_bytes()
        if data.decode().count(before) != 1:
            raise ValueError(f"source anchor not unique for {name}")
        result.append({
            "id": f"xz-lzma2-compress:{name}", "language": "c",
            "operator_class": "resource-reports", "mandatory": True,
            "upstream_commit": PINNED_COMMIT, "archive_sha256": PINNED_ARCHIVE_SHA256,
            "non_equivalence_evidence": (
                "Public estimate divided by two while owned allocations are unchanged."
                if name == "underreport-owned-workspace" else
                "Owned history allocation enlarged by 2097152 bytes while estimate is unchanged."
            ),
            "edits": [{"path": path, "original_sha256": hashlib.sha256(data).hexdigest(),
                       "before": before, "after": after}],
        })
    return result
