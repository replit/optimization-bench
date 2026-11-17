"""Record every unittest identity/outcome, including SQLGlot skip outcomes."""
import json
import os
from pathlib import Path
import sys
import unittest


def without_excluded(suite, excluded):
    """Drop explicitly declared test IDs; each must be discovered exactly once.

    Exclusions come only from the task release's protected upstream test
    environment (for SQLGlot: tests whose non-redistributable fixtures are absent
    from the image). Unknown or duplicated IDs fail closed rather than pass.
    """
    removed = []

    def prune(node):
        kept = []
        for test in node:
            if isinstance(test, unittest.TestSuite):
                prune(test)
                kept.append(test)
            elif test.id() in excluded:
                removed.append(test.id())
            else:
                kept.append(test)
        node._tests = kept

    prune(suite)
    if sorted(removed) != sorted(excluded) or len(set(excluded)) != len(excluded):
        raise RuntimeError("Declared upstream test exclusions were not each discovered exactly once")
    return sorted(removed)


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = {}
        self.subtests = []

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        self.subtests.append({"identity": subtest.id(),
                              "status": "passed" if error is None else "failed"})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[test.id()] = "passed"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes[test.id()] = {"skip": reason}

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.outcomes[test.id()] = "failed"

    def addError(self, test, error):
        super().addError(test, error)
        self.outcomes[test.id()] = "error"

    def addExpectedFailure(self, test, error):
        super().addExpectedFailure(test, error)
        self.outcomes[test.id()] = "expected_failure"

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.outcomes[test.id()] = "unexpected_success"


if __name__ == "__main__":
    # Equivalent to the task release's unittest discover -s tests -t .,
    # with an identity ledger instead of relying on aggregate counts alone.
    # Read protected settings before any candidate code is imported.
    declared_exclusions = os.environ.get("OPTIMBENCH_UNITTEST_EXCLUDE", "").split()
    sys.path.insert(0, str(Path.cwd()))
    sys.path.insert(0, "/app/build/runtime")
    import sqlglot
    if not Path(sqlglot.__file__).is_relative_to("/app/build/runtime"):
        raise RuntimeError("Upstream SQLGlot tests imported source instead of the submitted build")
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    excluded = without_excluded(suite, declared_exclusions)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result,
                                    failfast=os.environ.get("JUDGE_FAIL_FAST") == "1").run(suite)
    Path(sys.argv[1]).write_text(json.dumps({"outcomes": result.outcomes,
                                           "collected": result.testsRun,
                                           "subtests": result.subtests,
                                           "library_path": sqlglot.__file__,
                                           "excluded": excluded,
                                           "passed": result.wasSuccessful()}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
