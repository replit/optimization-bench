"""Partition-independent legal Python binary I/O schedules.

Err accepts zero bytes. Partial progress is a successful short read/write,
followed by an error on the next nonempty call. EINTR fires at most once.
These wrappers do not decide which library APIs must retry/flush/propagate.
"""
from dataclasses import dataclass
import errno
import io

KINDS = {"Other": errno.EIO, "BrokenPipe": errno.EPIPE,
         "PermissionDenied": errno.EACCES, "UnexpectedEof": errno.EIO}


@dataclass(frozen=True)
class FaultPlan:
    max_chunk: int | None = None
    fail_after: int | None = None
    zero_after: int | None = None
    eof_after: int | None = None
    interrupt_at: int | None = None
    error_kind: str = "Other"
    flush_error: bool = False

    def validate(self, direction):
        if direction not in {"read", "write"} or self.error_kind not in KINDS:
            raise ValueError("unsupported direction or public I/O error kind")
        for name in ("max_chunk", "fail_after", "zero_after", "eof_after", "interrupt_at"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < (1 if name == "max_chunk" else 0)):
                raise ValueError("invalid logical byte boundary")
        if sum(getattr(self, name) is not None for name in ("fail_after", "zero_after", "eof_after")) > 1:
            raise ValueError("contradictory terminal fault schedules")
        if direction == "write" and self.eof_after is not None:
            raise ValueError("EOF is a reader observation")
        if direction == "read" and self.flush_error:
            raise ValueError("flush is a writer operation")
        if type(self.flush_error) is not bool:
            raise ValueError("flush_error must be bool")


class _Schedule:
    def __init__(self, plan, direction):
        plan.validate(direction)
        self.plan, self.position, self.interrupted = plan, 0, False
        self.call_count = 0
        self.error_count = 0
        self.calls = []

    def length(self, requested):
        self.call_count += 1
        if not requested:
            return 0
        p = self.plan
        if p.interrupt_at is not None and not self.interrupted and self.position >= p.interrupt_at:
            self.interrupted = True
            self.error_count += 1
            raise InterruptedError(errno.EINTR, "verifier one-shot interruption")
        if p.fail_after is not None and self.position >= p.fail_after:
            self.error_count += 1
            raise OSError(KINDS[p.error_kind], "verifier scheduled I/O error")
        if p.zero_after is not None and self.position >= p.zero_after:
            return 0
        if p.eof_after is not None and self.position >= p.eof_after:
            return 0
        n = min(requested, p.max_chunk if p.max_chunk is not None else requested)
        for boundary in (p.fail_after, p.zero_after, p.eof_after,
                         p.interrupt_at if not self.interrupted else None):
            if boundary is not None and boundary > self.position:
                n = min(n, boundary - self.position)
        return n

    def accepted(self, requested, n):
        self.position += n
        if len(self.calls) < 256:
            self.calls.append({"requested": requested, "accepted": n})


class FaultWriter(io.RawIOBase):
    def __init__(self, plan=FaultPlan()):
        super().__init__()
        self.schedule = _Schedule(plan, "write")
        self.accepted_bytes = bytearray()

    def writable(self):
        return True

    def write(self, data):
        if self.closed:
            raise ValueError("write to closed stream")
        data = bytes(data)
        n = self.schedule.length(len(data))
        self.accepted_bytes.extend(data[:n])
        self.schedule.accepted(len(data), n)
        return n

    def flush(self):
        if getattr(self, "_verifier_cleanup", False) or not hasattr(self, "schedule"):
            return
        if self.closed:
            raise ValueError("flush of closed stream")
        if self.schedule.plan.flush_error:
            self.schedule.error_count += 1
            raise OSError(KINDS[self.schedule.plan.error_kind], "verifier scheduled flush error")

    def __del__(self):
        # Verifier teardown must not invent a library flush call or leak a
        # destructor exception to the observation channel. Explicit public
        # flush()/close() calls still exercise the configured fault normally.
        self._verifier_cleanup = True
        try:
            self.close()
        except Exception:
            pass


class FaultReader(io.RawIOBase):
    def __init__(self, data, plan=FaultPlan()):
        super().__init__()
        self.data = bytes(data)
        self.schedule = _Schedule(plan, "read")

    def readable(self):
        return True

    def readinto(self, output):
        if self.closed:
            raise ValueError("read of closed stream")
        n = min(self.schedule.length(len(output)), len(self.data) - self.schedule.position)
        output[:n] = self.data[self.schedule.position:self.schedule.position + n]
        self.schedule.accepted(len(output), n)
        return n
