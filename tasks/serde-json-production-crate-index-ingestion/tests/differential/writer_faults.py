"""Contract-derived writer schedules; no candidate or held-out data is read."""
import copy

SURFACES = (
    "serde_json::to_writer", "serde_json::to_writer_pretty",
    "serde_json::Serializer::new", "serde_json::Serializer::pretty",
)
MODES = ("success", "short", "error", "short-error", "zero", "interrupt", "flush-error")
BOUNDARIES = (0, 1, 2, 7, 63, 64, 65, 4095, 4096, 4097, 8191, 8192, 8193)


def case_at(index, rng):
    mode = MODES[index % len(MODES)]
    size = BOUNDARIES[index % len(BOUNDARIES)]
    value = {"text": "λ🙂\\\"\n" + "x" * size, "array": [None, True, index, -0.0],
             "object": {"a": [], "b": {}, "salt": rng.choice(["λ", "", "\\", "\n"])}}
    case = {"mode": mode, "value": value,
            "error_kind": ("Other", "BrokenPipe", "PermissionDenied")[index % 3]}
    at = (0, 1, 2, 7, 64, 65, 4096)[(index // len(MODES)) % 7]
    if mode in ("short", "short-error"):
        case["max_chunk"] = (1, 2, 7)[index % 3]
    if mode in ("error", "short-error"):
        case["fail_after"] = at
    if mode == "zero":
        case["zero_after"] = at
    if mode == "interrupt":
        case["interrupt_at"] = at  # once, never a permanent EINTR retry loop
    if mode == "flush-error":
        case["flush_error"] = True
    return case


def shrink(case):
    # Preserve fault class/API family while minimizing value and byte budget.
    for value in (None, "", [], {}):
        yield {**case, "value": value}
    for key in ("fail_after", "zero_after", "interrupt_at"):
        if key in case and case[key] > 0:
            yield {**case, key: 0}


def semantic(result):
    """Call partitioning/counts are diagnostics, not a serialization contract."""
    result = copy.deepcopy(result)
    for observation in result.get("observations", []):
        observation.pop("diagnostics", None)
    return result
