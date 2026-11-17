// Verifier-only instrumentation, never edit production or saved candidate trees.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { createInstrumenter } = require("/opt/fuzz-runners/node_modules/istanbul-lib-instrument");
const [original, output, receipt] = process.argv.slice(2);
if (!original || !output || !receipt || fs.existsSync(output)) {
    throw new Error("usage: instrument_task.cjs ORIGINAL NEW_COPY RECEIPT");
}
const sha = value => crypto.createHash("sha256").update(value).digest("hex");
fs.cpSync(original, output, { recursive: true, dereference: false });
function walk(directory) {
    return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
        const filename = path.join(directory, entry.name);
        return entry.isDirectory() ? walk(filename) : [filename];
    });
}
const files = [];
for (const filename of walk(path.join(original, "lib")).filter(x => x.endsWith(".js")).sort()) {
    const relative = path.relative(original, filename);
    const source = fs.readFileSync(filename, "utf8");
    const instrumenter = createInstrumenter({
        esModules: true, produceSourceMap: true, preserveComments: true, compact: false,
        coverageGlobalScope: "globalThis", coverageGlobalScopeFunc: false,
    });
    // Original input source locations, not generated bundle/VM line numbers.
    const generated = instrumenter.instrumentSync(source, filename);
    const destination = path.join(output, relative);
    fs.writeFileSync(destination, generated);
    const map = instrumenter.lastSourceMap();
    fs.writeFileSync(destination + ".map", JSON.stringify(map) + "\n");
    files.push({
        relative_path: relative, original_path: filename, instrumented_path: destination,
        original_sha256: sha(source), instrumented_sha256: sha(generated),
        instrumentation_map_sha256: sha(JSON.stringify(map)),
        source_unchanged: sha(fs.readFileSync(filename)) === sha(source),
    });
}
fs.writeFileSync(receipt, JSON.stringify({
    format: "optimbench-terser-task-instrumentation-v1",
    original_root: original, instrumented_root: output,
    instrumentation: "Istanbul ESModule AST before import; both loader entries rebuilt from same copy",
    original_source_changed: false, files,
    changed_target_denominator: null,
    qualification: "instrumentation feasibility, not changed-line/branch adequacy",
}, null, 2) + "\n");
