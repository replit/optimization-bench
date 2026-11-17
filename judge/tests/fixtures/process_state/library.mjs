export class Packr {
  pack(value) {
    process.env.REPRESENTATIVE_CALLED = JSON.stringify(value);
    return new Uint8Array([1, 2, 3]);
  }
}
export function decompressSync(value) {
  process.env.REPRESENTATIVE_CALLED = String(value.length);
  return new Uint8Array([4, 5, 6]);
}
