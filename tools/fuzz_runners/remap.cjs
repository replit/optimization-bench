// Transform task-code Istanbul maps through embedded/external source maps.
// Not V8 function coverage: branch locations must come from task instrumentation.
const fs = require("node:fs");
const {createCoverageMap} = require("istanbul-lib-coverage");
const {createSourceMapStore} = require("istanbul-lib-source-maps");
(async () => {
  const args = process.argv.slice(2);
  const merge = args[0] === "--merge";
  const output = merge ? args[1] : args[1];
  const inputs = merge ? args.slice(2) : [args[0]];
  if (!output || !inputs.length || !inputs[0]) throw new Error("usage: remap.cjs input.json output.json | --merge output.json input1.json input2.json ...");
  const map = createCoverageMap({});
  for (const input of inputs) map.merge(JSON.parse(fs.readFileSync(input, "utf8")));
  const store = createSourceMapStore();
  for (const filename of map.files()) {
    const data = map.fileCoverageFor(filename).data;
    if (data.inputSourceMap) store.registerMap(filename, data.inputSourceMap);
  }
  const result = await store.transformCoverage(map);
  fs.writeFileSync(output, JSON.stringify(result.toJSON()) + "\n");
})().catch(error => { console.error(error); process.exitCode = 1; });
