"""Validate concrete snapshot count/list/clock consistency, not candidate verdicts."""
import argparse
import json
from pathlib import Path

def validate(value):
    assert value["format"] == "optimbench-terser-process-snapshot-v2"
    assert value["phase"] in ["before-call", "after-call"]
    assert value["method"] and isinstance(value["scope_pid"], int)
    assert int(value["sample_clock"]["end_monotonic_ns"]) >= int(value["sample_clock"]["start_monotonic_ns"])
    for count, field in [("threads", "thread_identities"), ("children", "child_identities"),
                         ("fds", "fd_identities")]:
        if value[count + "_available"]:
            assert value[count] == len(value[field]), "Count/list mismatch is invalid evidence"
        else:
            assert value[count] is None, "Unavailable is not a fabricated zero"
        assert len({(x.get("tid", x.get("pid", x.get("fd"))),
                     x.get("start_ticks", x.get("target_sha256"))) for x in value[field]}) == len(value[field])
    for entry in value["thread_identities"] + value["child_identities"]:
        assert isinstance(entry["tid"], int) and entry["tid"] > 0
        assert isinstance(entry["start_ticks"], int) and entry["start_ticks"] > 0
        assert entry["state"] not in ["Z", "X", "x"]
        assert entry["scope_pid"] == value["scope_pid"]
    for entry in value["fd_identities"]:
        assert set(entry) == {"fd", "target_sha256"}
        assert len(entry["target_sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in entry["target_sha256"])
    return all(value[k + "_available"] for k in ["threads", "children", "fds"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    total = available = 0
    for path in args.root.glob("*/observations.jsonl"):
        for line in path.read_text().splitlines():
            for pair in json.loads(line)["diagnostics"]["snapshots"]:
                for phase in ["before", "after"]:
                    total += 1
                    available += validate(pair[phase])
    print(json.dumps({"snapshots": total, "all_identity_resources_available": available,
                      "consistency": "passed", "candidate_verdict": None}))
