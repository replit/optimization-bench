"""Protected complete-result contract, copied from the reviewed probe."""
import logging
import warnings
from analysis_lineage import Capture, encode


def run_cases(contract, cases, operation):
    rows = []
    for case in cases:
        capture = Capture()
        logger = logging.getLogger("sqlglot")
        logger.addHandler(capture)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                row = {"id": case["id"], "status": "ok", **operation(case)}
            except Exception as error:
                row = {"id": case["id"], "status": "error",
                       "exception": type(error).__module__ + "." + type(error).__qualname__,
                       "message": str(error)}
            finally:
                logger.removeHandler(capture)
            row["logs"] = capture.rows
            row["python_warnings"] = [
                {"category": w.category.__module__ + "." + w.category.__qualname__,
                 "message": str(w.message)}
                for w in caught
            ]
        rows.append(row)
    return encode({"contract": contract, "results": rows})
