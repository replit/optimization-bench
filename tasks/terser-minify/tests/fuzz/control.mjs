// Disposable pristine control overlay only; never edit a sweep candidate.
import fs from "node:fs";
import crypto from "node:crypto";
const [project, kind] = process.argv.slice(2);
const filename = project + "/lib/minify.js";
let source = fs.readFileSync(filename, "utf8");
const old = "    return result;\n";
if (source.split(old).length !== 2) throw new Error("Control anchor must be unique");
const before = crypto.createHash("sha256").update(source).digest("hex");
if (kind === "semantic") {
    source = source.replace(old, '    result.code += "\\n/*public-semantic-control*/";\n' + old);
} else if (kind === "state-counter") {
    source = "let verifierControlCounter = 0;\n" + source.replace(
        old, '    result.code += "\\n/*counter:" + (verifierControlCounter++) + "*/";\n' + old);
} else if (kind === "fd-leak") {
    source = 'import { openSync as verifierControlOpen } from "node:fs";\n' + source.replace(
        old, '    verifierControlOpen("/dev/null", "r");\n' + old);
} else if (kind === "thread-leak") {
    source = 'import { Worker as VerifierWorker } from "node:worker_threads";\n' + source.replace(
        old, '    new VerifierWorker("setInterval(() => {}, 1000)", { eval: true }).unref();\n' + old);
} else if (kind === "alias-cache") {
    const anchor = "    var timings = options.timings";
    if (source.split(anchor).length !== 2) throw new Error("Alias anchor must be unique");
    source = source.replace(anchor,
        "    if (options.nameCache) options.nameCache = JSON.parse(JSON.stringify(options.nameCache));\n" + anchor);
} else if (kind === "callback-swallow") {
    const anchor = "    var timings = options.timings";
    if (source.split(anchor).length !== 2) throw new Error("Callback anchor must be unique");
    source = source.replace(anchor, [
        '    if (options.format && typeof options.format.comments === "function") {',
        "        const original = options.format.comments;",
        "        options.format = { ...options.format, comments(...args) {",
        "            try { return original(...args); } catch (_) { return true; }",
        "        } };",
        "    }",
        anchor,
    ].join("\n"));
} else {
    throw new Error("Unknown reviewed control");
}
fs.writeFileSync(filename, source);
process.stderr.write(JSON.stringify({ control: kind, source_file: "lib/minify.js",
    before_sha256: before, after_sha256: crypto.createHash("sha256").update(source).digest("hex") }) + "\n");
