import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { captureState, compareState, parseStat } from "./process-state.mjs";

test("concrete live proc identities and same-list counts", () => {
    const value = captureState("unit-test");
    assert.equal(value.threads_available, true);
    assert.equal(value.threads, value.thread_identities.length);
    assert.equal(value.children, value.child_identities.length);
    assert.equal(value.fds, value.fd_identities.length);
    assert.ok(value.thread_identities.some(x => x.tid === process.pid && x.tgid === process.pid));
    for (const item of value.thread_identities) {
        assert.ok(Number.isInteger(item.start_ticks) && item.start_ticks > 0);
        assert.ok(!["Z", "X", "x"].includes(item.state));
        assert.equal(item.scope_pid, process.pid);
    }
    assert.ok(BigInt(value.sample_clock.end_monotonic_ns) >= BigInt(value.sample_clock.start_monotonic_ns));
});
test("fd identities hash paths and detect retained descriptor", () => {
    const before = captureState("before-test-fd");
    const fd = fs.openSync("/dev/null", "r");
    try {
        const after = captureState("after-test-fd");
        assert.ok(after.fd_identities.every(x => /^[a-f0-9]{64}$/.test(x.target_sha256)));
        assert.ok(!JSON.stringify(after.fd_identities).includes("/dev/null"));
        assert.equal(compareState(before, after).fds.added_count, 1);
    } finally {
        fs.closeSync(fd);
    }
});
test("stat parsing survives whitespace and parentheses in comm", () => {
    const fields = ["R", ...Array(18).fill("0"), "12345", ...Array(5).fill("0")];
    assert.deepEqual(parseStat("7 (a strange ) comm) " + fields.join(" ")),
                     { tid: 7, start_ticks: 12345, state: "R", flags: 0 });
});
