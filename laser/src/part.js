// Shared by the app and the API: material presets, procedural textures and model loading. No DOM.
import * as THREE from 'three';

// Material presets: base look plus the look of lasered material. mark_full = removed depth (mm) at which the
// mark look is fully developed; preview = overlay colour used while aligning.
export const PRESETS = {
  anodized_black: { label: 'Anodized aluminium, black', color: '#1c1d21', metal: 0.55, rough: 0.38, mark: '#d3d6d9', mark_metal: 0.75, mark_rough: 0.5, mark_full: 0.004, preview: '#ff7a3d' },
  anodized_blue: { label: 'Anodized aluminium, blue', color: '#1d4ea6', metal: 0.6, rough: 0.36, mark: '#d6d9dc', mark_metal: 0.75, mark_rough: 0.5, mark_full: 0.004, preview: '#ff9a3d' },
  anodized_red: { label: 'Anodized aluminium, red', color: '#9e1d27', metal: 0.6, rough: 0.36, mark: '#d6d9dc', mark_metal: 0.75, mark_rough: 0.5, mark_full: 0.004, preview: '#3dd2ff' },
  anodized_silver: { label: 'Anodized aluminium, natural', color: '#b8bbbf', metal: 0.85, rough: 0.42, mark: '#eeeeec', mark_metal: 0.2, mark_rough: 0.75, mark_full: 0.004, preview: '#1a4fd8' },
  stainless: { label: 'Brushed stainless', color: '#c9cbce', metal: 1, rough: 0.3, mark: '#24242a', mark_metal: 0.45, mark_rough: 0.42, mark_full: 0.01, tex: 'brushed', preview: '#1a4fd8' },
  brass: { label: 'Polished brass', color: '#cfa548', metal: 1, rough: 0.22, mark: '#7d5f2a', mark_metal: 0.85, mark_rough: 0.62, mark_full: 0.02, preview: '#1a4fd8' },
  acrylic: { label: 'Black acrylic (cast)', color: '#0a0a0c', metal: 0, rough: 0.06, clearcoat: 1, mark: '#d2d2d2', mark_metal: 0, mark_rough: 0.8, mark_full: 0.02, preview: '#ff7a3d' },
  walnut: { label: 'Walnut', color: '#ffffff', metal: 0, rough: 0.62, mark: '#1f1109', mark_metal: 0, mark_rough: 0.92, mark_full: 0.08, tex: 'walnut', preview: '#7fd4ff' },
  maple: { label: 'Maple', color: '#ffffff', metal: 0, rough: 0.58, mark: '#2e1a0c', mark_metal: 0, mark_rough: 0.9, mark_full: 0.08, tex: 'maple', preview: '#1a4fd8' },
  slate: { label: 'Slate', color: '#ffffff', metal: 0, rough: 0.82, mark: '#b9bcb8', mark_metal: 0, mark_rough: 0.95, mark_full: 0.01, tex: 'slate', bump: 0.6, preview: '#ff7a3d' },
  // Altamont Super Woods (Dymondwood): dyed birch laminates under a satin lacquer; the laser chars through to dark brown.
  super_rosewood: { label: 'Super Rosewood (red Dymondwood)', color: '#ffffff', metal: 0, rough: 0.42, clearcoat: 0.35, clearcoat_rough: 0.25, mark: '#24100c', mark_metal: 0, mark_rough: 0.93, mark_full: 0.05, tex: 'super_rosewood', bump: 0.02, preview: '#7fd4ff' },
  super_blackwood: { label: 'Super Blackwood (black Dymondwood)', color: '#ffffff', metal: 0, rough: 0.42, clearcoat: 0.35, clearcoat_rough: 0.25, mark: '#171512', mark_metal: 0, mark_rough: 0.96, mark_full: 0.05, tex: 'super_blackwood', bump: 0.02, preview: '#ff7a3d' },
  super_walnut: { label: 'Super Walnut (brown Dymondwood)', color: '#ffffff', metal: 0, rough: 0.42, clearcoat: 0.35, clearcoat_rough: 0.25, mark: '#22120a', mark_metal: 0, mark_rough: 0.93, mark_full: 0.05, tex: 'super_walnut', bump: 0.02, preview: '#7fd4ff' },
};

// Tiling value-noise fBm: lattice periods (px, py) must be powers of two so every octave wraps seamlessly.
const noise_vals = (() => {
  let s = 1234567;
  const v = new Float32Array(256 * 256);
  for (let k = 0; k < v.length; k++) { s = (s * 1103515245 + 12345) >>> 0; v[k] = s / 4294967296; }
  return v;
})();
function fbm(x, y, px, py, octaves) {
  let sum = 0, amp = 0.5, norm = 0;
  for (let o = 0; o < octaves; o++) {
    const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
    const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    const x0 = ix & (px - 1), x1 = (ix + 1) & (px - 1), y0 = (iy & (py - 1)) * 256, y1 = ((iy + 1) & (py - 1)) * 256;
    const a = noise_vals[y0 + x0], b = noise_vals[y0 + x1], c = noise_vals[y1 + x0], d = noise_vals[y1 + x1];
    sum += amp * (a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy);
    norm += amp;
    amp *= 0.5; x *= 2; y *= 2; px = Math.min(256, px * 2); py = Math.min(256, py * 2);
  }
  return sum / norm;
}

const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
// Dymondwood albedo palettes from Altamont product photos (darkened to take out the studio lighting):
// [share, rgb] layer dyes, then the glue-line colour.
export const LAMINATES = {
  super_rosewood: { dyes: [[0.7, [98, 22, 16]], [0.2, [62, 10, 8]], [0.1, [126, 40, 30]]], glue: [28, 7, 6] },
  super_blackwood: { dyes: [[0.5, [34, 33, 32]], [0.25, [20, 19, 18]], [0.13, [84, 82, 80]], [0.12, [66, 50, 40]]], glue: [10, 10, 9] },
  super_walnut: { dyes: [[0.65, [150, 74, 38]], [0.22, [104, 44, 20]], [0.13, [172, 100, 62]]], glue: [52, 22, 10] },
};
// RGBA pixels (N x N, row 0 = v 0) of a tiling procedural texture: wood grain and brushing run along X; laminates
// are stacked veneers, so v is height (z) and u runs sideways (texture coordinates (x + y, z) in mm).
export function procedural_pixels(kind, N = 1024) {
  const px = new Uint8ClampedArray(N * N * 4);
  const wood = { walnut: [[128, 86, 54], [58, 35, 20], 24], maple: [[236, 211, 166], [196, 154, 100], 30] }[kind];
  const lam = LAMINATES[kind];
  for (let y = 0; y < N; y++) {
    const v = y / N;
    for (let x = 0; x < N; x++) {
      const u = x / N, k = (y * N + x) * 4;
      let r, g, b;
      if (wood) {
        const [light, dark, rings] = wood;
        const t_ring = v * rings + fbm(u * 2, v * 2, 2, 2, 3) * 1.6 + fbm(u * 8, v * 16, 8, 16, 2) * 0.15;
        const f = t_ring - Math.floor(t_ring), ring = f * 0.25 + 0.45 * smooth(0.6, 0.88, f) * (1 - smooth(0.9, 1, f));
        const pores = fbm(u * 32, v * 128, 32, 128, 2);
        const t = Math.min(1, Math.max(0, ring + (pores - 0.5) * 0.4 + (fbm(u * 4, v * 8, 4, 8, 2) - 0.5) * 0.25));
        r = light[0] + (dark[0] - light[0]) * t; g = light[1] + (dark[1] - light[1]) * t; b = light[2] + (dark[2] - light[2]) * t;
      } else if (lam) {
        // 144 veneers (~0.55 mm at the default 80 mm scale), pressed very slightly uneven (gentle slopes stretch
        // any wobble into wide ragged bands); each gets a dye from the palette
        const s = v * 144 + (fbm(u * 2, v * 2, 2, 2, 2) - 0.5) * 0.2, layer = ((Math.floor(s) % 144) + 144) % 144, f = s - Math.floor(s);
        let pick = noise_vals[(layer * 2654435761 >>> 16) & 65535], rgb = lam.dyes[lam.dyes.length - 1][1];
        for (const [share, c] of lam.dyes) { if (pick < share) { rgb = c; break; } pick -= share; }
        const t = 1 + (fbm(u * 16, v * 144, 16, 256, 2) - 0.5) * 0.08 + (noise_vals[layer * 131 & 65535] - 0.5) * 0.12;
        const glue = 0.6 * (1 - smooth(0.0, 0.1, f));
        [r, g, b] = rgb.map((c, i) => (c * t) * (1 - glue) + lam.glue[i] * glue);
      } else if (kind === 'brushed') {
        const t = 0.82 + (fbm(u * 8, v * 256, 8, 256, 2) - 0.5) * 0.35 + (fbm(u * 2, v * 64, 2, 64, 2) - 0.5) * 0.15;
        r = g = b = 255 * t;
      } else if (kind === 'slate') {
        const layer = fbm(u * 2, v * 24, 2, 32, 4);
        const t = 0.55 * fbm(u * 8, v * 8, 8, 8, 5) + 0.45 * layer;
        r = 58 + 50 * t; g = 63 + 50 * t; b = 70 + 52 * t;
      } else throw new Error(`unknown texture ${kind}`);
      px[k] = r; px[k + 1] = g; px[k + 2] = b; px[k + 3] = 255;
    }
  }
  return px;
}

// All mesh triangles under a three.js object, in world space, as a flat xyz triangle soup.
export function triangle_soup(root) {
  root.updateMatrixWorld(true);
  const out = [], v = new THREE.Vector3();
  root.traverse((o) => {
    if (!o.isMesh) return;
    const pos = o.geometry.getAttribute('position'), idx = o.geometry.index;
    const count = idx ? idx.count : pos.count;
    for (let k = 0; k < count; k++) {
      v.fromBufferAttribute(pos, idx ? idx.getX(k) : k).applyMatrix4(o.matrixWorld);
      out.push(v.x, v.y, v.z);
    }
  });
  return new Float32Array(out);
}

// Move a triangle soup (in place) to the simulator frame: centred in XY, lowest point at Z = 0. Returns its size.
export function centre_model(positions, name) {
  if (!positions.length || positions.length % 9) throw new Error(`model ${name} has no usable triangles`);
  const box = new THREE.Box3().setFromArray(positions);
  const ox = (box.min.x + box.max.x) / 2, oy = (box.min.y + box.max.y) / 2, oz = box.min.z;
  for (let k = 0; k < positions.length; k += 3) { positions[k] -= ox; positions[k + 1] -= oy; positions[k + 2] -= oz; }
  return box.getSize(new THREE.Vector3());
}
