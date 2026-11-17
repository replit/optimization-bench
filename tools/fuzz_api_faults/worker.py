"""Independent long-lived Python worker; one library arm per process.

JSONL in/out. Only the explicit documented adapter registry may invoke APIs.
Invalid preconditions are rejected before candidate import/call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .factories import ADAPTERS, InvalidCase
from .lifecycle import observe

_OUTPUTS = None


class TargetOutput:
    """Keep target fd1/fd2 away from the observer's private report descriptor."""
    def __init__(self):
        self.stdout = tempfile.TemporaryFile()
        self.stderr = tempfile.TemporaryFile()

    def mark(self):
        return self.stdout.tell(), self.stderr.tell()

    def since(self, marks):
        effects = {}
        for name, file, start in (("target_stdout_hex", self.stdout, marks[0]),
                                  ("target_stderr_hex", self.stderr, marks[1])):
            end = file.tell()
            if end - start > 1024 * 1024 or end < start:
                raise RuntimeError("target output exceeds bounded observation transport")
            file.seek(start)
            effects[name] = file.read(end - start).hex()
            file.seek(end)
        return effects


def execute(case):
    if not isinstance(case, dict) or case.get("adapter_id") not in ADAPTERS:
        raise InvalidCase("unknown documented adapter")
    sequence = case.get("sequence")
    if not isinstance(sequence, list) or not 1 <= len(sequence) <= 256 or not all(isinstance(s, dict) for s in sequence):
        raise InvalidCase("bounded typed operation sequence required")
    adapter_class = ADAPTERS[case["adapter_id"]]
    try:
        adapter_class.precondition(sequence)  # No target import before validation.
    except (TypeError, KeyError, AttributeError) as error:
        raise InvalidCase("malformed typed operation") from error
    adapter = adapter_class()
    observations = []
    for index, step in enumerate(sequence):
        mark = _OUTPUTS.mark() if _OUTPUTS else None
        outcome, resources = observe(lambda: adapter.call(step))
        sys.stdout.flush()
        sys.stderr.flush()
        effects = _OUTPUTS.since(mark) if _OUTPUTS else {}
        observations.append({"format": "optimbench-fuzz-observation-v1",
                             "step": index, "operation": step["op"],
                             "termination": {"kind": "exception" if outcome["kind"] == "error" else "return",
                                             "exit_code": 0},
                             "outcome": outcome, "effects": effects,
                             "diagnostics": {"lifecycle": resources}})
        if outcome["kind"] == "error":
            break
    return {"format": "optimbench-api-sequence-v1", "case_id": case.get("case_id"),
            "adapter_id": case["adapter_id"],
            "case_sha256": hashlib.sha256(json.dumps(case, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "status": "observed", "observations": observations}


def semantic(observation):
    """PID/fd identities are diagnostics; interpreted by the shared lifecycle gate."""
    if "sequence_results" in observation:
        return [{**{k: v for k, v in row.items() if k != "observations"},
                 "observations": semantic(row)} for row in observation["sequence_results"]]
    return [{k: v for k, v in step.items() if k != "diagnostics"}
            for step in observation["observations"]]


def execute_request(request):
    if request.get("format") != "judge-generic-api-sequence-v1":
        raise InvalidCase("unsupported request protocol")
    if request.get("task") != "sqlfluff-project-lint" or request.get("adapter_id") not in ADAPTERS:
        raise InvalidCase("unregistered task/adapter")
    repetitions = request.get("repetitions", 3)
    if type(repetitions) is not int or not 1 <= repetitions <= 5:
        raise InvalidCase("invalid bounded repetitions")
    cases, order = request.get("cases"), request.get("order")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
        raise InvalidCase("bounded cases required")
    by_id = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str) or not case["case_id"]:
            raise InvalidCase("case identity required")
        if case["case_id"] in by_id or not isinstance(case.get("surface_id"), str) or not case["surface_id"]:
            raise InvalidCase("unique case/public surface identity required")
        if case.get("faults") not in (None, {}):
            raise InvalidCase("I/O faults do not apply to these factory APIs")
        operations = case.get("operations")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 256 or not all(isinstance(s, dict) for s in operations):
            raise InvalidCase("bounded typed operations required")
        try:
            ADAPTERS[request["adapter_id"]].precondition(operations)
        except (TypeError, KeyError, AttributeError) as error:
            raise InvalidCase("invalid public operation shape") from error
        by_id[case["case_id"]] = case
    if not isinstance(order, list) or not 1 <= len(order) <= 96 or not all(isinstance(i, str) and i in by_id for i in order):
        raise InvalidCase("order must name admitted cases")
    results = []
    # All preconditions have been checked before the first library import.
    # Python imports are cached once per arm worker, never reloaded here.
    for repetition in range(repetitions):
        for position, ident in enumerate(order):
            case = by_id[ident]
            flat = {"case_id": ident, "adapter_id": request["adapter_id"], "sequence": case["operations"]}
            observed = execute(flat)
            results.append({"case_id": ident, "surface_id": case["surface_id"],
                            "repetition": repetition, "order_index": position,
                            "observations": observed["observations"]})
    return {"format": "judge-generic-api-sequence-result-v1", "status": "observed",
            "task": request["task"], "adapter_id": request["adapter_id"],
            "request_id": request.get("request_id"), "sequence_results": results}


def main():
    global _OUTPUTS
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime", type=Path)
    args = p.parse_args()
    if args.runtime:
        sys.path.insert(0, str(args.runtime.resolve()))
    # This private fd is the only observation channel. Library print/os.write
    # calls on stdout/stderr remain target effects, not malformed JSON reports.
    report = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    _OUTPUTS = TargetOutput()
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(_OUTPUTS.stdout.fileno(), 1)
    os.dup2(_OUTPUTS.stderr.fileno(), 2)
    for line in sys.stdin:
        try:
            if len(line.encode()) > 1024 * 1024:
                raise InvalidCase("request exceeds transport bound")
            case = json.loads(line)
            result = execute_request(case) if isinstance(case, dict) and "format" in case else execute(case)
        except InvalidCase as error:
            result = {"format": "optimbench-api-sequence-v1", "status": "out_of_contract",
                      "candidate_verdict": None, "reason": str(error)}
        except Exception as error:
            # Import/driver failures are not mistaken for a candidate oracle mismatch.
            result = {"format": "optimbench-api-sequence-v1", "status": "invalid_run",
                      "reason": type(error).__name__ + ": " + str(error)}
        print(json.dumps(result, sort_keys=True, allow_nan=False), file=report, flush=True)


if __name__ == "__main__":
    main()
