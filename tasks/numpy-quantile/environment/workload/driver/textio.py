"""Numeric, text, time, random and I/O units (protected suite driver).

Each unit has a loader (untimed: read the staged files and build every call's arguments) and a
call (timed: one complete operation per input item, results materialized). Item order is the
sorted file order. The loader and call bodies are the screened prototypes, unchanged.
"""
import hashlib
import io
import json
from pathlib import Path

import numpy as np


def _interp_load(d):
    out = []
    for f in sorted(Path(d).glob("*.npy")):
        img = np.load(f)
        lo = int(img.min()); cnt = np.bincount((img.ravel().astype(np.int64) - lo))
        centers = np.arange(lo, lo + cnt.size); cdf = np.cumsum(cnt) / float(cnt.sum())
        out.append((f.stem, (img, centers, cdf)))
    return out


def _interp_run(p):
    img, xp, fp = p
    return np.interp(img.flat, xp, fp).reshape(img.shape)


HOLIDAYS = Path(__file__).with_name("busday_holidays.json")  # US federal and NYSE closures


def _busday_load(d):
    d = Path(d); hol = {k: np.array(v, dtype="datetime64[D]") for k, v in json.loads(HOLIDAYS.read_text()).items()}
    cals = [np.busdaycalendar(weekmask="1111100", holidays=hol["federal"]),
            np.busdaycalendar(weekmask="1111100", holidays=hol["nyse"]),
            np.busdaycalendar(weekmask="1111110", holidays=hol["federal"])]
    out = []
    for f in sorted(d.glob("dates-*.npz")):
        z = np.load(f)
        out.append((f.stem, (z["a"], z["b"], z["c"], cals)))
    return out


def _busday_run(p):
    a, b, c, cals = p
    res = []
    for cal in cals:
        res += [np.busday_count(a, b, busdaycal=cal), np.busday_count(b, c, busdaycal=cal),
                np.busday_offset(a, 10, roll="forward", busdaycal=cal),
                np.busday_offset(b, -5, roll="backward", busdaycal=cal),
                np.busday_offset(c, 0, roll="modifiedfollowing", busdaycal=cal),
                np.is_busday(a, busdaycal=cal)]
    return tuple(res)


def _random_load(d):
    out = []
    for f in sorted(Path(d).glob("*.npz")):
        z = np.load(f)
        out.append((f.stem, {k: z[k] for k in z.files}))
    return out


def _random_run(p):
    rng = np.random.Generator(np.random.PCG64(int(p["seed"])))
    size = (int(p["R"]), p["n"].shape[0])
    b = rng.binomial(p["n"], p["p"], size=size)
    g = rng.gamma(p["births"] + 0.5, 1.0 / p["n"], size=size)
    d = rng.poisson(p["deaths"], size=size)
    m = rng.normal(p["netmig"], np.sqrt(np.abs(p["netmig"])) + 1.0, size=size)
    return (b, g, d, m)


def _strings_load(d):
    return [(f.stem, np.load(f)) for f in sorted(Path(d).glob("*.npy"))]


def _strings_run(L):
    S = np.strings
    st = S.strip(L); n = S.str_len(st)
    fi = S.find(L, "the"); co = S.count(L, " ")
    rp = S.replace(L, "--", " - ")
    h, sep, t = S.partition(st, " ")
    sw = S.startswith(st, "CHAPTER"); ew = S.endswith(st, ".")
    ia = S.isalpha(h); ex = S.expandtabs(L, 4)
    return (st, n, fi, co, rp, h, sep, t, sw, ew, ia, ex)


GENFROMTXT_DTYPE = [("time", "datetime64[ms]"), ("lat", float), ("lon", float), ("dep", float),
                    ("mag", float), ("magtype", "U10"), ("id", "U50")]


def _genfromtxt_load(d):
    out = []
    for f in sorted(Path(d).glob("*.csv")):
        s = io.StringIO(f.read_text(encoding="utf-8")); names = s.readline().strip().split(",")
        out.append((f.stem, (s, list(range(len(names))))))
    return out


def _genfromtxt_run(p):
    f, usecols = p
    return np.genfromtxt(f, delimiter=",", usecols=usecols, dtype=GENFROMTXT_DTYPE)


def _loadtxt_load(d):
    return [(f.stem, io.StringIO(f.read_text())) for f in sorted(Path(d).glob("*.csv"))]


def _loadtxt_run(s):
    return np.loadtxt(s, delimiter=",", dtype=np.float64)


def _datetime_load(d):
    out = []
    for f in sorted(Path(d).glob("*.npz")):
        z = np.load(f)
        out.append((f.stem, (z["start"], z["end"])))
    return out


def _datetime_run(p):
    s, e = p
    ds = s.astype("datetime64[ms]"); de = e.astype("datetime64[ms]")
    dur = (de - ds).astype("timedelta64[s]")
    day = ds.astype("datetime64[D]")
    txt = ds.astype("S")
    return (ds, de, dur, day, txt)


def _float_load(d):
    out = []
    for f in sorted(Path(d).glob("*.npy")):
        x = np.load(f); out.append((f.stem, (x, x.astype(np.float32))))
    return out


def _float_run(p):
    x64, x32 = p
    return (x64.astype(str), x32.astype(str))


UNITS = {
    "interp-equalize": (_interp_load, _interp_run),
    "busday-calendar": (_busday_load, _busday_run),
    "random-dists": (_random_load, _random_run),
    "strings-text": (_strings_load, _strings_run),
    "genfromtxt-catalog": (_genfromtxt_load, _genfromtxt_run),
    "loadtxt-csv": (_loadtxt_load, _loadtxt_run),
    "datetime-iso": (_datetime_load, _datetime_run),
    "float-format": (_float_load, _float_run),
}


def _arrays(value):
    if isinstance(value, np.ndarray):
        yield value
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _arrays(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _arrays(item)


def _digest(array):
    return array.dtype.str, array.shape, hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def load(case, root):
    loader, call = UNITS[case["op"]]
    items = loader(root)
    guarded = [(a, _digest(a)) for _, p in items for a in _arrays(p)]
    return call, items, guarded


def run(state):
    call, items, _ = state
    return [call(p) for _, p in items]


def verify(state):
    for array, before in state[2]:
        if _digest(array) != before:
            raise ValueError("Input mutated")


def export(state, results):
    for (name, _), result in zip(state[1], results):
        arrays = result if isinstance(result, (tuple, list)) else (result,)
        for index, array in enumerate(arrays):
            yield f"{name}.{index}", array
