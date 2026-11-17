#!/usr/bin/env python3
"""bzip2 RC library facts inside the shared isolated executor. No candidate-label branches.

Compiles tests/fuzz/rc_probe.c against each arm's admitted ``source/bzlib.h`` and
``source/libbz2.a`` (built by the frozen task recipe from the arm's sources), then records:

* stream-facts.json: public streaming/buffer/stdio cases, two fresh-process
  observations per arm, including legal short/partial chunking, truncated,
  corrupt and bad-magic streams, parameter validation, output-buffer limits and
  a real OS failure (ENOSPC on /dev/full) with errno and operation retained;
  directory/write-only readers are recorded as pristine reports them (BZ_UNEXPECTED_EOF).
* repeat-facts.json: the same cases twice in one persistent process per arm
  (library in-process, fresh bz_stream per call), compared with the fresh oracle,
  plus per-call fd/thread/child lifecycle snapshots with concrete thread identities.

Every observation carries the actual ``diagnostics.execution_affinity``.
Compressed fixtures come from Python's independent bz2 module, never the candidate.
"""
import argparse
import bz2
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
TASK = "bzip2-codec"
SURFACE = {
    "id": "bzip2.c.bzlib.stream+buffer+stdio",
    "visibility": "public",
    "contract_id": "bzip2-bzlib-public-contract-v1",
    "precondition_id": "bzip2-rc-bounded-public-c-v1",
    "oracle": {"required_error_fields": {
        "code": "integer", "name": "nonempty_string", "stage": "nonempty_string",
        "errno": ["integer", "null"], "operation": ["string", "null"]},
        "baseline_terminations": ["return"], "baseline_exit_codes": [0]},
    "normalizations": [{
        "field": "outcome.stage", "operations": ["stdio_write"], "from": ["write", "write_close"],
        "to": "write_sequence", "raw_retained": "diagnostics.raw_stage",
        "scope": "only BZ_IO_ERROR/BZ_* reported by the BZ2_bzWrite...BZ2_bzWriteClose sequence on one BZFILE; "
                 "code, name, errno and operation stay exact",
        "authority": [
            {"path": "source/manual.html (bzip2-1.0.8 tarball, section 3.4.6)",
             "url": "https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
             "sha256": "34f5eaeb37488b51662316b8d9f54228c96f72b54aec3bfc17cd731e3ce9bbd2",
             "quote": "Absorbs len bytes from the buffer buf, eventually to be compressed and written to the file."},
            {"path": "source/manual.html (bzip2-1.0.8 tarball, section 3.4.7)",
             "url": "https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
             "sha256": "34f5eaeb37488b51662316b8d9f54228c96f72b54aec3bfc17cd731e3ce9bbd2",
             "quote": "Compresses and flushes to the compressed file all data so far supplied by BZ2_bzWrite."}],
        "bounds": "accepted BZ2_bzWrite calls before the first error are recorded in "
                  "diagnostics.write_calls_accepted_before_error (buffering latitude, not compared); "
                  "no call is issued on a BZFILE after its first error except the documented "
                  "BZ2_bzWriteClose(abandon=1) cleanup"}],
}
ALIAS_SURFACE = {**{k: v for k, v in SURFACE.items() if k != "normalizations"},
                 "id": "bzip2.c.bzlib.library-owned-pointers",
                 "review": {
                     "BZ2_bzReadGetUnused": "returns void** into the BZFILE's library-owned buffer, valid until "
                                            "BZ2_bzReadClose; checked: exposed bytes equal the file bytes after the "
                                            "consumed position, copied before close, and chained into the next "
                                            "BZ2_bzReadOpen (documented multi-stream pattern)",
                     "BZ2_bzerror": "returns a library-owned constant message for the handle's error state",
                     "BZ2_bzlibVersion": "returns a constant version string (covered by the header consumer)",
                     "not_mutable_getters": "bz_stream and all buffers are caller-owned; BZFILE is an opaque handle "
                                            "with no other accessor"},
                 "authority": [{"path": "source/bzlib.h",
                                "url": "https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
                                "sha256": "6ac62e811669598ee30c9e1c379b9e627f6ff17a5a3dc1e0b4fa8b8ea75e580d",
                                "quote": "BZ_EXTERN void BZ_API(BZ2_bzReadGetUnused) ( \n      int*    bzerror, \n      BZFILE* b, \n      void**  unused,  \n      int*    nUnused \n   );"}]}
LIMITATIONS = [
    "BZ2_bzlibVersion/bzopen/bzdopen zlib-compatibility wrappers not exercised",
    "custom bzalloc/bzfree allocators not exercised",
    "BZ_MEM_ERROR (allocation failure) not injected",
    "decompressor partial output before a data error is compared only as a prefix of the original",
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def xorshift(n, seed):
    out, x = bytearray(), seed
    while len(out) < n:
        x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 7
        x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
        out += x.to_bytes(8, "little")
    return bytes(out[:n])


def fixtures(public, root):
    """Name -> bytes. Natural public files plus edge shapes; all written under root."""
    data = {}
    natural = sorted((public / "data/dev/inputs").glob("*.raw"), key=lambda p: p.stat().st_size)
    if len(natural) < 2:
        raise ValueError("Public natural inputs unavailable")
    data["public-small"] = natural[0].read_bytes()
    data["public-medium"] = next(p for p in natural if p.stat().st_size >= 65536).read_bytes()[:262144]
    data["empty"] = b""
    data["one-byte"] = b"\x00"
    data["runs"] = b"a" * 200000 + b"b" * 3 + b"a" * 5000
    data["all-bytes"] = bytes(range(256)) * 64
    data["incompressible"] = xorshift(50000, 1729)
    good9 = bz2.compress(data["public-medium"], 9)
    good1 = bz2.compress(data["public-small"], 1)
    data["stream-good9"] = good9
    data["stream-good1"] = good1
    data["stream-truncated-tail"] = good9[:-10]
    data["stream-truncated-half"] = good9[: len(good9) // 2]
    flipped = bytearray(good9)
    flipped[len(flipped) // 2] ^= 0x10
    data["stream-bitflip"] = bytes(flipped)
    data["stream-bad-magic"] = b"X" + good9[1:]
    data["stream-empty"] = bz2.compress(b"", 9)
    data["multi-two"] = good1 + good9
    data["multi-three-small"] = bz2.compress(b"alpha", 1) + bz2.compress(b"", 9) + bz2.compress(b"omega" * 999, 2)
    data["multi-trailing-garbage"] = good1 + b"NOT-BZIP2-TRAILER" * 8
    root.mkdir(parents=True, exist_ok=True)
    for name, body in data.items():
        (root / name).write_bytes(body)
    return data, {"stream-good9": "public-medium", "stream-good1": "public-small",
                  "stream-truncated-tail": "public-medium", "stream-truncated-half": "public-medium",
                  "stream-bitflip": "public-medium", "stream-bad-magic": "public-medium",
                  "stream-empty": "empty"}, {
                      "multi-two": data["public-small"] + data["public-medium"],
                      "multi-three-small": b"alpha" + b"omega" * 999,
                      "multi-trailing-garbage": data["public-small"]}


def alias_cases(data, scratch):
    rows = []
    for operation, source, options, line in (
            ("read_get_unused_chain", "multi-two", {"small": 0}, f"unused {scratch / 'multi-two'} 0 0 - 0"),
            ("read_get_unused_chain", "multi-two", {"small": 1}, f"unused {scratch / 'multi-two'} 1 0 - 0"),
            ("read_get_unused_chain", "multi-three-small", {"small": 0}, f"unused {scratch / 'multi-three-small'} 0 0 - 0"),
            ("read_get_unused_chain", "multi-trailing-garbage", {"small": 0},
             f"unused {scratch / 'multi-trailing-garbage'} 0 0 - 0"),
            ("bzerror_string", "stream-good9", {}, f"errstr {scratch / 'stream-good9'} 0 0 - 0"),
            ("bzerror_string", "stream-truncated-half", {}, f"errstr {scratch / 'stream-truncated-half'} 0 0 - 0"),
            ("bzerror_string", "stream-bitflip", {}, f"errstr {scratch / 'stream-bitflip'} 0 0 - 0")):
        case = {"task": TASK, "surface_id": ALIAS_SURFACE["id"], "contract_id": SURFACE["contract_id"],
                "precondition_id": SURFACE["precondition_id"], "operation": operation,
                "input": {"fixture": source, "bytes": len(data[source]), "sha256": sha(data[source])},
                "options": options}
        case["case_sha256"] = digest(case)
        rows.append((case, line))
    return rows


def make_cases(data, originals, scratch):
    rows = []

    def add(operation, source, options, line):
        case = {"task": TASK, "surface_id": SURFACE["id"], "contract_id": SURFACE["contract_id"],
                "precondition_id": SURFACE["precondition_id"], "operation": operation,
                "input": None if source is None else {
                    "fixture": source, "bytes": len(data[source]), "sha256": sha(data[source])},
                "options": options}
        case["case_sha256"] = digest(case)
        rows.append((case, line))

    for source in ("public-small", "public-medium", "empty", "one-byte", "runs", "all-bytes", "incompressible"):
        for bs, wf, ichunk, ochunk in ((9, 30, 65536, 65536), (1, 0, 1, 7), (5, 250, 13, 1)):
            if source == "public-medium" and ichunk == 1:
                ichunk = 97  # Bounded call count; still a legal short-input stream.
            add("stream", source, {"blocksize": bs, "workfactor": wf, "in_chunk": ichunk, "out_chunk": ochunk},
                f"stream {scratch / source} {bs} {wf} {ichunk} {ochunk}")
    for source in ("stream-good9", "stream-good1", "stream-truncated-tail", "stream-truncated-half",
                   "stream-bitflip", "stream-bad-magic", "stream-empty"):
        for small, ichunk, ochunk in ((0, 65536, 65536), (1, 3, 5)):
            add("stream_decompress", source, {"small": small, "in_chunk": ichunk, "out_chunk": ochunk},
                f"sdecomp {scratch / source} {small} {ichunk} {ochunk} 0")
    plain = data["public-small"]
    exact = len(bz2.compress(plain, 9))  # Sizing from the independent oracle, never the candidate.
    for capacity in (exact + 64, exact, max(1, exact - 1), 1):
        add("buffer_compress", "public-small", {"blocksize": 9, "workfactor": 0, "dest_capacity": capacity},
            f"bufcomp {scratch / 'public-small'} 9 0 {capacity} 0")
    for source in ("stream-good1", "stream-truncated-tail", "stream-bitflip", "stream-bad-magic"):
        original = len(data[originals[source]])
        for capacity in (original + 64, max(1, original - 1)):
            add("buffer_decompress", source, {"small": 0, "dest_capacity": capacity},
                f"bufdecomp {scratch / source} 0 {capacity} 0 0")
    for bs, verbosity, wf in ((0, 0, 30), (10, 0, 30), (9, 0, 251), (9, 5, 0), (1, 0, 0), (9, 0, -1)):
        add("compress_init", None, {"blocksize": bs, "verbosity": verbosity, "workfactor": wf},
            f"param - {bs} {verbosity} {wf} 0")
    add("stdio_write", "public-medium", {"blocksize": 9, "workfactor": 30, "target": "dev_full"},
        f"fwrite {scratch / 'public-medium'} 9 30 /dev/full 0")
    add("stdio_write", "public-small", {"blocksize": 1, "workfactor": 0, "target": "regular_file"},
        f"fwrite {scratch / 'public-small'} 1 0 {{OUT}}/written.bz2 0")
    add("stdio_read", "stream-good9", {"small": 0, "mode": "read"}, f"fread {scratch / 'stream-good9'} 0 0 r 0")
    add("stdio_read", "stream-truncated-half", {"small": 1, "mode": "read"},
        f"fread {scratch / 'stream-truncated-half'} 1 0 r 0")
    add("stdio_read", "stream-bad-magic", {"small": 0, "mode": "read"},
        f"fread {scratch / 'stream-bad-magic'} 0 0 r 0")
    add("stdio_read", None, {"small": 0, "mode": "directory"}, f"fread {scratch} 0 0 r 0")
    add("stdio_read", "stream-good9", {"small": 0, "mode": "write_only_handle"},
        f"fread {scratch / 'stream-good9'} 0 0 w 0")
    return rows


def compile_probe(build, binary):
    command = ["cc", "-O1", "-std=c99", "-I" + str(build / "source"), str(HERE / "rc_probe.c"),
               str(build / "source/libbz2.a"), "-o", str(binary)]
    subprocess.run(command, check=True, capture_output=True)
    return {"command": command, "binary_sha256": sha(binary.read_bytes()),
            "libbz2_sha256": sha((build / "source/libbz2.a").read_bytes()),
            "header_sha256": sha((build / "source/bzlib.h").read_bytes())}


def run(binary, lines, work, name):
    work.mkdir(parents=True, exist_ok=True)
    cases = work / (name + ".cases")
    cases.write_text("".join(f"{index:04d} {line.replace('{OUT}', str(work))}\n"
                             for index, line in enumerate(lines)))
    result = subprocess.run([str(binary), str(cases), str(work)], capture_output=True, timeout=600)
    if result.returncode != 0 or result.stderr:
        raise RuntimeError(f"{name} observer exited {result.returncode}: {result.stderr[-2000:]!r}")
    rows = [json.loads(text) for text in result.stdout.decode().splitlines()]
    if len(rows) != len(lines):
        raise RuntimeError(name + " observer omitted calls")
    for row in rows:
        body = Path(row["output"]).read_bytes() if row["output"] else None
        row["output_body"] = body
        if row["output"]:
            os.unlink(row["output"])
    written = work / "written.bz2"
    if written.exists():
        for row in rows:
            row.setdefault("written_body", written.read_bytes())
        written.unlink()
    return rows


def observation(raw, case, build_sha256, mode, data, originals, chained=None):
    operation = case["operation"]
    ok = raw["code"] == 0
    effects = {"canary_ok": raw["canary_ok"]}
    body = raw.get("output_body")
    if operation == "stream":
        effects.update(roundtrip_exact=bool(raw["b"]), total_in=raw["a"] if ok else None,
                       compressed=None if body is None else {"bytes": len(body), "sha256": sha(body)})
    elif operation in ("stream_decompress", "stdio_read"):
        source = (case["input"] or {}).get("fixture")
        original = data.get(originals.get(source, ""), None)
        if ok:
            effects["decoded"] = {"bytes": len(body), "sha256": sha(body)}
        else:
            # Partial output before an error is legal implementation latitude; only its validity is contractual.
            effects["partial_is_prefix_of_original"] = (
                None if original is None or body is None else original.startswith(body))
    elif operation in ("buffer_compress", "buffer_decompress"):
        effects["output"] = None if body is None else {"bytes": len(body), "sha256": sha(body)}
    elif operation == "read_get_unused_chain":
        effects["unused_bytes_are_following_file_bytes"] = bool(raw["a"])
        expected = chained.get(case["input"]["fixture"]) if chained else None
        effects["decoded_prefix_of_all_streams"] = None if body is None or expected is None else expected.startswith(body)
        effects["decoded"] = None if body is None else {"bytes": len(body), "sha256": sha(body)}
    elif operation == "bzerror_string":
        effects["message"] = None if body is None else body.decode("ascii", "replace")
    elif operation == "stdio_write":
        written = raw.get("written_body") if case["options"]["target"] == "regular_file" else None
        effects.update(total_in=raw["a"] if ok else None, total_out=raw["b"] if ok else None,
                       file=None if written is None else {"bytes": len(written), "sha256": sha(written)})
    stage = raw["stage"]
    if operation == "stdio_write" and stage in ("write", "write_close"):
        stage = "write_sequence"  # Which of BZ2_bzWrite/BZ2_bzWriteClose reports an OS error is buffering latitude.
    outcome = ({"kind": "ok", "value": None} if ok else
               {"kind": "error", "code": raw["code"], "name": raw["name"], "stage": stage,
                "errno": raw["errno"] if raw["errno"] >= 0 else None,
                "operation": raw["operation"] or None})
    return {"format": "optimbench-fuzz-observation-v1",
            "identity": {"task": TASK, "surface_id": case["surface_id"], "case_sha256": case["case_sha256"],
                         "build_sha256": build_sha256},
            "termination": {"kind": "return", "exit_code": 0}, "outcome": outcome, "effects": effects,
            "diagnostics": {"execution_affinity": raw["execution_affinity"], "pid": raw["pid"],
                            "mode": mode, "raw_stage": raw["stage"],
                            "unused_bytes_exposed": raw["b"] if operation == "read_get_unused_chain" else None,
                            "write_calls_accepted_before_error": (
                                raw["a"] if operation == "stdio_write" and not ok else None),
                            "errno_name": os.strerror(raw["errno"]) if raw["errno"] >= 0 else None}}


def snapshot(raw, point):
    value = raw[point]
    return {"fds": value["fds"], "threads": value["threads"], "children": value["children"],
            "thread_identities": value["thread_identities"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "public", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    builds = {"baseline": args.baseline_sha, "candidate": args.candidate_sha}
    paths = {"baseline": args.baseline, "candidate": args.candidate}
    scratch = args.out / "fixtures"
    data, originals, chained = fixtures(args.public, scratch)
    rows = make_cases(data, originals, scratch)
    alias_rows = alias_cases(data, scratch)
    cases, lines = [case for case, _ in rows], [line for _, line in rows]
    binaries, compiled = {}, {}
    for arm in builds:
        binaries[arm] = args.out / (arm + "-rc-probe")
        compiled[arm] = compile_probe(paths[arm], binaries[arm])
    fresh, persistent, workers = {}, {}, {}
    for arm in builds:
        fresh[arm] = []
        for index, (case, line) in enumerate(rows):
            fresh[arm].append([observation(run(binaries[arm], [line], args.out / f"{arm}-fresh-{index}-{rep}",
                                               "fresh")[0], case, builds[arm], "fresh-process", data, originals)
                               for rep in range(2)])
        raw = run(binaries[arm], lines + lines, args.out / f"{arm}-persistent", "persistent")
        if len({row["pid"] for row in raw}) != 1:
            raise RuntimeError("Persistent worker changed process")
        persistent[arm] = (raw, [observation(r, c, builds[arm], "persistent", data, originals)
                                 for r, c in zip(raw, cases + cases)])
        workers[arm] = {"mode": "persistent", "target_library_in_process": True,
                        "worker_id": f"{arm}:{raw[0]['pid']}", "calls": len(raw), "build_sha256": builds[arm]}
    alias_obs = {arm: [[observation(run(binaries[arm], [line], args.out / f"{arm}-alias-{i}-{rep}", "fresh")[0],
                                    case, builds[arm], "fresh-process", data, originals, chained)
                        for rep in range(2)] for i, (case, line) in enumerate(alias_rows)] for arm in builds}
    (args.out / "alias-facts.json").write_text(json.dumps(
        {"format": "judge-generic-call-evidence-v1", "applicable": True, "compiled": compiled,
         "limitations": LIMITATIONS,
         "cases": [{"case": case, "surface": ALIAS_SURFACE, **{arm: alias_obs[arm][i] for arm in builds}}
                   for i, (case, _) in enumerate(alias_rows)]}, indent=1, sort_keys=True) + "\n")
    n = len(cases)
    stream_rows = [{"case": case, "surface": SURFACE, **{arm: fresh[arm][i] for arm in builds}}
                   for i, case in enumerate(cases)]
    repeat_rows = [{"case": case, "surface": SURFACE,
                    **{arm: [persistent[arm][1][i], persistent[arm][1][i + n]] for arm in builds},
                    **{"fresh_" + arm: fresh[arm][i] for arm in builds}}
                   for i, case in enumerate(cases)]
    lifecycle = [{"case_id": case["case_sha256"],
                  **{arm: {"available": True,
                           "before": snapshot(persistent[arm][0][i + n], "before"),
                           "after": snapshot(persistent[arm][0][i + n], "after")} for arm in builds}}
                 for i, case in enumerate(cases)]
    common = {"format": "judge-generic-call-evidence-v1", "applicable": True, "compiled": compiled,
              "limitations": LIMITATIONS}
    (args.out / "stream-facts.json").write_text(json.dumps(
        {**common, "cases": stream_rows}, indent=1, sort_keys=True) + "\n")
    (args.out / "repeat-facts.json").write_text(json.dumps(
        {**common, "cases": repeat_rows, "workers": workers, "lifecycle": lifecycle,
         "lifecycle_policy": {"kind": "native-os-threads"}}, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"cases": n, "fresh_calls_per_arm": 2 * n, "same_process_calls_per_arm": 2 * n}), flush=True)


if __name__ == "__main__":
    main()
