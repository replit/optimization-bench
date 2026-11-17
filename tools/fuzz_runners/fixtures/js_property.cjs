const fc = require("fast-check");
const {classify} = require("./js_target.cjs");
fc.assert(fc.property(fc.integer(), n => classify(n) === Math.sign(n)), {seed:1729, numRuns:100});
