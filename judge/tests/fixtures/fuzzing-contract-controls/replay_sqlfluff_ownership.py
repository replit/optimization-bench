#!/usr/bin/env python3
"""Replay pinned pristine ownership evidence, never a candidate verdict/campaign.

Run using SQLFluff 4.4.0 installed from the task's verified pinned source and
locked dependencies. This script downloads nothing and rejects changed modules.
"""
from dataclasses import FrozenInstanceError, asdict
import hashlib
import inspect
import json
from pathlib import Path
import platform

import sqlfluff
from sqlfluff.core.parser.match_result import MatchResult
from sqlfluff.utils.reflow.config import ReflowConfig

from ownership_probe import observe


def main():
    expected = {
        ReflowConfig: "dc52bb87168660673e34f82e94b26839167f5e5f861db8390e96f2718ce801bb",
        MatchResult: "2fc5869fd883bea49cd454c5c1377433db7b8b19cb8254684e5f173ed26d8908",
    }
    modules = {}
    if sqlfluff.__version__ != "4.4.0":
        raise ValueError("Expected pinned SQLFluff 4.4.0")
    for cls, digest in expected.items():
        actual = hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError("Changed module is not pristine contract evidence: " + cls.__name__)
        modules[cls.__module__] = actual
    cfg = ReflowConfig.from_dict({})
    blocks = observe(
        lambda: cfg.get_block_config({"comma"}), snapshot=asdict,
        mutate=lambda value: value.incorporate(before="touch"),
        interleave=lambda: cfg.get_block_config({"semicolon"}))
    values = [value["spacing_before"] for value in blocks["effects"]["after_refetch"]]
    if values != ["touch", "single", "single"]:
        raise AssertionError("Pinned pristine mutable BlockConfig control changed")
    first = MatchResult.empty_at(7)
    first.segment_kwargs["instance_types"] = ("keyword",)
    second = MatchResult.empty_at(7)
    frozen = False
    try:
        first.matched_slice = slice(0, 0)
    except FrozenInstanceError:
        frozen = True
    result = {
        "format": "optimbench-pristine-ownership-audit-v1",
        "task": "sqlfluff-project-lint",
        "source_commit": "0348222a3bcdb0226563f7349c5a39beb0641955",
        "source_archive_sha256": "e0afbbc9db5ceda286c660caffa640a6aea92ba99e964b9b7522207e01322a38",
        "runtime": {"python": platform.python_version(), "sqlfluff": sqlfluff.__version__},
        "module_sha256": modules,
        "observation_fixture_sha256": hashlib.sha256(
            Path(__file__).with_name("ownership_probe.py").read_bytes()).hexdigest(),
        "block_config": {"domain": "documented-mutable-extension-API", "observation": blocks},
        "match_result": {
            "domain": "nested-dict-mutation-scope-unresolved",
            "top_level_frozen": frozen,
            "nested_dict_mechanically_mutable": first.segment_kwargs == {"instance_types": ("keyword",)},
            "next_default_dict_empty": second.segment_kwargs == {},
            "candidate_verdict": None,
        },
        "candidate_executed": False,
        "qualification": "host-pristine-contract-evidence; not a pinned-image task/candidate campaign",
    }
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
