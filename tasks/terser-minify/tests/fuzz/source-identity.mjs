// Independent verifier file hashing; never call candidate serialization helpers.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
const [project, output] = process.argv.slice(2);
const sha = data => crypto.createHash("sha256").update(data).digest("hex");
function walk(directory) {
    return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
        const file = path.join(directory, entry.name);
        return entry.isDirectory() ? walk(file) : [file];
    });
}
const relative = [...walk(path.join(project, "lib"))
    .filter(file => file.endsWith(".js")).map(file => path.relative(project, file)),
    "main.js", "package.json", "package-lock.json", "tools/terser.d.ts"];
const sources = Object.fromEntries(relative.sort().map(name =>
    [name, sha(fs.readFileSync(path.join(project, name)))]));
const builds = { ...sources, "dist/bundle.min.js": sha(fs.readFileSync(path.join(project, "dist/bundle.min.js"))) };
const result = {
    format: "optimbench-terser-verifier-build-identity-v1",
    source_sha256: sha(JSON.stringify(sources)),
    build_sha256: sha(JSON.stringify(builds)), source_files: sources, build_files: builds,
    dependency_scope: "Pinned image package-lock and image identity; node_modules not recursively rehashed",
};
fs.writeFileSync(output, JSON.stringify(result, null, 2) + "\n");
process.stdout.write(result.build_sha256);
