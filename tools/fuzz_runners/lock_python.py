"""Maintenance only: resolve wheel hashes for exact versions, never during runs.

PyPI artifacts are then authenticated by pip --require-hashes. Hashes cover
CPython312/313/314 Linux x86_64 plus universal wheels without changing interpreter
ABI. Atheris on other ABIs is explicitly unsupported until a new lock is reviewed.
"""
import json
from pathlib import Path
import urllib.request

VERSIONS = {"atheris": "3.1.0", "coverage": "7.17.0", "hypothesis": "6.170.12",
            "sortedcontainers": "2.4.0"}


def main():
    lines = []
    for name, version in VERSIONS.items():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as response:
            metadata = json.load(response)
        selected = []
        for item in metadata["urls"]:
            filename = item["filename"]
            if not filename.endswith(".whl"):
                continue
            universal = filename.endswith("-none-any.whl")
            compatible = any(("-" + abi + "-") in filename for abi in ("cp312", "cp313", "cp314"))
            linux_x86 = "manylinux" in filename and "x86_64" in filename
            if universal or (compatible and linux_x86):
                selected.append(item["digests"]["sha256"])
        if not selected:
            raise ValueError("no supported locked wheels for " + name)
        lines.append(name + "==" + version + " \\\n" + " \\\n".join("    --hash=sha256:" + digest for digest in sorted(set(selected))))
    Path(__file__).with_name("python.lock").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
