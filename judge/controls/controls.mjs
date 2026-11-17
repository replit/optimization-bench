import {minify} from '/candidate/project/main.js';
const programs = [
  'function f(x){ return x + 0; } console.log(f(2));',
  'let calls=0; const x={get a(){calls++;return calls}}; console.log(x.a,x.a,calls);',
  'async function f(xs){for await(const x of xs){ console.log(x ?? 1); }}',
  'const f=(x)=>()=>x; console.log(f(1)());',
  'class A { #x=1; get(){return this.#x;} } console.log(new A().get());',
  'function broken( {',
];
const results = [];
for (let i = 0; i < programs.length; i++) {
  for (const options of [
    {compress: {passes: 1}, mangle: true},
    {compress: false, mangle: false, format: {beautify: true}},
    {compress: {passes: 2}, mangle: true, sourceMap: {filename: 'out.js', url: 'out.js.map', includeSources: true}},
  ]) {
    try {
      const result = await minify({'input.js': programs[i]}, options);
      results.push({case: i, options, code: result.code, map: result.map ?? null});
    } catch (error) {
      results.push({case: i, options, error: error.name, message: error.message, line: error.line, col: error.col});
    }
  }
}
console.log(JSON.stringify(results));
