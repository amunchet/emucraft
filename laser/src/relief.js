// Customer GLB: a render-only stand-in for the engraved part that is hard to turn back into the solid. Only the
// side the laser sees is kept, as a coarse open shell (top relief on a mesh_mm grid plus outline walls, no back,
// no real scale); everything fine (engraving, edges, outline) lives in textures: colour, a tangent-space normal
// map, roughness/metal/clearcoat and an alpha mask. No DOM: runs in node (API, tests).
import { rasterize_surface } from './sim.js';

const srgb_to_lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const lin_to_srgb = (c) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

// look: { color, mark: linear [r, g, b]; rough, metal, mark_rough, mark_metal, mark_full, clearcoat, clearcoat_rough;
//   tile: { px: RGBA bytes, w, h } | null, tile_mm, laminate (tile coords (x + y, z) instead of (x, y)), rough_tile }
// res: simulate() output; encode_png(rgba, w, h) -> Uint8Array (may be async). Returns the GLB bytes and a summary.
// jitter scales every length the file is built from in mm (mesh pitch, texel floor, tile size): a unit-less file
// would otherwise give the real size away as (known mm length) / (its length in the file). One factor for all of
// them, so they cannot be played off against each other; the size is then known only to within the jitter.
export async function customer_glb({ positions, res, placement: P, look, mesh_mm = 0.5, max_texture = 2048, true_scale = false, jitter = 1 }, encode_png) {
  if (!(mesh_mm > 0)) throw new Error('mesh_mm must be positive');
  if (!(jitter > 0)) throw new Error('jitter must be positive');
  const tile_mm = (look.tile_mm || 1) * jitter;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, top = -Infinity;
  for (let k = 0; k < positions.length; k += 3) {
    x0 = Math.min(x0, positions[k]); x1 = Math.max(x1, positions[k]);
    y0 = Math.min(y0, positions[k + 1]); y1 = Math.max(y1, positions[k + 1]);
    top = Math.max(top, positions[k + 2]);
  }
  // texel pitch: the whole part in max_texture texels (+ a 1-texel empty margin), whatever the engraving grid's pitch
  const { grid_w: gw, grid_h: gh, depth } = res, pw = P.width_mm / gw, ph = P.height_mm / gh;
  const t = Math.max(0.005 * jitter, (x1 - x0) / (max_texture - 2), (y1 - y0) / (max_texture - 2));
  const tw = Math.ceil((x1 - x0) / t) + 2, th = Math.ceil((y1 - y0) / t) + 2;
  const left = (x0 + x1) / 2 - (tw * t) / 2, upper = (y0 + y1) / 2 + (th * t) / 2; // texel (i, j) centre: left + (i + .5) t, upper - (j + .5) t
  const base = rasterize_surface(positions, { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, angle: 0, width_mm: tw * t, height_mm: th * t }, tw, th);
  const n = tw * th, ok = (k) => !Number.isNaN(base[k]);

  // removed depth per texel: the mean of the engraving cells inside it when texels are coarser than the engraving
  // (fine hatching and dither would alias otherwise), else bilinear from the engraving grid
  const c = Math.cos(P.angle), s = Math.sin(P.angle);
  const depth_at = (x, y) => {
    const dx = x - P.cx, dy = y - P.cy, gi = (c * dx + s * dy) / pw + gw / 2 - 0.5, gj = gh / 2 - 0.5 - (-s * dx + c * dy) / ph;
    if (gi < -0.5 || gj < -0.5 || gi > gw - 0.5 || gj > gh - 0.5) return 0;
    if (gw < 2 || gh < 2) return depth[Math.min(gh - 1, Math.max(0, Math.round(gj))) * gw + Math.min(gw - 1, Math.max(0, Math.round(gi)))];
    const i = Math.min(gw - 2, Math.max(0, Math.floor(gi))), j = Math.min(gh - 2, Math.max(0, Math.floor(gj)));
    const fi = Math.min(1, Math.max(0, gi - i)), fj = Math.min(1, Math.max(0, gj - j)), k = j * gw + i;
    return (depth[k] * (1 - fi) + depth[k + 1] * fi) * (1 - fj) + (depth[k + gw] * (1 - fi) + depth[k + gw + 1] * fi) * fj;
  };
  const scatter = t > 1.5 * Math.min(pw, ph), acc = new Float64Array(scatter ? n : 0), cnt = new Uint32Array(scatter ? n : 0);
  for (let gj = 0; scatter && gj < gh; gj++) {
    for (let gi = 0; gi < gw; gi++) {
      const lx = (gi + 0.5) * pw - P.width_mm / 2, ly = P.height_mm / 2 - (gj + 0.5) * ph;
      const i = Math.floor((P.cx + c * lx - s * ly - left) / t), j = Math.floor((upper - P.cy - s * lx - c * ly) / t);
      if (i < 0 || j < 0 || i >= tw || j >= th) continue;
      acc[j * tw + i] += depth[gj * gw + gi];
      cnt[j * tw + i]++;
    }
  }
  // z is the engraved surface as the file shows it (normal map, laminate bands): its depth is scaled by the jitter
  // like every other length, else depth from the marks (real mm via mark_full) over depth from the normals is a ruler
  const dep = new Float32Array(n), z = new Float32Array(n).fill(NaN);
  for (let j = 0; j < th; j++) {
    for (let i = 0; i < tw; i++) {
      const k = j * tw + i;
      if (!ok(k)) continue;
      dep[k] = scatter && cnt[k] ? acc[k] / cnt[k] : depth_at(left + (i + 0.5) * t, upper - (j + 0.5) * t);
      z[k] = base[k] - jitter * dep[k];
    }
  }

  // coarse vertex grid over the texture area; a cell is kept if a surface texel lies within one texel of it, so the
  // alpha edge (half a texel outside the last surface texel) always falls on kept cells
  // (never finer than 2 texels, nor than 1024 cells across: the shell must stay coarse and bounded)
  const m = Math.max(mesh_mm * jitter, 2 * t, (tw * t) / 1024, (th * t) / 1024), mw = Math.ceil((tw * t) / m) + 1, mh = Math.ceil((th * t) / m) + 1;
  const kept = new Uint8Array((mw - 1) * (mh - 1));
  for (let j = 0; j < th; j++) {
    for (let i = 0; i < tw; i++) {
      if (!ok(j * tw + i)) continue;
      const ca = Math.max(0, Math.floor((i - 0.5) * t / m)), cb = Math.min(mw - 2, Math.floor((i + 1.5) * t / m));
      const ra = Math.max(0, Math.floor((j - 0.5) * t / m)), rb = Math.min(mh - 2, Math.floor((j + 1.5) * t / m));
      for (let r = ra; r <= rb; r++) for (let q = ca; q <= cb; q++) kept[r * (mw - 1) + q] = 1;
    }
  }
  // vertices from the UNengraved surface: bilinear over the surface texels around one that sits on the surface;
  // one off the surface is pulled onto the outline next to its nearest surface texel, at that texel's height, so
  // the relief's edge meets the outline walls instead of overhanging or undercutting them
  const vx = new Float32Array(mw * mh), vy = new Float32Array(mw * mh), vz = new Float32Array(mw * mh).fill(NaN), reach = Math.ceil(m / t) + 2;
  for (let J = 0; J < mh; J++) {
    for (let I = 0; I < mw; I++) {
      if (![[I - 1, J - 1], [I, J - 1], [I - 1, J], [I, J]].some(([q, r]) => q >= 0 && r >= 0 && q < mw - 1 && r < mh - 1 && kept[r * (mw - 1) + q])) continue;
      const k = J * mw + I, fi = (I * m) / t - 0.5, fj = (J * m) / t - 0.5, i = Math.floor(fi), j = Math.floor(fj);
      const ri = Math.round(fi), rj = Math.round(fj);
      vx[k] = left + I * m; vy[k] = upper - J * m;
      if (ri >= 0 && rj >= 0 && ri < tw && rj < th && ok(rj * tw + ri)) {
        let acc = 0, wsum = 0;
        for (const [a, b, w] of [[i, j, (1 - fi + i) * (1 - fj + j)], [i + 1, j, (fi - i) * (1 - fj + j)], [i, j + 1, (1 - fi + i) * (fj - j)], [i + 1, j + 1, (fi - i) * (fj - j)]]) {
          if (a >= 0 && b >= 0 && a < tw && b < th && ok(b * tw + a) && w > 0) { acc += w * base[b * tw + a]; wsum += w; }
        }
        vz[k] = acc / wsum;
        continue;
      }
      let best = Infinity, ba = 0, bb = 0;
      for (let b = Math.max(0, j - reach); b <= Math.min(th - 1, j + reach); b++) {
        for (let a = Math.max(0, i - reach); a <= Math.min(tw - 1, i + reach); a++) {
          const d = (a - fi) ** 2 + (b - fj) ** 2;
          if (d < best && ok(b * tw + a)) { best = d; ba = a; bb = b; }
        }
      }
      if (best === Infinity) throw new Error('relief vertex found no surface nearby'); // cannot happen: kept cells touch surface texels
      const d = Math.sqrt(best), f = Math.min(1, 0.5 / d);
      vx[k] = left + (ba + 0.5 + (fi - ba) * f) * t; vy[k] = upper - (bb + 0.5 + (fj - bb) * f) * t; vz[k] = base[bb * tw + ba];
    }
  }
  // triangles on grid vertex ids: cell (I, J) holds (a, c, b) and (b, c, d), a = top-left, c = below a; snapping
  // can fold one over (plan-view area <= 0), which would show its back, so those are dropped
  const tri = [], plan_area = (a, b, d) => (vx[b] - vx[a]) * (vy[d] - vy[a]) - (vy[b] - vy[a]) * (vx[d] - vx[a]);
  for (let J = 0; J < mh - 1; J++) {
    for (let I = 0; I < mw - 1; I++) {
      if (!kept[J * (mw - 1) + I]) continue;
      const a = J * mw + I, b = a + 1, cc = a + mw, d = cc + 1;
      if (plan_area(a, cc, b) > 0) tri.push(a, cc, b);
      if (plan_area(b, cc, d) > 0) tri.push(b, cc, d);
    }
  }
  // area-weighted vertex normals, and tangents along +x (the texture's +u) made orthogonal to them
  const vn = new Float32Array(mw * mh * 3), vt = new Float32Array(mw * mh * 4);
  for (let q = 0; q < tri.length; q += 3) {
    const [a, b, d] = [tri[q], tri[q + 1], tri[q + 2]];
    const ex = vx[b] - vx[a], ey = vy[b] - vy[a], ez = vz[b] - vz[a], fx = vx[d] - vx[a], fy = vy[d] - vy[a], fz = vz[d] - vz[a];
    const nx = ey * fz - ez * fy, ny = ez * fx - ex * fz, nz = ex * fy - ey * fx;
    for (const v of [a, b, d]) { vn[v * 3] += nx; vn[v * 3 + 1] += ny; vn[v * 3 + 2] += nz; }
  }
  for (let k = 0; k < mw * mh; k++) {
    const len = Math.hypot(vn[k * 3], vn[k * 3 + 1], vn[k * 3 + 2]);
    if (!(len > 0)) { vn.set([0, 0, 1], k * 3); vt.set([1, 0, 0, 1], k * 4); continue; }
    const nx = (vn[k * 3] /= len), ny = (vn[k * 3 + 1] /= len), nz = (vn[k * 3 + 2] /= len);
    const tx = 1 - nx * nx, ty = -nx * ny, tz = -nx * nz, tl = Math.hypot(tx, ty, tz) || 1;
    vt.set([tx / tl, ty / tl, tz / tl, 1], k * 4);
  }

  // textures: colour + alpha mask, tangent-space normals, packed R clearcoat / G roughness / B metalness
  const col = new Uint8Array(n * 4), nrm = new Uint8Array(n * 4), orm = new Uint8Array(n * 4);
  const tile = look.tile;
  // bilinear, wrapping tile lookup at mm coords (u, v), channel ch, 0..1; v runs up the tile as in the app (flipY)
  const tile_at = (u, v, ch) => {
    const fu = (((u / tile_mm) % 1) + 1) % 1 * tile.w - 0.5, fv = (1 - ((((v / tile_mm) % 1) + 1) % 1)) * tile.h - 0.5;
    const a = Math.floor(fu), b = Math.floor(fv), wa = fu - a, wb = fv - b;
    const px = (q, r) => tile.px[(((r + tile.h) % tile.h) * tile.w + ((q + tile.w) % tile.w)) * 4 + ch] / 255;
    return (px(a, b) * (1 - wa) + px(a + 1, b) * wa) * (1 - wb) + (px(a, b + 1) * (1 - wa) + px(a + 1, b + 1) * wa) * wb;
  };
  const zat = (i, j) => z[Math.min(th - 1, Math.max(0, j)) * tw + Math.min(tw - 1, Math.max(0, i))];
  for (let j = 0; j < th; j++) {
    for (let i = 0; i < tw; i++) {
      const k = j * tw + i;
      if (!ok(k)) continue;
      const x = left + (i + 0.5) * t, y = upper - (j + 0.5) * t;
      const lt = Math.min(1, Math.max(0, dep[k] / look.mark_full)), lm = lt * lt * (3 - 2 * lt) * 0.96;
      // base colour, 2x2 supersampled (laminate bands are a function of z and can be finer than a texel on slopes)
      const rgb = [0, 0, 0];
      let rough = look.rough;
      if (tile) {
        for (const [a, b] of [[-0.25, -0.25], [0.25, -0.25], [-0.25, 0.25], [0.25, 0.25]]) {
          const zs = Number.isNaN(zat(i + Math.sign(a), j + Math.sign(b))) ? z[k] : z[k] + (zat(i + Math.sign(a), j + Math.sign(b)) - z[k]) * 0.25;
          const xs = x + a * t, ys = y - b * t, u = look.laminate ? xs + ys : xs, v = look.laminate ? zs : ys;
          for (let ch = 0; ch < 3; ch++) rgb[ch] += srgb_to_lin(tile_at(u, v, ch)) * look.color[ch] / 4;
        }
        if (look.rough_tile) rough *= tile_at(x, y, 1);
      } else rgb.splice(0, 3, ...look.color);
      for (let ch = 0; ch < 3; ch++) col[k * 4 + ch] = Math.round(lin_to_srgb(rgb[ch] + (look.mark[ch] - rgb[ch]) * lm) * 255);
      col[k * 4 + 3] = 255;
      orm[k * 4] = Math.round((1 - lm) * 255);
      orm[k * 4 + 1] = Math.round((rough + (look.mark_rough - rough) * lm) * 255);
      orm[k * 4 + 2] = Math.round((look.metal + (look.mark_metal - look.metal) * lm) * 255);
      orm[k * 4 + 3] = 255;
      // fine normal of the engraved surface, expressed in the frame the coarse mesh interpolates at this texel
      const l = i > 0 && ok(k - 1) ? k - 1 : k, r = i < tw - 1 && ok(k + 1) ? k + 1 : k, up = j > 0 && ok(k - tw) ? k - tw : k, dn = j < th - 1 && ok(k + tw) ? k + tw : k;
      const gx = r === l ? 0 : (z[r] - z[l]) / ((r - l) * t), gy = up === dn ? 0 : (z[up] - z[dn]) / (((dn - up) / tw) * t);
      const fn = [-gx, -gy, 1].map((q) => q / Math.hypot(gx, gy, 1));
      // (barycentric, from the snapped vertex positions, in the triangle of the cell holding the texel, as the GPU
      // interpolates; clamped where snapping left the texel outside it)
      const fx = (x - left) / m, fy = (upper - y) / m, I = Math.min(mw - 2, Math.floor(fx)), J = Math.min(mh - 2, Math.floor(fy)), q0 = J * mw + I;
      const [qa, qb, qc] = fx - I + fy - J <= 1 ? [q0, q0 + 1, q0 + mw] : [q0 + mw + 1, q0 + mw, q0 + 1], area = plan_area(qa, qb, qc);
      const wb = Math.max(0, ((x - vx[qa]) * (vy[qc] - vy[qa]) - (y - vy[qa]) * (vx[qc] - vx[qa])) / area || 0);
      const wc = Math.max(0, ((vx[qb] - vx[qa]) * (y - vy[qa]) - (vy[qb] - vy[qa]) * (x - vx[qa])) / area || 0), wa = Math.max(0, 1 - wb - wc);
      const N = [0, 0, 0];
      for (const [q, w] of [[qa, wa], [qb, wb], [qc, wc]]) for (let a = 0; a < 3; a++) N[a] += w * vn[q * 3 + a];
      const nl = Math.hypot(...N);
      N.forEach((q, a) => (N[a] = q / nl));
      const T = [1 - N[0] * N[0], -N[0] * N[1], -N[0] * N[2]], tl = Math.hypot(...T);
      T.forEach((q, a) => (T[a] = q / tl));
      const B = [N[1] * T[2] - N[2] * T[1], N[2] * T[0] - N[0] * T[2], N[0] * T[1] - N[1] * T[0]];
      let ts = [T, B, N].map((e) => e[0] * fn[0] + e[1] * fn[1] + e[2] * fn[2]);
      if (ts[2] < 0.05) { ts[2] = 0.05; ts = ts.map((q) => q / Math.hypot(...ts)); }
      ts.forEach((q, a) => (nrm[k * 4 + a] = Math.round((q * 0.5 + 0.5) * 255)));
      nrm[k * 4 + 3] = 255;
    }
  }
  // bleed edge texels outward (alpha stays 0) so filtering and mipmaps at the cut-out never pick up black
  const filled = Uint8Array.from(base, (q) => (Number.isNaN(q) ? 0 : 1));
  if (!filled.some(Boolean)) throw new Error('the model has no top surface');
  for (let pass = 0; pass < 8; pass++) {
    const was = filled.slice();
    for (let k = 0; k < n; k++) {
      if (was[k]) continue;
      const i = k % tw, from = [i > 0 ? k - 1 : -1, i < tw - 1 ? k + 1 : -1, k - tw, k + tw].find((q) => q >= 0 && q < n && was[q]);
      if (from === undefined) continue;
      for (const img of [col, nrm, orm]) img.copyWithin(k * 4, from * 4, from * 4 + 3);
      nrm[k * 4 + 3] = orm[k * 4 + 3] = 255;
      filled[k] = 1;
    }
  }
  for (let k = 0; k < n; k++) if (!filled[k]) { nrm.set([128, 128, 255, 255], k * 4); orm[k * 4 + 3] = 255; }

  // relief mesh: only the used grid vertices
  const vid = new Int32Array(mw * mh).fill(-1), rpos = [], rnor = [], rtan = [], ruv = [];
  const ridx = tri.map((k) => {
    if (vid[k] < 0) {
      vid[k] = rpos.length / 3;
      rpos.push(vx[k], vy[k], vz[k]);
      rnor.push(vn[k * 3], vn[k * 3 + 1], vn[k * 3 + 2]);
      rtan.push(vt[k * 4], vt[k * 4 + 1], vt[k * 4 + 2], 1);
      ruv.push((vx[k] - left) / (tw * t), (upper - vy[k]) / (th * t));
    }
    return vid[k];
  });

  // outline walls: marching squares on the surface mask (texel centres), segments oriented with the surface on
  // their left, linked into loops, simplified, then dropped from the surface height at the edge (where the relief
  // edge was pulled to) down to the model's base (z = 0)
  const edge_pt = (e) => {
    const k = e >> 1, i = k % tw, j = (k - i) / tw, o = e & 1 ? k + tw : k + 1;
    return e & 1 ? [left + (i + 0.5) * t, upper - (j + 1) * t, base[ok(k) ? k : o]] : [left + (i + 1) * t, upper - (j + 0.5) * t, base[ok(k) ? k : o]];
  };
  const next = new Map();
  for (let j = 0; j < th - 1; j++) {
    for (let i = 0; i < tw - 1; i++) {
      const k = j * tw + i, q = [ok(k), ok(k + 1), ok(k + tw + 1), ok(k + tw)]; // TL TR BR BL
      // cell edges T, R, B, L as edge ids (horizontal neighbour pair -> even, vertical -> odd) and their corners
      const edges = [[k * 2, 0, 1], [(k + 1) * 2 + 1, 1, 2], [(k + tw) * 2, 3, 2], [k * 2 + 1, 0, 3]].filter(([, a, b]) => q[a] !== q[b]);
      if (!edges.length) continue;
      // two crossings: one segment; four (saddle): cut off each inside corner on its own
      const pairs = edges.length === 2 ? [[edges[0], edges[1]]] : q[0] ? [[edges[3], edges[0]], [edges[1], edges[2]]] : [[edges[0], edges[1]], [edges[2], edges[3]]];
      for (const [[e1, a1, b1], [e2]] of pairs) {
        const p = edge_pt(e1), r = edge_pt(e2), inside = q[a1] ? a1 : b1;
        const ci = [left + (i + (inside === 1 || inside === 2 ? 1.5 : 0.5)) * t, upper - (j + (inside >= 2 ? 1.5 : 0.5)) * t];
        const left_of = (r[0] - p[0]) * (ci[1] - p[1]) - (r[1] - p[1]) * (ci[0] - p[0]) > 0;
        if (left_of) next.set(e1, e2); else next.set(e2, e1);
      }
    }
  }
  const loops = [];
  for (const start of next.keys()) {
    if (!next.has(start)) continue;
    const loop = [];
    for (let e = start; next.has(e);) { loop.push(edge_pt(e)); const f = next.get(e); next.delete(e); e = f; }
    if (loop.length >= 3) loops.push(simplify_loop(loop, t * 0.3));
  }
  const wpos = [], wnor = [], wuv = [], widx = [];
  for (const loop of loops) {
    const L = loop.length, segn = loop.map((p, a) => {
      const q = loop[(a + 1) % L], dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy);
      return [dy / len, -dx / len];
    });
    const smooth_n = (a, b) => (a[0] * b[0] + a[1] * b[1] > Math.SQRT1_2 ? [a[0] + b[0], a[1] + b[1]].map((q, _, v) => q / Math.hypot(...v)) : b);
    let along = 0;
    for (let a = 0; a < L; a++) {
      const p = loop[a], q = loop[(a + 1) % L], len = Math.hypot(q[0] - p[0], q[1] - p[1]);
      const zp = p[2], zq = q[2];
      if (Math.max(zp, zq) < 1e-3 * t) { along += len; continue; } // relative to the part: a fixed mm cut-off is a ruler
      const np = smooth_n(segn[(a + L - 1) % L], segn[a]), nq = smooth_n(segn[(a + 1) % L], segn[a]), v0 = wpos.length / 3;
      for (const [[x, y], zz, nn, sa] of [[p, 0, np, along], [q, 0, nq, along + len], [q, zq, nq, along + len], [p, zp, np, along]]) {
        wpos.push(x, y, zz);
        wnor.push(nn[0], nn[1], 0);
        wuv.push(sa / tile_mm, -zz / tile_mm); // tile v runs up, as tile_at(); glTF v runs down the image
      }
      widx.push(v0, v0 + 1, v0 + 2, v0, v0 + 2, v0 + 3);
      along += len;
    }
  }

  // to glTF: Y up, centred, unit-less (largest dimension = 1) unless true_scale (metres)
  const k_s = true_scale ? 0.001 : 1 / Math.max(x1 - x0, y1 - y0, top), ox = (x0 + x1) / 2, oy = (y0 + y1) / 2;
  const place = (a, scale) => { const out = new Float32Array(a.length); for (let q = 0; q < a.length; q += 3) { out[q] = (a[q] - (scale ? ox : 0)) * (scale || 1); out[q + 1] = a[q + 2] * (scale || 1); out[q + 2] = -(a[q + 1] - (scale ? oy : 0)) * (scale || 1); } return out; };
  const tan4 = new Float32Array(rtan.length);
  for (let q = 0; q < rtan.length; q += 4) tan4.set([rtan[q], rtan[q + 2], -rtan[q + 1], rtan[q + 3]], q);
  const glb = await write_glb({
    relief: { pos: place(rpos, k_s), nor: place(rnor), tan: tan4, uv: new Float32Array(ruv), idx: ridx },
    wall: { pos: place(wpos, k_s), nor: place(wnor), uv: new Float32Array(wuv), idx: widx },
    images: { col: await encode_png(col, tw, th), nrm: await encode_png(nrm, tw, th), orm: await encode_png(orm, tw, th), tile: tile ? await encode_png(tile.px, tile.w, tile.h) : null },
    look,
  });
  return { glb, summary: { texture: [tw, th], texel_mm: t, mesh_mm: m, relief_triangles: ridx.length / 3, wall_triangles: widx.length / 3, scale: k_s } };
}

// Douglas-Peucker on a closed loop of [x, y, z] (z deviation counts too: it is the wall top), split at the point
// farthest from its first point.
function simplify_loop(loop, tol) {
  const far = loop.reduce((b, p, a) => ((p[0] - loop[0][0]) ** 2 + (p[1] - loop[0][1]) ** 2 > (loop[b][0] - loop[0][0]) ** 2 + (loop[b][1] - loop[0][1]) ** 2 ? a : b), 0);
  const keep = new Uint8Array(loop.length);
  keep[0] = keep[far] = 1;
  const stack = [[0, far], [far, loop.length]];
  while (stack.length) {
    const [a, b] = stack.pop(), p = loop[a], q = loop[b % loop.length], dx = q[0] - p[0], dy = q[1] - p[1], len2 = dx * dx + dy * dy || 1;
    let best = -1, dmax = tol;
    for (let k = a + 1; k < b; k++) {
      const r = loop[k], f = Math.min(1, Math.max(0, ((r[0] - p[0]) * dx + (r[1] - p[1]) * dy) / len2));
      const d = Math.max(Math.abs((r[0] - p[0]) * dy - (r[1] - p[1]) * dx) / Math.sqrt(len2), Math.abs(r[2] - p[2] - f * (q[2] - p[2])));
      if (d > dmax) { dmax = d; best = k; }
    }
    if (best >= 0) { keep[best] = 1; stack.push([a, best], [best, b]); }
  }
  return loop.filter((_, k) => keep[k]);
}

// Binary glTF 2.0 with two primitives (relief, wall) and their materials; images are PNG bytes.
async function write_glb({ relief, wall, images, look }) {
  const chunks = [], views = [], accessors = [];
  let offset = 0;
  const view = (bytes, target) => {
    views.push({ buffer: 0, byteOffset: offset, byteLength: bytes.byteLength, ...(target && { target }) });
    chunks.push(bytes, new Uint8Array((4 - (bytes.byteLength % 4)) % 4));
    offset += bytes.byteLength + ((4 - (bytes.byteLength % 4)) % 4);
    return views.length - 1;
  };
  const accessor = (arr, type, target) => {
    const size = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[type], acc = { bufferView: view(new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength), target), componentType: arr instanceof Uint32Array ? 5125 : 5126, count: arr.length / size, type };
    if (target === 34962 && type === 'VEC3') {
      acc.min = [0, 1, 2].map((a) => arr.reduce((v, q, k) => (k % 3 === a ? Math.min(v, q) : v), Infinity));
      acc.max = [0, 1, 2].map((a) => arr.reduce((v, q, k) => (k % 3 === a ? Math.max(v, q) : v), -Infinity));
    }
    accessors.push(acc);
    return accessors.length - 1;
  };
  const image_ids = {}, img_list = [];
  for (const [key, png] of Object.entries(images)) if (png) { image_ids[key] = img_list.length; img_list.push({ bufferView: view(png), mimeType: 'image/png' }); }
  const textures = [], tex = (key, sampler) => ({ index: textures.push({ sampler, source: image_ids[key] }) - 1 });
  const coat = look.clearcoat > 0, orm = tex('orm', 0);
  const relief_mat = {
    pbrMetallicRoughness: { baseColorTexture: tex('col', 0), metallicRoughnessTexture: orm, metallicFactor: 1, roughnessFactor: 1 },
    normalTexture: tex('nrm', 0),
    alphaMode: 'MASK',
    alphaCutoff: 0.5,
    ...(coat && { extensions: { KHR_materials_clearcoat: { clearcoatFactor: look.clearcoat, clearcoatTexture: orm, clearcoatRoughnessFactor: look.clearcoat_rough } } }),
  };
  const wall_mat = {
    pbrMetallicRoughness: { baseColorFactor: [...look.color, 1], metallicFactor: look.metal, roughnessFactor: look.rough, ...(images.tile && { baseColorTexture: tex('tile', 1) }) },
    ...(coat && { extensions: { KHR_materials_clearcoat: { clearcoatFactor: look.clearcoat, clearcoatRoughnessFactor: look.clearcoat_rough } } }),
  };
  const prim = (g, material) => ({
    attributes: { POSITION: accessor(g.pos, 'VEC3', 34962), NORMAL: accessor(g.nor, 'VEC3', 34962), ...(g.tan && { TANGENT: accessor(g.tan, 'VEC4', 34962) }), TEXCOORD_0: accessor(g.uv, 'VEC2', 34962) },
    indices: accessor(Uint32Array.from(g.idx), 'SCALAR', 34963),
    material,
  });
  const primitives = [prim(relief, 0)];
  if (wall.idx.length) primitives.push(prim(wall, 1));
  const json = {
    asset: { version: '2.0', generator: 'emucraft laser' },
    ...(coat && { extensionsUsed: ['KHR_materials_clearcoat'] }),
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives }],
    materials: [relief_mat, wall_mat],
    samplers: [{ magFilter: 9729, minFilter: 9987, wrapS: 33071, wrapT: 33071 }, { magFilter: 9729, minFilter: 9987, wrapS: 10497, wrapT: 10497 }],
    textures,
    images: img_list,
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: offset }],
  };
  const enc = new TextEncoder().encode(JSON.stringify(json)), jpad = (4 - (enc.length % 4)) % 4;
  const total = 12 + 8 + enc.length + jpad + 8 + offset, out = new Uint8Array(total), dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, enc.length + jpad, true); dv.setUint32(16, 0x4e4f534a, true);
  out.set(enc, 20); out.fill(0x20, 20 + enc.length, 20 + enc.length + jpad);
  let o = 20 + enc.length + jpad;
  dv.setUint32(o, offset, true); dv.setUint32(o + 4, 0x004e4942, true); o += 8;
  for (const ch of chunks) { out.set(ch, o); o += ch.byteLength; }
  return out;
}
