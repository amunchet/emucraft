// Generates a full-size 1911 left grip panel as a binary STL (outer face up, +Y toward the slide).
// Parametric approximation of the common panel: tapered outline, crowned face, rounded edges, two
// screw-bushing holes and the magazine-release notch. Usage: node make_1911_grip.mjs [out.stl]
import fs from 'fs';

const out = process.argv[2] || new URL('./1911_grip.stl', import.meta.url).pathname;
const STEP = 0.25; // grid pitch (mm)
const EDGE_R = 3.2; // edge round-over radius (mm)
const LIP = 0.9; // thickness left at the very edge (mm)
const HOLES = [[1, 38.5, 3.5], [1, -38.5, 3.5]]; // screw bushing clearance holes [x, y, r]
const NOTCH = [-24.5, 41, 6.5]; // magazine release cut on the front edge

// Outline: front edge at x = -21.5, rear edge tapers 21.5 -> 20.2 toward the bottom; rounded corners.
const outline = [];
const corner = (cx, cy, r, a0, a1) => { for (let k = 0; k <= 12; k++) { const a = a0 + (a1 - a0) * k / 12; outline.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); } };
corner(21.5 - 3, 54 - 3, 3, 0, Math.PI / 2);
corner(-21.5 + 3, 54 - 3, 3, Math.PI / 2, Math.PI);
corner(-21.5 + 6, -54 + 6, 6, Math.PI, 1.5 * Math.PI);
corner(20.2 - 6, -54 + 6, 6, 1.5 * Math.PI, 2 * Math.PI);

// Signed distance to the panel boundary, positive inside.
function dist(x, y) {
  let d = Infinity, inside = false;
  for (let i = 0, j = outline.length - 1; i < outline.length; j = i++) {
    const [ax, ay] = outline[j], [bx, by] = outline[i];
    if ((ay > y) !== (by > y) && x < ax + (y - ay) * (bx - ax) / (by - ay)) inside = !inside;
    const ex = bx - ax, ey = by - ay;
    const t = Math.max(0, Math.min(1, ((x - ax) * ex + (y - ay) * ey) / (ex * ex + ey * ey)));
    d = Math.min(d, Math.hypot(x - ax - t * ex, y - ay - t * ey));
  }
  d = inside ? d : -d;
  for (const [cx, cy, r] of [...HOLES, NOTCH]) d = Math.min(d, Math.hypot(x - cx, y - cy) - r);
  return d;
}

// Strongly crowned outer face (7.6 mm on the centreline, ~3.3 mm at the long edges, ends arched 1.4 mm lower)
// with a quarter-round edge.
function top(x, y, d) {
  const crown = 2.4 + 5.2 * Math.max(0, 1 - (x / 23) ** 2) ** 0.85 - 1.4 * (y / 54) ** 2;
  const e = d >= EDGE_R ? 1 : Math.sqrt(1 - ((EDGE_R - d) / EDGE_R) ** 2);
  return LIP + (crown - LIP) * e;
}

const x0 = -23.13, y0 = -55.13, nx = Math.ceil(46 / STEP) + 1, ny = Math.ceil(110 / STEP) + 1;
const D = new Float64Array(nx * ny), Z = new Float64Array(nx * ny);
for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
  const x = x0 + i * STEP, y = y0 + j * STEP, d = dist(x, y);
  D[j * nx + i] = d;
  Z[j * nx + i] = d > 0 ? top(x, y, d) : 0;
}
const kept = (i, j) => i >= 0 && j >= 0 && i < nx - 1 && j < ny - 1 &&
  D[j * nx + i] > 0 && D[j * nx + i + 1] > 0 && D[(j + 1) * nx + i] > 0 && D[(j + 1) * nx + i + 1] > 0;

// Snap vertices on the cell-mask border onto the true outline (d = 0) so the edge is smooth, not stair-stepped.
const VX = new Float64Array(nx * ny), VY = new Float64Array(nx * ny);
for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) { VX[j * nx + i] = x0 + i * STEP; VY[j * nx + i] = y0 + j * STEP; }
for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
  const cells = [kept(i - 1, j - 1), kept(i, j - 1), kept(i - 1, j), kept(i, j)];
  if (!cells.some(Boolean) || cells.every(Boolean)) continue;
  const k = j * nx + i, x = VX[k], y = VY[k], h = 1e-3;
  for (let it = 0; it < 4; it++) { // a few Newton steps along the distance gradient
    const d = dist(VX[k], VY[k]), gx = (dist(VX[k] + h, VY[k]) - dist(VX[k] - h, VY[k])) / (2 * h), gy = (dist(VX[k], VY[k] + h) - dist(VX[k], VY[k] - h)) / (2 * h);
    VX[k] -= d * gx; VY[k] -= d * gy;
  }
  if (Math.hypot(VX[k] - x, VY[k] - y) > STEP * 1.5) { VX[k] = x; VY[k] = y; } // corner fallback: leave it
  else Z[k] = LIP;
}

const tris = [];
const P = (i, j, z) => [VX[j * nx + i], VY[j * nx + i], z];
const T = (i, j) => P(i, j, Z[j * nx + i]);
const B = (i, j) => P(i, j, 0);
for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
  if (!kept(i, j)) continue;
  // split along whichever diagonal keeps both triangles counter-clockwise (snapped border cells can be skewed)
  const q = [[i, j], [i + 1, j], [i + 1, j + 1], [i, j + 1]];
  const ccw = (a, b, c) => (P(...b)[0] - P(...a)[0]) * (P(...c)[1] - P(...a)[1]) - (P(...b)[1] - P(...a)[1]) * (P(...c)[0] - P(...a)[0]) > 0;
  const [a, b, c, e] = ccw(q[0], q[1], q[2]) && ccw(q[0], q[2], q[3]) ? q : [q[1], q[2], q[3], q[0]];
  tris.push([T(...a), T(...b), T(...c)], [T(...a), T(...c), T(...e)]);
  tris.push([B(...a), B(...c), B(...b)], [B(...a), B(...e), B(...c)]);
  // side walls on boundary edges (CCW cell edges; outward normal faces the missing neighbour)
  const walls = [[0, -1, [i, j], [i + 1, j]], [1, 0, [i + 1, j], [i + 1, j + 1]], [0, 1, [i + 1, j + 1], [i, j + 1]], [-1, 0, [i, j + 1], [i, j]]];
  for (const [di, dj, a, b] of walls) {
    if (kept(i + di, j + dj)) continue;
    tris.push([B(...a), B(...b), T(...b)], [B(...a), T(...b), T(...a)]);
  }
}

const buf = Buffer.alloc(84 + tris.length * 50);
buf.write('emucraft 1911 grip panel', 0);
buf.writeUInt32LE(tris.length, 80);
tris.forEach((t, k) => {
  const o = 84 + k * 50;
  const [a, b, c] = t;
  const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
  const n = [uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx], l = Math.hypot(...n) || 1;
  [...n.map((q) => q / l), ...a, ...b, ...c].forEach((q, m) => buf.writeFloatLE(q, o + m * 4));
});
fs.writeFileSync(out, buf);
console.log(`${out}: ${tris.length} triangles, ${(buf.length / 1e6).toFixed(1)} MB`);
