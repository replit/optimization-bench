import hashlib, io, json, subprocess, sys
if sys.argv[1:] == ["--one"]:
    sys.path.insert(0, "/app/baseline-build/site"); sys.path.insert(0, "/task/environment/workload")
    import numpy as np
    from numpy_helpers import prepare, arguments, buffer_hash
    plan = json.load(open("/dl/out/plan.json")); res = {}
    assert np.__file__.startswith("/app/baseline-build/site")
    for split in plan.values():
        for name, v in split.items():
            parent = np.load(f"/dl/out/{name}.npy", allow_pickle=False)
            for case in v["cases"]:
                a = prepare(parent, case["prepare"])
                assert {"shape": list(a.shape), "dtype": str(a.dtype), "strides": list(a.strides)} == case["input"], case["id"]
                before = buffer_hash(a)
                r = np.asarray(np.quantile(a, **arguments(case)))
                assert [list(r.shape), str(r.dtype)] == [case["output"]["shape"], case["output"]["dtype"]], case["id"]
                s = io.BytesIO(); np.save(s, r, allow_pickle=False); data = s.getvalue()
                assert buffer_hash(a) == before
                b = b"result.npy\0" + str(len(data)).encode() + b"\0" + data
                res[case["id"]] = {"len": len(data), "sha": hashlib.sha256(b).hexdigest(), "bundle_hex": b.hex()}
    print(json.dumps(res))
else:
    runs = [json.loads(subprocess.run([sys.executable, __file__, "--one"], check=True, capture_output=True, text=True).stdout) for _ in range(2)]
    assert runs[0] == runs[1], "outputs differ between fresh runs"
    json.dump(runs[0], open("/dl/out/outputs.json", "w"))
    print("cases", len(runs[0]), "identical across 2 fresh runs")
