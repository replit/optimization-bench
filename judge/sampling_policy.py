"""Resolve protected task sampling settings and retain every explicit override."""
import math


def resolve(settings, minimum=None, maximum=None, warmups=None, half_width=None):
    declared = {"minimum_pairs": settings.get("minimum_pairs", 5),
                "maximum_pairs": settings.get("maximum_pairs", 30),
                "warmups": settings.get("warmups", 1),
                "relative_ci_half_width": settings.get("relative_ci_half_width", .02)}
    requested = {"minimum_pairs": minimum, "maximum_pairs": maximum,
                 "warmups": warmups, "relative_ci_half_width": half_width}
    effective = {name: declared[name] if value is None else value
                 for name, value in requested.items()}
    low, high = effective["minimum_pairs"], effective["maximum_pairs"]
    width = effective["relative_ci_half_width"]
    if (type(low) is not int or type(high) is not int or low < 5 or high < low
            or type(effective["warmups"]) is not int or effective["warmups"] < 1
            or type(width) not in (int, float) or not math.isfinite(width) or width <= 0):
        raise ValueError("Require integer minimum_pairs >= 5, maximum_pairs >= minimum_pairs, "
                         "warmups >= 1 and finite positive relative_ci_half_width")
    return {"version": "task-sampling-policy-v1",
            "declared": declared, "effective": effective,
            "explicit": {name: value is not None for name, value in requested.items()},
            "overrides": {name: value for name, value in requested.items()
                          if value is not None and value != declared[name]}}
