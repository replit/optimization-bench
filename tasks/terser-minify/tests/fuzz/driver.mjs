// Verifier-owned, long-lived worker. Never restart/import-bust between requests.
// stdin: one JSON case per line; fd 3: independent typed observer JSONL.
import fs from "node:fs";
import readline from "node:readline";
import crypto from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { captureState, compareState } from "./process-state.mjs";

const stringify = JSON.stringify.bind(JSON);
const parse = JSON.parse.bind(JSON);
const argv = process.argv.slice(2);
function flag(name) {
    const index = argv.indexOf(name);
    if (index < 0 || !argv[index + 1]) throw new Error("Missing verifier flag " + name);
    return argv[index + 1];
}
const project = path.resolve(flag("--project"));
const buildSha = flag("--build-sha256");
if (!/^[0-9a-f]{64}$/.test(buildSha)) throw new Error("Malformed build SHA-256");
const observerFd = argv.includes("--observer-fd") ? Number(flag("--observer-fd")) : 3;
if (!Number.isSafeInteger(observerFd) || observerFd < 3) throw new Error("Invalid observer fd");
fs.fstatSync(observerFd); // Never mix candidate stdout with observer output.
const require = createRequire(path.join(project, "main.js"));
const esm = await import(pathToFileURL(path.join(project, "main.js")).href);
const cjs = require(path.join(project, "dist/bundle.min.js"));
const modules = { import: esm, require: cjs };
let requestCount = 0;
function codePointOrder(a, b) {
    const left = Array.from(a, x => x.codePointAt(0));
    const right = Array.from(b, x => x.codePointAt(0));
    for (let i = 0; i < Math.min(left.length, right.length); i++) {
        if (left[i] !== right[i]) return left[i] - right[i];
    }
    return left.length - right.length;
}
function canonicalJSON(value) {
    if (Array.isArray(value)) return "[" + value.map(canonicalJSON).join(",") + "]";
    if (value && typeof value === "object") {
        // Build JSON text directly: ordinary object construction reorders
        // numeric keys and can reinterpret a legal "__proto__" file name.
        return "{" + Object.keys(value).sort(codePointOrder).map(key =>
            stringify(key) + ":" + canonicalJSON(value[key])).join(",") + "}";
    }
    return stringify(value);
}
function digest(value) {
    // Canonical Python contract.digest uses ensure_ascii=True and no whitespace.
    const ascii = canonicalJSON(value).replace(/[\u007f-\uffff]/g, character =>
        "\\u" + character.charCodeAt(0).toString(16).padStart(4, "0"));
    return crypto.createHash("sha256").update(ascii).digest("hex");
}
function hydrate(value, events) {
    if (Array.isArray(value)) return value.map(x => hydrate(x, events));
    if (!value || typeof value !== "object") return value;
    if ("$regex" in value) return new RegExp(value.$regex, value.flags || "");
    if ("$callback" in value) {
        switch (value.$callback) {
        case "comments-error": {
            if (![1, 2].includes(value.after) || !["EPIPE", "ENOSPC"].includes(value.code)) {
                throw new Error("Unsupported callback fault descriptor");
            }
            let calls = 0;
            return (node, comment) => {
                calls++;
                const thrown = calls === value.after;
                events.push({ callback: "comments-error", calls, thrown, code: value.code,
                              type: comment.type, value: comment.value });
                if (thrown) {
                    const error = new Error("verifier callback " + value.code);
                    error.code = value.code;
                    throw error;
                }
                return true;
            };
        }
        case "comments-select": return (node, comment) => {
            events.push({ callback: "comments", node: node.TYPE, type: comment.type, value: comment.value });
            return /keep|license|preserve|!/.test(comment.value);
        };
        case "top-retain-select": return definition => {
            events.push({ callback: "top_retain", name: definition.name });
            return /^keep/.test(definition.name);
        };
        case "nth-simple": return { get: n => {
            events.push({ callback: "get", n });
            return "v" + n;
        } };
        case "nth-weighted": return {
            get(n) { events.push({ callback: "get", n }); return "v" + n; },
            reset() { events.push({ callback: "reset" }); },
            sort() { events.push({ callback: "sort" }); },
            consider(chars, delta) { events.push({ callback: "consider", chars, delta }); return 0; },
        };
        default: throw new Error("Unsupported verifier callback " + value.$callback);
        }
    }
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, hydrate(v, events)]));
}
function publicError(error) {
    const result = { class: String(error.name || error.constructor?.name || "Error"),
        message: String(error.message) };
    for (const field of ["filename", "line", "col", "pos", "code"]) {
        if (error[field] !== undefined) result[field] = error[field];
    }
    return result;
}
export async function run_case(value, trustedCaseSha = null) {
    if (trustedCaseSha !== null && (typeof trustedCaseSha !== "string" ||
        !/^[0-9a-f]{64}$/.test(trustedCaseSha))) {
        throw new Error("Malformed verifier case digest");
    }
    const api = value.input?.api, mode = value.input?.mode;
    if (value.task !== "terser-minify" || !["minify", "minify_sync"].includes(api) ||
        !["import", "require"].includes(mode) || !Array.isArray(value.faults) || value.faults.length) {
        throw new Error("Unsupported verifier case/precondition");
    }
    const events = [], caches = new Map(), steps = [], rawSnapshots = [];
    const affinityText = fs.readFileSync("/proc/self/status", "utf8");
    const allowed = /^Cpus_allowed_list:\s+([0-9,-]+)$/m.exec(affinityText);
    if (!allowed) throw new Error("Actual process affinity unavailable");
    const executionAffinity = allowed[1].split(",").flatMap(part => {
        const [start, end = start] = part.split("-").map(Number);
        return Array.from({ length: end - start + 1 }, (_, index) => start + index);
    });
    const options = hydrate(value.options, events);
    const defaultCache = options.nameCache;
    if (defaultCache !== undefined) caches.set("$default", defaultCache);
    const schedule = value.sequence?.length ? value.sequence : [{ files: value.input.files }];
    for (const step of schedule) {
        if (step.cache) {
            if (!caches.has(step.cache)) caches.set(step.cache, {});
            options.nameCache = caches.get(step.cache);
        } else if (defaultCache !== undefined) {
            options.nameCache = defaultCache;
        } else {
            delete options.nameCache;
        }
        const before = captureState("before-call");
        let outcome;
        try {
            const result = await modules[mode][api](step.files, options);
            // Only documented data outputs, never call candidate toJSON/reporters.
            const output = {};
            for (const field of ["code", "map", "decoded_map"]) {
                if (result[field] !== undefined) output[field] = result[field];
            }
            outcome = { kind: "ok", value: output };
        } catch (error) {
            outcome = { kind: "error", ...publicError(error) };
        }
        await new Promise(resolve => setImmediate(resolve));
        const after = captureState("after-call");
        steps.push({ outcome, cache: options.nameCache ? parse(stringify(options.nameCache)) : null,
                     lifecycle: compareState(before, after) });
        rawSnapshots.push({ before, after });
    }
    const final = steps.at(-1).outcome;
    requestCount++;
    return {
        format: "optimbench-fuzz-observation-v1",
        identity: { task: value.task, surface_id: value.surface_id,
                    case_sha256: trustedCaseSha || digest(value), build_sha256: buildSha },
        termination: { kind: final.kind === "error" ? "exception" : "return", exit_code: 0 },
        outcome: final,
        effects: { steps, encoding: "UTF-8", name_cache: Object.fromEntries(caches),
                   ...(events.some(event => event.callback === "comments-error") ?
                       { callback_faults: events.filter(event => event.callback === "comments-error") } : {}) },
        diagnostics: { worker_pid: process.pid, per_entry_load_count: 1,
                       entry_modes_loaded: ["import", "require"], request_index: requestCount,
                       execution_affinity: executionAffinity,
                       affinity_method: "in-process /proc/self/status Cpus_allowed_list",
                       lifecycle: {
                           available: rawSnapshots.every(({ before, after }) =>
                               [before, after].every(snapshot => snapshot.fds_available &&
                                   snapshot.threads_available && snapshot.children_available)),
                           before: rawSnapshots[0].before, after: rawSnapshots.at(-1).after,
                       },
                       snapshots: rawSnapshots, callback_events: events,
                       callback_order_policy: "recorded; exact ordering requires surface-specific review" },
    };
}

const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
    if (!line.trim()) continue;
    // Transport/harness errors stay infrastructure errors; never candidate error outcomes.
    const request = parse(line);
    const wrapped = request.format === "optimbench-fuzz-request-v1";
    if (wrapped && typeof request.case_sha256 !== "string") {
        throw new Error("Wrapped verifier request is missing its canonical case digest");
    }
    const observation = await run_case(wrapped ? request.case : request,
                                      wrapped ? request.case_sha256 : null);
    fs.writeSync(observerFd, stringify(observation) + "\n", null, "utf8");
}
const coverageIndex = argv.indexOf("--coverage-out");
if (coverageIndex >= 0) {
    if (!argv[coverageIndex + 1] || !globalThis.__coverage__) {
        throw new Error("Requested task coverage was not collected");
    }
    fs.writeFileSync(argv[coverageIndex + 1], stringify(globalThis.__coverage__) + "\n");
}
