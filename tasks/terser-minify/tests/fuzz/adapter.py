"""Public minify/minify_sync case hooks; no candidate-dependent expected answers."""
from copy import deepcopy
import hashlib
import json
import math

TASK = "terser-minify"
POLICY = "exact-public-result-error-and-mutable-cache-v1"
SOURCES = [
    "",
    "function f(longName){return longName+1} console.log(f(2));",
    "/*keep*/class C{#x=1;get(){return this.#x}};console.log(new C().get());",
    "'use strict';function f(a){var o={x:a,y:2};return o.x+o.y}f(4);",
    "let λ='🙂';const x={['a'+1]:λ};console.log(x);",
    "async function f(){return await Promise.resolve(1)};f();",
    "function f(a){return a?1:2}var x=f(0);",
    "for(let i=0;i<3;i++){console.log(i)}",
]
OPTION_CASES = [
    {}, {"compress": False, "mangle": False},
    {"compress": {"passes": 3, "hoist_props": True, "hoist_vars": True}},
    {"compress": {"toplevel": "funcs", "unused": "keep_assign", "inline": 3}},
    {"compress": {"unsafe": True, "unsafe_math": True}, "ecma": 2020},
    {"compress": {"global_defs": {"DEBUG": False}, "pure_funcs": ["noop"]}},
    {"mangle": {"properties": {"regex": {"$regex": "^_"}, "keep_quoted": "strict"}}},
    {"mangle": {"reserved": ["f"], "nth_identifier": {"$callback": "nth-simple"}}},
    {"mangle": {"nth_identifier": {"$callback": "nth-weighted"}}},
    {"compress": {"top_retain": {"$callback": "top-retain-select"}}, "toplevel": True},
    {"format": {"comments": {"$callback": "comments-select"}, "beautify": True}},
    {"format": {"ascii_only": True, "quote_style": 3, "semicolons": False}},
    {"sourceMap": {"filename": "out.js", "url": "out.js.map", "includeSources": True}},
    {"sourceMap": {"asObject": True, "root": "src", "includeSources": True}},
    {"sourceMap": {"url": "inline", "includeSources": True}},
    {"parse": {"bare_returns": True}, "compress": False},
    {"ie8": True, "safari10": True, "keep_fnames": {"$regex": "^f"}},
    {"enclose": "x:1", "format": {"preamble": "/*! stable */", "braces": True}},
]

def digest(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def request(case):
    """Controller-bound digest avoids cross-language JSON number spelling drift."""
    return {"format": "optimbench-fuzz-request-v1", "case": case, "case_sha256": digest(case)}

def case(source, options=None, *, mode="import", api="minify", sequence=None):
    return {
        "task": TASK, "surface_id": f"terser.package.{mode}.{api}.v1",
        "contract_id": POLICY, "precondition_id": "public-source-and-options-v1",
        "input": {"files": source, "mode": mode, "api": api},
        "options": deepcopy(options or {}), "sequence": sequence or [],
        "faults": [],
    }

def from_legacy_public(value):
    """Retain existing minify cases; don't relabel private walker cases public."""
    if value.get("kind") != "minify":
        raise ValueError("Legacy internal walker surface needs contract review")
    return case(value["source"], value.get("options", {}))

def generate(rng, budget):
    limit = budget if isinstance(budget, int) else budget.get("case_limit", 128)
    emitted = 0
    # Core modes include repeated/interleaved independent caches and error recovery.
    for mode in ["import", "require"]:
        for api in ["minify", "minify_sync"]:
            for options in OPTION_CASES:
                if emitted >= limit:
                    return
                yield case(rng.choice(SOURCES), options, mode=mode, api=api)
                emitted += 1
            for source in ["if (", "'unterminated", "class C{#x;get(){return this.#x}}"]:
                if emitted >= limit:
                    return
                yield case(source, mode=mode, api=api, sequence=[
                    {"files": SOURCES[1], "cache": "a"},
                    {"files": "if (", "cache": "b"},
                    {"files": SOURCES[2], "cache": "a"},
                    {"files": source, "cache": "b"},
                    {"files": SOURCES[1], "cache": "a"},
                ])
                emitted += 1
    while emitted < limit:
        index = rng.randrange(10000)
        source = f"function f{index}(a){{var x={{v:a}};return x.v+{index}}} console.log(f{index}(2));"
        yield case(source, rng.choice(OPTION_CASES),
                   api=rng.choice(["minify", "minify_sync"]),
                   mode=rng.choice(["import", "require"]))
        emitted += 1

def precondition(value, surface):
    if not isinstance(value, dict) or value.get("task") != TASK:
        return {"valid": False, "reason": "wrong task/envelope"}
    try:
        json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return {"valid": False, "reason": "case is not finite JSON transport data"}
    if value.get("surface_id") not in {
            f"terser.package.{m}.{a}.v1" for m in ["import", "require"]
            for a in ["minify", "minify_sync"]}:
        return {"valid": False, "reason": "not a mapped public package entry"}
    data = value.get("input", {})
    if data.get("mode") not in ["import", "require"] or data.get("api") not in ["minify", "minify_sync"]:
        return {"valid": False, "reason": "unknown import/API mode"}
    expected = f'terser.package.{data["mode"]}.{data["api"]}.v1'
    if value["surface_id"] != expected:
        return {"valid": False, "reason": "surface/mode mismatch"}
    for files in [data.get("files")] + [s.get("files") for s in value.get("sequence", [])]:
        valid = (isinstance(files, str) or
                 isinstance(files, list) and all(isinstance(x, str) for x in files) or
                 isinstance(files, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                for k, v in files.items()))
        if not valid or len(json.dumps(files, ensure_ascii=True)) > 262144:
            return {"valid": False, "reason": "unsupported source type/declared size bound"}
    if not isinstance(value.get("options"), dict) or value.get("faults") != []:
        return {"valid": False, "reason": "options/fault schedule unsupported"}
    return {"valid": True, "reason": "bounded public source/options including documented syntax-error domain"}

def shrink(value):
    # Preserve surface, sequence class and fault schedule. A controller must
    # recheck the finding; failed minimization never erases the original.
    for source in ["f(1);", "var a=1;", ""]:
        result = deepcopy(value)
        result["input"]["files"] = source
        yield result
    if value.get("sequence"):
        for index in range(len(value["sequence"])):
            result = deepcopy(value)
            result["sequence"][index]["files"] = ""
            yield result

def compare(baseline, candidate, value):
    differences = []
    if precondition(value, None)["valid"] is not True:
        return {"equal": False, "policy_id": POLICY, "differences": ["invalid case precondition"]}
    for arm, observation in [("baseline", baseline), ("candidate", candidate)]:
        if not isinstance(observation, dict) or observation.get("format") != "optimbench-fuzz-observation-v1":
            differences.append(f"{arm}: malformed observation")
        elif not all(k in observation for k in ["identity", "termination", "outcome", "effects", "diagnostics"]):
            differences.append(f"{arm}: incomplete observation")
        else:
            identity = observation["identity"]
            if any(identity.get(k) != v for k, v in {
                    "task": TASK, "surface_id": value["surface_id"], "case_sha256": digest(value)}.items()):
                differences.append(f"{arm}: case identity mismatch")
            for outcome in [observation["outcome"]] + [
                    step.get("outcome", {}) for step in observation["effects"].get("steps", [])]:
                if outcome.get("kind") == "error" and (
                        not isinstance(outcome.get("class"), str) or not outcome["class"]
                        or not isinstance(outcome.get("message"), str)):
                    differences.append(f"{arm}: missing typed error class/message")
    if not differences:
        for key in ["termination", "outcome", "effects"]:
            if not typed_equal(baseline[key], candidate[key]):
                differences.append(key)
    return {"equal": not differences, "policy_id": POLICY, "differences": differences}

def typed_equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(typed_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(typed_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and a == b == 0:
        return math.copysign(1, a) == math.copysign(1, b)
    return a == b
