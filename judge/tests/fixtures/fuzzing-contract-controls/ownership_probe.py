"""Test-fixture observations for legal ownership sequences, not a verdict engine.

Callers provide contract-reviewed factory/mutation/lifetime operations. No tested
library serializes this evidence; snapshots use verifier-owned code. Identity
relationships are diagnostics unless a surface explicitly promises them.
"""
import copy


def observe(factory, *, snapshot, mutate=None, interleave=None, views=None):
    first, second = factory(), factory()
    kept = [first, second]

    def capture():
        return [copy.deepcopy(snapshot(value)) for value in kept]

    effects = {"initial": capture()}
    if mutate is not None:
        mutate(first)
    effects["after_mutation"] = capture()
    if interleave is not None:
        # The caller must establish that retained values remain live/valid here.
        interleave()
    effects["after_interleave"] = capture()
    kept.append(factory())
    effects["after_refetch"] = capture()
    diagnostics = {
        "root_aliases": [[left is right for right in kept] for left in kept],
        "view_aliases": {},
    }
    if views is not None:
        per_value = [views(value) for value in kept]
        for path in sorted(set().union(*(value.keys() for value in per_value))):
            if not all(path in value for value in per_value):
                raise ValueError("ownership view path missing from a retained value")
            diagnostics["view_aliases"][path] = [
                [left[path] is right[path] for right in per_value] for left in per_value]
    return {"effects": effects, "diagnostics": diagnostics}
