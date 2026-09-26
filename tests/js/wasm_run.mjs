// Replays a viewer payload through the WebAssembly kernel (the viewer's own
// kernel.js wrapper) and prints digests of the resulting stock, so tests can
// compare it bit for bit with the native kernel.
//   node wasm_run.mjs payload.json [chunk]
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, '..', '..', 'emucraft', 'web');
const { Kernel } = await import(join(web, 'js', 'kernel.js'));

const payload = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const chunk = Number(process.argv[3] || 0);
const decode = (b64, T) => {
  const b = Buffer.from(b64, 'base64');
  return new T(b.buffer, b.byteOffset, b.byteLength / T.BYTES_PER_ELEMENT);
};
const p = payload.path;
const path = { n: p.n, points: decode(p.points, Float64Array), flags: decode(p.flags, Int32Array), tools: decode(p.tools, Int32Array) };
const kernel = await Kernel.create(new Uint8Array(readFileSync(join(web, 'wasm', 'emucraft.wasm'))));
kernel.setup({ grid: payload.display, path, tools: payload.kernel_tools, tolerance: payload.report.stats.tolerance });
const t0 = performance.now();
if (chunk > 0) {
  for (let i = 0; i < path.n; i += chunk) kernel.run(i, Math.min(path.n, i + chunk));
} else {
  kernel.run(0, path.n);
}
const ms = performance.now() - t0;
const digest = arr => createHash('sha256').update(Buffer.from(arr.buffer, arr.byteOffset, arr.byteLength)).digest('hex');
const events = kernel.takeEvents();
console.log(JSON.stringify({
  heights: digest(kernel.heights),
  marks: digest(kernel.marks),
  events: events.map(e => [e.kind, e.move]),
  ms,
}));
