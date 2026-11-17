"""Verifier-owned SQLite RC registrations. Facts only; shared controller policy."""
import json
import hashlib
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import closing

from .rc_driver import ALIAS_SURFACE, FAULTS, OPERATIONS, SURFACE, TASK

HERE = Path(__file__).resolve().parent
CLI_SURFACE = {"id": "sqlite3.shell", "visibility": "public",
               "contract_id": "sqlite3-shell-public-v1", "precondition_id": "sqlite3-shell-bounded-v1"}


def PRECONDITION(case, surface):
    valid = (case.get("task") == TASK and case.get("surface_id") in (SURFACE["id"], ALIAS_SURFACE["id"])
             and case.get("fault") in FAULTS and type(case.get("mmap")) is int
             and case["mmap"] in (0, 1) and type(case.get("seed")) is int and case["seed"] in (0, 17)
             and ((case["surface_id"] == SURFACE["id"] and case.get("operation") in OPERATIONS)
                  or (case["surface_id"] == ALIAS_SURFACE["id"] and case.get("operation") == "alias"
                      and case["fault"] == "healthy" and case["mmap"] == 0)))
    return {"valid": valid, "reason": "bounded public SQL/VFS/pointer lifetime cases" if valid else "unregistered case"}


def _facts(context, filename):
    from judge.verifier import HARDENING
    from runner import Context, Step
    from docker_executor import DockerExecutor

    task = Path(context["task"]).resolve()
    output = Path(context["out"]) / "sqlite-library"
    output.mkdir(parents=True, exist_ok=True)
    key = (str(output.resolve()), context["cpuset"], context["builds"]["baseline"], context["builds"]["candidate"])
    cache = context.setdefault("_sqlite_rc_facts", {})
    if cache.get("key") != key:
        inputs = Path(context.get("public_inputs") or task / "environment/workload/inputs").resolve()
        with tempfile.TemporaryDirectory(prefix="sqlite-rc-") as temporary:
            scratch = Path(temporary)
            ctx = Context(scratch, Path(context["candidate"]), inputs, scratch,
                          task / "environment/workload", docker=True)
            executor = DockerExecutor(context["image"], context["cpuset"], extra_docker_flags=[
                *HARDENING, "-v", f"{Path(context['baseline']).resolve()}:/verifier-baseline:ro",
                "-v", f"{HERE}:/verifier-fuzz:ro",
            ], tmpfs="/tmp:rw,nosuid,nodev,size=2g")
            results = executor.run([Step([
                "python3", "/verifier-fuzz/rc_driver.py", "--baseline", "/verifier-baseline",
                "--candidate", "/app/build", "--baseline-sha", context["builds"]["baseline"],
                "--candidate-sha", context["builds"]["candidate"], "--out", "/scratch/facts",
            ], timeout=800)], ctx, {})
            for i, result in enumerate(results):
                (output / f"step-{i}.stdout").write_bytes(result.stdout)
                (output / f"step-{i}.stderr").write_bytes(result.stderr)
            if any(result.exit_code for result in results):
                raise ValueError("SQLite RC observer failed; inspect retained stdout/stderr")
            shutil.copytree(scratch / "facts", output / "facts", dirs_exist_ok=True)
        cache["key"] = key
    return json.loads((output / "facts" / filename).read_text())


def stream_faults(context):
    return _facts(context, "stream-facts.json")


def repeat_in_process(context):
    return _facts(context, "repeat-facts.json")


def aliasing(context):
    return _facts(context, "alias-facts.json")


GATES = {"stream_faults": stream_faults, "repeat_in_process": repeat_in_process, "aliasing": aliasing}


def CLI_PRECONDITION(case, surface):
    valid = (case.get("task") == TASK and case.get("surface_id") == CLI_SURFACE["id"]
             and case.get("mode") in ("small", "large", "file"))
    return {"valid": valid, "reason": "public sqlite3 readonly shell query/output modes"}


def CLI_CASES(context):
    root = Path(context["out"]) / "sqlite-cli-inputs"
    specs = []
    sqls = {
        "small": "SELECT family,count(*) FROM plants GROUP BY family ORDER BY family;",
        "large": ("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<12000) "
                  "SELECT x,family FROM n CROSS JOIN plants ORDER BY x,family;"),
        "file": ".once out/result.txt\nSELECT * FROM plants ORDER BY id;\n",
    }
    for mode, sql in sqls.items():
        tree = root / mode
        tree.mkdir(parents=True, exist_ok=True)
        database = tree / "input.db"
        database.unlink(missing_ok=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("CREATE TABLE plants(id INTEGER PRIMARY KEY,family TEXT);"
                                     "INSERT INTO plants VALUES(1,'fern'),(2,'moss');")
            connection.commit()
        (tree / "query.sql").write_text(sql + "\n")
        (tree / "out").mkdir(exist_ok=True)
        specs.append({
            "case": {"task": TASK, "surface_id": CLI_SURFACE["id"],
                     "contract_id": CLI_SURFACE["contract_id"],
                     "precondition_id": CLI_SURFACE["precondition_id"], "mode": mode,
                     "input": {"database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                               "sql_sha256": hashlib.sha256((tree / "query.sql").read_bytes()).hexdigest()}},
            "surface": CLI_SURFACE, "payload_class": "large" if mode == "large" else "small",
            "step": {"argv": ["{build}/objects/sqlite3", "-batch", "-bail", "-readonly",
                              "file:input.db?immutable=1", ".read query.sql"], "cwd": ".", "timeout": 60},
            "inputs": str(tree), "watch_inputs": ["input.db", "query.sql"],
            **({"watch_outputs": ["out"], "output_dirs": ["out"],
                "required_outputs": ["out/result.txt"]} if mode == "file" else {}),
        })
    return {"format": "judge-generic-cli-cases-v1", "applicable": True, "cases": specs}


def HEADER_CONSUMERS(context):
    return {"format": "judge-generic-header-request-v1", "applicable": True,
            "manifest": str(HERE / "header-manifest.json")}
