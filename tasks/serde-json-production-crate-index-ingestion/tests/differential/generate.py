"""Verifier-only schema, API, malformed/Unicode/numeric and state coverage."""
import json
from pathlib import Path
import importlib.util

_writer_spec = importlib.util.spec_from_file_location(
    "private_serde_writer_faults", Path(__file__).with_name("writer_faults.py"))
_writer = importlib.util.module_from_spec(_writer_spec)
_writer_spec.loader.exec_module(_writer)


def generate(rng, budget):
    fixed = [
        b"",b"{",b"null",b"true",b"false",b"[]",b"{}",b'""',
        b'"\\ud83d\\ude42"',b'"\\ud800"',b'"\xff"',b'{"x":1,"x":2}',
        b"18446744073709551616",b"-9223372036854775809",b"1e400",
        b"-0.0",b"1.234567890123456789e-250",b"0 1 true null []",
        b'{"a":[true,false,null],"escaped":"\\u0000\\n\\\\\\""}',
        b'{"path":{"~1":true,"/":false},"large":12345678901234567890}',
        ('{"unicode":"λ🙂é中","x":['+','.join(map(str,range(1024)))+']}').encode(),
    ]
    # Complete original registry records supplement generic JSON boundary probes.
    inputs = Path(__file__).parents[2]/"environment/workload/inputs"
    manifest = json.loads((inputs/"manifest.json").read_text())
    originals = []
    for group in manifest["splits"]["public"]:
        for doc in group["docs"]:
            path = inputs/"files"/doc["sha256"]
            if not path.is_file():
                raise ValueError("public corpus must be authenticated/staged before differential generation")
            originals.append(path.read_bytes().splitlines()[0]+b"\n")
    for i in range(budget):
        raw = fixed[i] if i<len(fixed) else json.dumps(
            {"k"+str(i):rng.choice([True,False,None,-1,0,2**63,"λ",["x",1]])},
            ensure_ascii=False,separators=(",",":")).encode()
        record = originals[i%len(originals)]
        if i%4==1:
            row=json.loads(record);row["features"]["boundary"]=["dep:unicode","λ"]
            record=json.dumps(row,ensure_ascii=False).encode()+b"\n"
        elif i%4==2:
            record=record[:-2]  # Observed error behavior, not silently discarded.
        yield {"hex":raw.hex(),"registry_hex":record.hex(),"repeat":1+i%3,
               "writer_fault":_writer.case_at(i, rng),
               "coverage":["parse","serialize","stream","errors","borrowed","pointer",
                           "mutation","interleaved-instances","repeat","numeric-boundaries","registry"]}


def shrink(case):
    if "writer_fault" in case:
        for fault in _writer.shrink(case["writer_fault"]):
            yield {**case, "writer_fault":fault}
    for raw in [b"true",b"false",b"null",b"{}",b'"x"',b"{",b""]:
        yield {**case,"hex":raw.hex(),"repeat":1}


def equal(left, right):
    def normalize(observation):
        import copy
        observation = copy.deepcopy(observation)
        writer = observation.get("result", {}).get("writer")
        if isinstance(writer, dict) and isinstance(writer.get("output"), dict):
            writer["output"] = _writer.semantic(writer["output"])
        return observation
    def exact(a,b):
        if type(a) is not type(b):
            return False
        if isinstance(a,dict):
            return a.keys()==b.keys() and all(exact(a[k],b[k]) for k in a)
        if isinstance(a,list):
            return len(a)==len(b) and all(exact(x,y) for x,y in zip(a,b))
        return a==b
    return exact(normalize(left),normalize(right))
