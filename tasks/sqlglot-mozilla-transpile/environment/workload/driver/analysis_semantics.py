"""Protected complete-result contract, copied from the reviewed probe."""
import enum
from analysis_lineage import encode


class Materializer:
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
            return [self.value(v) for v in value]
        if isinstance(value, tuple):
            return {"tuple": [self.value(v) for v in value]}
        if isinstance(value, dict):
            keys = sorted(((encode(self.value(key)), key) for key in value), key=lambda row: row[0])
            return {"dict": [[self.value(key), self.value(value[key])] for _, key in keys]}
        if value is None or type(value) in (str, int, bool):
            return value
        if isinstance(value, float):
            return {"float_hex": value.hex()}
        raise TypeError(f"Unrepresented observable value: {type(value).__name__}")
