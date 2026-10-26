// Pure laser-engraving simulation core. No DOM, no three.js: runs in node (tests) and in the Web Worker.
// Units are millimetres; Z is up and the laser fires along -Z.

export const DEFAULT_LASER = {
  spot_mm: 0.05,
  focal_length_mm: 160,
  beam_diameter_mm: 7,
  focus_offset_mm: 0,
  power_w: 20,
  max_power_w: 20,
  depth_per_pass_mm: 0.02,
  passes: 10,
  threshold: 0.05,
  invert: false,
  gamma: 1,
};

const MAX_STEPS = 24;
const MAX_LEVELS = 6;
const MIN_SIGMA_PX = 0.3;

// 1/e² beam diameter at distance dz from the focal plane (Gaussian beam with an f-number set by the lens).
export function beam_diameter(laser, dz) {
  const cone = (dz * laser.beam_diameter_mm) / laser.focal_length_mm;
  return Math.sqrt(laser.spot_mm * laser.spot_mm + cone * cone);
}

// Rayleigh range: distance from focus at which the spot has grown by sqrt(2).
export function rayleigh_range(laser) {
  return (laser.spot_mm * laser.focal_length_mm) / laser.beam_diameter_mm;
}

// Map 0..255 grey to 0..1 burn intensity (black burns deepest unless inverted), with a gamma response curve.
export function grey_to_intensity(grey, laser) {
  const out = new Float32Array(grey.length);
  const gamma = laser.gamma ?? 1;
  for (let k = 0; k < grey.length; k++) {
    const t = Math.min(1, Math.max(0, grey[k] / 255));
    out[k] = Math.pow(laser.invert ? t : 1 - t, gamma);
  }
  return out;
}

// Top surface Z seen from +Z at each grid cell centre (placement mapping from the contract), NaN where no surface.
// CPU z-buffer: every triangle is moved into continuous grid coordinates and bbox-rasterized, keeping max Z.
export function rasterize_surface(positions, placement, grid_w, grid_h) {
  if (positions.length % 9 !== 0) throw new Error(`positions length ${positions.length} is not a multiple of 9`);
  if (!(placement.width_mm > 0 && placement.height_mm > 0)) throw new Error('placement size must be positive');
  const out = new Float32Array(grid_w * grid_h).fill(-Infinity);
  const cos = Math.cos(placement.angle), sin = Math.sin(placement.angle);
  // world -> continuous grid index, where integer (gi, gj) are cell centres
  const sx = grid_w / placement.width_mm, sy = grid_h / placement.height_mm;
  const gx = new Float64Array(3), gy = new Float64Array(3), gz = new Float64Array(3);
  for (let t = 0; t < positions.length; t += 9) {
    for (let v = 0; v < 3; v++) {
      const dx = positions[t + v * 3] - placement.cx, dy = positions[t + v * 3 + 1] - placement.cy;
      const lx = cos * dx + sin * dy, ly = -sin * dx + cos * dy;
      gx[v] = lx * sx + grid_w * 0.5 - 0.5;
      gy[v] = grid_h * 0.5 - 0.5 - ly * sy;
      gz[v] = positions[t + v * 3 + 2];
    }
    const area = (gx[1] - gx[0]) * (gy[2] - gy[0]) - (gx[2] - gx[0]) * (gy[1] - gy[0]);
    if (!(Math.abs(area) > 1e-12)) continue; // degenerate in plan view (incl. vertical walls)
    const i0 = Math.max(0, Math.ceil(Math.min(gx[0], gx[1], gx[2]) - 1e-9));
    const i1 = Math.min(grid_w - 1, Math.floor(Math.max(gx[0], gx[1], gx[2]) + 1e-9));
    const j0 = Math.max(0, Math.ceil(Math.min(gy[0], gy[1], gy[2]) - 1e-9));
    const j1 = Math.min(grid_h - 1, Math.floor(Math.max(gy[0], gy[1], gy[2]) + 1e-9));
    if (i0 > i1 || j0 > j1) continue;
    const inv = 1 / area;
    // barycentric weights are affine in (i, j): w = a + b*i + c*j
    const a0 = (gx[1] * gy[2] - gx[2] * gy[1]) * inv, b0 = (gy[1] - gy[2]) * inv, c0 = (gx[2] - gx[1]) * inv;
    const a1 = (gx[2] * gy[0] - gx[0] * gy[2]) * inv, b1 = (gy[2] - gy[0]) * inv, c1 = (gx[0] - gx[2]) * inv;
    const eps = -1e-7;
    for (let j = j0; j <= j1; j++) {
      const row = j * grid_w;
      for (let i = i0; i <= i1; i++) {
        const w0 = a0 + b0 * i + c0 * j;
        if (w0 < eps) continue;
        const w1 = a1 + b1 * i + c1 * j;
        if (w1 < eps) continue;
        const w2 = 1 - w0 - w1;
        if (w2 < eps) continue;
        const z = w0 * gz[0] + w1 * gz[1] + w2 * gz[2];
        if (z > out[row + i]) out[row + i] = z;
      }
    }
  }
  for (let k = 0; k < out.length; k++) if (out[k] === -Infinity) out[k] = NaN;
  return out;
}

// Separable Gaussian blur (kernel radius ~3 sigma, normalised, clamped edges) of src, ADDED into out; tmp is
// scratch. Rows flagged empty in `live` (all zero) are skipped in the horizontal pass.
function blur_add(src, w, h, sigma, tmp, out, live) {
  const r = Math.max(1, Math.ceil(sigma * 3));
  const kernel = new Float32Array(2 * r + 1);
  let sum = 0;
  for (let k = -r; k <= r; k++) sum += kernel[k + r] = Math.exp((-k * k) / (2 * sigma * sigma));
  for (let k = 0; k < kernel.length; k++) kernel[k] /= sum;
  for (let j = 0; j < h; j++) {
    const row = j * w;
    if (!live[j]) { tmp.fill(0, row, row + w); continue; }
    for (let i = 0; i < w; i++) {
      let acc = 0;
      if (i >= r && i < w - r) for (let k = -r, o = row + i - r; k <= r; k++, o++) acc += kernel[k + r] * src[o];
      else for (let k = -r; k <= r; k++) acc += kernel[k + r] * src[row + Math.min(w - 1, Math.max(0, i + k))];
      tmp[row + i] = acc;
    }
  }
  // vertical pass row by row, so the inner loop runs along memory
  for (let j = 0; j < h; j++) {
    const row = j * w;
    for (let k = -r; k <= r; k++) {
      const wk = kernel[k + r], base = Math.min(h - 1, Math.max(0, j + k)) * w;
      for (let i = 0; i < w; i++) out[row + i] += wk * tmp[base + i];
    }
  }
}

// Multi-step ablation: each step removes material in proportion to local fluence, spread by the spot at the
// burning cell (depth-dependent, defocused) via a spatially-variant Gaussian blur. Returns final heights/depths.
// The variant blur is a scatter: each source is split between the two sigma-ladder levels bracketing its own
// sigma (weights linear in 1/sigma^2, so peaks match), and every level is blurred once; volume is conserved.
export function engrave(surface, intensity, grid_w, grid_h, pixel_mm, laser, on_progress) {
  const n = grid_w * grid_h;
  if (surface.length !== n || intensity.length !== n) throw new Error(`surface/intensity must have ${n} cells`);
  if (!(pixel_mm > 0)) throw new Error('pixel_mm must be positive');
  if (!(laser.threshold >= 0 && laser.threshold < 1)) throw new Error('threshold must be in [0, 1)');
  const passes = Math.max(1, Math.round(laser.passes));
  const steps = Math.min(passes, MAX_STEPS);
  const per_step = (laser.depth_per_pass_mm * passes) / steps;
  const power = laser.power_w / laser.max_power_w;
  const th = laser.threshold;

  let top = -Infinity;
  for (let k = 0; k < n; k++) if (surface[k] > top) top = surface[k];
  const focal_z = (top === -Infinity ? 0 : top) + laser.focus_offset_mm;

  const z = Float32Array.from(surface);
  const r = new Float32Array(n), sig = new Float32Array(n), band = new Float32Array(n), tmp = new Float32Array(n), removal = new Float32Array(n);
  const lev = new Int8Array(n), live = new Uint8Array(grid_h);
  let min_spot = Infinity, max_spot = -Infinity;

  for (let s = 0; s < steps; s++) {
    let smin = Infinity, smax = -Infinity, any = false;
    for (let k = 0; k < n; k++) {
      r[k] = 0;
      if (Number.isNaN(z[k])) continue;
      const d = beam_diameter(laser, focal_z - z[k]);
      const e = intensity[k] * power * (laser.spot_mm / d) ** 2;
      if (e <= th) continue;
      r[k] = ((e - th) / (1 - th)) * per_step;
      sig[k] = d / pixel_mm / 4;
      any = true;
      if (sig[k] < smin) smin = sig[k];
      if (sig[k] > smax) smax = sig[k];
      if (d < min_spot) min_spot = d;
      if (d > max_spot) max_spot = d;
    }
    if (any) {
      // sigma ladder over the burning cells: optional identity level, then geometric levels up to the widest spot
      const ladder = [];
      if (smin < MIN_SIGMA_PX) ladder.push(0);
      if (smax >= MIN_SIGMA_PX) {
        const lo = Math.max(smin, MIN_SIGMA_PX), count = Math.min(MAX_LEVELS - ladder.length, smax / lo < 1.001 ? 1 : Math.ceil(Math.log(smax / lo) / Math.log(1.15)) + 1);
        for (let l = 0; l < count; l++) ladder.push(count === 1 ? smax : lo * Math.pow(smax / lo, l / (count - 1)));
      }
      const last = ladder.length - 1, g0 = ladder[0] === 0 ? 1 : 0;
      // per source: lower bracketing level in lev, weight of the upper level in sig
      for (let k = 0; k < n; k++) {
        if (r[k] === 0) continue;
        const sigma = sig[k];
        let l = g0;
        if (sigma < MIN_SIGMA_PX || sigma <= ladder[g0]) { lev[k] = sigma < MIN_SIGMA_PX ? 0 : g0; sig[k] = 0; continue; }
        if (sigma >= ladder[last]) { lev[k] = last; sig[k] = 0; continue; }
        while (ladder[l + 1] < sigma) l++;
        const a = ladder[l] ** -2, b = ladder[l + 1] ** -2;
        lev[k] = l;
        sig[k] = (a - sigma ** -2) / (a - b);
      }
      removal.fill(0);
      for (let l = 0; l <= last; l++) {
        live.fill(0);
        for (let j = 0; j < grid_h; j++) {
          for (let k = j * grid_w, e = k + grid_w; k < e; k++) {
            const v = r[k] === 0 ? 0 : lev[k] === l ? r[k] * (1 - sig[k]) : lev[k] === l - 1 ? r[k] * sig[k] : 0;
            band[k] = v;
            if (v !== 0) live[j] = 1;
          }
        }
        if (ladder[l] === 0) for (let k = 0; k < n; k++) removal[k] += band[k];
        else if (live.includes(1)) blur_add(band, grid_w, grid_h, ladder[l], tmp, removal, live);
      }
      for (let k = 0; k < n; k++) if (!Number.isNaN(z[k])) z[k] -= removal[k];
    }
    on_progress?.((s + 1) / steps);
  }

  const depth = new Float32Array(n);
  let max_depth = 0;
  for (let k = 0; k < n; k++) {
    if (Number.isNaN(z[k])) continue;
    depth[k] = Math.max(0, surface[k] - z[k]);
    if (depth[k] > max_depth) max_depth = depth[k];
  }
  if (min_spot === Infinity) min_spot = max_spot = beam_diameter(laser, laser.focus_offset_mm);
  return { height: z, depth, max_depth, stats: { min_spot_mm: min_spot, max_spot_mm: max_spot, focal_z } };
}

// Resample a grey image to the grid size, separably and pixel-centre aligned (clamped at the borders): bilinear
// when upsampling; when downsampling, a tent one grid cell in radius integrated over each source pixel, so fine
// dither/halftone patterns are averaged instead of aliasing into moire.
export function resample_image(grey, img_w, img_h, grid_w, grid_h) {
  if (grey.length !== img_w * img_h) throw new Error(`grey has ${grey.length} values, expected ${img_w * img_h}`);
  // taps[o] = [[source index, weight], ...] for output index o along one axis
  const taps = (n_src, n_dst) => {
    const f = n_src / n_dst, out = [];
    // cumulative area of the unit-area tent of radius f, at offset u from its centre
    const cdf = (u) => (u <= -f ? 0 : u >= f ? 1 : u < 0 ? (u + f) ** 2 / (2 * f * f) : 1 - (f - u) ** 2 / (2 * f * f));
    for (let o = 0; o < n_dst; o++) {
      const x = (o + 0.5) * f - 0.5;
      if (f <= 1) {
        const c = Math.min(n_src - 1, Math.max(0, x)), x0 = Math.floor(c), t = c - x0;
        out.push([[x0, 1 - t], [Math.min(n_src - 1, x0 + 1), t]]);
        continue;
      }
      const list = [];
      for (let k = Math.round(x - f); k <= Math.round(x + f); k++) {
        const w = cdf(k + 0.5 - x) - cdf(k - 0.5 - x);
        if (w > 0) list.push([Math.min(n_src - 1, Math.max(0, k)), w]);
      }
      out.push(list);
    }
    return out;
  };
  const tx = taps(img_w, grid_w), ty = taps(img_h, grid_h);
  const rows = new Float64Array(grid_w * img_h), out = new Float32Array(grid_w * grid_h);
  for (let j = 0; j < img_h; j++) {
    for (let i = 0; i < grid_w; i++) {
      let acc = 0;
      for (const [k, w] of tx[i]) acc += w * grey[j * img_w + k];
      rows[j * grid_w + i] = acc;
    }
  }
  for (let j = 0; j < grid_h; j++) {
    for (let i = 0; i < grid_w; i++) {
      let acc = 0;
      for (const [k, w] of ty[j]) acc += w * rows[k * grid_w + i];
      out[j * grid_w + i] = acc;
    }
  }
  return out;
}

// Grid size for an image at a supersample factor, uniformly shrunk to fit max_cells (aspect preserved).
export function plan_grid(img_w, img_h, supersample = 1, max_cells = 4e6) {
  let grid_w = Math.max(1, Math.round(img_w * supersample));
  let grid_h = Math.max(1, Math.round(img_h * supersample));
  if (grid_w * grid_h > max_cells) {
    const s = Math.sqrt(max_cells / (grid_w * grid_h));
    grid_w = Math.max(1, Math.floor(grid_w * s));
    grid_h = Math.max(1, Math.floor(grid_h * s));
  }
  return { grid_w, grid_h };
}
