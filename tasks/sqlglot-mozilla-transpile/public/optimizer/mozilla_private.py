"""Blind, publisher-schema-backed private optimizer acquisition.

Authoring only, outside the benchmark image. Never print selected membership,
query text, table names, parser diagnostics or expected results.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import tarfile
import time

import yaml

from prepare import TASK

COMMIT = "c5dcc248916a4eb19b1b37cb4780fa794bb1fa56"
ARCHIVE = {
    "url": f"https://codeload.github.com/mozilla/bigquery-etl/tar.gz/{COMMIT}",
    "sha256": "d36192ee00d185c120a4a80286fbaa8f3a356c354b9f2fe9de3315476e16b1dc",
    "bytes": 4339612,
}
TYPES = {
    "INTEGER": "INT64", "INT64": "INT64", "FLOAT": "FLOAT64", "FLOAT64": "FLOAT64",
    "BOOLEAN": "BOOL", "BOOL": "BOOL", "STRING": "STRING", "BYTES": "BYTES",
    "NUMERIC": "NUMERIC", "BIGNUMERIC": "BIGNUMERIC", "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE", "TIME": "TIME", "DATETIME": "DATETIME", "GEOGRAPHY": "GEOGRAPHY",
    "JSON": "JSON", "INTERVAL": "INTERVAL",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dtype(field):
    kind = field["type"].upper()
    if kind in ("RECORD", "STRUCT"):
        fields = field["fields"]
        if not fields or not isinstance(fields, list):
            raise ValueError("Record schema has no declared fields")
        value = "STRUCT<" + ", ".join(
            "`" + item["name"].replace("`", "\\`") + "` " + dtype(item) for item in fields
        ) + ">"
    elif kind == "RANGE":
        value = "RANGE<" + TYPES[field["rangeElementType"]["type"].upper()] + ">"
    else:
        value = TYPES[kind]
    return "ARRAY<" + value + ">" if field.get("mode", "NULLABLE").upper() == "REPEATED" else value


def read_pool(archive):
    if archive.stat().st_size != ARCHIVE["bytes"] or sha(archive.read_bytes()) != ARCHIVE["sha256"]:
        raise ValueError("Publisher archive authentication failed")
    counts = Counter()
    catalog, schemas, queries = {}, {}, []
    license_raw = None
    prefix = "bigquery-etl-" + COMMIT + "/"
    with tarfile.open(archive) as bundle:
        for member in bundle:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            path = Path(relative)
            if path.name in ("LICENSE", "LICENSE.md", "LICENSE.txt") and len(path.parts) == 1:
                license_raw = bundle.extractfile(member).read()
            if path.suffix == ".sql":
                counts["all_sql_files"] += 1
            if len(path.parts) != 5 or path.parts[0] != "sql":
                continue
            project, dataset, table = path.parts[1:4]
            identity = ".".join((project, dataset, table))
            if path.name in ("schema.yaml", "schema.yml"):
                counts["publisher_schema_files"] += 1
                raw = bundle.extractfile(member).read()
                try:
                    value = yaml.safe_load(raw)
                    fields = value["fields"] if isinstance(value, dict) else value
                    if not fields or not isinstance(fields, list):
                        raise ValueError("Missing publisher fields")
                    columns = {field["name"]: dtype(field) for field in fields}
                    if len(columns) != len(fields):
                        raise ValueError("Duplicate publisher columns")
                    catalog[identity] = {
                        "project": project, "dataset": dataset, "table": table, "columns": columns,
                    }
                    schemas[identity] = {"member": relative, "sha256": sha(raw)}
                    counts["typed_catalog_tables"] += 1
                except (KeyError, ValueError, TypeError, AttributeError, yaml.YAMLError):
                    counts["unsupported_schema"] += 1
            elif path.name in ("query.sql", "view.sql"):
                raw = bundle.extractfile(member).read()
                queries.append({
                    "member": relative, "project": project, "dataset": dataset,
                    "sql": raw.decode(), "sha256": sha(raw),
                })
                counts["publisher_query_and_view_files"] += 1
    if license_raw is None or b"Mozilla Public License" not in license_raw:
        raise ValueError("Publisher license missing")
    notice = TASK / "environment/notices/Mozilla-MPL-2.0"
    if not notice.is_file() or notice.read_bytes() != license_raw:
        raise ValueError("Publisher notice must be retained verbatim")
    return catalog, schemas, queries, counts


def prepare(archive, optimizer, template):
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope

    catalog, schemas, queries, counts = read_pool(archive)
    public = TASK / "environment/workload/inputs"
    manifest = json.loads((public / "manifest.json").read_text())
    seen_raw, seen_sql, seen_templates = set(), set(), set()
    for batches in manifest["splits"].values():
        for batch in batches:
            for doc in batch["docs"]:
                raw = (public / "files" / doc["sha256"]).read_bytes()
                if sha(raw) != doc["sha256"]:
                    raise ValueError("Existing public input authentication failed")
                if doc.get("family") == "optimizer":
                    request = json.loads(raw)
                    sql, dialect = request["sql"], request["dialect"]
                else:
                    sql, dialect = raw.decode(), "bigquery"
                seen_raw.add(sha(sql.encode()))
                try:
                    tree = sqlglot.parse_one(sql, read=dialect)
                    seen_sql.add(tree.sql(comments=False))
                    seen_templates.add(template(tree))
                except Exception:
                    counts["public_unparsed_but_raw_hash_excluded"] += 1
    cases, detailed = [], []
    started = time.monotonic()
    for query in sorted(queries, key=lambda value: value["member"]):
        if time.monotonic() - started >= 180:
            raise RuntimeError("Bounded source preparation incomplete")
        counts["query_files_examined"] += 1
        raw = query["sql"]
        if "{{" in raw or "{%" in raw or "${" in raw:
            counts["unrendered_template"] += 1
            continue
        if sha(raw.encode()) in seen_raw:
            counts["exact_public_or_selected_sql"] += 1
            continue
        try:
            statements = sqlglot.parse(raw, read="bigquery", error_level=sqlglot.ErrorLevel.RAISE)
            if len(statements) != 1 or not isinstance(statements[0], exp.Query):
                counts["not_one_query"] += 1
                continue
            tree = statements[0]
            if not any(tree.find(kind) for kind in (exp.Join, exp.Group, exp.Window, exp.With, exp.Subquery)):
                counts["outside_nontrivial_feature_envelope"] += 1
                continue
            normalized, shape = tree.sql(comments=False), template(tree)
            if normalized in seen_sql or shape in seen_templates:
                counts["public_or_selected_sql_template_overlap"] += 1
                continue
            required = {}
            for scope in traverse_scope(tree):
                for source in scope.sources.values():
                    if isinstance(source, exp.Table):
                        project = source.catalog or query["project"]
                        dataset = source.db or query["dataset"]
                        key = ".".join((project, dataset, source.name))
                        if key not in catalog:
                            raise LookupError("Referenced publisher schema unavailable")
                        required[key] = catalog[key]
            if not required:
                counts["no_physical_table"] += 1
                continue
            schema = {}
            for key, value in sorted(required.items()):
                for value_type in value["columns"].values():
                    parsed = exp.DataType.build(value_type, dialect="bigquery")
                    if parsed.this == exp.DataType.Type.UNKNOWN:
                        raise ValueError("Unknown publisher column type")
                schema.setdefault(value["project"], {}).setdefault(
                    value["dataset"], {})[value["table"]] = value["columns"]
            request = {"sql": raw, "schema": schema, "dialect": "bigquery"}
            result = optimizer.run_case(request)
            value = json.loads(result)
            if value["status"] != "ok" or value["logs"] or value["python_warnings"]:
                counts["full_optimizer_rejected"] += 1
                continue
            request_raw = optimizer.encode(request)
            cases.append((request_raw, result))
            detailed.append({
                "source": query["member"], "source_sha256": query["sha256"],
                "group": query["project"] + "." + query["dataset"],
                "schema_sources": {key: schemas[key] for key in sorted(required)},
                "request_sha256": sha(request_raw), "parsed_sql_sha256": sha(normalized.encode()),
                "template_sha256": sha(shape.encode()),
            })
            seen_raw.add(sha(raw.encode()))
            seen_sql.add(normalized)
            seen_templates.add(shape)
            counts["distinct_typed_queries_selected"] += 1
        except LookupError:
            counts["referenced_schema_missing"] += 1
        except Exception as error:
            counts["unsupported:" + type(error).__name__] += 1
    aggregate = {
        "new_private_optimizer_cases": len(cases),
        "origin_repository_count": 1,
        "source_project_dataset_groups": len({row["group"] for row in detailed}),
        "counts": dict(sorted(counts.items())),
        "public_origin_and_template_overlap": False,
        "split_rule": "Origin-disjoint within the optimizer family, not across different workload families.",
        "original_conversion_origin_overlap": "Mozilla origin retained unchanged in the original conversion family.",
        "old_private_input_bodies_opened": False,
    }
    provenance = {
        "format": "sqlglot-private-optimizer-v2",
        "source_archive": ARCHIVE, "source_commit": COMMIT,
        "source_license": "MPL-2.0, retained verbatim in the public image notices",
        "selection": "Distinct nontrivial Mozilla query files, with publisher schemas for every base table.",
        "transform": "SQL bytes unchanged. Publisher RECORD/REPEATED schemas become STRUCT/ARRAY types. Implicit project/dataset use the publisher query directory.",
        "deduplication": "Reject identical raw/parsed SQL and related identifier/literal-normalized templates against all public inputs and within private.",
        "eligibility": "Authenticated unchanged baseline completes the exact optimizer/materializer without errors, logs or warnings.",
        "split_rule": aggregate["split_rule"],
        "rationale": "Keep the original conversion family unchanged while evaluating the new optimizer on a separate private origin.",
        "limitations": "Development corpus, not an official qualification or timing admission.",
        "aggregate": aggregate, "members": detailed,
    }
    return cases, detailed, aggregate, provenance
