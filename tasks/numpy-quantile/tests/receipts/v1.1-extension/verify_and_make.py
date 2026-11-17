import sys, json, hashlib
sys.path.insert(0, "/dl")
import numpy as np
from taxi import make, npy_bytes
out = {}
r = make("/dl/yellow_tripdata_2023-01.parquet", 2023, 1)
a, rows = npy_bytes(r["array"]), npy_bytes(r["rows"])
print("verify taxi-2023-01", hashlib.sha256(a).hexdigest(), hashlib.sha256(rows).hexdigest(), r["pickup_range"])
for ym in sys.argv[1:]:
    y, m = map(int, ym.split("-"))
    r = make(f"/dl/yellow_tripdata_{ym}.parquet", y, m)
    a, rows = npy_bytes(r["array"]), npy_bytes(r["rows"])
    open(f"/dl/out/taxi-{ym}.npy", "wb").write(a)
    open(f"/dl/out/taxi-{ym}-source-rows.npy", "wb").write(rows)
    out[ym] = {"sha256": hashlib.sha256(a).hexdigest(), "bytes": len(a),
               "rows_sha256": hashlib.sha256(rows).hexdigest(), "rows_bytes": len(rows),
               "source_rows": r["source_rows"], "eligible_rows": r["eligible_rows"],
               "excluded_rows": r["excluded_rows"], "pickup_range": r["pickup_range"]}
    print(ym, json.dumps(out[ym]))
json.dump(out, open("/dl/out/taxi-meta.json", "w"), indent=1)
