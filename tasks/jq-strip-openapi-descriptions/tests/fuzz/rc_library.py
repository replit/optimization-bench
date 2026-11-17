"""Native library facts; verdicts remain in the shared judge."""
import hashlib
import json
from pathlib import Path
import tempfile

TASK = "jq-strip-openapi-descriptions"
SURFACE = {"id": "jq.c.public-probe-v1", "visibility": "public",
           "contract_id": "jq-public-probe-exact-events-v1",
           "precondition_id": "jq-public-probe-bounded-v1"}
EXAMPLES = (
    ("parse", '{"value":[1,2]}', "."),
    ("parse", '{"incomplete":', "."),
    ("parser", '1\n{"x":2}\n[3,4]\n', "."),
    ("parser", '{"incomplete":', "."),
    ("pipeline", '{"value":[1,2]}', ".value[]"),
    ("pipeline", '{"value":[1,2]}', 'error("controlled")'),
    ("ownership", "null", "."),
    ("input-fault", "null", "."),
)


def cases():
    from judge.fuzzing_contract import case_digest
    result = []
    for mode, body, program in EXAMPLES:
        value = {"task": TASK, "surface_id": SURFACE["id"],
                 "contract_id": SURFACE["contract_id"],
                 "precondition_id": SURFACE["precondition_id"],
                 "input": {"text": body}, "options": {"mode": mode, "program": program}}
        value["case_sha256"] = case_digest(value)
        result.append(value)
    return result


def precondition(case, surface):
    from judge.fuzzing_contract import case_digest
    valid = (surface == SURFACE and any(
        case_digest(case) == row["case_sha256"] for row in cases()))
    return {"valid": valid, "reason": "Frozen bounded public API probes"}


def observation(raw, case, build):
    return {"format": "optimbench-fuzz-observation-v1",
            "identity": {"task": TASK, "surface_id": SURFACE["id"],
                         "case_sha256": case["case_sha256"], "build_sha256": build},
            "termination": {"kind": "return", "exit_code": 0},
            "outcome": {"kind": "ok", "value": raw["observation"]},
            "effects": {},
            "diagnostics": {"pid": raw["pid"],
                            "execution_affinity": raw["execution_affinity"]}}


def facts(context):
    from judge.verifier import HARDENING, ROOT
    from runner import Context, Step
    from docker_executor import DockerExecutor
    rows = cases()
    output = Path(context["out"]) / "jq-public-library"
    output.mkdir(parents=True, exist_ok=True)
    raw, fresh, compiled = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="jq-library-") as temporary:
        scratch = Path(temporary)
        for arm in ("baseline", "candidate"):
            arm_scratch = scratch / arm
            arm_scratch.mkdir()
            for index, case in enumerate(rows):
                (arm_scratch / f"input-{index}").write_text(case["input"]["text"])
            build = Path(context[arm]).resolve()
            ctx = Context(arm_scratch, build, Path(context["task"]) / "environment/workload/inputs",
                          arm_scratch, Path(context["task"]) / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"], extra_docker_flags=[
                *HARDENING, "-v", f"{Path(context['task']) / 'tests/fuzz'}:/verifier-fuzz:ro",
                "-v", f"{ROOT / 'tools/fuzz_api_faults/native_lifecycle.hpp'}:/verifier-native-lifecycle.hpp:ro"])
            binary = f"/scratch/{arm}-probe"
            command = ["c++", "-O1", "-std=c++17", "-I/app/build/project/src",
                       "/verifier-fuzz/native/repeat.cpp", "/app/build/project/.libs/libjq.a",
                       "/app/build/project/vendor/oniguruma/src/.libs/libonig.a",
                       "-lm", "-pthread", "-o", binary]
            arguments = [[case["options"]["mode"], f"/scratch/input-{i}", case["options"]["program"]]
                         for i, case in enumerate(rows)]
            commands = [("compile", command)]
            for i, args in enumerate(arguments):
                commands.extend((f"fresh-{i}-{j}", [binary, *args]) for j in range(2))
            commands.append(("persistent", [binary, *sum(arguments + list(reversed(arguments)), [])]))
            results = executor.run([Step(argv, timeout=120) for _, argv in commands], ctx, {})
            if len(results) != len(commands):
                raise ValueError("Incomplete jq native probe execution")
            streams = {}
            for (label, _), result in zip(commands, results):
                (output / f"{arm}-{label}.stdout").write_bytes(result.stdout)
                (output / f"{arm}-{label}.stderr").write_bytes(result.stderr)
                if result.exit_code:
                    raise ValueError("jq native probe failed: " + arm + "-" + label)
                streams[label] = result.stdout
            compiled[arm] = {"command": command,
                            "sha256": hashlib.sha256((arm_scratch / f"{arm}-probe").read_bytes()).hexdigest()}
            fresh[arm] = []
            for i in range(len(arguments)):
                fresh[arm].append([json.loads(streams[f"fresh-{i}-{j}"]) for j in range(2)])
            raw[arm] = [json.loads(line) for line in streams["persistent"].splitlines()]
            if len(raw[arm]) != 2 * len(rows) or len({r["pid"] for r in raw[arm]}) != 1:
                raise ValueError("Incomplete same-process jq observations")
        n = len(rows)
        records = [{"case": case, "surface": SURFACE,
                    **{arm: [observation(raw[arm][i], case, context["builds"][arm]),
                             observation(raw[arm][2*n-1-i], case, context["builds"][arm])]
                       for arm in raw},
                    **{"fresh_" + arm: [observation(value, case, context["builds"][arm])
                                       for value in fresh[arm][i]] for arm in raw}}
                   for i, case in enumerate(rows)]
        lifecycle = [{"case_id": case["case_sha256"],
                      **{arm: {"available": raw[arm][i]["before"]["available"]
                               and raw[arm][i]["after"]["available"],
                               "before": raw[arm][i]["before"], "after": raw[arm][i]["after"]}
                         for arm in raw}}
                     for i, case in enumerate(rows + list(reversed(rows)))]
    result = {"format": "judge-generic-call-evidence-v1", "applicable": True,
              "cases": records, "compiled": compiled, "lifecycle": lifecycle,
              "lifecycle_policy": {"kind": "native-os-threads"},
              "workers": {arm: {"mode": "persistent", "target_library_in_process": True,
                               "worker_id": str(raw[arm][0]["pid"]), "calls": len(raw[arm])}
                          for arm in raw}}
    (output / "facts.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
