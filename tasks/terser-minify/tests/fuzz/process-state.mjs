// Verifier-owned procfs observations. No thread-name/runtime-pool whitelist.
import fs from "node:fs";
import crypto from "node:crypto";
import v8 from "node:v8";

const sha = value => crypto.createHash("sha256").update(value).digest("hex");
const vanished = error => ["ENOENT", "ESRCH"].includes(error.code);
export function parseStat(text) {
    const end = text.lastIndexOf(")");
    if (end < 0) throw new Error("Malformed proc stat");
    const prefix = text.slice(0, text.indexOf(" "));
    const fields = text.slice(end + 2).trim().split(/\s+/);
    if (fields.length < 20) throw new Error("Incomplete proc stat");
    const tid = Number(prefix), start_ticks = Number(fields[19]);
    if (!Number.isSafeInteger(tid) || !Number.isSafeInteger(start_ticks)) {
        throw new Error("Unrepresentable kernel identity");
    }
    return { tid, start_ticks, state: fields[0], flags: Number(fields[6]) };
}
function identity(statPath, statusPath) {
    const result = parseStat(fs.readFileSync(statPath, "utf8"));
    if (["Z", "X", "x"].includes(result.state) || (result.flags & 4)) return null;
    const status = fs.readFileSync(statusPath, "utf8");
    const tgid = /^Tgid:\s+(\d+)$/m.exec(status);
    return { ...result, tgid: tgid ? Number(tgid[1]) : null, scope_pid: process.pid };
}
function runtimeFingerprint() {
    return sha(JSON.stringify({
        env: Object.fromEntries(Object.entries(process.env).sort(([a], [b]) => a.localeCompare(b))),
        execArgv: process.execArgv, heap_limit: v8.getHeapStatistics().heap_size_limit,
        umask: process.umask(), cwd: process.cwd(),
        listeners: Object.fromEntries(["SIGINT", "SIGTERM", "uncaughtException", "unhandledRejection"]
            .map(name => [name, process.listenerCount(name)])),
    }));
}
export function captureState(phase) {
    const output = {
        format: "optimbench-terser-process-snapshot-v2", phase,
        method: "procfs-task-stat22-status-tgid;fd-readlink-sha256;all-task-children",
        scope_pid: process.pid, sample_clock: {
            start_monotonic_ns: process.hrtime.bigint().toString(),
            start_wall_ms: Date.now(),
        },
        thread_identities: [], fd_identities: [], child_identities: [],
        threads_available: true, fds_available: true, children_available: true,
        unavailable: [], vanished_during_sample: [], dead_states_ignored: [],
    };
    let tids = [];
    try { tids = fs.readdirSync("/proc/self/task"); }
    catch (error) {
        output.threads_available = output.children_available = false;
        output.unavailable.push({ resource: "tasks", code: error.code || error.name });
    }
    const childIds = new Set();
    for (const tid of tids) {
        try {
            const item = identity(`/proc/self/task/${tid}/stat`, `/proc/self/task/${tid}/status`);
            if (item) output.thread_identities.push(item);
            else output.dead_states_ignored.push({ scope: "thread", tid: Number(tid) });
        } catch (error) {
            if (vanished(error)) output.vanished_during_sample.push({ scope: "thread", tid: Number(tid) });
            else {
                output.threads_available = false;
                output.unavailable.push({ resource: "thread", tid: Number(tid), code: error.code || error.name });
            }
        }
        try {
            const children = fs.readFileSync(`/proc/self/task/${tid}/children`, "utf8").trim();
            for (const pid of children.split(/\s+/).filter(Boolean)) childIds.add(pid);
        } catch (error) {
            if (!vanished(error)) {
                output.children_available = false;
                output.unavailable.push({ resource: "children", tid: Number(tid), code: error.code || error.name });
            }
        }
    }
    for (const pid of childIds) {
        try {
            const item = identity(`/proc/${pid}/stat`, `/proc/${pid}/status`);
            if (item) output.child_identities.push({ ...item, pid: item.tid });
            else output.dead_states_ignored.push({ scope: "child", pid: Number(pid) });
        } catch (error) {
            if (vanished(error)) output.vanished_during_sample.push({ scope: "child", pid: Number(pid) });
            else {
                output.children_available = false;
                output.unavailable.push({ resource: "child", pid: Number(pid), code: error.code || error.name });
            }
        }
    }
    try {
        for (const fd of fs.readdirSync("/proc/self/fd")) {
            try {
                const target = fs.readlinkSync(`/proc/self/fd/${fd}`);
                output.fd_identities.push({ fd: Number(fd), target_sha256: sha(target) });
            } catch (error) {
                if (!vanished(error)) {
                    output.fds_available = false;
                    output.unavailable.push({ resource: "fd", fd: Number(fd), code: error.code || error.name });
                }
            }
        }
    } catch (error) {
        output.fds_available = false;
        output.unavailable.push({ resource: "fds", code: error.code || error.name });
    }
    output.thread_identities.sort((a, b) => a.tid - b.tid);
    output.child_identities.sort((a, b) => a.pid - b.pid);
    output.fd_identities.sort((a, b) => a.fd - b.fd);
    // Counts derive from these exact sampled lists, never a separate query.
    output.threads = output.threads_available ? output.thread_identities.length : null;
    output.children = output.children_available ? output.child_identities.length : null;
    output.fds = output.fds_available ? output.fd_identities.length : null;
    output.runtime_configuration_sha256 = runtimeFingerprint();
    output.sample_clock.end_monotonic_ns = process.hrtime.bigint().toString();
    output.sample_clock.end_wall_ms = Date.now();
    return output;
}
export function compareState(before, after) {
    const result = {};
    for (const [resource, field, key] of [
        ["threads", "thread_identities", x => `${x.tid}:${x.start_ticks}`],
        ["fds", "fd_identities", x => `${x.fd}:${x.target_sha256}`],
        ["children", "child_identities", x => `${x.pid}:${x.start_ticks}`],
    ]) {
        const a = before[field].map(key), b = after[field].map(key);
        result[resource] = {
            available: before[resource + "_available"] && after[resource + "_available"],
            added_count: b.filter(x => !a.includes(x)).length,
            removed_count: a.filter(x => !b.includes(x)).length,
        };
    }
    result.runtime_configuration_changed =
        before.runtime_configuration_sha256 !== after.runtime_configuration_sha256;
    return result;
}
