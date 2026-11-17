#!/usr/bin/env python3
"""Hypothesis case generation; shared controller owns isolated both-arm replay."""
import argparse
import json
import os
from pathlib import Path

from adapter import DIALECT_PAIRS, GRAMMAR, ROOT, SURFACES, digest, make_case, precondition, public_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--examples", type=int, required=True)
    parser.add_argument("--output", type=Path, default=Path(os.environ.get(
        "SQLGLOT_FUZZ_CORPUS_OUT", "/scratch/sqlglot-property-cases")))
    args = parser.parse_args()
    if not 0 < args.examples <= 100_000 or args.seed < 0:
        parser.error("Require bounded examples 1..100000 and nonnegative seed")
    os.environ.setdefault(
        "HYPOTHESIS_STORAGE_DIRECTORY", str(args.output.parent / "hypothesis-storage"))
    from hypothesis import Phase, given, seed, settings, strategies as st
    args.output.mkdir(parents=True, exist_ok=False)
    surface_map = {x["id"]: x for x in json.loads((ROOT / "surface.json").read_text())["surfaces"]}
    public = tuple(public_corpus())
    sql_strategy = st.one_of(
        st.sampled_from((*GRAMMAR, *public)),
        st.integers(min_value=-2**63, max_value=2**63 - 1).map(lambda n: f"SELECT {n} AS a"),
        st.lists(st.integers(min_value=0, max_value=999), min_size=0, max_size=257).map(
            lambda values: "SELECT " + ",".join(f"{v} AS c{i}" for i, v in enumerate(values))),
        st.text(alphabet=st.characters(exclude_categories=("Cs",)), max_size=2048),
    )
    order = []

    @seed(args.seed)
    @settings(max_examples=args.examples, deadline=None, database=None,
              phases=(Phase.generate,), suppress_health_check=())
    @given(st.sampled_from(SURFACES), sql_strategy,
           st.sampled_from(DIALECT_PAIRS), st.integers(min_value=0, max_value=255),
           st.sampled_from(("RAISE", "IMMEDIATE", "WARN", "IGNORE")))
    def emit(symbol, sql, pair, flags, error_level):
        case = make_case(symbol, sql, read=pair[0], write=pair[1], index=flags)
        case["generator"] = "sqlglot-hypothesis-public-options-v1"
        case["options"]["error_level"] = error_level
        if not precondition(case, surface_map[symbol])["valid"]:
            raise RuntimeError("Property generator produced an inadmissible case")
        sha = digest(case)
        (args.output / (sha + ".json")).write_text(json.dumps(case, sort_keys=True) + "\n")
        order.append(sha)

    emit()
    (args.output / "order.json").write_text(json.dumps({
        "format": "sqlglot-property-corpus-v1", "seed": args.seed,
        "requested_examples": args.examples, "observed_examples": len(order),
        "hypothesis_version": __import__("hypothesis").__version__, "order": order,
        "comparison_status": "generation only; every case still needs isolated both-arm replay",
    }, indent=2) + "\n")
    print(json.dumps({"generated": len(order), "unique": len(set(order)), "seed": args.seed}))


if __name__ == "__main__":
    main()
