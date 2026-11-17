"""Verifier-owned identity ledger for the original bzip2 sample/API and FFmpeg selections."""
import json
from pathlib import Path
import shutil
import subprocess
import sys


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, **kwargs)


def api_guard_outcomes(output):
    """Keep semantic identities stable; allocator callback counts are observations."""
    rows = [line.split("\t", 2) for line in output.splitlines() if line.startswith("IDENTITY\t")]
    if (not rows or any(len(row) != 3 or row[1] != str(i) or not row[2]
                        for i, row in enumerate(rows, 1))):
        raise ValueError("Original API guard identity ledger is incomplete")
    try:
        summary = json.loads(output.splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise ValueError("Original API guard summary is missing") from error
    if summary != {"checks": len(rows), "status": "passed"}:
        raise ValueError("Original API guard summary disagrees with executed checks")
    callback = [row for row in rows if row[2] == "callback free ownership"]
    semantic = [row for row in rows if row[2] != "callback free ownership"]
    if len(semantic) != 50:
        raise ValueError("Original API guard semantic check count changed")
    if not callback:
        raise ValueError("Original API guard callback ownership was not exercised")
    outcomes = [{"identity": f"api/{i}/{name}", "status": "passed"}
                for i, (_, _, name) in enumerate(semantic, 1)]
    outcomes.append({"identity": "api/callback-free-ownership", "status": "passed"})
    return outcomes, {"total_checks": len(rows), "semantic_checks": len(semantic),
                      "callback_ownership_checks": len(callback), "raw_identity_rows": rows}


def main(kind):
    source = Path("/scratch/source")
    outcomes = []
    if kind == "bzip2":
        run(["make", "-j8", "bzip2"], cwd=source)
        for i in (1, 2, 3):
            for direction in ("encode", "decode"):
                original = source / f"sample{i}.{'ref' if direction == 'encode' else 'bz2'}"
                want = source / f"sample{i}.{'bz2' if direction == 'encode' else 'ref'}"
                argv = [str(source / "bzip2"),
                        f"-{i}" if direction == "encode" else "-ds" if i == 3 else "-d"]
                got = run(argv, input=original.read_bytes()).stdout
                if got != want.read_bytes():
                    raise ValueError("Upstream sample bytes differ")
                outcomes.append({"identity": f"sample{i}/{direction}", "status": "passed"})
        # Instrument only a verifier-private copy's diagnostic line. Original
        # test expressions, assertions, names and order remain byte-for-byte.
        guard = Path("/judge-task-tests/api_guard.c").read_text()
        marker = "    checks++;"
        if guard.count(marker) != 1:
            raise ValueError("Unexpected original API guard")
        guard = guard.replace(marker, '    checks++; printf("IDENTITY\\t%d\\t%s\\n", checks, name);')
        Path("/scratch/api_guard.c").write_text(guard)
        files = ["blocksort.c", "huffman.c", "crctable.c", "randtable.c", "compress.c",
                 "decompress.c", "bzlib.c"]
        run(["cc", "-O3", "-DNDEBUG", "-D_FILE_OFFSET_BITS=64", "-I", str(source),
             *(str(source / f) for f in files), "/scratch/api_guard.c", "-o", "/scratch/api_guard"])
        output = run(["/scratch/api_guard"], text=True).stdout
        api, api_observations = api_guard_outcomes(output)
        outcomes.extend(api)
    elif kind == "ffmpeg":
        audit = json.loads(run(["/app/build/frame-pipeline", "--audit"], text=True).stdout)
        if audit["hardware_device_types"]:
            raise ValueError("Hardware devices are forbidden")
        target = Path("/scratch/fate")
        shutil.copytree("/app/build/cmake", target)
        for name in ("fate-sws-pixdesc-query", "fate-sws-floatimg-cmp", "fate-sws-rgb2rgb-bounds"):
            run(["make", "-C", str(target), "-j8", name])
            outcomes.append({"identity": name, "status": "passed"})
    else:
        raise ValueError("No invented upstream selections")
    report = {"tests": outcomes}
    if kind == "bzip2":
        report["api_guard_observations"] = api_observations
    if kind == "ffmpeg":
        report.update(cpu_audit=audit, workload_threads=1, simd="software-auto; x86-64; no scalar override")
    Path("/scratch/upstream.json").write_text(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1])
