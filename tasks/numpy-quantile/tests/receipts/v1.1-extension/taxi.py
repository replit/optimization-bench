import sys, json, hashlib, io
import numpy as np, pyarrow.parquet as pq, pyarrow.compute as pc
COLS = ["trip_distance", "fare_amount", "tip_amount", "total_amount"]
N = 1_000_000

def build(path, year, month):
    t = pq.read_table(path, columns=["tpep_pickup_datetime"] + COLS)
    src = t.num_rows
    pick = t.column("tpep_pickup_datetime").to_numpy().astype("datetime64[us]")
    lo = np.datetime64(f"{year:04d}-{month:02d}-01T00:00:00", "us")
    hi = np.datetime64(f"{year + (month == 12):04d}-{month % 12 + 1:02d}-01T00:00:00", "us")
    data = np.column_stack([t.column(c).to_numpy(zero_copy_only=False).astype(np.float64) for c in COLS])
    ok = (pick >= lo) & (pick < hi) & np.isfinite(data).all(axis=1)
    elig = np.flatnonzero(ok)
    return src, elig, data, pick

def npy_bytes(a):
    b = io.BytesIO(); np.save(b, a, allow_pickle=False); return b.getvalue()

if __name__ == "__main__":
    path, year, month = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    src, elig, data, pick = build(path, year, month)
    E = len(elig)
    print("source", src, "eligible", E, "excluded", src - E)
    ref = np.load(sys.argv[4]) if len(sys.argv) > 4 else None
    variants = {
        "round_linspace": np.rint(np.linspace(0, E - 1, N)).astype(np.int64),
        "floor_linspace": np.floor(np.linspace(0, E - 1, N)).astype(np.int64),
        "intdiv": (np.arange(N, dtype=np.int64) * (E - 1)) // (N - 1),
        "intdiv_round": (np.arange(N, dtype=np.int64) * (E - 1) * 2 + (N - 1)) // (2 * (N - 1)),
    }
    if ref is not None:
        print("ref", ref.dtype, ref.shape, ref[:5], ref[-3:])
        for k, r in variants.items():
            rows = elig[r]
            print(k, bool(np.array_equal(rows.astype(ref.dtype), ref)))


def make(path, year, month):
    src, elig, data, pick = build(path, year, month)
    E = len(elig)
    rows = elig[(np.arange(N, dtype=np.int64) * (E - 1)) // (N - 1)]
    arr = np.ascontiguousarray(data[rows])
    p = pick[elig]
    return {"array": arr, "rows": rows.astype(np.int64), "source_rows": src, "eligible_rows": E,
            "excluded_rows": src - E,
            "pickup_range": [str(p.min()), str(p.max())]}
