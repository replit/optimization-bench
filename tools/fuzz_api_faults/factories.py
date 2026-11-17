"""Explicit factory/mutator adapters, never arbitrary getattr or private mutation."""
from copy import deepcopy


class InvalidCase(ValueError):
    pass


SPACING = {"single", "touch", "any"}
FIELDS = ("spacing_before", "spacing_after", "spacing_within", "line_position",
          "keyword_line_position", "keyword_line_position_exclusions")


class SQLFluffBlocks:
    def __init__(self):
        from sqlfluff.utils.reflow.config import ReflowConfig
        self.factory_class = ReflowConfig
        self.factories = {}
        self.blocks = {}

    @staticmethod
    def precondition(steps):
        factories, blocks = set(), set()
        for step in steps:
            op, handle = step.get("op"), step.get("handle")
            if op == "factory":
                if not isinstance(handle, str) or handle in factories:
                    raise InvalidCase("factory handle must be new")
                config = step.get("config")
                if not isinstance(config, dict) or not config or len(config) > 16:
                    raise InvalidCase("bounded documented config mapping required")
                for name, options in config.items():
                    if not isinstance(name, str) or not name or not isinstance(options, dict):
                        raise InvalidCase("invalid config type")
                    if not set(options) <= {"spacing_before", "spacing_after", "spacing_within"}:
                        raise InvalidCase("unsupported configuration option")
                    if any(value not in SPACING for value in options.values()):
                        raise InvalidCase("unsupported spacing value")
                factories.add(handle)
            elif op == "get":
                if step.get("factory") not in factories or not isinstance(handle, str) or handle in blocks:
                    raise InvalidCase("get needs an existing factory and new block handle")
                types = step.get("types")
                if not isinstance(types, list) or not types or not all(isinstance(s, str) and s for s in types):
                    raise InvalidCase("nonempty documented class types required")
                blocks.add(handle)
            elif op == "incorporate":
                if handle not in blocks:
                    raise InvalidCase("mutation needs a constructed block")
                kwargs = step.get("kwargs")
                if not isinstance(kwargs, dict) or not kwargs or not set(kwargs) <= {"before", "after", "within"}:
                    raise InvalidCase("only documented incorporate spacing arguments admitted")
                if any(value not in SPACING for value in kwargs.values()):
                    raise InvalidCase("invalid incorporate spacing")
            elif op == "observe":
                if handle not in blocks:
                    raise InvalidCase("observe needs a constructed block")
            else:
                raise InvalidCase("unsupported public factory operation")

    @staticmethod
    def block_value(block):
        # Read documented dataclass fields; no identity, repr, or private dictionaries.
        return {name: deepcopy(getattr(block, name)) for name in FIELDS}

    def call(self, step):
        op, handle = step["op"], step["handle"]
        if op == "factory":
            self.factories[handle] = self.factory_class.from_dict(deepcopy(step["config"]))
            return {"stored": handle}
        if op == "get":
            block = self.factories[step["factory"]].get_block_config(set(step["types"]))
            self.blocks[handle] = block
            return self.block_value(block)
        if op == "incorporate":
            return self.blocks[handle].incorporate(**step["kwargs"])
        return self.block_value(self.blocks[handle])


class SQLFluffMatches:
    def __init__(self):
        from sqlfluff.core.parser.match_result import MatchResult
        self.match_class = MatchResult
        self.matches = {}

    @staticmethod
    def precondition(steps):
        handles = set()
        for step in steps:
            op, handle = step.get("op"), step.get("handle")
            if op == "empty":
                if not isinstance(handle, str) or handle in handles:
                    raise InvalidCase("match handle must be new")
                if type(step.get("index")) is not int or not 0 <= step["index"] <= 4096:
                    raise InvalidCase("valid empty match index required")
                handles.add(handle)
            elif op == "append":
                if step.get("left") not in handles or step.get("right") not in handles:
                    raise InvalidCase("append needs public constructed empty matches")
                if not isinstance(handle, str) or handle in handles:
                    raise InvalidCase("append result handle must be new")
                handles.add(handle)
            elif op == "observe":
                if handle not in handles:
                    raise InvalidCase("observe needs constructed match")
            else:
                # In particular, there is NO segment_kwargs/nested-dict mutation.
                raise InvalidCase("frozen MatchResult mutation is out of contract")

    @staticmethod
    def value(match):
        return {"slice": [match.matched_slice.start, match.matched_slice.stop],
                "length": len(match), "truth": bool(match)}

    def call(self, step):
        op, handle = step["op"], step["handle"]
        if op == "empty":
            self.matches[handle] = self.match_class.empty_at(step["index"])
        elif op == "append":
            self.matches[handle] = self.matches[step["left"]].append(self.matches[step["right"]])
        return self.value(self.matches[handle])


ADAPTERS = {
    "sqlfluff.mutable-block-config.v1": SQLFluffBlocks,
    "sqlfluff.frozen-match-result.v1": SQLFluffMatches,
}


def block_case():
    return {"case_id": "block-get-twice-incorporate-interleave", "adapter_id": "sqlfluff.mutable-block-config.v1",
            "sequence": [
                {"op": "factory", "handle": "A", "config": {"comma": {"spacing_before": "touch"}}},
                {"op": "get", "factory": "A", "types": ["comma"], "handle": "first"},
                {"op": "get", "factory": "A", "types": ["comma"], "handle": "second"},
                {"op": "observe", "handle": "second"},
                {"op": "incorporate", "handle": "first", "kwargs": {"before": "any", "after": "touch"}},
                {"op": "observe", "handle": "second"},
                {"op": "factory", "handle": "B", "config": {"comma": {"spacing_after": "touch"}}},
                {"op": "get", "factory": "B", "types": ["comma"], "handle": "other"},
                {"op": "incorporate", "handle": "other", "kwargs": {"before": "single"}},
                {"op": "get", "factory": "A", "types": ["comma"], "handle": "third"},
                {"op": "observe", "handle": "second"},
                {"op": "observe", "handle": "first"},
            ]}


def match_case():
    return {"case_id": "frozen-match-legal-repeat-interleave", "adapter_id": "sqlfluff.frozen-match-result.v1",
            "sequence": [
                {"op": "empty", "handle": "first", "index": 0},
                {"op": "empty", "handle": "second", "index": 0},
                {"op": "empty", "handle": "other", "index": 1},
                {"op": "append", "handle": "combined", "left": "first", "right": "other"},
                {"op": "observe", "handle": "second"},
                {"op": "empty", "handle": "again", "index": 0},
                {"op": "observe", "handle": "first"},
            ]}


def sequence_request(frozen=False):
    first = match_case() if frozen else block_case()
    second = deepcopy(first)
    if frozen:
        for step in second["sequence"]:
            if step["op"] == "empty":
                step["index"] += 2
    else:
        second["sequence"][0]["config"]["comma"]["spacing_before"] = "single"
    surface = ("sqlfluff.core.parser.match_result.MatchResult.empty_at" if frozen else
               "sqlfluff.utils.reflow.config.ReflowConfig.get_block_config")
    cases = [{"case_id": name, "surface_id": surface, "operations": value["sequence"], "faults": {}}
             for name, value in (("A", first), ("B", second))]
    return {"format": "judge-generic-api-sequence-v1", "task": "sqlfluff-project-lint",
            "adapter_id": first["adapter_id"], "request_id": "frozen-control" if frozen else "mutable-isolation",
            "cases": cases, "repetitions": 3, "order": ["A", "B", "A"]}
