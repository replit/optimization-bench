"""Render a Task's agent prompt from its instruction.md.njk template.

Each Task's instruction.md is the default rendering of instruction.md.njk, with the
values configured in its task.toml. Re-rendering with other values rewrites the local
instruction.md in place; the committed copy always stays the default rendering.

    python3 -m tools.render_instruction --all --set agent_timeout=30m   # every Task, 30 minutes
    python3 -m tools.render_instruction --task sqlglot-mozilla-transpile --set agent_timeout=10m
    python3 -m tools.render_instruction --all                           # back to the defaults

Templates use Jinja/Nunjucks-compatible `{{ name }}` placeholders, so other harnesses
can render them directly. Variables:

    agent_timeout  agent time limit (default: task.toml [agent] timeout_sec). Accepts
                   seconds (600) or a duration (90m, 2h). Renders two placeholders:
                   {{ agent_timeout_seconds }} -> "7,200" and {{ agent_timeout }} -> "2 hours".
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "instruction.md.njk"
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def parse_duration(value):
    """Seconds from 600, 600s, 90m or 2h."""
    match = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", str(value))
    if not match or int(match[1]) <= 0:
        raise ValueError(f"invalid agent_timeout {value!r}; use seconds (600) or 90m / 2h")
    return int(match[1]) * {"": 1, "s": 1, "m": 60, "h": 3600}[match[2]]


def human_duration(seconds):
    for size, unit in ((3600, "hour"), (60, "minute")):
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds:,} seconds"


def defaults(task):
    config = tomllib.loads((task / "task.toml").read_text())
    return {"agent_timeout": int(config["agent"]["timeout_sec"])}


def placeholders(values):
    """Template placeholder text for resolved variable values."""
    seconds = parse_duration(values["agent_timeout"])
    return {"agent_timeout_seconds": f"{seconds:,}", "agent_timeout": human_duration(seconds)}


def render(template, values):
    table = placeholders(values)

    def substitute(match):
        if match[1] not in table:
            raise ValueError(f"unknown template variable {match[1]!r}")
        return table[match[1]]

    return PLACEHOLDER.sub(substitute, template)


def templatize(text, values):
    """Template for an instruction rendered with `values` (used by tools/update_catalog.py)."""
    if PLACEHOLDER.search(text):
        raise ValueError("instruction already contains template placeholders")
    table = placeholders(values)
    seconds, human = re.escape(table["agent_timeout_seconds"]), re.escape(table["agent_timeout"])
    template = re.sub(rf"\b{seconds}-second agent timeout(\s+)\({human}\)",
                      r"{{ agent_timeout_seconds }}-second agent timeout\1({{ agent_timeout }})", text)
    template = re.sub(rf"\bup to {human} \({seconds} seconds\)",
                      "up to {{ agent_timeout }} ({{ agent_timeout_seconds }} seconds)", template)
    if render(template, values) != text:
        raise ValueError("template does not reproduce the instruction")
    return template


def task_dirs(names, everything):
    if everything:
        return sorted(path.parent for path in (ROOT / "tasks").glob(f"*/{TEMPLATE}"))
    paths = []
    for name in names:
        path = (ROOT / "tasks" / name).resolve()
        if path.parent != (ROOT / "tasks").resolve() or not (path / TEMPLATE).is_file():
            raise ValueError(f"no {TEMPLATE} for task {name!r}")
        paths.append(path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--task", action="append", default=[], help="Task to render; repeatable")
    scope.add_argument("--all", action="store_true", help="Render every Task")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="Override a variable, e.g. agent_timeout=30m")
    parser.add_argument("--stdout", action="store_true", help="Print instead of rewriting instruction.md")
    args = parser.parse_args(argv)
    overrides = {}
    for item in args.set:
        name, sep, value = item.partition("=")
        if not sep or name not in ("agent_timeout",):
            parser.error(f"unknown or malformed --set {item!r}; available: agent_timeout")
        overrides[name] = value
    try:
        for task in task_dirs(args.task, args.all):
            text = render((task / TEMPLATE).read_text(), {**defaults(task), **overrides})
            if args.stdout:
                sys.stdout.write(text)
            else:
                (task / "instruction.md").write_text(text)
                print(f"{task.name}: instruction.md rendered "
                      f"({placeholders({**defaults(task), **overrides})['agent_timeout']})")
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
