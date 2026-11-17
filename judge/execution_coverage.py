"""Untimed probe evidence. Missing, ambiguous or mismatched source never proves execution."""
import bisect
import hashlib
import heapq
import json
from pathlib import Path
import re
import ast
import dis
import sys
import threading
from urllib.parse import unquote, urlparse


class PythonCollector:
    """Candidate Python CALL/write opcode spans, not just a hit parent line."""
    def __init__(self, root, run_id):
        self.root, self.run_id = Path(root).resolve(), run_id
        self.files, self.codes, self.executed = {}, {}, {}

    def source(self, name):
        if name in self.files:
            return self.files[name]
        path = Path(name)
        if not path.is_file() or not path.resolve().is_relative_to(self.root) or path.suffix != ".py":
            self.files[name] = None
            return None
        data = path.read_bytes()
        starts = [0]
        for line in data.splitlines(keepends=True):
            starts.append(starts[-1] + len(line))
        def span(node):
            return (starts[node.lineno - 1] + node.col_offset,
                    starts[node.end_lineno - 1] + node.end_col_offset)
        allowed = {span(node) for node in ast.walk(ast.parse(data))
                   if isinstance(node, (ast.Call, ast.Assign, ast.AugAssign, ast.Delete))}
        self.files[name] = data, starts, allowed
        return self.files[name]

    def trace(self, frame, event, arg):
        name = frame.f_code.co_filename
        source = self.source(name)
        if source is None:
            return None
        if event == "call":
            frame.f_trace_opcodes = True
            # CPython 3.14 suppresses opcode delivery when line delivery is
            # disabled; line events themselves are never used as evidence.
            frame.f_trace_lines = True
        elif event == "opcode":
            self.instruction(frame.f_code, frame.f_lasti)
        return self.trace

    def instruction(self, code, offset):
        source = self.source(code.co_filename)
        if source is None:
            return
        instructions = self.codes.setdefault(code, None)
        if instructions is None:
            instructions = self.codes[code] = {i.offset: i for i in dis.get_instructions(code)}
        instruction = instructions.get(offset)
        if instruction is not None and (instruction.opname.startswith("CALL") or instruction.opname in
                                        ("STORE_ATTR", "STORE_SUBSCR", "DELETE_ATTR", "DELETE_SUBSCR")):
            p = instruction.positions
            if p.lineno is not None and p.end_lineno is not None and p.col_offset is not None and p.end_col_offset is not None:
                data, starts, allowed = source
                span = starts[p.lineno - 1] + p.col_offset, starts[p.end_lineno - 1] + p.end_col_offset
                if span in allowed:
                    self.executed.setdefault(code.co_filename, set()).add(span)

    def start(self):
        self.monitoring_id = None
        if hasattr(sys, "monitoring"):
            for identity in range(6):
                if sys.monitoring.get_tool(identity) is None:
                    sys.monitoring.use_tool_id(identity, "optimbench-untimed-coverage")
                    self.monitoring_id = identity
                    break
        if self.monitoring_id is not None:
            self.monitored_codes = set()
            def enter(code, offset):
                if self.source(code.co_filename) is not None:
                    self.monitored_codes.add(code)
                    sys.monitoring.set_local_events(self.monitoring_id, code, sys.monitoring.events.INSTRUCTION)
            sys.monitoring.register_callback(self.monitoring_id, sys.monitoring.events.PY_START, enter)
            sys.monitoring.register_callback(self.monitoring_id, sys.monitoring.events.INSTRUCTION, self.instruction)
            sys.monitoring.set_events(self.monitoring_id, sys.monitoring.events.PY_START)
            return
        self.previous, self.thread_previous = sys.gettrace(), threading.gettrace()
        # Some CPython versions enable opcode delivery only when a frame has
        # requested it before global tracing is installed.
        self.activation_frame = sys._getframe(1)
        self.activation_previous = self.activation_frame.f_trace_opcodes
        self.activation_frame.f_trace_opcodes = True
        sys._getframe().f_trace_opcodes = True
        sys.settrace(self.trace)
        threading.settrace(self.trace)

    def stop(self):
        if self.monitoring_id is not None:
            sys.monitoring.set_events(self.monitoring_id, 0)
            for code in self.monitored_codes:
                sys.monitoring.set_local_events(self.monitoring_id, code, 0)
            for event in (sys.monitoring.events.PY_START, sys.monitoring.events.INSTRUCTION):
                sys.monitoring.register_callback(self.monitoring_id, event, None)
            sys.monitoring.free_tool_id(self.monitoring_id)
        else:
            sys.settrace(self.previous)
            threading.settrace(self.thread_previous)
            self.activation_frame.f_trace_opcodes = self.activation_previous
            self.activation_frame = None
        return {"kind": "python", "run_id": self.run_id, "status": "collected",
                "files": [file_row(name, self.files[name][0], spans)
                          for name, spans in sorted(self.executed.items())]}


def unavailable(kind, run_id, reason):
    return {"kind": kind, "run_id": run_id, "status": "unavailable", "reason": reason, "files": []}


def file_row(path, data, spans):
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "executed_spans": [list(span) for span in sorted(set(spans)) if span[0] < span[1]]}


def positive_segments(ranges):
    """Subtract unexecuted nested blocks/functions, never trust a positive parent."""
    events = {}
    for index, (start, end, count) in enumerate(ranges):
        if start >= end:
            continue
        events.setdefault(start, []).append((True, index, end - start, count))
        events.setdefault(end, []).append((False, index, end - start, count))
    active, heap, result = set(), [], []
    positions = sorted(events)
    for offset, position in enumerate(positions[:-1]):
        for begin, index, length, count in events[position]:
            if begin:
                active.add(index)
                heapq.heappush(heap, (length, bool(count), index, count))
            else:
                active.discard(index)
        while heap and heap[0][2] not in active:
            heapq.heappop(heap)
        if heap and heap[0][3] > 0:
            result.append((position, positions[offset + 1]))
    return result


def node(scratch, build, run_id):
    directory = Path(scratch) / "v8-coverage"
    paths = sorted(directory.glob("*.json"))
    if not paths:
        return unavailable("node", run_id, "V8 emitted no coverage")
    rows = []
    for path in paths:
        for script in json.loads(path.read_text()).get("result", []):
            url = urlparse(script.get("url", ""))
            name = unquote(url.path)
            if url.scheme != "file" or not name.startswith("/app/build/"):
                continue
            relative = Path(name).relative_to("/app/build")
            target = Path(build) / relative
            if not target.resolve().is_relative_to(Path(build).resolve()) or not target.is_file():
                continue
            data = target.read_bytes()
            text = data.decode("utf8")
            utf16, utf8 = [0], [0]
            for char in text:
                utf16.append(utf16[-1] + len(char.encode("utf-16-le")) // 2)
                utf8.append(utf8[-1] + len(char.encode("utf8")))
            def byte(offset):
                index = bisect.bisect_left(utf16, offset)
                if index >= len(utf16) or utf16[index] != offset:
                    raise ValueError("V8 coverage offset is not a character boundary")
                return utf8[index]
            ranges = [(r["startOffset"], r["endOffset"], r["count"])
                      for function in script.get("functions", []) for r in function.get("ranges", [])]
            rows.append(file_row(name, data, [(byte(a), byte(b)) for a, b in positive_segments(ranges)]))
    return {"kind": "node", "run_id": run_id, "status": "collected", "files": rows}


def go(scratch, build, module, kind, run_id):
    profile = Path(scratch) / "go-cover.txt"
    if not profile.is_file():
        return unavailable(kind, run_id, "Go emitted no coverage profile")
    files, unmapped, entries = {}, set(), 0
    for line in profile.read_text().splitlines()[1:]:
        match = re.fullmatch(r"(.+):(\d+)\.(\d+),(\d+)\.(\d+)\s+\d+\s+(\d+)", line)
        if not match:
            raise ValueError("Malformed Go coverage block")
        name, sl, sc, el, ec, count = match.groups()
        entries += 1
        if name.startswith("/app/build/project/"):
            relative = name.removeprefix("/app/build/project/")
        elif name.startswith(module + "/"):
            relative = name[len(module) + 1:]
        else:
            unmapped.add(name)
            continue
        target = Path(build) / "project" / relative
        if not target.resolve().is_relative_to((Path(build) / "project").resolve()) or not target.is_file():
            unmapped.add(name)
            continue
        data = target.read_bytes()
        lines = data.splitlines(keepends=True)
        starts = [0]
        for raw in lines:
            starts.append(starts[-1] + len(raw))
        start, end = starts[int(sl) - 1] + int(sc) - 1, starts[int(el) - 1] + int(ec) - 1
        if not 0 <= start <= end <= len(data):
            raise ValueError("Go coverage position outside source")
        entry = files.setdefault(relative, (data, []))
        entry[1].append((start, end, int(count)))
    return {"kind": kind, "run_id": run_id, "status": "collected", "profile_blocks": entries,
            "unmapped_files": sorted(unmapped),
            "files": [file_row("project/" + name, data, positive_segments(ranges))
                      for name, (data, ranges) in sorted(files.items())]}


def candidate_executed(finding, state, kind):
    """Evidence must belong to this candidate probe and exact finding source."""
    evidence = state.get("execution_coverage", {}).get("candidate", {})
    run_id = state.get("candidate_probe_id")
    if (not run_id or evidence.get("run_id") != run_id or evidence.get("kind") != kind
            or evidence.get("status") != "collected"):
        return False
    digest, start, end = finding.get("source_sha256"), finding.get("start_byte"), finding.get("end_byte")
    if not digest or type(start) is not int or type(end) is not int or start >= end:
        return False
    # Equal files may occur twice in a build; require this digest's evidence to
    # cover the exact operation span, not merely a basename or a hit parent line.
    for row in evidence.get("files", []):
        if row.get("sha256") != digest:
            continue
        path = finding.get("path", "")
        names = [path, path.removeprefix("src/")]
        covered_path = row.get("path", "").replace("\\", "/")
        if not any(name and (covered_path == name or covered_path.endswith("/" + name)) for name in names):
            continue
        spans = sorted(row.get("executed_spans", []))
        covered = start
        for left, right in spans:
            if left <= covered < right:
                covered = right
            if covered >= end:
                return True
    return False
