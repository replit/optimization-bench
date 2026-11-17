"""Verifier-only C API calls: capacities, canaries, chunking and independent sessions."""
import ctypes as C
import json
from pathlib import Path
import subprocess
import sys

case = json.loads(Path(sys.argv[1]).read_text())
root = Path("/app/build/source")
sources = ["blocksort.c", "huffman.c", "crctable.c", "randtable.c", "compress.c", "decompress.c", "bzlib.c"]
p = subprocess.run(["cc", "-O2", "-shared", "-fPIC", "-I" + str(root),
                    *(str(root / f) for f in sources), "-o", "/scratch/libprobe.so"],
                   capture_output=True, check=True)
lib = C.CDLL("/scratch/libprobe.so")


class Stream(C.Structure):
    _fields_ = [("next_in", C.c_void_p), ("avail_in", C.c_uint),
                ("total_in_lo32", C.c_uint), ("total_in_hi32", C.c_uint),
                ("next_out", C.c_void_p), ("avail_out", C.c_uint),
                ("total_out_lo32", C.c_uint), ("total_out_hi32", C.c_uint),
                ("state", C.c_void_p), ("bzalloc", C.c_void_p),
                ("bzfree", C.c_void_p), ("opaque", C.c_void_p)]


for name, args in {
    "bzBuffToBuffCompress": [C.c_void_p, C.POINTER(C.c_uint), C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int],
    "bzBuffToBuffDecompress": [C.c_void_p, C.POINTER(C.c_uint), C.c_void_p, C.c_uint, C.c_int, C.c_int],
    "bzCompressInit": [C.POINTER(Stream), C.c_int, C.c_int, C.c_int],
    "bzCompress": [C.POINTER(Stream), C.c_int], "bzCompressEnd": [C.POINTER(Stream)],
    "bzDecompressInit": [C.POINTER(Stream), C.c_int, C.c_int],
    "bzDecompress": [C.POINTER(Stream)], "bzDecompressEnd": [C.POINTER(Stream)],
}.items():
    f = getattr(lib, "BZ2_" + name)
    f.argtypes, f.restype = args, C.c_int

# Retained input names are authenticated by the image build; use natural raw files.
inputs = sorted(p for p in Path("/app/workload/inputs/data").rglob("*")
                if p.is_file() and not p.name.endswith(".bz2") and "guard" not in str(p))
if not inputs:
    raise ValueError("No authenticated natural inputs")
body = inputs[case["source"] % len(inputs)].read_bytes()
raw = body[case["offset"]:case["offset"] + case["size"]]
if case["mutation"] == 1:
    raw = raw[::-1]
elif case["mutation"] == 2:
    raw = bytes(x ^ 128 for x in raw)
elif case["mutation"] == 3:
    raw = (raw[:7] + b"\0\xff") * max(1, case["size"] // 9)


def buffer(data, capacity, decode=False, level=None):
    incoming = C.create_string_buffer(data)
    output = C.create_string_buffer(b"\xa5" * (capacity + 32), capacity + 32)
    length = C.c_uint(capacity)
    if decode:
        status = lib.BZ2_bzBuffToBuffDecompress(output, C.byref(length), incoming, len(data), 0, 0)
    else:
        status = lib.BZ2_bzBuffToBuffCompress(output, C.byref(length), incoming, len(data),
                                            case["level"] if level is None else level, 0, 30)
    # On errors length may stay at capacity; retain every actually exposed byte.
    return {"status": status, "length": length.value,
            "bytes": output.raw[:min(length.value, capacity)].hex(),
            "boundary": output.raw[capacity:] == b"\xa5" * 32}


def streaming(data, decode=False):
    s = Stream()
    status = (lib.BZ2_bzDecompressInit(C.byref(s), 0, 0) if decode else
              lib.BZ2_bzCompressInit(C.byref(s), case["level"], 0, 30))
    if status:
        return {"init": status}
    incoming = C.create_string_buffer(data)
    offset, output, transcript = 0, bytearray(), []
    try:
        for _ in range(200000):
            if s.avail_in == 0 and offset < len(data):
                n = min(case["chunk"], len(data) - offset)
                s.next_in, s.avail_in = C.addressof(incoming) + offset, n
                offset += n
            out = C.create_string_buffer(b"\xa5" * (case["out_chunk"] + 32), case["out_chunk"] + 32)
            s.next_out, s.avail_out = C.addressof(out), case["out_chunk"]
            status = (lib.BZ2_bzDecompress(C.byref(s)) if decode else
                      lib.BZ2_bzCompress(C.byref(s), 2 if offset == len(data) else 0))
            produced = case["out_chunk"] - s.avail_out
            output.extend(out.raw[:produced])
            if out.raw[case["out_chunk"]:] != b"\xa5" * 32:
                raise ValueError("Streaming destination overrun")
            transcript.append((status, produced, s.avail_in))
            if status == 4 or status < 0 or (decode and offset == len(data) and not s.avail_in and not produced):
                break
        else:
            raise ValueError("Streaming budget exhausted")
        return {"status": status, "bytes": output.hex(), "calls": transcript,
                "input_total": s.total_in_lo32, "output_total": s.total_out_lo32}
    finally:
        (lib.BZ2_bzDecompressEnd if decode else lib.BZ2_bzCompressEnd)(C.byref(s))


# Fixed independent oracle supplies capacity boundaries; candidate output never
# determines which boundary is tested. System bz2 uses pristine image Python.
import bz2
encoded = bz2.compress(raw, compresslevel=case["level"])
capacities = sorted({0, 1, max(0, len(encoded) - 1), *(len(encoded) + i for i in range(9))})
compression = [buffer(raw, cap) for cap in capacities]
decompression = [buffer(encoded, cap, True)
                 for cap in sorted({0, 1, max(0, len(raw) - 1), len(raw), len(raw) + 1})]
damaged = encoded
if case["damage"] == "truncate":
    damaged = encoded[:-3]
elif case["damage"] == "flip":
    damaged = encoded[:10] + bytes([encoded[10] ^ 0x80]) + encoded[11:]
elif case["damage"] == "garbage":
    damaged = b"not-bzip2"
print(json.dumps({"compression": compression, "decompression": decompression,
                  "stream": streaming(raw), "decode_stream": streaming(damaged, True),
                  "repeat": buffer(raw, len(encoded) + 8), "invalid_level": buffer(raw, 100, level=0)}))
