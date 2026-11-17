const fs = require("node:fs");
const vm = require("node:vm");
const {createInstrumenter} = require("istanbul-lib-instrument");
const {SourceMapGenerator} = require("source-map");
// Deliberate generated->original line offset proves remapping is not a no-op.
const original = fs.readFileSync("js_target.cjs", "utf8");
const generated = "\n\n" + original;
const originalPath = process.cwd() + "/js_target.cjs";
const generatedPath = process.cwd() + "/generated.cjs";
const map = new SourceMapGenerator({file: generatedPath});
original.split("\n").forEach((_, index) => {
  for (let column = 0; column <= (original.split("\n")[index] || "").length; column++) {
    map.addMapping({generated:{line:index+3,column}, original:{line:index+1,column}, source:originalPath});
  }
});
map.setSourceContent(originalPath, original);
const instrumenter = createInstrumenter({produceSourceMap:true});
const code = instrumenter.instrumentSync(generated, generatedPath, map.toJSON());
const context = {exports:{}, __coverage__:{}};
vm.createContext(context);
vm.runInContext(code, context, {filename:generatedPath});
for (const n of [-1, 0, 1]) context.exports.classify(n);
fs.writeFileSync("istanbul-raw.json", JSON.stringify(context.__coverage__));
