"""Complete buffer/streaming/error matrix from authenticated natural-file fragments."""


def generate(rng, budget):
    for i in range(min(budget, 12)):
        yield {"level": [1, 3, 9][i % 3], "size": [0, 1, 7, 31, 1024, 8193][i % 6],
               "chunk": [1, 7, 4096][i % 3], "out_chunk": [1, 17, 4096][(i // 3) % 3],
               "source": rng.randrange(23), "offset": rng.randrange(100),
               "mutation": i % 4, "damage": ["none", "truncate", "flip", "garbage"][i % 4]}


def shrink(case):
    yield {**case, "size": 1, "offset": 0}
    yield {**case, "size": 0, "chunk": 1, "out_chunk": 1}
