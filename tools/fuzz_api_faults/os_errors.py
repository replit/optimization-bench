"""Conservative OS-error stderr facts, never equivalence or verdict policy.

Only a single terminal Python exception or a single Node system-error header is
recognized. Ambiguous/multiple reports remain unclassified. Paths are decoded,
not normalized, redacted, or assigned an input/output/internal role.
"""

import ast
import re


_PY_CLASSES = (
    "OSError", "IOError", "EnvironmentError", "BlockingIOError",
    "ChildProcessError", "ConnectionError", "BrokenPipeError",
    "ConnectionAbortedError", "ConnectionRefusedError", "ConnectionResetError",
    "FileExistsError", "FileNotFoundError", "InterruptedError",
    "IsADirectoryError", "NotADirectoryError", "PermissionError",
    "ProcessLookupError", "TimeoutError",
)
_PY_ERROR = re.compile(
    r"^(?P<class>" + "|".join(_PY_CLASSES) + r"): \[Errno (?P<errno>\d+)\] (?P<body>.+)$"
)
_STRING = r"""(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""
_PY_PATH = re.compile(
    r": (?P<path>" + _STRING + r")(?:(?: -> )(?P<destination>" + _STRING + r"))?$"
)
_FRAME = re.compile(r'^\s+File "(?P<path>[^"]+)", line \d+, in (?P<function>\w+)$')
_STDLIB_TEMPFILE = re.compile(
    r"(?:^|/)lib(?:64)?/python\d+\.\d+/tempfile\.py$|"
    r"(?:^|[\\/])Lib[\\/]tempfile\.py$"
)
# Node prints a stable symbolic system code, not an arbitrary Error message.
_NODE_ERROR = re.compile(
    r"^(?P<class>Error): (?P<code>"
    r"EACCES|EPERM|EROFS|ENOENT|EEXIST|ENOTDIR|EISDIR|EMFILE|ENFILE|"
    r"ENOSPC|EIO|EPIPE|EBADF|EINVAL|EINTR|ENOTEMPTY|EXDEV|ELOOP"
    r"): (?P<body>.+)$"
)
_NODE_OPERATION = re.compile(
    r"^[^,\n]+, (?P<operation>open|read|write|stat|lstat|access|mkdir|"
    r"rmdir|unlink|rename|copyfile|scandir|chmod|chown|realpath)"
    r" (?P<path>" + _STRING + r")"
    r"(?:(?: -> )(?P<destination>" + _STRING + r"))?$"
)


def _paths(match):
    if match is None:
        return None, None
    try:
        path = ast.literal_eval(match["path"])
        destination = match["destination"]
        destination = ast.literal_eval(destination) if destination else None
    except (ValueError, SyntaxError):
        return None, None
    return path, destination


def parse_os_error(stderr: str) -> dict | None:
    """Return raw facts for strongly recognized stderr, otherwise ``None``.

    ``internal_temp_frame`` means only that the same terminal Python traceback
    contains a stdlib tempfile ``_mkstemp_inner`` frame. It does NOT establish a
    path role or authorize ignoring a filename. Chained/multiple errors are not
    collapsed. Unknown operations are explicitly ``unreported``. Numeric errno
    and symbolic code are never translated using the observer host's OS.
    """
    if not isinstance(stderr, str):
        raise TypeError("stderr must be text")
    lines = stderr.splitlines()
    nonempty = [line for line in lines if line.strip()]
    if not nonempty:
        return None
    python_errors = [(i, _PY_ERROR.fullmatch(line)) for i, line in enumerate(lines)]
    python_errors = [(i, match) for i, match in python_errors if match]
    node_errors = [_NODE_ERROR.fullmatch(line) for line in lines]
    node_errors = [match for match in node_errors if match]
    if len(python_errors) + len(node_errors) != 1:
        return None
    internal = False
    operation = "unreported"
    if python_errors:
        index, match = python_errors[0]
        # A traceback ends at its exception, not a later log or another error.
        if lines[index] != nonempty[-1]:
            return None
        traceback_starts = [
            i for i, line in enumerate(lines[:index])
            if line == "Traceback (most recent call last):"
        ]
        if len(traceback_starts) > 1:
            return None
        if traceback_starts:
            for line in lines[traceback_starts[0] + 1:index]:
                frame = _FRAME.fullmatch(line)
                if (frame and frame["function"] == "_mkstemp_inner"
                        and _STDLIB_TEMPFILE.search(frame["path"])):
                    internal = True
        path, destination = _paths(_PY_PATH.search(match["body"]))
        number, code = int(match["errno"]), None
    else:
        match = node_errors[0]
        # Require the header first and only stack frames after it. Structured
        # Node object dumps are deliberately unsupported rather than ignoring
        # potentially conflicting errno/code/path fields.
        if match[0] != nonempty[0]:
            return None
        for line in nonempty[1:]:
            if not re.fullmatch(r"\s+at .+", line):
                return None
        operation_match = _NODE_OPERATION.fullmatch(match["body"])
        path, destination = _paths(operation_match)
        if operation_match:
            operation = operation_match["operation"]
        number, code = None, match["code"]
    return {
        "exception_class": match["class"],
        "errno": number,
        "code": code,
        "operation_category": operation,
        "path": path,
        "destination_path": destination,
        "internal_temp_frame": internal,
        "rawmessage": stderr,
    }
