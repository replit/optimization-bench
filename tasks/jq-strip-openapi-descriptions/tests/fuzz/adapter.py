"""Bounded contract cases and byte-exact oracle; no private corpus discovery."""
import base64
import copy
import hashlib
import json

TASK = "jq-strip-openapi-descriptions"
POLICY = "jq-public-complete-streams-v1"
PROGRAMS = (
    ".",
    "walk(.)",
    "walk(if type == \"object\" then del(.description) else . end)",
    "walk(if type == \"number\" then . + 1 else . end)",
    "walk(if type == \"number\" then empty else . end)",
    "walk(if type == \"number\" then (., . + 1) else . end)",
    "try walk(error(\"controlled\")) catch .",
    "path(walk(.))",
    "walk(if type == \"array\" then reverse else . end)",
)
CLI_OPTIONS = ((), ("-s",), ("-e",), ("-S",), ("--ascii-output",),
               ("--stream",), ("--stream", "--stream-errors"), ("--seq",),
               ("-R",), ("-R", "-s"))


def encode(raw):
    return base64.b64encode(raw).decode("ascii")


def decode(value):
    return base64.b64decode(value, validate=True)


def content_hash(case):
    data = {key: value for key, value in case.items() if key != "case_sha256"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def make_case(raw, program=".", options=(), source=None):
    case = {
        "task": TASK, "surface_id": "jq.cli:filter",
        "contract_id": "jq-cli-exact-output-and-status-v1",
        "precondition_id": "bounded-cli-byte-input-v1",
        "input": {"encoding": "base64", "data": encode(raw)},
        "options": {"program": program, "argv": list(options)},
        "sequence": [{"operation": "execute-fresh-cli"}],
        "faults": [], "generator": "jq-grammar-v1",
    }
    if source is not None:
        case["public_provenance"] = source
    case["case_sha256"] = content_hash(case)
    return case


def generate(rng, budget):
    """Grammar cases; public bytes must be supplied explicitly by the controller.

    No filesystem walks, held-out reads, expected outputs, or candidate answers.
    `public_cases` is a list of records with data_b64, sha256, split=public_dev
    and a provenance string. The controller selects the public manifest.
    """
    config = budget if isinstance(budget, dict) else {"cases": budget}
    limit = min(max(int(config.get("cases", 64)), 0), 4096)
    values = [None, True, False, 0, -1, 1.5, "", "λ\u0000", [], {},
              {"z": 1, "a": [None, {"description": "ordinary key", "v": 4}]},
              {"": 1, "a": [], "b": {}, "0": "x"}]
    raw_cases = [b"{", b"[1,", b'{"a":1,"a":2}', b'"\xff"', b'"\x1e"',
                 b'"\x00"', b"\x1e{}\n", b"null\n{}\n[]\n"]
    for index in range(limit):
        if index < len(raw_cases):
            raw = raw_cases[index]
        else:
            value = copy.deepcopy(rng.choice(values))
            for _ in range(rng.randrange(4)):
                value = rng.choice([{"description": "erase", "value": value},
                                    [value, copy.deepcopy(rng.choice(values))]])
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        yield make_case(raw, rng.choice(PROGRAMS), rng.choice(CLI_OPTIONS))
    for record in config.get("public_cases", []):
        if record.get("split") != "public_dev" or not record.get("provenance"):
            raise ValueError("Explicit public_dev provenance required")
        raw = decode(record["data_b64"])
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Public input content hash differs")
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("Public document exceeds declared large-case bound")
        for transformed, operation in ((raw, "identity"),
                                       (raw[:max(len(raw) // 2, 1)], "truncate-half"),
                                       (b"[" + raw + b"]", "wrap-array")):
            source = {key: record[key] for key in ("split", "sha256", "provenance")}
            source["transformation"] = operation
            yield make_case(transformed, PROGRAMS[2], source=source)


def precondition(case, surface):
    try:
        if case["task"] != TASK or case["surface_id"] != "jq.cli:filter":
            return {"valid": False, "reason": "unmapped surface"}
        if (case["contract_id"] != "jq-cli-exact-output-and-status-v1" or
                case["precondition_id"] != "bounded-cli-byte-input-v1"):
            return {"valid": False, "reason": "unknown contract or valid-domain identity"}
        if case.get("case_sha256") != content_hash(case):
            return {"valid": False, "reason": "case identity mismatch"}
        if case["input"]["encoding"] != "base64":
            return {"valid": False, "reason": "unknown byte encoding"}
        if len(decode(case["input"]["data"])) > 32 * 1024 * 1024:
            return {"valid": False, "reason": "input too large"}
        if case["options"]["program"] not in PROGRAMS:
            return {"valid": False, "reason": "program outside declared grammar"}
        if tuple(case["options"]["argv"]) not in CLI_OPTIONS:
            return {"valid": False, "reason": "undeclared CLI option combination"}
        if case["faults"] or case["sequence"] != [{"operation": "execute-fresh-cli"}]:
            return {"valid": False, "reason": "fault/state class not implemented"}
        return {"valid": True, "reason": "bounded documented CLI; malformed JSON may return an error"}
    except (KeyError, TypeError, ValueError):
        return {"valid": False, "reason": "malformed case"}


def shrink(case):
    """Preserve the public surface, options, sequence and fault class."""
    raw = decode(case["input"]["data"])
    seen = {raw}
    for smaller in (raw[:len(raw)//2], b"{}", b"[]", b"null", b"{", b""):
        if smaller in seen or len(smaller) >= len(raw):
            continue
        seen.add(smaller)
        item = copy.deepcopy(case)
        item["input"]["data"] = encode(smaller)
        item["case_sha256"] = content_hash(item)
        yield item


def compare(baseline, candidate, case):
    """Strict contractual channels; diagnostics never erase byte differences."""
    differences = []
    for label, observation in (("baseline", baseline), ("candidate", candidate)):
        if (observation.get("format") != "optimbench-fuzz-observation-v1" or
                observation.get("identity", {}).get("case_sha256") != content_hash(case) or
                observation.get("identity", {}).get("task") != TASK or
                observation.get("identity", {}).get("surface_id") != case["surface_id"] or
                len(observation.get("identity", {}).get("build_sha256", "")) != 64):
            raise ValueError("Malformed or wrong-case " + label + " observation")
        termination = observation.get("termination", {})
        outcome = observation.get("outcome", {})
        if (termination.get("kind") not in ("return", "exception", "panic", "signal", "timeout") or
                "exit_code" not in termination or outcome.get("kind") not in ("ok", "error")):
            raise ValueError("Missing termination/outcome contract")
        effects = observation.get("effects", {})
        for stream in ("stdout", "stderr"):
            if effects.get(stream, {}).get("encoding") != "base64":
                raise ValueError("Missing exact " + stream + " bytes")
            decode(effects[stream]["data"])
    for field in ("termination", "outcome", "effects"):
        if baseline.get(field) != candidate.get(field):
            differences.append({"field": field, "baseline": baseline.get(field),
                                "candidate": candidate.get(field)})
    return {"equal": not differences, "policy_id": POLICY, "differences": differences}
