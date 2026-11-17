"""Seeded quantile matrix; exact transcripts with only the protected numeric tolerance."""
import base64
import importlib.util
from pathlib import Path
import sys


def generate(rng, budget):
    for i in range(min(budget, 12)):
        yield {"dtype": ["float64", "float32", "int64"][i % 3],
               "shape": [[1], [7, 9], [3, 5, 7]][i % 3],
               "axis": [None, 0, -1][i % 3], "keepdims": bool(i % 2),
               "method": ["linear", "nearest", "lower", "higher", "midpoint", "inverted_cdf",
                          "averaged_inverted_cdf", "closest_observation", "hazen", "weibull",
                          "median_unbiased", "normal_unbiased"][i],
               "q": [[0, .5, 1], [.01, .25, .99], [-.1], [1.1]][i % 4],
               "nan": i % 4 == 0, "view": i % 3, "source": rng.randrange(4),
               "offset": rng.randrange(1000), "salt": rng.randrange(17), "empty": i == 11}


def shrink(case):
    yield {**case, "shape": [1], "axis": None, "view": 0, "empty": False}
    yield {**case, "q": [.5], "method": "linear", "nan": False}


def equal(left, right):
    # Compare every error/warning/mutation transcript exactly, and result arrays
    # through the unchanged task comparator (dtype/shape/NaN/8eps).
    a, b = dict(left), dict(right)
    ar, br = a.pop("result", None), b.pop("result", None)
    if a != b or not isinstance(ar, dict) or not isinstance(br, dict):
        return left == right
    aa, bb = dict(ar), dict(br)
    av, bv = aa.pop("arrays", []), bb.pop("arrays", [])
    if aa != bb or len(av) != len(bv):
        return False
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "tools/runner"))
    spec = importlib.util.spec_from_file_location("protected_numpy_adapter",
        Path(__file__).resolve().parents[2] / "environment/workload/task.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    from runner import bundle
    def array_equal(x, y):
        if x == y:
            return True
        try:
            return adapter.compare(bundle({"result.npy": base64.b64decode(x)}),
                                   bundle({"result.npy": base64.b64decode(y)}))
        except ValueError:
            # Non-linear methods may return integers: their contract is exact.
            return False
    return all(array_equal(x, y) for x, y in zip(av, bv))
