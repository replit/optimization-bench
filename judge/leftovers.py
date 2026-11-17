"""PID-independent Rule D process identities and resident worker violations."""
import ast
from collections import Counter
from pathlib import Path
import re
import shlex


class _RuntimeInteger(ast.AST):
    _fields = ()


class _DescriptorNumbers(ast.NodeTransformer):
    def __init__(self, descriptors):
        self.descriptors = set(descriptors)

    def visit_Constant(self, node):
        # CPython's -c launchers embed runtime pipe/socket FDs as integers.
        # Retain names, strings and full code structure, not descriptor values.
        return (_RuntimeInteger() if type(node.value) is int and node.value in self.descriptors
                else node)


def command_identity(process):
    argv = process.get("argv")
    if argv and not argv[0]:
        raise ValueError("Live Rule D leftover has no command identity")
    if not argv:
        command = process.get("cmdline", "").strip()
        if not command:
            raise ValueError("Live Rule D leftover has no command identity")
        prefix, separator, code = command.partition(" -c ")
        argv = shlex.split(prefix) + ["-c", code] if separator else shlex.split(command)
    argv = list(argv)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(argv[0]).name) and "-c" in argv:
        index = argv.index("-c") + 1
        if index < len(argv):
            try:
                argv[index] = ast.dump(_DescriptorNumbers(process.get("ipc_fds", ())).visit(ast.parse(argv[index])),
                                       include_attributes=False)
            except SyntaxError:
                pass  # Preserve opaque command bytes; never discard a process.
    return tuple(argv)


def _worker_kind(process, index):
    numbers = ("pid", "process_pid", "tid", "tgid", "start_ticks", "flags",
               "sampled_monotonic_ns", "step_index", "threads")
    fields = ("tid", "tgid", "start_ticks", "state", "flags", "sampled_monotonic_ns")
    identities = process.get("thread_identities")
    kind = process.get("kind")
    if (kind not in ("thread", "process")
            or any(type(process.get(key)) is not int or process[key] < 0 for key in numbers)
            or not all(process[key] > 0 for key in ("pid", "process_pid", "tid", "tgid"))
            or process["pid"] != process["tid"] or process["process_pid"] != process["tgid"]
            or (process["tid"] != process["tgid"]) != (kind == "thread")
            or process["threads"] != 1 or process["step_index"] != index
            or not isinstance(process.get("state"), str) or len(process["state"]) != 1
            or process["flags"] & 0x00000004
            or process.get("reason") != "worker outlived measured operation"
            or process.get("observation_boundary") != "resident-result-stop"
            or not isinstance(identities, list) or len(identities) != 1
            or not isinstance(identities[0], dict)
            or any(type(identities[0].get(key)) is not type(process[key])
                   or identities[0].get(key) != process[key] for key in fields)):
        raise ValueError("Invalid resident Rule D worker identity")
    return kind


def resident_workers(steps):
    workers = Counter()
    for index, step in enumerate(steps):
        for process in step.leftovers:
            if process.get("state") in ("Z", "X"):
                continue
            if any(key in process for key in ("kind", "tid", "process_pid")):
                workers[(index, _worker_kind(process, index))] += 1
    return [{"step": index, "kind": kind, "threads": 1, "count": count}
            for (index, kind), count in sorted(workers.items())]


def classification(steps):
    counts = Counter()
    for index, step in enumerate(steps):
        for process in step.leftovers:
            # Same exclusion as the shared Rule D snapshot: dead processes
            # cannot do useful work. Missing state is not assumed to be dead.
            if (process.get("state") in ("Z", "X")
                    or any(key in process for key in ("kind", "tid", "process_pid"))):
                continue
            counts[(index, command_identity(process), process.get("threads", 1))] += 1
    return [{"step": index, "command": list(command), "threads": threads, "count": count}
            for (index, command, threads), count in sorted(counts.items())] + resident_workers(steps)
