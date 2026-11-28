// Reads a DWG file on stdin and writes the same drawing as DXF on stdout.
//
// The conversion is done by GNU LibreDWG (compiled to WebAssembly, package
// @mlightcad/libredwg-web, GPL-3.0). It runs here as a separate program: CEP
// only exchanges files with it. `--version` prints the reader's name and version.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LibreDwg } from '@mlightcad/libredwg-web';

const pkgDir = join(dirname(fileURLToPath(import.meta.url)), 'node_modules', '@mlightcad', 'libredwg-web');
const pkg = JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf8'));

if (process.argv.includes('--version')) {
  process.stdout.write(`LibreDWG (${pkg.name} ${pkg.version})\n`);
  process.exit(0);
}

const input = readFileSync(0);
if (input.length === 0) {
  process.stderr.write('no DWG data on stdin\n');
  process.exit(2);
}
const lib = await LibreDwg.create(join(pkgDir, 'wasm') + '/');
const dxf = lib.dwg_write_dxf(input.buffer.slice(input.byteOffset, input.byteOffset + input.length));
if (!dxf || dxf.length === 0) {
  process.stderr.write('LibreDWG could not read this DWG file\n');
  process.exit(3);
}
process.stdout.write(Buffer.from(dxf));
