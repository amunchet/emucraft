import { describe, it, expect } from 'vitest';
import {
  DEFAULT_LASER, beam_diameter, rayleigh_range, grey_to_intensity, rasterize_surface, engrave, resample_image, plan_grid,
} from '../src/sim.js';

// Triangle soup of an axis-aligned box centred in XY with min Z = 0 (walls included, mixed winding).
function box(w, h, t) {
  const x0 = -w / 2, x1 = w / 2, y0 = -h / 2, y1 = h / 2;
  const q = (a, b, c, d) => [...a, ...b, ...c, ...a, ...c, ...d];
  return new Float32Array([
    ...q([x0, y0, t], [x1, y0, t], [x1, y1, t], [x0, y1, t]), // top, CCW
    ...q([x0, y0, 0], [x0, y1, 0], [x1, y1, 0], [x1, y0, 0]), // bottom, CW from above
    ...q([x0, y0, 0], [x1, y0, 0], [x1, y0, t], [x0, y0, t]),
    ...q([x1, y0, 0], [x1, y1, 0], [x1, y1, t], [x1, y0, t]),
    ...q([x1, y1, 0], [x0, y1, 0], [x0, y1, t], [x1, y1, t]),
    ...q([x0, y1, 0], [x0, y0, 0], [x0, y0, t], [x0, y1, t]),
  ]);
}

// Triangulated heightfield z = f(x, y) over [-w/2, w/2] x [-h/2, h/2].
function heightfield(w, h, n, f) {
  const out = [];
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const xa = -w / 2 + (w * i) / n, xb = -w / 2 + (w * (i + 1)) / n;
    const ya = -h / 2 + (h * j) / n, yb = -h / 2 + (h * (j + 1)) / n;
    const p = (x, y) => [x, y, f(x, y)];
    out.push(...p(xa, ya), ...p(xb, ya), ...p(xb, yb), ...p(xa, ya), ...p(xb, yb), ...p(xa, yb));
  }
  return new Float32Array(out);
}

// World XY of grid cell (i, j) per the contract mapping.
function cell_world(p, gw, gh, i, j) {
  const u = (i + 0.5) / gw - 0.5, v = 0.5 - (j + 0.5) / gh;
  const lx = u * p.width_mm, ly = v * p.height_mm;
  return [Math.cos(p.angle) * lx - Math.sin(p.angle) * ly + p.cx, Math.sin(p.angle) * lx + Math.cos(p.angle) * ly + p.cy];
}

// Flat plate fully covering the grid, with a black square of half-size `half` px in the middle.
function square_job(n, half, grey_out = 255, grey_in = 0) {
  const surface = new Float32Array(n * n).fill(5);
  const grey = new Float32Array(n * n).fill(grey_out);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++)
    if (Math.abs(i - n / 2 + 0.5) < half && Math.abs(j - n / 2 + 0.5) < half) grey[j * n + i] = grey_in;
  return { surface, grey };
}

describe('beam model', () => {
  it('spot is the focus spot at dz=0 and sqrt2 larger at the Rayleigh range', () => {
    const l = DEFAULT_LASER;
    expect(beam_diameter(l, 0)).toBeCloseTo(l.spot_mm, 12);
    const zr = rayleigh_range(l);
    expect(zr).toBeCloseTo((0.05 * 160) / 7, 12);
    expect(beam_diameter(l, zr)).toBeCloseTo(l.spot_mm * Math.SQRT2, 12);
    expect(beam_diameter(l, -zr)).toBeCloseTo(beam_diameter(l, zr), 12);
  });
});

describe('grey_to_intensity', () => {
  it('maps black to full burn, white to none, with invert and gamma', () => {
    const g = new Uint8Array([0, 255, 128]);
    const a = grey_to_intensity(g, DEFAULT_LASER);
    expect([a[0], a[1]]).toEqual([1, 0]);
    expect(a[2]).toBeCloseTo(127 / 255, 6);
    const b = grey_to_intensity(g, { ...DEFAULT_LASER, invert: true });
    expect([b[0], b[1]]).toEqual([0, 1]);
    const c = grey_to_intensity(g, { ...DEFAULT_LASER, gamma: 2 });
    expect(c[2]).toBeCloseTo((127 / 255) ** 2, 6);
  });
});

describe('rasterize_surface', () => {
  it('flat box gives constant top Z inside, NaN outside', () => {
    const p = { cx: 0, cy: 0, angle: 0, width_mm: 100, height_mm: 80 };
    const s = rasterize_surface(box(60, 40, 5), p, 100, 80);
    let inside = 0, outside = 0;
    for (let j = 0; j < 80; j++) for (let i = 0; i < 100; i++) {
      const [x, y] = cell_world(p, 100, 80, i, j), z = s[j * 100 + i];
      if (Math.abs(x) < 29.9 && Math.abs(y) < 19.9) { expect(z).toBeCloseTo(5, 5); inside++; }
      else if (Math.abs(x) > 30.1 || Math.abs(y) > 20.1) { expect(z).toBeNaN(); outside++; }
    }
    expect(inside).toBeGreaterThan(2000);
    expect(outside).toBeGreaterThan(4000);
  });

  it('rotated, offset placement samples where the contract mapping says', () => {
    // small box sits in model space at (12, -7); place a rotated grid so that one specific cell lands on it
    const soup = box(1, 1, 3).map((v, k) => (k % 3 === 0 ? v + 12 : k % 3 === 1 ? v - 7 : v));
    const p = { cx: 10, cy: -5, angle: 0.7, width_mm: 20, height_mm: 10 };
    const gw = 80, gh = 40;
    const s = rasterize_surface(soup, p, gw, gh);
    let hits = 0;
    for (let j = 0; j < gh; j++) for (let i = 0; i < gw; i++) {
      const [x, y] = cell_world(p, gw, gh, i, j), z = s[j * gw + i];
      if (Math.abs(x - 12) < 0.45 && Math.abs(y + 7) < 0.45) { expect(z).toBeCloseTo(3, 5); hits++; }
      else if (Math.abs(x - 12) > 0.55 || Math.abs(y + 7) > 0.55) expect(z).toBeNaN();
    }
    expect(hits).toBeGreaterThan(5);
  });

  it('interpolates sloped and curved tops and keeps the topmost surface', () => {
    const p = { cx: 0, cy: 0, angle: 0.3, width_mm: 20, height_mm: 20 };
    const slope = rasterize_surface(heightfield(40, 40, 4, (x, y) => 2 + 0.1 * x + 0.05 * y), p, 50, 50);
    const f = (x, y) => 4 - 0.01 * (x * x + y * y);
    const dome = rasterize_surface(heightfield(40, 40, 60, f), p, 50, 50);
    for (const [i, j] of [[0, 0], [25, 10], [49, 49], [10, 40]]) {
      const [x, y] = cell_world(p, 50, 50, i, j);
      expect(slope[j * 50 + i]).toBeCloseTo(2 + 0.1 * x + 0.05 * y, 4);
      expect(dome[j * 50 + i]).toBeCloseTo(f(x, y), 1);
    }
    // stacking a lower copy and reversing winding must not change the result
    const under = heightfield(40, 40, 4, (x, y) => 0.1 * x);
    const flipped = new Float32Array(under.length);
    for (let t = 0; t < under.length; t += 9) flipped.set([...under.slice(t, t + 3), ...under.slice(t + 6, t + 9), ...under.slice(t + 3, t + 6)], t);
    const both = new Float32Array([...flipped, ...heightfield(40, 40, 4, (x, y) => 2 + 0.1 * x + 0.05 * y)]);
    expect(Array.from(rasterize_surface(both, p, 50, 50))).toEqual(Array.from(slope));
  });

  it('skips degenerate triangles', () => {
    const soup = new Float32Array([0, 0, 9, 1, 1, 9, 2, 2, 9, 0, 0, 9, 0, 0, 9, 0, 0, 9]);
    const s = rasterize_surface(soup, { cx: 0, cy: 0, angle: 0, width_mm: 4, height_mm: 4 }, 8, 8);
    expect(s.every(Number.isNaN)).toBe(true);
  });

  it('is fast for 200k triangles on a 1000x1000 grid', () => {
    const soup = heightfield(60, 40, 316, (x, y) => 5 + 0.002 * x * y);
    expect(soup.length / 9).toBeGreaterThan(199000);
    const t0 = performance.now();
    const s = rasterize_surface(soup, { cx: 0, cy: 0, angle: 0.2, width_mm: 50, height_mm: 30 }, 1000, 1000);
    expect(performance.now() - t0).toBeLessThan(3000);
    expect(s.every(Number.isFinite)).toBe(true);
  });
});

describe('engrave', () => {
  const n = 64, pixel = 0.05;
  const run = (laser, job = square_job(n, 12)) =>
    engrave(job.surface, grey_to_intensity(job.grey, laser), n, n, pixel, laser);

  it('black square on white: full depth in the middle, nothing far away, symmetric', () => {
    const l = { ...DEFAULT_LASER };
    const progress = [];
    const job = square_job(n, 12);
    const res = engrave(job.surface, grey_to_intensity(job.grey, l), n, n, pixel, l, (f) => progress.push(f));
    const mid = res.depth[(n / 2) * n + n / 2];
    expect(mid).toBeGreaterThan(0.9 * l.passes * l.depth_per_pass_mm);
    expect(mid).toBeLessThanOrEqual(l.passes * l.depth_per_pass_mm + 1e-6);
    expect(res.depth[2 * n + 2]).toBeLessThan(1e-9);
    expect(res.height[(n / 2) * n + n / 2]).toBeCloseTo(5 - mid, 5);
    expect(res.max_depth).toBeCloseTo(Math.max(...res.depth), 6);
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      expect(res.depth[j * n + i]).toBeCloseTo(res.depth[j * n + (n - 1 - i)], 6);
      expect(res.depth[j * n + i]).toBeCloseTo(res.depth[i * n + j], 6);
    }
    expect(progress.length).toBe(10);
    expect(progress.at(-1)).toBe(1);
    expect(res.stats.focal_z).toBe(5);
    expect(res.stats.min_spot_mm).toBeCloseTo(l.spot_mm, 6);
    expect(res.stats.max_spot_mm).toBeGreaterThan(l.spot_mm);
  });

  it('defocus widens and weakens the mark', () => {
    const focused = run({ ...DEFAULT_LASER });
    const defocused = run({ ...DEFAULT_LASER, focus_offset_mm: -3, threshold: 0 });
    const c = (n / 2) * n + n / 2;
    expect(defocused.depth[c]).toBeLessThan(0.5 * focused.depth[c]);
    // one pixel outside the square edge, relative to the centre depth: the defocused edge is softer
    const out = (n / 2) * n + n / 2 + 12;
    expect(defocused.depth[out] / defocused.depth[c]).toBeGreaterThan(2 * (focused.depth[out] / focused.depth[c]));
    expect(defocused.stats.min_spot_mm).toBeGreaterThan(focused.stats.min_spot_mm);
  });

  it('zero intensity removes nothing; removal grows with darkness, power and passes', () => {
    const blank = run(DEFAULT_LASER, square_job(n, 12, 255, 255));
    expect(blank.max_depth).toBe(0);
    const c = (n / 2) * n + n / 2;
    const at = (laser, grey_in = 0) => run(laser, square_job(n, 12, 255, grey_in)).depth[c];
    expect(at(DEFAULT_LASER, 128)).toBeLessThan(at(DEFAULT_LASER, 0));
    expect(at(DEFAULT_LASER, 200)).toBeLessThan(at(DEFAULT_LASER, 128));
    expect(at({ ...DEFAULT_LASER, power_w: 10 })).toBeLessThan(at(DEFAULT_LASER));
    expect(at({ ...DEFAULT_LASER, power_w: 10, threshold: 0 })).toBeCloseTo(at({ ...DEFAULT_LASER, threshold: 0 }) / 2, 2);
    expect(at({ ...DEFAULT_LASER, passes: 20 })).toBeGreaterThan(at(DEFAULT_LASER));
  });

  it('many passes are grouped into 24 steps without changing the total', () => {
    const progress = [];
    const job = square_job(n, 12);
    const l = { ...DEFAULT_LASER, passes: 100, depth_per_pass_mm: 0.002 };
    const res = engrave(job.surface, grey_to_intensity(job.grey, l), n, n, pixel, l, (f) => progress.push(f));
    expect(progress.length).toBe(24);
    expect(res.depth[(n / 2) * n + n / 2]).toBeCloseTo(0.2, 2);
  });

  it('invert burns the white field instead of the black square', () => {
    const res = run({ ...DEFAULT_LASER, invert: true });
    expect(res.depth[(n / 2) * n + n / 2]).toBeLessThan(1e-9);
    expect(res.depth[2 * n + 2]).toBeGreaterThan(0.15);
  });

  it('threshold suppresses faint greys', () => {
    const faint = square_job(n, 12, 255, 240); // intensity ~0.06
    expect(run({ ...DEFAULT_LASER, threshold: 0.1 }, faint).max_depth).toBe(0);
    expect(run({ ...DEFAULT_LASER, threshold: 0 }, faint).max_depth).toBeGreaterThan(0);
  });

  it('NaN cells neither contribute nor receive', () => {
    const job = square_job(n, 12);
    const l = { ...DEFAULT_LASER, focus_offset_mm: -1, threshold: 0 };
    for (let j = 0; j < n; j++) for (let i = n / 2; i < n; i++) job.surface[j * n + i] = NaN;
    const res = engrave(job.surface, grey_to_intensity(job.grey, l), n, n, pixel, l);
    for (let j = 0; j < n; j++) for (let i = n / 2; i < n; i++) {
      expect(res.height[j * n + i]).toBeNaN();
      expect(res.depth[j * n + i]).toBe(0);
    }
    expect(res.depth.every(Number.isFinite)).toBe(true);
    expect(res.depth[(n / 2) * n + n / 2 - 4]).toBeGreaterThan(0.1);
    // only-NaN surface: nothing happens, nothing throws
    const none = engrave(new Float32Array(16).fill(NaN), new Float32Array(16).fill(1), 4, 4, pixel, DEFAULT_LASER);
    expect(none.max_depth).toBe(0);
  });

  it('a burn on an upper plate does not bleed onto a lower, out-of-focus, unmarked flange; volume is conserved', () => {
    // plate at z=10 fully black for i<200, flange 20 mm lower and white beyond (the spread is set by the burning cell)
    const W = 400, H = 20, surface = new Float32Array(W * H), inten = new Float32Array(W * H);
    for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) { surface[j * W + i] = i < 200 ? 10 : -10; inten[j * W + i] = i < 200 ? 1 : 0; }
    const l = { ...DEFAULT_LASER, threshold: 0 };
    const res = engrave(surface, inten, W, H, 0.025, l);
    const row = 10 * W;
    expect(res.depth[row + 202]).toBeLessThan(1e-4);
    expect(res.depth[row + 210]).toBe(0);
    let vol = 0;
    for (let k = 0; k < W * H; k++) vol += res.depth[k];
    expect(vol / (200 * H * res.depth[row + 100])).toBeCloseTo(1, 3); // the plate keeps its own removal
  });

  it('isolated dots on a slope keep the peak of a Gaussian at their own spot size', () => {
    const W = 300, H = 41, pix = 0.025, surface = new Float32Array(W * H), inten = new Float32Array(W * H);
    for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) surface[j * W + i] = 10 - i * 0.01;
    const l = { ...DEFAULT_LASER, threshold: 0, passes: 1, focus_offset_mm: 0.5 };
    for (const i of [20, 80, 140, 200, 260]) inten[20 * W + i] = 1;
    const res = engrave(surface, inten, W, H, pix, l);
    for (const i of [20, 80, 140, 200, 260]) {
      const d = beam_diameter(l, 10.5 - surface[20 * W + i]), sigma = d / pix / 4;
      const r = (l.spot_mm / d) ** 2 * l.depth_per_pass_mm;
      let norm = 0;
      for (let k = -40; k <= 40; k++) norm += Math.exp(-k * k / (2 * sigma * sigma));
      expect(res.depth[20 * W + i] / (r / (norm * norm))).toBeCloseTo(1, 1);
    }
  });

  it('runs a 1000x1000 grid in reasonable time', () => {
    const big = 1000;
    const job = square_job(big, 300, 255, 0);
    const l = { ...DEFAULT_LASER, focus_offset_mm: -2 };
    const t0 = performance.now();
    const res = engrave(job.surface, grey_to_intensity(job.grey, l), big, big, 0.025, l);
    expect(performance.now() - t0).toBeLessThan(30000);
    expect(res.max_depth).toBeGreaterThan(0);
  }, 60000);
});

describe('resample_image and plan_grid', () => {
  it('bilinear resample keeps constants, interpolates and supersamples', () => {
    const g = new Float32Array([0, 100, 200, 300]); // 2x2
    const up = resample_image(g, 2, 2, 4, 4);
    expect(up[0]).toBe(0);
    expect(up[15]).toBe(300);
    expect(up[1]).toBeCloseTo(25, 5);
    const flat = resample_image(new Float32Array(30).fill(77), 6, 5, 3, 2);
    expect(Array.from(flat)).toEqual(new Array(6).fill(77));
    expect(() => resample_image(g, 3, 3, 4, 4)).toThrow();
  });

  it('downsampling averages a 1-px stripe pattern instead of aliasing', () => {
    const w = 400, h = 4, g = new Float32Array(w * h);
    for (let k = 0; k < g.length; k++) g[k] = k % 2 ? 255 : 0;
    const out = resample_image(g, w, h, 231, 2);
    for (let i = 2; i < 229; i++) expect(Math.abs(out[i] - 127.5)).toBeLessThan(6);
  });

  it('plan_grid supersamples and caps cell count preserving aspect', () => {
    expect(plan_grid(300, 200, 2)).toEqual({ grid_w: 600, grid_h: 400 });
    const { grid_w, grid_h } = plan_grid(4000, 1000, 4, 4e6);
    expect(grid_w * grid_h).toBeLessThanOrEqual(4e6);
    expect(grid_w / grid_h).toBeCloseTo(4, 2);
    expect(grid_w * grid_h).toBeGreaterThan(3.9e6);
  });
});
