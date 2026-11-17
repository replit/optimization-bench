/* Verifier-only, untimed Mocha identity reporter; retain the original reporter. */
const Mocha = require("/app/deps/node_modules/mocha");
module.exports = function(runner, options) {
  new Mocha.reporters.Spec(runner, options);
  for (const [event, status] of [["pass", "passed"], ["pending", "skipped"], ["fail", "failed"]]) {
    runner.on(event, test => console.log("JUDGE_TEST " + JSON.stringify({
      target: "mocha", name: [test.file.replace(process.cwd() + "/", ""), test.fullTitle()],
      status
    })));
  }
};
