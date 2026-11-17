const {classify} = require("./js_target.cjs");
module.exports.fuzz = function(data) {
  const n = data.length ? data.readInt8(0) : 0;
  if (classify(n) !== Math.sign(n)) throw new Error("classification");
};
