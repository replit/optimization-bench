"""Bounded, public-contract cases. No candidate/held-out inputs are consulted."""
import copy
import hashlib
import json
import struct

TASK = "serde-json-production-crate-index-ingestion"
SURFACES = (
    "serde_json::to_writer", "serde_json::to_writer_pretty",
    "serde_json::Serializer::new", "serde_json::Serializer::pretty",
    "serde_json::from_slice", "serde_json::from_str",
    "serde_json::from_reader", "serde_json::StreamDeserializer::next",
)
MODES = ("success", "short", "error", "zero", "interrupt")
BOUNDARIES = (0, 1, 2, 7, 63, 64, 65, 4095, 4096, 4097, 8191, 8192, 8193)
POLICY = "serde-public-json-io-v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def case_sha256(case):
    return hashlib.sha256(canonical(case)).hexdigest()


def typed(value, depth=0):
    """Independent typed transport, NOT JSON parsed by candidate serde_json."""
    if depth > 64:
        raise ValueError("typed input exceeds verifier depth bound")
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"t" if value else b"f"
    if isinstance(value, int) and -(2**63) <= value < 2**63:
        return b"i" + struct.pack("<q", value)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return b"s" + struct.pack("<I", len(raw)) + raw
    if isinstance(value, list):
        return b"a" + struct.pack("<I", len(value)) + b"".join(
            typed(item, depth+1) for item in value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return b"o" + struct.pack("<I", len(value)) + b"".join(
            typed(key, depth+1) + typed(value[key], depth+1) for key in sorted(value))
    raise ValueError("outside independent typed domain")


def generate(rng, budget):
    count = budget.get("cases", budget.get("cases_per_seed", 90))
    if not isinstance(count, int) or not 0 <= count <= 10000:
        raise ValueError("case budget must be in [0, 10000]")
    for index in range(count):
        api = index % len(SURFACES)
        size = BOUNDARIES[(index // len(SURFACES)) % len(BOUNDARIES)]
        value = {"text": "λ🙂\\\"\n" + "x" * size,
                 "array": [None, True, False, rng.randint(-10000, 10000)],
                 "object": {"a": [], "b": {}, "salt": rng.choice(["", "λ", "\\", "\t"])}}
        mode = MODES[(index // 4) % len(MODES)]
        # Direct slice/string parsing does not have an I/O transport to fault.
        if api in (4, 5):
            mode = "success"
        raw = canonical(value)
        variant = (index // len(SURFACES)) % 5
        if api >= 4:
            if variant == 1:
                raw = raw[:-1]  # valid error-domain case: truncated JSON
            elif variant == 2:
                raw = b'{"x":"\\uD800"}'  # unpaired surrogate
            elif variant == 3:
                raw = b'{"x":]}' if api == 5 else b"\xff"
            elif api == 7:
                raw = b"null true 42 " + raw
        case = {
            "task": TASK, "surface_id": SURFACES[api],
            "contract_id": POLICY, "precondition_id": "public-typed-json-io-v1",
            "input": {"value": value} if api < 4 else {"bytes_hex": raw.hex()},
            "options": {"feature_profile": "pinned-default",
                        "serialize_then_panic": api == 0 and index % 16 == 0},
            "sequence": ["serialize"] if api < 4 else
                        (["next-until-end-or-error"] if api == 7 else ["deserialize"]),
            "faults": {"mode": mode, "max_chunk": rng.choice([1, 2, 7, 64]),
                       "threshold": rng.choice([0, 1, 2, 7, 64, 4096]),
                       "error_kind": rng.choice(["Other", "BrokenPipe", "PermissionDenied"])},
        }
        if case["options"]["serialize_then_panic"]:
            # The custom Serialize panic is unreachable on the pristine arm:
            # its first public serializer write must return this I/O error.
            case["faults"].update(mode="error", threshold=0)
        yield case


def generate_aliases(rng):
    for _ in range(2):
        case = list(generate(rng, {"cases": 5}))[-1]
        case["options"]["aliasing"] = True
        case["sequence"] = ["deserialize-shared-input", "clone", "mutate-owned", "read-retained-results"]
        yield case


def precondition(case, surface):
    try:
        if case["task"] != TASK or case["surface_id"] not in SURFACES:
            raise ValueError("unknown task/public surface")
        if surface and case["surface_id"] not in (
                {surface["id"]} if "id" in surface else
                {item["id"] for item in surface.get("surfaces", [])}):
            raise ValueError("surface not inventoried")
        if case["contract_id"] != POLICY or case["precondition_id"] != "public-typed-json-io-v1":
            raise ValueError("unknown public contract/precondition")
        faults = case["faults"]
        if faults["mode"] not in MODES:
            raise ValueError("unsupported fault class")
        if faults["error_kind"] not in ("Other", "BrokenPipe", "PermissionDenied"):
            raise ValueError("unsupported I/O kind")
        for key in ("max_chunk", "threshold"):
            value = faults[key]
            if type(value) is not int or not 0 <= value <= 1048576:
                raise ValueError("unbounded/invalid fault")
        if faults["max_chunk"] < 1:
            raise ValueError("short reads/writes must make positive progress")
        if case["options"]["feature_profile"] != "pinned-default":
            raise ValueError("unsupported build feature profile")
        api = SURFACES.index(case["surface_id"])
        if type(case["options"]["serialize_then_panic"]) is not bool:
            raise ValueError("invalid custom serializer flag")
        if case["options"]["serialize_then_panic"] and api != 0:
            raise ValueError("custom serializer supported only for to_writer")
        if case["options"]["serialize_then_panic"] and (
                faults["mode"] != "error" or faults["threshold"] != 0):
            raise ValueError("custom serializer requires unreachable pristine panic")
        aliasing = case["options"].get("aliasing", False)
        if type(aliasing) is not bool or aliasing and (
                api != 4 or faults["mode"] != "success"):
            raise ValueError("aliasing requires a successful shared-slice parse")
        expected = ["serialize"] if api < 4 else (
            ["next-until-end-or-error"] if api == 7 else ["deserialize"])
        if aliasing:
            expected = ["deserialize-shared-input", "clone", "mutate-owned", "read-retained-results"]
        if case["sequence"] != expected:
            raise ValueError("unsupported sequence")
        if api < 4:
            payload = typed(case["input"]["value"])
        else:
            payload = bytes.fromhex(case["input"]["bytes_hex"])
            if api == 5:
                payload.decode("utf-8")  # &str requires valid UTF-8, unlike &[u8].
            if api in (4, 5) and faults["mode"] != "success":
                raise ValueError("I/O fault not applicable to slice/string")
            if aliasing:
                value = json.loads(payload)
                if (not isinstance(value, dict) or not isinstance(value.get("array"), list)
                        or not isinstance(value.get("object"), dict)):
                    raise ValueError("aliasing requires the declared nested object schema")
        if len(payload) > 1048576:
            raise ValueError("payload exceeds declared bound")
        return {"valid": True, "reason": "documented public API; legal typed value or JSON error domain"}
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        return {"valid": False, "reason": str(error)}


def encode(case):
    valid = precondition(case, None)
    if not valid["valid"]:
        raise ValueError(valid["reason"])
    api = SURFACES.index(case["surface_id"])
    faults = case["faults"]
    payload = typed(case["input"]["value"]) if api < 4 else bytes.fromhex(case["input"]["bytes_hex"])
    return b"SJF1" + struct.pack(
        "<BBB I I B I", api, MODES.index(faults["mode"]),
        ("Other", "BrokenPipe", "PermissionDenied").index(faults["error_kind"]),
        faults["max_chunk"], faults["threshold"],
        2 if case["options"].get("aliasing") else int(case["options"]["serialize_then_panic"]),
        len(payload)) + payload


def shrink(case):
    api = SURFACES.index(case["surface_id"])
    if case["options"].get("aliasing"):
        result = copy.deepcopy(case)
        result["input"] = {"bytes_hex": canonical({"array": [], "object": {}}).hex()}
        if result != case:
            yield result
        return
    for value in (None, "", [], {}):
        result = copy.deepcopy(case)
        result["input"] = {"value": value} if api < 4 else {"bytes_hex": canonical(value).hex()}
        if result != case and precondition(result, None)["valid"]:
            yield result
    if case["faults"]["threshold"] > 0:
        result = copy.deepcopy(case)
        result["faults"]["threshold"] = 0
        yield result


def compare(baseline, candidate, case):
    """Transport validity checked first; never let malformed reports compare equal."""
    required = {"format", "identity", "termination", "outcome", "effects", "diagnostics"}
    for side, report in (("baseline", baseline), ("candidate", candidate)):
        if not isinstance(report, dict) or not required <= report.keys():
            raise ValueError(side + " observation transport is malformed")
        if report["format"] != "optimbench-fuzz-observation-v1":
            raise ValueError(side + " observation format is unknown")
        identity = report["identity"]
        if any(identity.get(key) != expected for key, expected in (
                ("task", TASK), ("surface_id", case["surface_id"]),
                ("case_sha256", case_sha256(case)))):
            raise ValueError(side + " observation is bound to another case")
        if not isinstance(identity.get("build_sha256"), str) or len(identity["build_sha256"]) != 64:
            raise ValueError(side + " build identity is missing")
        if report["termination"].get("kind") not in ("return", "exception", "panic", "signal", "timeout"):
            raise ValueError(side + " termination class is invalid")
        if report["outcome"].get("kind") not in ("ok", "error", "unavailable"):
            raise ValueError(side + " outcome class is invalid")
    def typed_equal(left, right):
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(
                typed_equal(value, right[key]) for key, value in left.items())
        if isinstance(left, list):
            return len(left) == len(right) and all(
                typed_equal(a, b) for a, b in zip(left, right))
        if isinstance(left, float):
            return left.hex() == right.hex()
        return left == right
    differences = [key for key in ("termination", "outcome", "effects")
                   if not typed_equal(baseline[key], candidate[key])]
    return {"equal": not differences, "policy_id": POLICY, "differences": differences}
