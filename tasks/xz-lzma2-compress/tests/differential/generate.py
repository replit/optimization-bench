"""Deterministic public liblzma API cases; source bytes come from frozen public inputs."""


def variants(rng):
    # Every seed covers each mode, every supported check, all advertised filter
    # families below, extreme presets, invalid options and malformed streams.
    modes = ["buffer", "stream", "mt", "decode"]
    for i in range(24):
        yield {
            "mode": modes[i % 4], "preset": [0, 1, 6, 9][(i // 4) % 4],
            "extreme": i in (17, 18), "check": [0, 1, 4, 10][(i // 4) % 4],
            "filter": [0, 3, 4, 10][(i // 4) % 4],
            "threads": 1 + (i // 4) % 2, "chunk": [1, 7, 4096][i % 3],
            "out_chunk": [1, 17, 4096][(i // 3) % 3],
            "source": rng.randrange(8), "other": rng.randrange(8),
            "offset": rng.randrange(256), "size": [0, 1, 31, 1024, 8192][i % 5],
            "mutation": i % 4, "invalid": i in (20, 21, 22),
            "damage": ["none", "truncate", "flip", "garbage", "concat", "empty"][i // 4],
        }


def generate(rng, budget):
    rows = list(variants(rng))
    for i in range(min(budget, 12)):
        yield {"variants": [rows[i], rows[i + 12]]}


def shrink(case):
    for row in case["variants"]:
        yield {"variants": [row]}
        for smaller in shrink_variant(row):
            yield {"variants": [smaller]}


def shrink_variant(case):
    for size in (31, 1, 0):
        if size < case["size"]:
            yield {**case, "size": size, "offset": 0}
    yield {**case, "size": 0, "offset": 0, "chunk": 1, "out_chunk": 1}
