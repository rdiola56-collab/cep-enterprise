// Development helper: writes a DXF drawing out as DWG with an independent library
// (acad-ts, a port of ACadSharp), so the importer can be tested on real DWG bytes.
//   node make_sample.mjs in.dxf out.dwg [AC1018|AC1024|AC1027|AC1032]
import { readFileSync, writeFileSync } from 'node:fs';
import { ACadVersion, DwgWriter, DxfReader } from '@node-projects/acad-ts';

const [src, out, ver = 'AC1018'] = process.argv.slice(2);
const doc = DxfReader.readFromStream(new Uint8Array(readFileSync(src)));
doc.header.version = ACadVersion[ver];
try { doc.header.currentDimensionStyle; } catch { doc.header.currentDimensionStyleName = 'Standard'; }
writeFileSync(out, DwgWriter.writeToBuffer(doc));
