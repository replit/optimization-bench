// Workload only: inputs have already been authenticated and staged by the runner.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const [library, scratch] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(path.join(scratch, 'cases.json'), 'utf8'));
const {minify} = await import(pathToFileURL(path.resolve(library, 'main.js')).href);
for (const c of cases) {
  let bytes;
  try {
    const result = await minify({[c.source_filename]: fs.readFileSync(path.join(scratch, c.sha256), 'utf8')}, {
      compress: {passes: 1}, mangle: true, format: {comments: false}, sourceMap: false,
    });
    if (typeof result.code !== 'string') throw new TypeError('Missing code');
    bytes = Buffer.from(result.code, 'utf8');
  } catch (error) {
    bytes = Buffer.from(`${error.name}: ${error.message}`, 'utf8');
  }
  fs.writeFileSync(path.join(scratch, `${c.path}.out`), bytes);
}
