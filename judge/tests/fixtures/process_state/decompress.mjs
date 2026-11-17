export default function run(module) {
  module.decompressSync(new Uint8Array([1, 2]));
}
