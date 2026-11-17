"""Reproduce public optimizer inputs. Existing references are immutable.

This authoring tool is outside the agent image. --freeze admits a new family
once. Later runs only check it. The baseline runtime must match its source lock.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import urllib.request

TASK = Path(__file__).resolve().parents[2]
GLOT = "5d16fa83bfb8f3582eaffa4b99c7d05a061d8a76"
MESH = "f6527ffbd8d54d91e26fab7d36c8f4a061bc6451"
GLOT_PINS = {
    "README.md": "7bd7d13129838a166b447f9b45022b72ac700ae2dde9a1e4c17d8f236d884b8e",
    "tests/helpers.py": "3e60e402106039cd121968b44ace160cb05099c7faf3e616ddbd639a47cbb615",
    "tests/fixtures/optimizer/tpc-h/tpc-h.sql": "8a10e67fceb92ef36c4cb0ab26f064a2761b2a06f8cf931e77bd71d71a9ef1a6",
    "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql": "f584cd3a0dac63ca9654f74ef53011f4b9db7cad223f812de621dadb2ff982d4",
    "LICENSE": "b0bf909b472ddf9bd4eab30f6e6fd8b33bf3997ce038b882fe0663f291958d2d",
}
MESH_PINS = {
    "orders.py": "1edd38bb55d6fc683f1e2ae54ea5bc7fb732d9565097e5b7d72b3c2b6b423b27",
    "order_items.py": "f717e8d02703ac338407a450a89f532e9855375eefb394fe004ca29f185ad477",
    "items.py": "40280ff7aa9013a5713bbe7780ad6f8138c12a27ad5a120c6002a55d74d4c3da",
    "waiter_revenue_by_day.sql": "87095e19c22807fd3cf65f3d13bb9b27e641ee2562bcd52e9105a1a8d2b520dc",
    "customer_revenue_by_day.sql": "78a41318d8b5b19c6e3d60336dfaed67f144533fd884dc10b486ab6b63f89926",
    "customer_revenue_lifetime.sql": "3dd161acd7aa4c39481e6f952c901ee88283d78a688d6e889be506f4b7173957",
    "LICENSE": "9f2ad42f5c3a469e7702c6557b7a08bc5b2b31c51b2a43cad4227eb599ed19ee",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def derived_commitments():
    """Fetch-only public inputs derived from pinned TPC fixtures (never Git bodies)."""
    path = TASK / "environment/public-input-packet.json"
    packet = json.loads(path.read_text()) if path.exists() else {}
    return {row["destination"]: (row["bytes"], row["sha256"]) for row in packet.get("derived_inputs", [])}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline(runtime):
    runtime = runtime.resolve()
    lock = json.loads((TASK / "environment/pristine-files.lock.json").read_text())
    expected = lock["source_files"]
    actual = {path.relative_to(runtime).as_posix(): sha(path.read_bytes())
              for path in (runtime / "sqlglot").rglob("*.py")}
    if actual != expected:
        raise ValueError("Baseline runtime does not match the complete source inventory")
    if "sqlglot" in sys.modules:
        raise ValueError("Authoring needs a fresh interpreter")
    sys.path.insert(0, str(runtime))
    import sqlglot
    if Path(sqlglot.__file__).resolve() != runtime / "sqlglot/__init__.py":
        raise ValueError("Wrong package was imported")
    return load_module("protected_optimizer", TASK / "environment/workload/driver/optimizer.py")


def acquire(repo, commit, relative, digest, local=None):
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{relative}"
    if local is not None and local.is_file():
        raw = local.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(1_000_001)
    if len(raw) > 1_000_000 or sha(raw) != digest:
        raise ValueError("Public source size/hash mismatch: " + relative)
    return raw.decode(), {"url": url, "sha256": digest, "bytes": len(raw)}


def sources(args):
    glot, mesh, records = {}, {}, {}
    for name, digest in GLOT_PINS.items():
        local = args.sqlglot_source / name if args.sqlglot_source else None
        glot[name], records["sqlglot/" + name] = acquire("tobymao/sqlglot", GLOT, name, digest, local)
    for name, digest in MESH_PINS.items():
        relative = name if name == "LICENSE" else "examples/sushi/models/" + name
        cache_name = "LICENSE.sqlmesh" if name == "LICENSE" else "sushi-" + name
        local = args.sqlmesh_cache / cache_name if args.sqlmesh_cache else None
        mesh[name], records["sqlmesh/" + relative] = acquire("SQLMesh/sqlmesh", MESH, relative, digest, local)
    return glot, mesh, records


def schema_literal(source, constant):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == constant for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Missing schema literal")


def public_cases(glot, mesh):
    schema = {"sushi": {}}
    for name in ("orders", "order_items", "items"):
        tree = ast.parse(mesh[name + ".py"])
        decorators = [decorator for node in tree.body if isinstance(node, ast.FunctionDef)
                      for decorator in node.decorator_list
                      if isinstance(decorator, ast.Call)
                      and isinstance(decorator.func, ast.Name) and decorator.func.id == "model"]
        if len(decorators) != 1:
            raise ValueError("Expected one literal SQLMesh model")
        columns = next(item.value for item in decorators[0].keywords if item.arg == "columns")
        schema["sushi"][name] = ast.literal_eval(columns)
    schema["sushi"]["customer_revenue_lifetime"] = {
        "customer_id": "INT", "revenue": "DOUBLE", "event_date": "DATE"}
    cases = []
    for name, dialect in (("waiter_revenue_by_day", "duckdb"),
                          ("customer_revenue_by_day", "hive"),
                          ("customer_revenue_lifetime", "hive")):
        body = mesh[name + ".sql"].split(");", 1)[1].strip()
        body = body.replace("@start_date", "CAST('2024-01-01' AS DATE)")
        body = body.replace("@end_date", "CAST('2024-01-07' AS DATE)")
        if not body.startswith(("SELECT", "WITH")) or "@" in body:
            raise ValueError("Unresolved SQLMesh rendering")
        cases.append(("sqlmesh-sushi-" + name, "sqlmesh-sushi", {
            "sql": body, "schema": schema, "dialect": dialect,
        }))
    section = glot["README.md"].split("### SQL Optimizer\n", 1)[1].split("\n### ", 1)[0]
    code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    call = next(node for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "optimize")
    cases.append(("sqlglot-readme", "sqlglot-readme", {
        "sql": ast.literal_eval(call.args[0].args[0]),
        "schema": ast.literal_eval(next(item.value for item in call.keywords if item.arg == "schema")),
        "dialect": None,
    }))
    # Reproduce the pinned upstream fixture reader. Keep only original inputs,
    # never the neighboring expected SQL. The complete group stays public.
    filtered = "\n".join(line for line in glot["tests/fixtures/optimizer/tpc-h/tpc-h.sql"].splitlines()
                         if line and not line.startswith("--"))
    statements = filtered.split(";")
    schema = schema_literal(glot["tests/helpers.py"], "TPCH_SCHEMA")
    queries = [statements[index].strip() for index in range(0, len(statements) - 1, 2)]
    if len(queries) != 22 or any(query.startswith("#") for query in queries):
        raise ValueError("Unexpected pinned TPC-H fixture shape")
    cases.extend((f"sqlglot-tpch-{index:02d}", "sqlglot-tpch", {
        "sql": sql, "schema": schema, "dialect": None,
    }) for index, sql in enumerate(queries, 1))
    # The pinned upstream reader removes comments and the execute-test marker.
    # Keep every original in the complete TPC-DS group, never the expected SQL.
    filtered = "\n".join(line for line in glot["tests/fixtures/optimizer/tpc-ds/tpc-ds.sql"].splitlines()
                         if line and not line.startswith(("--", "#")))
    statements = filtered.split(";")
    queries = [statements[index].strip() for index in range(0, len(statements) - 1, 2)]
    if len(queries) != 99 or any(query.startswith("#") for query in queries):
        raise ValueError("Unexpected pinned TPC-DS fixture shape")
    schema = schema_literal(glot["tests/helpers.py"], "TPCDS_SCHEMA")
    cases.extend((f"sqlglot-tpcds-{index:02d}", "sqlglot-tpcds", {
        "sql": sql, "schema": schema, "dialect": None,
    }) for index, sql in enumerate(queries, 1))
    return cases


def check_or_plan(path, raw, freeze, writes):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Frozen artifact differs: " + str(path.relative_to(TASK)))
    elif freeze:
        writes[path] = raw
    else:
        raise ValueError("Missing frozen artifact: " + str(path.relative_to(TASK)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--sqlglot-source", type=Path)
    parser.add_argument("--sqlmesh-cache", type=Path)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--extend", action="store_true",
                        help="Append a complete new source group without altering existing commitments.")
    args = parser.parse_args()
    if args.extend and not args.freeze:
        parser.error("--extend requires --freeze")
    optimizer = baseline(args.runtime)
    glot, mesh, source_records = sources(args)
    cases = public_cases(glot, mesh)
    inputs = TASK / "environment/workload/inputs"
    descriptors, records, seen = [], [], set()
    writes = {}
    for identity, group, request in cases:
        raw = optimizer.encode(request)
        digest = sha(raw)
        sql_digest = sha(request["sql"].encode())
        if digest in seen or sql_digest in seen:
            raise ValueError("Duplicate public optimizer input")
        seen.update((digest, sql_digest))
        result = optimizer.run_case(request)
        value = json.loads(result)
        if value["status"] != "ok" or value["logs"] or value["python_warnings"]:
            raise ValueError("Public optimizer eligibility failed: " + identity)
        descriptors.append({"path": "optimizer-" + identity, "family": "optimizer",
                            "sha256": digest, "bytes": len(raw),
                            "expect_len": len(result), "expect_sha": sha(result)})
        records.append({"id": identity, "group": group, "request_sha256": digest,
                        "sql_sha256": sql_digest})
        check_or_plan(inputs / "files" / digest, raw, args.freeze, writes)
    manifest_path = inputs / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    current = [doc for batches in manifest["splits"].values() for batch in batches
               for doc in batch["docs"] if doc.get("family") == "optimizer"]
    if current:
        if current != descriptors:
            if not (args.freeze and args.extend and len(descriptors) > len(current)
                    and current == descriptors[:len(current)]):
                raise ValueError("Existing optimizer input/reference commitments differ")
            manifest["splits"]["train"].append({"docs": descriptors[len(current):]})
            writes[manifest_path] = dump(manifest)
    elif args.freeze:
        manifest["splits"]["train"].append({"docs": descriptors})
        writes[manifest_path] = dump(manifest)
    else:
        raise ValueError("Optimizer family is not frozen")
    payload_path = TASK / "payload.json"
    payload = json.loads(payload_path.read_text())
    additions = [
        {"scope": "public", "name": doc["sha256"],
         "destination": "environment/workload/inputs/files/" + doc["sha256"],
         "bytes": doc["bytes"], "sha256": doc["sha256"], "distribution": "git"}
        for doc in descriptors
    ]
    by_path = {row["destination"]: row for row in payload["files"]}
    derived = derived_commitments()
    for row in additions:
        if derived.get(row["destination"]) == (row["bytes"], row["sha256"]):
            continue  # Committed in environment/public-input-packet.json, not payload.json.
        if row["destination"] in by_path:
            if by_path[row["destination"]] != row:
                raise ValueError("Frozen public payload differs")
        elif args.freeze:
            payload["files"].append(row)
        else:
            raise ValueError("Missing public payload commitment")
    if args.freeze:
        writes[payload_path] = dump(payload)
    subset = {"schema_version": 1, "task": payload["task"], "files": [
        {**row, "destination": row["destination"].removeprefix("environment/")}
        for row in payload["files"] if row["scope"] == "public"]}
    subset_path = TASK / "environment/payload-public.json"
    existing_subset = json.loads(subset_path.read_text())
    if existing_subset != subset:
        if not args.freeze or any(row not in subset["files"] for row in existing_subset["files"]):
            raise ValueError("Public-only inventory changed an existing commitment")
        writes[subset_path] = dump(subset)
    provenance = {
        "format": "sqlglot-public-optimizer-v1",
        "source_files": source_records,
        "cases": records,
        "grouping": "Whole source groups are public. None is eligible for the new private split.",
        "recipe": [
            "Read Python schema and README SQL literals using ast.literal_eval, without running source code.",
            "Strip SQLMesh MODEL headers, replace only start/end dates with the two fixed DATE casts.",
            "Retain SQLGlot TPC-H original query members, not neighboring expected SQL.",
            "Use the protected optimizer call contract for every case, not each source example's invocation.",
            "Retain all 99 original TPC-DS members and their publisher schema, without neighboring expected SQL.",
        ],
        "limitations": "Adapted application examples, documentation and benchmark queries, not production traffic.",
    }
    provenance_path = TASK / "public/optimizer/provenance.json"
    if args.extend and provenance_path.exists():
        previous = json.loads(provenance_path.read_text())
        if (previous["cases"] != records[:len(previous["cases"])]
                or any(provenance["source_files"].get(key) != value
                       for key, value in previous["source_files"].items())
                or previous["recipe"] != provenance["recipe"][:len(previous["recipe"])]
                or any(provenance.get(key) != value for key, value in previous.items()
                       if key not in ("cases", "source_files", "recipe"))):
            raise ValueError("Public expansion altered existing provenance")
        writes[provenance_path] = dump(provenance)
    else:
        check_or_plan(provenance_path, dump(provenance), args.freeze, writes)
    check_or_plan(TASK / "environment/notices/SQLMesh-LICENSE", mesh["LICENSE"].encode(), args.freeze, writes)
    # Complete eligibility and preservation preflight precedes every write.
    # Each replacement is atomic, but this is not a directory transaction.
    for path, raw in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
    print(json.dumps({"public_optimizer_cases": len(descriptors),
                      "source_groups": sorted({group for _, group, _ in cases}),
                      "original_inputs_and_references": "preserved",
                      "references": "verified against locked baseline"}))


if __name__ == "__main__":
    main()
