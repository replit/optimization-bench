"""Resident worker for the suite's performance units (one unit per case).

Same boundary as the quantile warm batch: the protected driver loads every staged input and
derives every call's arguments, then the worker stops itself (ready). The timed region runs
every call of the unit and keeps the materialized results. After the second stop the worker
checks that no input buffer changed, then writes each result as a C-order .npy file under the
name listed in the protected case. A driver is one module under workload/driver/ with
load(case, root), run(state), verify(state) and export(state, results).
"""
import importlib
import io
import json
import os
from pathlib import Path
import signal

DRIVERS = {"textio": "driver.textio"}


def result_name(case, name):
    return "suite." + case["workload"] + "." + name + ".npy"


def worker():
    import numpy as np
    case = json.loads(Path("case.json").read_text())
    driver = importlib.import_module(DRIVERS[case["driver"]])
    # Untimed: load and prepare every input, derive arguments, hash input buffers.
    state = driver.load(case, Path("inputs"))
    os.kill(os.getpid(), signal.SIGSTOP)
    # Timed: every call of the unit with materialized results.
    results = driver.run(state)
    os.kill(os.getpid(), signal.SIGSTOP)
    # Untimed: input preservation, canonical form and export.
    driver.verify(state)
    produced = []
    Path("results").mkdir()
    for name, array in driver.export(state, results):
        stream = io.BytesIO()
        np.save(stream, np.ascontiguousarray(array), allow_pickle=False)
        (Path("results") / result_name(case, name)).write_bytes(stream.getvalue())
        produced.append(name)
    if produced != case["outputs"]:
        raise ValueError("Result names differ from the protected case")
