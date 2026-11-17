"""Audit all saved image layers, including files hidden by later deletions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

EXTERNAL_COMMAND_CONTEXTS = {"main": "host"}


def audit_archive(archive, known_bodies, canary):
    if not known_bodies or not canary:
        raise ValueError("Original body fingerprints and a nonempty audit canary are required")
    hits = []
    files = total = 0

    def scan(stream, size, location):
        nonlocal files, total
        digest = hashlib.sha256()
        tail = b""
        found = False
        while block := stream.read(1024 * 1024):
            digest.update(block)
            found = found or canary in tail + block
            tail = (tail + block)[-(len(canary) - 1):] if len(canary) > 1 else b""
        files += 1
        total += size
        checksum = digest.hexdigest()
        if known_bodies.get(checksum) == size:
            hits.append({**location, "kind": "known-public-input",
                         "sha256": checksum, "bytes": size})
        if found:
            hits.append({**location, "kind": "audit-canary",
                         "sha256": checksum, "bytes": size})

    with tarfile.open(archive, "r:*") as saved:
        manifest = json.load(saved.extractfile("manifest.json"))
        layers = list(dict.fromkeys(layer for image in manifest for layer in image["Layers"]))
        if not layers:
            raise ValueError("Saved image manifest contains no layers")
        for name in layers:
            with saved.extractfile(name) as blob:
                with tarfile.open(fileobj=blob, mode="r|*") as layer:
                    for member in layer:
                        if member.isfile():
                            with layer.extractfile(member) as data:
                                scan(data, member.size, {"layer": name, "path": member.name})
        for member in saved:
            if member.isfile() and member.name not in layers:
                with saved.extractfile(member) as data:
                    scan(data, member.size, {"metadata": member.name})
    return {"passed": not hits, "layers": len(layers),
            "files_examined": files, "bytes_examined": total, "hits": hits}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--canary-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task = Path(__file__).resolve().parents[2]
    manifest = json.loads((task / "payload.json").read_text())
    bodies = [row for row in manifest["files"]
              if row.get("scope", "public") == "public"
              and row["destination"].startswith("environment/workload/inputs/files/")]
    known = {row["sha256"]: row["bytes"] for row in bodies}
    canary = args.canary_file.read_bytes()
    with tempfile.TemporaryDirectory(prefix="sqlfmt-image-layers-") as temporary:
        archive = Path(temporary) / "image.tar"
        subprocess.run(["docker", "image", "save", "--output", str(archive), args.image], check=True)
        report = audit_archive(archive, known, canary)
    report.update(schema="sqlfmt-image-layers-v1", image=args.image,
                  public_input_entries=len(bodies), unique_body_fingerprints=len(known),
                  canary_sha256=hashlib.sha256(canary).hexdigest(),
                  scope="Every saved layer regular file and image metadata: complete-file "
                        "original-body fingerprints and byte-canary detection, including deleted files")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("passed", "layers", "files_examined", "public_input_entries", "hits")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
