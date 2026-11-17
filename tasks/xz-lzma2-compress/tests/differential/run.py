"""Private compiler/launcher; no network, source edits, or installed agent files."""
import json
import lzma
from pathlib import Path
import subprocess
import sys

case = json.loads(Path(sys.argv[1]).read_text())
if "variants" in case:
    results = []
    for i, row in enumerate(case["variants"]):
        path = Path(f"/scratch/variant-{i}.json")
        path.write_text(json.dumps(row))
        p = subprocess.run([sys.executable, __file__, str(path)], capture_output=True)
        if p.returncode:
            sys.stderr.buffer.write(p.stderr)
            raise SystemExit(p.returncode)
        results.append(json.loads(p.stdout))
    print(json.dumps(results))
    raise SystemExit(0)
root = Path("/app/workload/inputs")
manifest = json.loads((root / "manifest.json").read_text())
docs = [d for groups in manifest["splits"].values() for g in groups for d in g["docs"]]
docs.sort(key=lambda d: d["sha256"])


def fragment(index):
    raw = (root / "files" / docs[index % len(docs)]["sha256"]).read_bytes()
    start = case["offset"]
    return raw[start:start + case["size"]]


raw = fragment(case["source"])
other = fragment(case["other"])
if case["mutation"] == 1:
    raw = raw[:len(raw) // 2] + other[len(other) // 2:]
elif case["mutation"] == 2:
    raw = bytes(b ^ 0x80 if i % 17 == 0 else b for i, b in enumerate(raw))
elif case["mutation"] == 3:
    raw = raw[::-1] + b"\0\xff\r\n"
if case["mode"] == "decode":
    raw = lzma.compress(raw, check=lzma.CHECK_CRC64)
    damage = case["damage"]
    if damage == "truncate":
        raw = raw[:-1]
    elif damage == "flip":
        raw = raw[:12] + bytes([raw[12] ^ 0x80]) + raw[13:]
    elif damage == "garbage":
        raw = b"\xffnot-xz\0" + raw
    elif damage == "concat":
        raw += lzma.compress(other, check=lzma.CHECK_CRC32)
    elif damage == "empty":
        raw = b""
Path("/scratch/input").write_bytes(raw)
args = [case["mode"], str(case["preset"] | (1 << 31 if case["extreme"] else 0)),
        str(case["check"]), str(case["filter"]), str(case["threads"]),
        str(case["chunk"]), str(case["out_chunk"]), str(int(case["invalid"]))]


def compile_at(build, binary):
    p = subprocess.run(["cc", "-O2", "-I" + build + "/project/src/liblzma/api",
                        "/verifier-differential/probe.c", build + "/cmake/liblzma.a",
                        "-lpthread", "-o", binary], capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace"))


compile_at("/app/build", "/scratch/probe")
# Fixed capacities are derived ONLY from the pristine image's library, never
# from candidate output. The generous pass reports the exact encoded size.
caps = "0"
if case["mode"] == "buffer":
    compile_at("/app/baseline-build", "/scratch/oracle")
    p = subprocess.run(["/scratch/oracle", *args, "-1"], capture_output=True, check=True)
    exacts = [int(n) for n in p.stdout.splitlines()[0].split()]
    capacities = {0, 1}
    for exact in exacts:
        capacities.update({max(0, exact - 1), exact, exact + 1})
    caps = ",".join(str(n) for n in sorted(capacities))
p = subprocess.run(["/scratch/probe", *args, caps], capture_output=True)
print(json.dumps({"code": p.returncode, "stdout_hex": p.stdout.hex(),
                  "stderr_hex": p.stderr.hex()}))
