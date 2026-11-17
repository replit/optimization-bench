"""Public-contract cases. No library imports, private corpora or oracle answers."""
import copy
import hashlib
import json
from pathlib import Path

TASK = "black-project-format"
FORMAT_APIS = (
    "black.format_str",
    "black.format_file_contents",
    "black.format_cell",
    "black.format_ipynb_string",
    "black.format_file_in_place",
    "black.format_stdin_to_stdout",
)
IMPLEMENTED = frozenset((*FORMAT_APIS, "black.cli"))
MODE_BOOL = (
    "string_normalization", "is_pyi", "skip_source_first_line",
    "magic_trailing_comma", "preview", "unstable",
)
TARGETS = ("PY310", "PY311", "PY312", "PY313", "PY314")
CONTRACT = "black-public-v1"
PRECONDITION = "black-typed-bounded-v1"
POLICY = "black-library-values-error-classes-and-exact-cli-v1"


def _public_corpus():
    folder = Path(__file__).resolve().parent / "corpus"
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["visibility"] == "public"
    for entry in manifest["files"]:
        data = (folder / entry["path"]).read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    row = next(entry for entry in manifest["files"] if entry["path"] == "django-tree.py")
    return (folder / row["path"]).read_text(), row["sha256"]


def case_sha256(case):
    # Exact canonical convention from published judge.fuzzing_contract.digest.
    value = {key: item for key, item in case.items() if key != "case_sha256"}
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if {(type(key), key) for key in left} != {(type(key), key) for key in right}:
            return False
        return all(_typed_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_typed_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return left.hex() == right.hex()
    return left == right


def _notebook(source):
    # Input structure, not a formatter-specific expected answer.
    return json.dumps({
        "cells": [{"cell_type": "code", "execution_count": None, "metadata": {},
                   "outputs": [], "source": source.splitlines(keepends=True)}],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
    }, ensure_ascii=False)


def generate(rng, budget):
    """Bounded deterministic RNG input; the controller freezes actual output."""
    limit = budget if isinstance(budget, int) else budget.get("max_cases", 120)
    limit = max(0, min(int(limit), 10000))
    public_source, public_sha = _public_corpus()
    templates = (
        "value={n}\n",
        "def f(x:{annotation}=1):\n return [x,2,3]\n",
        "items=[i for i in range({n}) if i % 2]\n",
        "# λ🙂\ntext='hello world'\n",
        "# fmt: off\nx=  [1,2,3]\n# fmt: on\nx=2\n",
        "def broken(:\n",
        "",
        "if True:\n    pass\n",
        "match value:\n case 1: print('one')\n",
        "f'answer: {1 + 2}'\n",
    )
    for index in range(limit):
        surface = FORMAT_APIS[index % len(FORMAT_APIS)]
        if index % 11 == 10:
            surface = "black.cli"
        fault = None
        if index % 23 == 22:
            surface, fault = "black.format_file_in_place", "readonly-input-file"
        elif index % 29 == 28:
            surface, fault = "black.format_file_in_place", "missing-input-file"
        elif index % 31 == 30:
            surface, fault = "black.cli", "readonly-input-file"
        source = templates[index % len(templates)].format(
            n=rng.choice((0, 1, 2, 7, 79, 80, 88, 89, 256)), annotation="int"
        ) if index % len(templates) not in (4, 9) else templates[index % len(templates)]
        corpus_mutation = index % 17 == 16
        if corpus_mutation:
            # Original file remains verbatim in corpus/. Only the generated case
            # receives a deterministic comment/newline mutation; no oracle text.
            source = "# public-corpus mutation λ🙂\n" + public_source + "\n"
        # Braces in arbitrary public corpus are never used as template syntax.
        line_length = rng.choice((1, 20, 63, 79, 80, 88, 89, 120))
        options = {
            "line_length": line_length, "target_versions": [rng.choice(TARGETS)],
            "string_normalization": bool(rng.getrandbits(1)),
            "magic_trailing_comma": bool(rng.getrandbits(1)),
            "preview": index % 5 == 0, "unstable": index % 7 == 0,
            "is_pyi": index % 13 == 0, "fast": bool(rng.getrandbits(1)),
            "ranges": [[1, 1]] if surface == "black.format_str" and index % 3 == 0 else [],
        }
        if surface == "black.format_ipynb_string":
            source = _notebook(source)
        if surface == "black.cli":
            options["cli_mode"] = "write" if fault else rng.choice(("check", "write", "stdin"))
        if surface in ("black.format_file_in_place", "black.format_stdin_to_stdout"):
            options["write_back"] = "YES" if fault else rng.choice(("NO", "YES", "CHECK"))
        yield {
            "task": TASK, "surface_id": surface, "contract_id": CONTRACT,
            "precondition_id": PRECONDITION,
            "generator_id": "explicit-public-corpus-mutation-v1" if corpus_mutation else "typed-grammar-v1",
            "input": {"text": source, "filename": "example.pyi" if options["is_pyi"] else "example.py",
                      **({"public_original_sha256": public_sha} if corpus_mutation else {})},
            "options": options,
            "sequence": ([{"op": "call"}, {"op": "alternate_mode"}, {"op": "clear_targets"},
                          {"op": "alternate_mode"}, {"op": "call"}] if index % 5 == 0
                         else [{"op": "call"}, {"op": "alternate_mode"}, {"op": "call"}]),
            "faults": [{"kind": fault}] if fault else [],
        }


def precondition(case, surface):
    """Reject malformed harness states; legal syntax errors remain valid cases."""
    def invalid(reason):
        return {"valid": False, "reason": reason}
    if not isinstance(case, dict) or case.get("task") != TASK:
        return invalid("wrong_task_or_case_type")
    if case.get("surface_id") not in IMPLEMENTED:
        return invalid("unimplemented_or_nonpublic_surface")
    if case.get("contract_id") != CONTRACT or case.get("precondition_id") != PRECONDITION:
        return invalid("unknown_contract_or_precondition")
    if "case_sha256" in case and case["case_sha256"] != case_sha256(case):
        return invalid("case_self_hash_mismatch")
    if not isinstance(case.get("input"), dict) or not isinstance(case["input"].get("text"), str):
        return invalid("source_must_be_text")
    try:
        size = len(case["input"]["text"].encode("utf-8"))
    except UnicodeError:
        return invalid("unpaired_surrogate_not_in_text_domain")
    if size > 1_048_576:
        return invalid("case_size_budget_exceeded")
    if case["input"].get("filename") not in ("example.py", "example.pyi", "example.ipynb"):
        return invalid("fixture_path_outside_declared_domain")
    options = case.get("options")
    if not isinstance(options, dict):
        return invalid("options_must_be_object")
    allowed = {*MODE_BOOL, "line_length", "target_versions", "fast", "ranges", "write_back", "cli_mode"}
    if set(options) - allowed:
        return invalid("unknown_option")
    if type(options.get("line_length", 88)) is not int or not 1 <= options.get("line_length", 88) <= 10000:
        return invalid("line_length_domain")
    if any(type(options[k]) is not bool for k in (*MODE_BOOL, "fast") if k in options):
        return invalid("boolean_option_domain")
    targets = options.get("target_versions", [])
    if not isinstance(targets, list) or any(target not in TARGETS for target in targets):
        return invalid("target_version_domain")
    ranges = options.get("ranges", [])
    if not isinstance(ranges, list) or len(ranges) > 16:
        return invalid("range_collection_domain")
    for item in ranges:
        if not isinstance(item, list) or len(item) != 2 or any(type(i) is not int for i in item):
            return invalid("range_pair_domain")
        if not 1 <= item[0] <= item[1] <= 100000:
            return invalid("range_bounds_domain")
    if ranges and case["surface_id"] != "black.format_str":
        return invalid("range_surface_not_yet_implemented")
    if options.get("write_back", "NO") not in ("NO", "YES", "CHECK", "DIFF", "COLOR_DIFF"):
        return invalid("write_back_domain")
    if options.get("cli_mode", "check") not in ("check", "write", "diff", "stdin"):
        return invalid("cli_mode_domain")
    if options.get("write_back") in ("DIFF", "COLOR_DIFF") or options.get("cli_mode") == "diff":
        return invalid("diff_clock_control_not_yet_implemented_not_a_surface_exclusion")
    sequence = case.get("sequence")
    if not isinstance(sequence, list) or not 1 <= len(sequence) <= 16:
        return invalid("sequence_budget_domain")
    if any(not isinstance(step, dict) or step.get("op") not in
           ("call", "alternate_mode", "clear_targets") for step in sequence):
        return invalid("unsupported_state_transition")
    faults = case.get("faults")
    if not isinstance(faults, list) or len(faults) > 1:
        return invalid("fault_schedule_budget")
    if faults:
        if faults[0] not in ({"kind": "readonly-input-file"}, {"kind": "missing-input-file"}):
            return invalid("fault_schedule_not_yet_implemented")
        if case["surface_id"] not in ("black.format_file_in_place", "black.cli"):
            return invalid("fault_surface_not_yet_implemented")
        if case["surface_id"] == "black.cli" and options.get("cli_mode") != "write":
            return invalid("filesystem_fault_requires_write_cli")
    if not isinstance(surface, dict):
        return invalid("surface_manifest_type")
    if surface and surface.get("task") not in (None, TASK):
        return invalid("surface_manifest_task_mismatch")
    return {"valid": True, "reason": "documented_typed_api_or_documented_parse_error_domain"}


def shrink(case):
    """Keep surface, options, sequence and fault class; never invent oracle data."""
    text = case["input"]["text"]
    candidates = []
    if len(text) > 1:
        candidates.extend((text[:len(text) // 2], text[len(text) // 2:]))
        lines = text.splitlines(keepends=True)
        if len(lines) > 1:
            candidates.extend(("".join(lines[:len(lines) // 2]), "".join(lines[1:])))
    seen = {text}
    for candidate in candidates[:8]:
        if candidate in seen:
            continue
        seen.add(candidate)
        changed = copy.deepcopy(case)
        changed["input"]["text"] = candidate
        if precondition(changed, {})["valid"]:
            yield changed


def compare(baseline, candidate, case):
    """Typed results/effects exact; private diagnostic strings are never erased."""
    differences = []
    for arm, observation in (("baseline", baseline), ("candidate", candidate)):
        if not isinstance(observation, dict) or observation.get("format") != "optimbench-fuzz-observation-v1":
            differences.append({"path": arm, "kind": "malformed_transport"})
            continue
        required = ("identity", "termination", "outcome", "effects", "diagnostics")
        if any(not isinstance(observation.get(key), dict) for key in required):
            differences.append({"path": arm, "kind": "malformed_transport"})
            continue
        identity = observation.get("identity", {})
        build = identity.get("build_sha256")
        if (identity.get("task") != TASK or identity.get("case_sha256") != case_sha256(case)
                or identity.get("surface_id") != case["surface_id"]
                or not isinstance(build, str) or len(build) != 64
                or any(c not in "0123456789abcdef" for c in build)):
            differences.append({"path": arm + ".identity", "kind": "identity_mismatch"})
        if observation["termination"].get("kind") not in ("return", "exception", "panic", "signal", "timeout"):
            differences.append({"path": arm + ".termination", "kind": "malformed_transport"})
        if observation["outcome"].get("kind") not in ("ok", "error"):
            differences.append({"path": arm + ".outcome", "kind": "malformed_transport"})
        if observation["outcome"].get("kind") == "ok" and "value" not in observation["outcome"]:
            differences.append({"path": arm + ".outcome", "kind": "missing_return_value"})
        records = (observation["outcome"].get("value", [])
                   if observation["outcome"].get("kind") == "ok"
                   else [observation["outcome"]])
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict) or record.get("kind") not in ("ok", "error"):
                    differences.append({"path": arm + ".outcome", "kind": "malformed_call_record"})
                elif record["kind"] == "error" and any(
                    not isinstance(record.get(key), str) for key in ("class", "message")
                ):
                    differences.append({"path": arm + ".outcome", "kind": "missing_contractual_error_field"})
        if observation.get("diagnostics", {}).get("infrastructure_error"):
            differences.append({"path": arm, "kind": "infrastructure_error"})
    if not differences:
        for key in ("termination", "outcome", "effects"):
            if not _typed_equal(baseline.get(key), candidate.get(key)):
                differences.append({"path": key, "kind": "contract_value_or_effect_difference",
                                    "baseline": baseline.get(key), "candidate": candidate.get(key)})
    return {"equal": not differences, "policy_id": POLICY, "differences": differences}
