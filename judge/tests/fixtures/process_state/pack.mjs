export async function run(module) {
  await Promise.resolve();
  new module.Packr().pack({hello: "world"});
}
export default run;
