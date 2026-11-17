"""Read-only development-recipe / fresh-worker runtime knob parity."""
import ast
import re
import shlex
from pathlib import Path

RUNTIME_KNOBS = frozenset("""
GOMAXPROCS OMP_NUM_THREADS OPENBLAS_NUM_THREADS MKL_NUM_THREADS RAYON_NUM_THREADS
UV_THREADPOOL_SIZE NUMEXPR_NUM_THREADS NUMEXPR_MAX_THREADS VECLIB_MAXIMUM_THREADS
GOTO_NUM_THREADS POLARS_MAX_THREADS NODE_OPTIONS BLACK_NUM_WORKERS PYTHONHASHSEED
BABEL_DISABLE_CACHE NODE_DISABLE_COMPILE_CACHE
""".split())


def measured_env(task):
    module = ast.parse((Path(task) / "environment/workload/task.py").read_text())
    values = [ast.literal_eval(node.value) for node in module.body
              if isinstance(node, ast.Assign) and
              any(isinstance(target, ast.Name) and target.id == "ENV" for target in node.targets)]
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("Protected task ENV must be one literal dictionary")
    return {key: value for key, value in values[0].items() if key in RUNTIME_KNOBS}


def development_env(task):
    source = (Path(task) / "environment/Dockerfile").read_text()
    stages, current = {}, {}
    for line in re.sub(r"\\\n", " ", source).splitlines():
        line = line.lstrip()
        if not re.match(r"^(?:FROM|ENV)\s", line, re.I):
            continue
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        if tokens[0].upper() == "FROM":
            base = tokens[1]
            current = dict(stages.get(base, {}))
            if len(tokens) >= 4 and tokens[-2].upper() == "AS":
                stages[tokens[-1]] = current
        elif tokens[0].upper() == "ENV":
            fields = tokens[1:]
            if fields and "=" not in fields[0]:
                fields = [fields[0] + "=" + " ".join(fields[1:])]
            for field in fields:
                if "=" in field:
                    key, value = field.split("=", 1)
                    if key in RUNTIME_KNOBS:
                        current[key] = value
    return current


def audit(task):
    measured, development = measured_env(task), development_env(task)
    return {"task": Path(task).name, "measured": measured, "development": development,
            "matched": measured == development,
            "not_inferred": ["agent live affinity", "runtime parallelism of irrelevant language knobs",
                             "external base-image/environment overrides"]}
