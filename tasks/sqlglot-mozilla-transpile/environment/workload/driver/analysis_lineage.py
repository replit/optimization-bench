"""Protected complete-result contract, copied from the reviewed probe."""
import enum
import json
import logging
import warnings


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.rows = []

    def emit(self, record):
        self.rows.append({"level": record.levelname, "message": record.getMessage()})


def graph_record(roots):
    """Complete ordered graph plus canonical semantic AST values.

    Graph references follow ordered roots/edges and preserve Node sharing.
    AST values are content-interned, not keyed by their allocation topology.
    Parent links and caller/working snapshots belong to a separate API check.
    """
    from sqlglot import exp
    expression_memo, content_ids, expressions, active = {}, {}, [], set()
    node_ids, nodes = {}, []

    def expr_ref(value):
        identity = id(value)
        if identity in expression_memo:
            return {"expression": expression_memo[identity]}
        if identity in active:
            raise ValueError("Cyclic semantic AST value")
        active.add(identity)
        dtype = value.type
        row = {
            "class": type(value).__module__ + "." + type(value).__qualname__,
            "args": primitive({k: v for k, v in value.args.items() if v is not None}),
            # DataType.type is itself in this public API, not an AST child.
            "type": {"self_type": True} if dtype is value else primitive(dtype),
            "comments": primitive(value.comments or []),
            "meta": primitive(value._meta or {}),
        }
        active.remove(identity)
        key = encode(row)
        if key not in content_ids:
            content_ids[key] = len(expressions)
            expressions.append(row)
        reference = content_ids[key]
        expression_memo[identity] = reference
        return {"expression": reference}

    def node_ref(value):
        identity = id(value)
        if identity not in node_ids:
            node_ids[identity] = len(nodes)
            nodes.append(value)
        return node_ids[identity]

    def primitive(value):
        if isinstance(value, exp.Expr):
            return expr_ref(value)
        if isinstance(value, enum.Enum):
            return {"enum": type(value).__module__ + "." + type(value).__qualname__,
                    "value": primitive(value.value)}
        if isinstance(value, list):
            return [primitive(v) for v in value]
        if isinstance(value, tuple):
            return {"tuple": [primitive(v) for v in value]}
        if isinstance(value, dict):
            # Sort keys before visiting values so AST references are independent
            # of argument-map insertion order. Child lists stay ordered.
            keys = sorted(((encode(primitive(k)), k) for k in value), key=lambda pair: pair[0])
            return {"dict": [[primitive(k), primitive(value[k])] for _, k in keys]}
        if value is None or type(value) in (str, int, bool):
            return value
        if isinstance(value, float):
            return {"float_hex": value.hex()}
        raise TypeError(f"Unrepresented observable value: {type(value).__name__}")

    root_rows = [{"column": name, "node": node_ref(root)} for name, root in roots]
    node_rows = []
    index = 0
    while index < len(nodes):
        node = nodes[index]
        node_rows.append({
            "class": type(node).__module__ + "." + type(node).__qualname__,
            "name": node.name, "expression": expr_ref(node.expression), "source": expr_ref(node.source),
            "source_name": node.source_name, "reference_node_name": node.reference_node_name,
            "payload": primitive(node.payload), "downstream": [node_ref(n) for n in node.downstream],
        })
        index += 1
    return {
        "roots": root_rows, "nodes": node_rows, "expressions": expressions,
    }


def qualify_query(caller, schema, dialect):
    from sqlglot.optimizer.qualify import qualify
    # SQLMesh lineage's query copy, explicit defaults from the pinned qualify API.
    query = caller.copy()
    return qualify(
        query, dialect=dialect, db=None, catalog=None, schema=schema,
        expand_alias_refs=True, expand_stars=True, infer_schema=True, isolate_tables=False,
        qualify_columns=True, allow_partial_qualification=False, validate_qualify_columns=False,
        quote_identifiers=True, identify=True, canonicalize_table_aliases=False,
        on_qualify=None, sql=None,
    )


def lineage_roots(query, scope, dialect):
    from sqlglot.lineage import lineage
    # Observed SQLMesh per-column pattern, no cross-call cache or callbacks.
    return [
        (projection.alias_or_name, lineage(
            projection.alias_or_name, sql=query, schema=None, sources=None, dialect=dialect,
            scope=scope, trim_selects=False, copy=False, on_node=None,
        ))
        for projection in query.selects
    ]


def one_case(case):
    import sqlglot
    from sqlglot.optimizer.scope import build_scope
    capture = Capture()
    logger = logging.getLogger("sqlglot")
    logger.addHandler(capture)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            caller = sqlglot.parse_one(case["sql"], read=case["dialect"], error_level=sqlglot.ErrorLevel.RAISE)
            query = qualify_query(caller, case["schema"], case["dialect"])
            scope = build_scope(query)
            roots = lineage_roots(query, scope, case["dialect"])
            result = {
                "status": "ok", "id": case["id"], "graph": graph_record(roots),
            }
        except Exception as error:
            result = {
                "status": "error", "id": case["id"],
                "exception": type(error).__module__ + "." + type(error).__qualname__,
                "message": str(error),
            }
        finally:
            logger.removeHandler(capture)
        result["python_warnings"] = [
            {"category": w.category.__module__ + "." + w.category.__qualname__, "message": str(w.message)}
            for w in caught
        ]
        result["logs"] = capture.rows
    return result


def run_operation(cases):
    return encode({"contract": "scratch-lineage-graph-v2", "results": [one_case(c) for c in cases]})
