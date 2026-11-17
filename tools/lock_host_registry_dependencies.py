"""Maintainer-only hash lock generation for the supported host interpreter ABIs.

No dependency resolution or index lookup happens in preflight. Exact versions
are fixed here; published wheel SHA256 identities are fetched only when an
owner intentionally regenerates/reviews the lock.
"""
import json
from pathlib import Path
import urllib.request

VERSIONS = {
    "jsonschema": "4.19.2",
    "attrs": "25.4.0",
    "jsonschema-specifications": "2025.9.1",
    "referencing": "0.37.0",
    "rpds-py": "0.30.0",
    "typing-extensions": "4.15.0",
}
ROOT = Path(__file__).resolve().parents[1]


def main():
    lines = [
        "# Host registry dependency closure for CPython 3.12/3.14, Linux x86_64/aarch64.",
        "# typing-extensions is pinned on both minors for an identical declared closure.",
        "# Install with --require-hashes --only-binary=:all:; no optional format extras.",
    ]
    for package, version in VERSIONS.items():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=60) as response:
            metadata = json.load(response)
        hashes = set()
        for item in metadata["urls"]:
            filename = item["filename"]
            universal = filename.endswith("-none-any.whl")
            host = ("manylinux" in filename and any(arch in filename for arch in ("x86_64", "aarch64"))
                    and any(f"-{abi}-{abi}-" in filename for abi in ("cp312", "cp314")))
            if filename.endswith(".whl") and (universal or host):
                hashes.add(item["digests"]["sha256"])
        if not hashes:
            raise ValueError("No supported wheel artifacts for " + package)
        lines.append(package + "==" + version + " \\\n" + " \\\n".join(
            "    --hash=sha256:" + value for value in sorted(hashes)))
    (ROOT / "judge/requirements-host-registry.lock").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
