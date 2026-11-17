import fs from "node:fs";
import { minify } from "/app/build/project/main.js";
import { parse } from "/app/build/project/lib/parse.js";
import { TreeWalker } from "/app/build/project/lib/ast.js";
import "/app/build/project/lib/output.js";
const c = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
let result;
try {
    if (c.kind === "walker") {
        const ast = parse(c.source), seen = [], nested = [];
        let callback;
        if (c.callback === "default") callback = function(node, descend = () => {}) { seen.push(node.TYPE); descend(); return true; };
        else if (c.callback === "rest") callback = function(node, ...args) { seen.push(node.TYPE); args[0](); return true; };
        else callback = function(node, descend) { seen.push(node.TYPE); descend(); return true; };
        ast.walk(new TreeWalker(callback));
        ast.walk(new TreeWalker((node) => { nested.push(node.TYPE); }));
        const again = [];
        parse(c.source).walk(new TreeWalker(node => { again.push(node.TYPE); }));
        result = { seen, nested, again };
    } else result = await minify(c.source, c.options);
} catch (error) { result = { exception: error.name, message: error.message }; }
console.log(JSON.stringify(result));
