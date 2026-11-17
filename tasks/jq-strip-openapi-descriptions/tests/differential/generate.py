"""JSON/OpenAPI-shaped trees, recombinations, malformed and Unicode inputs."""
def generate(rng, budget):
    seeds = [{"description": "root", "paths": {"/λ": {"description": "nested", "get": {"x": 1}}}},
             {}, [], "", None, [1, {"description": "drop", "keep": "🙂"}]]
    from pathlib import Path
    import json
    for path in sorted((Path(__file__).parents[2] / "environment/workload/inputs/files").glob("*")):
        if path.is_file() and path.stat().st_size < 32768:
            try:
                seeds.append(json.loads(path.read_text()))
            except (ValueError, UnicodeDecodeError):
                continue
            if len(seeds) >= 8:
                break
    for i in range(budget):
        if i == 0:
            yield {"kind": "stateful"}
        elif i == 1:
            yield {"raw": "{"}
        else:
            value = rng.choice(seeds)
            if i == budget - 1:
                value = {"description": "λ" * 65536, "values": list(range(4096))}
            for _ in range(rng.randrange(4)):
                value = rng.choice([{"description": "erase", "value": value}, [value, rng.choice(seeds)]])
            import json
            yield {"raw": json.dumps(value, ensure_ascii=False), "repeat": rng.randint(1, 3)}


def shrink(case):
    for raw in ["{}", "[]", "", "{"]:
        yield {**case, "raw": raw}
