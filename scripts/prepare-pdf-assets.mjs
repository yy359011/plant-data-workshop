import {cpSync,mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const target=path.join(root,'public','pdfjs');
mkdirSync(target,{recursive:true});
for(const name of ['cmaps','standard_fonts','wasm']) {
  const src = path.join(root,'node_modules','pdfjs-dist',name);
  const dst = path.join(target,name);
  try { cpSync(src,dst,{recursive:true}); } catch { /* v4 无 wasm 目录，跳过 */ }
}
cpSync(path.join(root,'node_modules','pdfjs-dist','LICENSE'),path.join(target,'LICENSE'));
console.log('Prepared local PDF fonts, CMaps and rendering assets.');
