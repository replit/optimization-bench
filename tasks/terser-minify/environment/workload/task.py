"""Sequential Terser batches; one result per original JavaScript input.

`batch` is the original cdnjs asset batch (driver/minify.mjs). `webpack-esm` minifies
unminified npm ES-module dist files with webpack 5's production-default Terser options
(driver/webpack-esm.mjs). Each case is one Node process over its manifest split's documents.
"""
import json
from pathlib import Path

from runner import Case, Step, documents

ENV = {"UV_THREADPOOL_SIZE": "8"}
WEBPACK_ESM = "webpack-esm"
DRIVERS = {"batch": "driver/minify.mjs", WEBPACK_ESM: "driver/webpack-esm.mjs"}


def cases(inputs):
    docs = documents(inputs)
    splits = json.loads((Path(inputs) / "manifest.json").read_text())["splits"]
    added = {doc["path"] for batch in splits.get(WEBPACK_ESM, []) for doc in batch["docs"]}
    result = [Case("batch", {"docs": [doc for doc in docs if doc["path"] not in added]})]
    if added:
        result.append(Case(WEBPACK_ESM, {"docs": [doc for doc in docs if doc["path"] in added]}))
    return result


def prepare(case, ctx):
    ctx.batch(case.data["docs"])
    return [Step(["/usr/local/bin/node", str(ctx.workload / DRIVERS[case.id]),
                  str(ctx.build / "project"), str(ctx.scratch)], timeout=1800)]


def collect(case, ctx, results):
    return ctx.batch_results(case.data["docs"], results)
