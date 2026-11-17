"""Protected public-source-derived xdiff cases; no precomputed oracle values."""
from pathlib import Path


def _families(rng, budget):
    source = (Path(__file__).parent / "original-lines.txt").read_text().splitlines(True)
    for index in range(budget):
        lines = source[:min(len(source), 80)]
        for _ in range(8):
            at = rng.randrange(len(lines) + 1)
            lines.insert(at, rng.choice(["repeat\n", "}\n", "\n", "é 中 🦀\n"]))
        original = "".join(lines)
        changed = lines.copy()
        for _ in range(12):
            at = rng.randrange(len(changed))
            changed[at] = rng.choice(["new line\n", "\twhitespace  \n", "}\n", ""])
        modified = "".join(changed)
        kind = index % 6
        if kind == 0:
            tests = [{"kind": "diff", "a": original, "b": modified,
                      "options": [f"--diff-algorithm={algorithm}", "-U3"]}
                     for algorithm in ("myers", "minimal", "patience", "histogram")]
        elif kind == 1:
            tests = [{"kind": "diff", "a": original, "b": modified,
                      "options": [option]} for option in
                     ("--ignore-space-change", "--ignore-all-space", "--ignore-blank-lines",
                      "--word-diff=porcelain", "--numstat")]
        elif kind == 2:
            tests = [{"kind": "diff", "a": a, "b": b, "options": ["--patience", "-U0"]}
                     for a, b in [("", ""), ("", modified), (original, ""),
                                  (original * 16, modified * 16)]]
        elif kind == 3:
            tests = [{"kind": "diff", "a": a, "b": b, "options": ["--binary"]}
                     for a, b in [("a\0b\n", "a\0c\n"), ("x" * 8193, "y" * 8191),
                                  (original.rstrip("\n"), modified.rstrip("\n"))]]
        elif kind == 4:
            tests = [{"kind": "merge", "a": a, "base": base, "b": b, "options": []}
                     for a, base, b in [("same\n", "same\n", "same\n"),
                                       (modified, original, original),
                                       ("left\n" + original, original, "right\n" + original)]]
        else:
            tests = [{"kind": "diff", "a": a, "b": b,
                      "options": ["--histogram", "--stat"]}
                     for a, b in [(original, modified), (modified, original),
                                  (original, original), (original, modified)]]
        yield {"family": kind, "tests": [{**test, "family": kind} for test in tests]}


def generate(rng, budget):
    if budget != 1:
        raise ValueError("One protected transport batch per seed, containing all six families")
    families = list(_families(rng, 6))
    tests = [test for family in families for test in family["tests"]]
    yield {"family": "all-six-families",
           "tests": [{**test, "atom": index} for index, test in enumerate(tests)]}


def shrink(case):
    tests = case["tests"]
    if len(tests) > 1:
        yield {**case, "tests": [tests[0]]}
        yield {**case, "tests": tests[:max(1, len(tests) // 2)]}
    elif tests[0]["kind"] == "diff":
        test = tests[0]
        a, b = test["a"], test["b"]
        if max(len(a), len(b)) > 1:
            at = next((i for i in range(max(len(a), len(b)))
                       if a[i:i+1] != b[i:i+1]), 0)
            yield {**case, "tests": [{**test, "a": a[at:at+1], "b": b[at:at+1]}]}


def equal(left, right):
    return left == right


def mismatch_attribution(left, right):
    """Seed is retained by the framework; name each changed atomic observation."""
    a = left.get("result", {}).get("observations", [])
    b = right.get("result", {}).get("observations", [])
    if (left.get("exit_code") != right.get("exit_code")
            or left.get("stderr") != right.get("stderr") or len(a) != len(b)):
        return ["transport-envelope"]
    return [{"family": x["family"], "atom": x["atom"]}
            for x, y in zip(a, b) if x != y]
