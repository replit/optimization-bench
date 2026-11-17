"""Linux per-public-call resource identities, not a process-exit-only count."""
from pathlib import Path
import hashlib
import os
import time

DEAD_STATES = {"Z", "X", "x"}
# Linux include/linux/sched.h: task is leaving through do_exit(). A successful
# pthread_join can precede its final /proc removal, even while state is R.
PF_EXITING = 0x00000004


def _stat_identity(text):
    fields = text.rsplit(")", 1)[1].split()
    flags = int(fields[6])  # proc_pid_stat(5), field 9; state is field 3.
    if flags < 0:
        raise ValueError("negative kernel task flags")
    return {"pid": int(text.split(" ", 1)[0]), "state": fields[0], "flags": flags,
            "ppid": int(fields[1]), "start_ticks": int(fields[19])}


def _live_threads(root, scope_pid, excluded_exiting=None):
    identities = []
    if excluded_exiting is None:
        excluded_exiting = []
    def row(identity, tgid=None):
        return {"tid": identity["pid"], "start_ticks": identity["start_ticks"],
                "state": identity["state"], "flags": identity["flags"],
                "tgid": tgid, "scope_pid": scope_pid}
    for task in list((root / "task").iterdir()):
        if not task.name.isdigit():
            continue
        try:
            identity = _stat_identity((task / "stat").read_text())
            if identity["flags"] & PF_EXITING:
                excluded_exiting.append(row(identity))
                continue
            if identity["state"] in DEAD_STATES:
                continue
            tgid = None
            try:
                with (task / "status").open() as status:
                    for line in status:
                        if line.startswith("Tgid:"):
                            tgid = int(line.split()[1])
                            break
            except (OSError, ValueError):
                # Tgid is optional; kernel stat/start time is not.
                pass
            # Confirm live state and identity after the optional status read.
            current = _stat_identity((task / "stat").read_text())
            if (current["pid"], current["start_ticks"]) != (identity["pid"], identity["start_ticks"]):
                raise OSError("thread identity changed during sample")
            if current["flags"] & PF_EXITING:
                excluded_exiting.append(row(current, tgid))
                continue
            if current["state"] in DEAD_STATES:
                continue
            identities.append(row(current, tgid))
        except FileNotFoundError:
            if task.exists():
                raise OSError("thread identity file unsupported for visible task")
            # The task genuinely vanished between enumeration and observation.
    if not identities:
        raise OSError("no concrete live identity for executing worker")
    return sorted(identities, key=lambda row: row["tid"])


class ChildListUnsupported(OSError):
    pass


def _task_children(pid):
    base = Path("/proc") / str(pid) / "task"
    children = []
    read_any = False
    for task in list(base.iterdir()):
        try:
            children.extend(int(n) for n in (task / "children").read_text().split())
            read_any = True
        except FileNotFoundError:
            if task.exists():
                raise ChildListUnsupported("kernel task children list unavailable")
    if not read_any:
        raise ChildListUnsupported("no readable task children lists")
    return children


def children_by_ppid(parent, proc=Path("/proc")):
    """Fallback inside the controller's private PID namespace; no cmdline/env."""
    rows = {}
    for base in proc.iterdir():
        if not base.name.isdigit():
            continue
        try:
            stat = (base / "stat").read_text()
            fields = stat.rsplit(")", 1)[1].split()
            proc_pid = int(stat.split(" ", 1)[0])
            rows[proc_pid] = {"pid": proc_pid, "ppid": int(fields[1]),
                              "start_ticks": int(fields[19]), "state": fields[0]}
        except FileNotFoundError:
            continue  # A process vanished during the non-atomic snapshot.
    if parent not in rows:
        raise ChildListUnsupported("parent process not visible in proc-stat namespace")
    todo, seen, result = [parent], {parent}, []
    while todo:
        current = todo.pop()
        for pid, row in rows.items():
            if row["ppid"] == current and pid not in seen:
                seen.add(pid)
                if len(seen) > 4096:
                    raise OSError("descendant snapshot bound exceeded")
                todo.append(pid)
                if row["state"] not in DEAD_STATES:
                    result.append({k: v for k, v in row.items() if k != "ppid"})
    return sorted(result, key=lambda row: row["pid"])


def validate_snapshot(facts):
    """Missing identities are pending; an identity/count mismatch is invalid."""
    identities = facts.get("thread_identities")
    if identities is None:
        return {"complete": False, "reason": "thread_identity_unavailable"}
    if not isinstance(identities, list):
        raise ValueError("invalid thread identity list")
    seen = set()
    missing_flags = False
    for row in identities:
        if not isinstance(row, dict) or any(type(row.get(k)) is not int for k in
                                            ("tid", "start_ticks", "scope_pid")):
            raise ValueError("malformed concrete thread identity")
        if row["tid"] <= 0 or row["start_ticks"] < 0 or row["scope_pid"] <= 0:
            raise ValueError("invalid concrete thread identity")
        if row.get("state") in DEAD_STATES or not isinstance(row.get("state"), str):
            raise ValueError("non-live thread in live identity list")
        if "flags" not in row:
            missing_flags = True
        elif type(row["flags"]) is not int or row["flags"] < 0 or row["flags"] & PF_EXITING:
            raise ValueError("invalid or exiting thread in live identity list")
        if row["tid"] in seen:
            raise ValueError("duplicate live thread identity")
        seen.add(row["tid"])
    if (type(facts.get("threads")) is not int or facts["threads"] != len(identities)
            or type(facts.get("counts", {}).get("threads")) is not int
            or facts["counts"]["threads"] != len(identities)):
        raise ValueError("thread count/identity mismatch")
    clock = facts.get("sample_clock")
    if not isinstance(clock, dict):
        return {"complete": False, "reason": "sample_clock_unavailable"}
    if (any(type(clock.get(k)) is not int for k in ("start_ns", "end_ns"))
            or not 0 <= clock["start_ns"] <= clock["end_ns"]
            or not isinstance(clock.get("method"), str) or not clock["method"]
            or not isinstance(facts.get("phase"), str) or not facts["phase"]):
        raise ValueError("invalid sample clock/phase")
    if missing_flags:
        return {"complete": False, "reason": "thread_flags_unavailable"}
    return {"complete": True}


def snapshot(phase="sample"):
    start = time.monotonic_ns()
    root = Path("/proc/self")
    try:
        proc_pid = int((root / "stat").read_text().split(" ", 1)[0])
        thread_error = None
        excluded_exiting_threads = []
        try:
            thread_identities = _live_threads(root, proc_pid, excluded_exiting_threads)
        except (OSError, ValueError, IndexError) as error:
            thread_identities, thread_error = None, type(error).__name__
        thread_count = len(thread_identities) if thread_identities is not None else None
        fds = []
        for p in list((root / "fd").iterdir()):
            try:
                target = os.readlink(p)
                digest = hashlib.sha256(os.fsencode(target)).hexdigest()
                fds.append({"fd": int(p.name), "target": "sha256:" + digest, "target_sha256": digest})
            except FileNotFoundError:
                pass  # The observer's own already-closed directory descriptor.
        fds.sort(key=lambda row: row["fd"])
        result = {"schema": "per-call-live-identities-v2", "available": thread_identities is not None,
                  "pid": os.getpid(), "proc_pid": proc_pid, "phase": phase,
                  "raw_method": "proc-self/task-stat-status+fd-link-sha256+descendants",
                  "thread_method": "proc-task-stat-starttime-live-state-flags-pf-exiting",
                  "fds": fds, "threads": thread_count, "thread_identities": thread_identities,
                  "excluded_exiting_threads": sorted(excluded_exiting_threads, key=lambda r: r["tid"]),
                  "thread_error": thread_error,
                  "availability": {"fds": True, "threads": thread_identities is not None, "children": True}}
        try:
            try:
                todo = _task_children(proc_pid)
                children, seen = [], set()
                while todo:
                    pid = todo.pop()
                    if pid in seen:
                        continue
                    if len(seen) >= 4096:
                        raise OSError("descendant snapshot bound exceeded")
                    seen.add(pid)
                    base = Path("/proc") / str(pid)
                    try:
                        stat = (base / "stat").read_text().rsplit(")", 1)[1].split()
                        if stat[0] not in DEAD_STATES:
                            children.append({"pid": pid, "start_ticks": int(stat[19]), "state": stat[0]})
                        todo.extend(_task_children(pid))
                    except FileNotFoundError:
                        pass
                result["children_method"] = "task-children"
            except (ChildListUnsupported, FileNotFoundError):
                children = children_by_ppid(proc_pid)
                result["children_method"] = "proc-stat-ppid-fallback"
            result["children"] = sorted(children, key=lambda row: row["pid"])
            result["counts"] = {"fds": len(fds), "threads": thread_count, "children": len(children)}
        except (OSError, ValueError, IndexError) as error:
            result.update(available=False, children=None, reason=type(error).__name__)
            result["availability"]["children"] = False
            result["counts"] = {"fds": len(fds), "threads": thread_count, "children": None}
        result["sample_clock"] = {"method": "time.monotonic_ns", "scope_pid": os.getpid(),
                                  "start_ns": start, "end_ns": time.monotonic_ns()}
        validate_snapshot(result)
        return result
    except (OSError, ValueError, IndexError) as error:
        return {"schema": "per-call-live-identities-v2", "available": False,
                "phase": phase, "thread_identities": None, "threads": None,
                "counts": {"fds": None, "threads": None, "children": None},
                "availability": {"fds": False, "threads": False, "children": False},
                "reason": type(error).__name__,
                "sample_clock": {"method": "time.monotonic_ns", "scope_pid": os.getpid(),
                                 "start_ns": start, "end_ns": time.monotonic_ns()}}


def observe(call):
    before = snapshot("before")
    try:
        value = call()
        outcome = {"kind": "ok", "value": value}
    except Exception as error:
        outcome = {"kind": "error", "class": type(error).__module__ + "." + type(error).__qualname__,
                   "message": str(error)}
    after = snapshot("after")
    return outcome, {"before": before, "after": after}
