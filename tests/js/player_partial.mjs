// Playback sweeps partial moves; the result must match one full run.
//   node player_partial.mjs payload.json
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, '..', '..', 'emucraft', 'web');
const { Kernel } = await import(join(web, 'js', 'kernel.js'));
const { Player } = await import(join(web, 'js', 'player.js'));

const payload = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const decode = (b64, T) => {
  const b = Buffer.from(b64, 'base64');
  return new T(b.buffer, b.byteOffset, b.byteLength / T.BYTES_PER_ELEMENT);
};
const p = payload.path;
const path = {
  n: p.n, points: decode(p.points, Float64Array), flags: decode(p.flags, Int32Array),
  tools: decode(p.tools, Int32Array), feed: decode(p.feed, Float32Array),
};
const wasm = new Uint8Array(readFileSync(join(web, 'wasm', 'emucraft.wasm')));
const setup = k => k.setup({ grid: payload.display, path, tools: payload.kernel_tools, tolerance: payload.report.stats.tolerance });

const full = await Kernel.create(wasm);
setup(full);
full.run(0, path.n);
const refHeights = Float32Array.from(full.heights);

const k = await Kernel.create(wasm);
setup(k);
const player = new Player(k, path, payload.report.stats.rapid_rate);
// real-time-like playback: many small time steps, then a scrub back and forward again
const steps = 997;
for (let s = 1; s <= steps; s++) {
  const [m, f] = player.at(player.duration * s / steps);
  player.advance(m, f, 1e9);
}
const mid = player.at(player.duration * 0.37);
player.advance(mid[0], mid[1], 1e9);           // backwards: resets and replays
const end = player.at(player.duration);
player.advance(end[0], end[1], 1e9);

let worst = 0, differing = 0;
for (let i = 0; i < refHeights.length; i++) {
  const d = Math.abs(refHeights[i] - k.heights[i]);
  if (d > 0) differing++;
  if (d > worst) worst = d;
}
console.log(JSON.stringify({
  move: player.move, n: path.n, worst, differing, cells: refHeights.length,
  duration: player.duration, times_monotonic: player.times.every((t, i, a) => i === 0 || t >= a[i - 1]),
}));
