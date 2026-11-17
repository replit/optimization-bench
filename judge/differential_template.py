"""Copy into tasks/<id>/tests/differential/generate.py before enabling coverage."""


def generate(rng, budget):
    """Yield JSON-serializable cases in deterministic order.

    Include frozen real inputs plus mutations/recombinations, bounded grammar
    cases, the configured option matrix, empty/large/malformed/Unicode inputs,
    and stateful public API sequences. Exercise concurrency only where the
    upstream library documents thread safety. No expected outputs belong here:
    the pristine build is the oracle.

    For codecs, sweep output-buffer capacities below, at and just above pristine's
    exact encoded size; compare status codes and output bytes, not just a generously
    allocated successful run. Include tiny/odd dimensions and partial final words.
    """
    raise NotImplementedError("Task-specific generator and private run harness required")


def shrink(case):
    """Yield smaller cases; the framework keeps only mismatch-preserving ones."""
    return iter(())


def equal(baseline, candidate):
    """Use exact observable results, or adapt the protected task.compare rule."""
    return baseline == candidate
