"""Judge-owned pytest plugin: record test outcomes and verify candidate imports."""
import importlib
import json
import os
from pathlib import Path
import sys

_outcomes = {}

def pytest_sessionstart(session):
    name = os.environ['OPTIMBENCH_PACKAGE']
    module = importlib.import_module(name)
    required = Path(os.environ.get('OPTIMBENCH_IMPORT_ROOT', '/app/build/runtime'))
    if not Path(module.__file__).is_relative_to(required):
        raise RuntimeError('Upstream tests imported a different library: ' + module.__file__)
    if name == 'black' and str(required) == '/app/build/runtime' and not module.COMPILED:
        raise RuntimeError('Native Black tests imported the pure-Python fallback')

def pytest_runtest_logreport(report):
    # Setup/teardown failures matter even when the test call succeeds.
    _outcomes.setdefault(report.nodeid, {})[report.when] = report.outcome
    if hasattr(report, "wasxfail"):
        _outcomes[report.nodeid][report.when + "_wasxfail"] = str(report.wasxfail)

def pytest_deselected(items):
    for item in items:
        _outcomes[item.nodeid] = {"collection": "deselected"}

def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, 'workerinput'):
        return
    name = os.environ['OPTIMBENCH_PACKAGE']
    module = sys.modules[name]
    result = {'collected': session.testscollected, 'exit_status': int(exitstatus),
              'library_path': module.__file__, 'outcomes': _outcomes}
    Path(os.environ['OPTIMBENCH_REPORT']).write_text(json.dumps(result, sort_keys=True))
