// Workload only: inputs have already been authenticated and staged by the runner.
// webpack 5 production minification of ES-module assets with the default minimizer:
// new TerserPlugin({terserOptions: {compress: {passes: 2}}}) (webpack lib/config/defaults.js).
// terser-webpack-plugin then sets module (ES-module asset), ecma 2015 (module environment),
// compress.ecma, sourceMap undefined and extractComments: true, i.e. a comments callback that
// extracts "some" license comments into a separate LICENSE file and preserves none in the code.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const [library, scratch] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(path.join(scratch, 'cases.json'), 'utf8'));
const {minify} = await import(pathToFileURL(path.resolve(library, 'main.js')).href);
const SOME = /@preserve|@lic|@cc_on|^\**!/i;
for (const c of cases) {
  let bytes;
  try {
    const extracted = [];
    const comments = (node, comment) => {
      if ((comment.type === 'comment2' || comment.type === 'comment1') && SOME.test(comment.value)) {
        const text = comment.type === 'comment2' ? `/*${comment.value}*/` : `//${comment.value}`;
        if (!extracted.includes(text)) extracted.push(text);
      }
      return false;
    };
    const result = await minify({[c.source_filename]: fs.readFileSync(path.join(scratch, c.sha256), 'utf8')}, {
      compress: {passes: 2, ecma: 2015}, mangle: true, module: true, ecma: 2015,
      output: {beautify: false, comments}, parse: {}, sourceMap: undefined,
    });
    if (typeof result.code !== 'string') throw new TypeError('Missing code');
    const license = extracted.length ? extracted.sort().join('\n\n') + '\n' : '';
    bytes = Buffer.from(JSON.stringify({code: result.code, license}), 'utf8');
  } catch (error) {
    bytes = Buffer.from(`${error.name}: ${error.message}`, 'utf8');
  }
  fs.writeFileSync(path.join(scratch, `${c.path}.out`), bytes);
}
