"""Protected complete-result contract, copied from the reviewed probe."""
from dataclasses import fields


def paths(tree):
    """Stable positions are result data, not memory addresses or an audit."""
    from sqlglot import exp
    found = {}

    def visit(node, path):
        if id(node) in found:
            raise ValueError("Expected independently parsed trees without shared AST nodes")
        found[id(node)] = path
        for key in sorted(node.args):
            value = node.args[key]
            values = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
            for index, child in values:
                if isinstance(child, exp.Expr):
                    visit(child, [*path, [key, index]])
    visit(tree, [])
    return found


def materialize(source, target, edits):
    from sqlglot.diff import Insert, Remove, Move, Update, Keep
    from analysis_lineage import encode
    from analysis_semantics import Materializer
    source_paths, target_paths = paths(source), paths(target)
    values = Materializer()
    roots = [values.expression(source), values.expression(target)]

    def endpoint(node, positions):
        return {"path": positions[id(node)], "expression": values.expression(node)}

    rows = []
    for edit in edits:
        kind = type(edit)
        row = {"kind": kind.__module__ + "." + kind.__qualname__}
        actual_fields = [field.name for field in fields(edit)]
        if kind is Insert:
            assert actual_fields == ["expression"]
            row["target"] = endpoint(edit.expression, target_paths)
        elif kind is Remove:
            assert actual_fields == ["expression"]
            row["source"] = endpoint(edit.expression, source_paths)
        elif kind in (Move, Update, Keep):
            assert actual_fields == ["source", "target"]
            row["source"] = endpoint(edit.source, source_paths)
            row["target"] = endpoint(edit.target, target_paths)
        else:
            raise TypeError("Unknown edit class: " + str(kind))
        rows.append(row)
    # Materialize complete roots first, so references never depend on edit order.
    rows.sort(key=encode)
    return {"trees": {"roots": roots, "expressions": values.expressions}, "edits": rows}
