"""Validate verifier-owned liblzma allocator transcripts (not an RSS cap).

Estimates are upper bounds, never exact-value differential comparisons.
Malformed/truncated transport is invalid_run; a well-formed underreport or
outstanding owned allocation after end is a public contract regression.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

UINT64_MAX = (1 << 64) - 1
SYMBOLS = {
    "raw-encode": "lzma_raw_encoder_memusage",
    "stream-encode": "lzma_raw_encoder_memusage",
    "easy-encode": "lzma_easy_encoder_memusage",
    "mt-encode": "lzma_stream_encoder_mt_memusage",
    "raw-decode": "lzma_raw_decoder_memusage",
    "easy-decode": "lzma_easy_decoder_memusage",
    "stream-decode": "lzma_memusage",
}
POLICY = "xz-requested-owned-workspace-upper-bound-v1"


class InvalidLedger(ValueError):
    pass


def integer(row, key, low=0):
    value = row.get(key)
    if type(value) is not int or not low <= value <= UINT64_MAX:
        raise InvalidLedger(f"invalid {key}")
    return value


def evaluate(rows):
    if not rows or rows[0].get("kind") != "header":
        raise InvalidLedger("missing header")
    header = rows[0]
    if header.get("schema") != "xz-allocation-ledger-v1" or header.get("mode") not in SYMBOLS:
        raise InvalidLedger("unsupported schema or mode")
    for key in ("preset", "threads", "input_bytes", "input_chunk", "output_chunk"):
        integer(header, key)
    if not 0 <= header["preset"] <= 6 or not 1 <= header["threads"] <= 8:
        raise InvalidLedger("out-of-contract options")
    if not 0 <= header["input_bytes"] <= 2097152:
        raise InvalidLedger("out-of-contract size")
    if not all(1 <= header[k] <= 2097152 for k in ("input_chunk", "output_chunk")):
        raise InvalidLedger("out-of-contract chunk")
    live = peak = 0
    allocations = {}
    seen_ids = set()
    findings, observations = [], []
    cycle = 0
    active = False
    finished = False
    api_error = False
    pending_reset = False
    previous_io = (0, 0)
    for row in rows[1:]:
        kind = row.get("kind")
        if cycle >= 3:
            raise InvalidLedger("data after final end")
        if pending_reset:
            peak = live
            pending_reset = False
        if kind == "alloc":
            ident, size = integer(row, "id", 1), integer(row, "bytes")
            if ident in seen_ids or live + size > UINT64_MAX:
                raise InvalidLedger("duplicate allocation or byte overflow")
            seen_ids.add(ident)
            allocations[ident] = size
            live += size
            peak = max(peak, live)
        elif kind == "free":
            ident = integer(row, "id", 1)
            if ident not in allocations:
                raise InvalidLedger("unknown allocation free")
            live -= allocations.pop(ident)
        elif kind == "allocator-contract-error":
            if row.get("reason") != "unknown-free":
                raise InvalidLedger("unknown allocator diagnosis")
            findings.append({"category": "allocator-contract-error", "cycle": cycle,
                             "reason": "free pointer not owned by supplied allocator"})
        elif kind == "observation":
            if integer(row, "cycle") != cycle or row.get("symbol") != SYMBOLS[header["mode"]]:
                raise InvalidLedger("cycle or API mismatch")
            if (integer(row, "live_bytes"), integer(row, "peak_bytes"),
                    integer(row, "outstanding")) != (live, peak, len(allocations)):
                raise InvalidLedger("aggregate does not match allocation events")
            phase = row.get("phase")
            if phase == "init":
                if active:
                    raise InvalidLedger("duplicate init")
                active, finished, api_error, previous_io = True, False, False, (0, 0)
            elif phase not in ("update", "finish", "api-error", "end", "early-end", "error-end") or not active:
                raise InvalidLedger("invalid lifecycle")
            elif (finished or api_error) and phase not in ("end", "early-end", "error-end"):
                raise InvalidLedger("step after finish")
            status = integer(row, "status")
            io = (integer(row, "total_in"), integer(row, "total_out"))
            if any(a < b for a, b in zip(io, previous_io)):
                raise InvalidLedger("non-monotonic stream counters")
            previous_io = io
            report = integer(row, "reported_bytes")
            if phase in ("end", "early-end", "error-end"):
                if phase == "error-end" and not api_error:
                    raise InvalidLedger("error cleanup without preceding API error")
                if phase != "error-end" and phase != ("early-end" if cycle == 1 else "end"):
                    raise InvalidLedger("wrong lifecycle termination")
                if cycle != 1 and not finished and not api_error:
                    raise InvalidLedger("missing stream finish")
                if report != 0:
                    raise InvalidLedger("end observation must not query ended stream")
                if live or allocations:
                    findings.append({"category": "outstanding-allocation", "cycle": cycle,
                                     "live_bytes": live, "allocation_ids": sorted(allocations)})
                cycle += 1
                active = False
                pending_reset = True
            else:
                if status not in ((1,) if phase == "finish" else (0,)) or phase == "api-error":
                    # This probe accepts only valid input and options.
                    findings.append({"category": "unexpected-api-status", "cycle": cycle,
                                     "phase": phase, "status": status})
                    api_error = True
                if phase == "finish":
                    finished = True
                # lzma_memusage is the current decoder workspace, not a peak.
                # Pre-init/previous-block estimates must not be substituted.
                observed = live if header["mode"] == "stream-decode" else peak
                if report in (0, UINT64_MAX) or observed > report:
                    findings.append({"category": "underreported-workspace", "cycle": cycle,
                                     "phase": phase, "symbol": row["symbol"],
                                     "reported_bytes": report, "observed_bytes": observed})
            observations.append(row)
        else:
            raise InvalidLedger(f"unknown ledger event: {kind}")
    if active or cycle != 3:
        raise InvalidLedger("incomplete lifecycle transcript")
    return {
        "schema": "xz-memusage-result-v1", "policy": POLICY,
        "verdict": "contract_regression" if findings else "passed",
        "findings": findings, "mode": header["mode"],
        "observations": observations, "allocation_count": len(seen_ids),
        "peak_owned_bytes": max((x["peak_bytes"] for x in observations), default=0),
        "memory_ratio_verdict_impact": False,
    }


def read_result(path, exit_code=0):
    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        return {"schema": "xz-memusage-result-v1", "policy": POLICY,
                "verdict": "invalid_run", "reason": "ledger exceeds transport bound",
                "worker_exit_code": exit_code}
    data = path.read_bytes()
    try:
        if exit_code != 0:
            raise InvalidLedger(f"probe failed with exit {exit_code}; replay/library diagnosis required")
        if len(data) > 128 * 1024 * 1024:
            raise InvalidLedger("ledger exceeds transport bound")
        rows = [json.loads(line) for line in data.splitlines()]
        if any(not isinstance(r, dict) for r in rows):
            raise InvalidLedger("non-object event")
        result = evaluate(rows)
    except (ValueError, KeyError, TypeError) as exc:
        result = {"schema": "xz-memusage-result-v1", "policy": POLICY,
                  "verdict": "invalid_run", "reason": str(exc)}
    result["transcript_sha256"] = hashlib.sha256(data).hexdigest()
    result["worker_exit_code"] = exit_code
    return result


def compare(baseline, candidate):
    """Do not attribute a failing baseline/harness to the candidate."""
    if baseline["verdict"] != "passed":
        return {"verdict": "invalid_run", "reason": "pristine did not satisfy ledger contract",
                "baseline": baseline, "candidate": candidate}
    return {"verdict": candidate["verdict"], "baseline": baseline, "candidate": candidate}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("candidate", type=Path)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--exit-code", type=int, default=0)
    args = p.parse_args()
    result = read_result(args.candidate, args.exit_code)
    if args.baseline:
        result = compare(read_result(args.baseline), result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
