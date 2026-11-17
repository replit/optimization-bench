"""Protected complete-result contract for the public schema-aware optimizer."""
import enum
import json
import logging
import warnings


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class Materializer:
    """Preserve semantic values, not incidental allocation or mapping order."""

    def __init__(self):
        self.memo = {}
        self.interned = {}
        self.expressions = []
        self.active = set()

    def expression(self, value):
        identity = id(value)
        if identity in self.memo:
            return {"expression": self.memo[identity]}
        if identity in self.active:
            raise ValueError("Cyclic semantic AST value")
        self.active.add(identity)
        dtype = value.type
        row = {
            "class": type(value).__module__ + "." + type(value).__qualname__,
            "args": self.value({key: val for key, val in value.args.items() if val is not None}),
            "type": {"self_type": True} if dtype is value else self.value(dtype),
            "comments": self.value(value.comments or []),
            "meta": self.value(value._meta or {}),
        }
        self.active.remove(identity)
        key = encode(row)
        if key not in self.interned:
            self.interned[key] = len(self.expressions)
            self.expressions.append(row)
        reference = self.interned[key]
        self.memo[identity] = reference
        return {"expression": reference}

    def value(self, value):
        from sqlglot import exp
        if isinstance(value, exp.Expr):
            return self.expression(value)
        if isinstance(value, enum.Enum):
            return {"enum": type(value).__module__ + "." + type(value).__qualname__,
                    "value": self.value(value.value)}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return {"tuple": [self.value(item) for item in value]}
        if isinstance(value, dict):
            keys = sorted(((encode(self.value(key)), key) for key in value), key=lambda row: row[0])
            return {"dict": [[self.value(key), self.value(value[key])] for _, key in keys]}
        if value is None or type(value) in (str, int, bool):
            return value
        if isinstance(value, float):
            return {"float_hex": value.hex()}
        raise TypeError(f"Unrepresented observable value: {type(value).__name__}")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.rows = []

    def emit(self, record):
        self.rows.append({"level": record.levelname, "message": record.getMessage()})


def prepare(case):
    import sqlglot
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from sqlglot.optimizer.qualify_columns import quote_identifiers
    tree = sqlglot.parse_one(case["sql"], read=case["dialect"], error_level=sqlglot.ErrorLevel.RAISE)
    normalize_identifiers(tree, dialect=case["dialect"])
    quote_identifiers(tree, dialect=case["dialect"])
    return tree


def optimize_tree(tree, case):
    from sqlglot.optimizer import RULES, optimize
    return optimize(
        tree, schema=case["schema"], dialect=case["dialect"], rules=RULES,
        infer_schema=True, quote_identifiers=True, db=None, catalog=None,
    )


def operation(case):
    optimized = optimize_tree(prepare(case), case)
    generated = optimized.sql(dialect=case["dialect"], pretty=False)
    materializer = Materializer()
    root = materializer.expression(optimized)
    return {"optimized_sql": generated,
            "ast": {"roots": [root], "expressions": materializer.expressions}}


def run_case(case):
    capture = Capture()
    logger = logging.getLogger("sqlglot")
    logger.addHandler(capture)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = {"status": "ok", **operation(case)}
        except Exception as error:
            result = {"status": "error",
                      "exception": type(error).__module__ + "." + type(error).__qualname__,
                      "message": str(error)}
        finally:
            logger.removeHandler(capture)
        result["logs"] = capture.rows
        result["python_warnings"] = [
            {"category": item.category.__module__ + "." + item.category.__qualname__,
             "message": str(item.message)}
            for item in caught
        ]
    return encode({"contract": "sqlglot-schema-optimizer-v1", **result})
