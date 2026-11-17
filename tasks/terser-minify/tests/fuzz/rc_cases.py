"""Bounded public fixtures, separate from the frozen differential/timed corpus."""
from .adapter import case, precondition, compare, typed_equal


def cases():
    values = []
    for mode in ("import", "require"):
        for api in ("minify", "minify_sync"):
            # Same caller-owned cache, interleaved with a distinct cache and error.
            # Terser README nameCache example explicitly promises this reuse.
            values.append(case("function add(first,second){return first+second}",
                {"compress": False, "mangle": {"toplevel": True}},
                mode=mode, api=api, sequence=[
                    {"files": "function add(first,second){return first+second}", "cache": "a"},
                    {"files": "function other(value){return value}", "cache": "b"},
                    {"files": "if (", "cache": "b"},
                    {"files": "console.log(add(1,2));", "cache": "a"},
                    {"files": "console.log(other(3));", "cache": "b"},
                ]))
            for code, after in (("EPIPE", 1), ("ENOSPC", 2)):
                values.append(case("/*one*/console.log(1);/*two*/console.log(2);",
                    {"compress": False, "mangle": False,
                     "format": {"comments": {"$callback": "comments-error",
                                             "code": code, "after": after}}},
                    mode=mode, api=api, sequence=[
                        {"files": "/*one*/console.log(1);/*two*/console.log(2);"},
                        {"files": "/*recovery*/console.log(3);"},
                    ]))
    return values


def PRECONDITION(value, surface):
    admitted = precondition(value, surface)
    if not admitted["valid"]:
        return admitted
    valid = any(typed_equal(value, fixture) for fixture in cases())
    return {"valid": valid, "reason": "Bounded documented cache reuse and callback-error fixtures"
            if valid else "Not an RC public fixture"}


COMPARE = compare
