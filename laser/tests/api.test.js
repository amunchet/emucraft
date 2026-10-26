import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import * as THREE from 'three';
import { PNG } from 'pngjs';
import validator from 'gltf-validator';
import { DEFAULT_LASER, simulate } from '../src/sim.js';
import { PRESETS, procedural_pixels, triangle_soup, centre_model } from '../src/part.js';
import { customer_glb } from '../src/relief.js';
import { make_server } from '../server.mjs';

// 30 x 20 x 4 mm plate with a 3 mm through hole, as a centred triangle soup
const plate = () => {
  const shape = new THREE.Shape().moveTo(-15, -10).lineTo(15, -10).lineTo(15, 10).lineTo(-15, 10).lineTo(-15, -10);
  shape.holes.push(new THREE.Path().absarc(8, 0, 1.5, 0, Math.PI * 2, true));
  const p = triangle_soup(new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: 4, bevelEnabled: false, curveSegments: 48 })));
  centre_model(p, 'plate');
  return p;
};
// 60 x 40 px test bitmap: black square in the middle, white elsewhere
const bitmap = () => {
  const grey = new Float32Array(60 * 40).fill(255);
  for (let j = 10; j < 30; j++) for (let i = 15; i < 45; i++) grey[j * 60 + i] = 0;
  return grey;
};
const png = (rgba, w, h) => PNG.sync.write({ width: w, height: h, data: Buffer.from(rgba.buffer, rgba.byteOffset, rgba.byteLength) }, { colorType: 6 });
const stl = (positions) => {
  const buf = Buffer.alloc(84 + (positions.length / 9) * 50);
  buf.writeUInt32LE(positions.length / 9, 80);
  for (let t = 0; t < positions.length / 9; t++) for (let k = 0; k < 9; k++) buf.writeFloatLE(positions[t * 9 + k], 84 + t * 50 + 12 + k * 4);
  return buf;
};
// parse a GLB: JSON chunk + float view of an accessor
const parse = (glb) => {
  const dv = new DataView(glb.buffer, glb.byteOffset), jlen = dv.getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(glb.subarray(20, 20 + jlen))), bin = 20 + jlen + 8;
  const floats = (a) => { const acc = json.accessors[a], v = json.bufferViews[acc.bufferView]; return new Float32Array(glb.slice(bin + v.byteOffset, bin + v.byteOffset + v.byteLength).buffer); };
  return { json, floats };
};

describe('customer GLB', () => {
  const positions = plate(), placement = { cx: 0, cy: 0, angle: 0.2, width_mm: 24, height_mm: 16 }, p = PRESETS.super_walnut;
  const laser = { ...DEFAULT_LASER, passes: 10 };
  const res = simulate({ positions, grey: bitmap(), img_w: 60, img_h: 40, placement, laser, supersample: 2 });
  const look = {
    color: [1, 1, 1], mark: new THREE.Color(p.mark).toArray(), rough: p.rough, metal: p.metal, mark_rough: p.mark_rough, mark_metal: p.mark_metal,
    mark_full: p.mark_full, clearcoat: p.clearcoat, clearcoat_rough: p.clearcoat_rough, tile: { px: procedural_pixels(p.tex, 64), w: 64, h: 64 }, tile_mm: 80, laminate: true,
  };
  let out;
  beforeAll(async () => { out = await customer_glb({ positions, res, placement, look, mesh_mm: 1, max_texture: 512 }, png); });

  it('is valid glTF 2.0', async () => {
    const report = await validator.validateBytes(out.glb);
    expect(report.issues.numErrors).toBe(0);
    expect(report.issues.numWarnings).toBe(0);
  });

  it('is an open, coarse, unit-less front shell', () => {
    const { json, floats } = parse(out.glb);
    const [relief, wall] = json.meshes[0].primitives;
    expect(json.materials[relief.material].alphaMode).toBe('MASK');
    const pos = floats(relief.attributes.POSITION), nor = floats(relief.attributes.NORMAL);
    // nothing faces down (no back), and the relief is far coarser than the source mesh / engraving grid
    for (let k = 1; k < nor.length; k += 3) expect(nor[k]).toBeGreaterThan(-1e-6);
    expect(floats(wall.attributes.NORMAL).every((v, k) => k % 3 !== 1 || Math.abs(v) < 1e-6)).toBe(true);
    expect(pos.length / 3).toBeLessThan((out.summary.texture[0] * out.summary.texture[1]) / 10);
    // largest side 1, not millimetres or metres
    const ext = [0, 1, 2].map((a) => { let lo = Infinity, hi = -Infinity; for (let k = a; k < pos.length; k += 3) { lo = Math.min(lo, pos[k]); hi = Math.max(hi, pos[k]); } return hi - lo; });
    expect(Math.max(...ext)).toBeCloseTo(1, 1);
    // no names or extras that describe the source
    expect(JSON.stringify(json)).not.toMatch(/"name"|extras|\.stl/i);
  });

  it('carries the engraving in the textures, not the geometry', async () => {
    const { json, floats } = parse(out.glb);
    const relief = json.meshes[0].primitives[0], pos = floats(relief.attributes.POSITION);
    const top = Math.max(...pos.filter((_, k) => k % 3 === 1));
    // the relief sits on the unengraved top: every top-face vertex at the plate top (4 mm, scaled)
    expect(top).toBeCloseTo(4 * out.summary.scale, 5);
    expect(res.max_depth).toBeGreaterThan(0.05);
    const img = (i) => { const v = json.bufferViews[json.images[i].bufferView], bin = 20 + new DataView(out.glb.buffer, out.glb.byteOffset).getUint32(12, true) + 8; return PNG.sync.read(Buffer.from(out.glb.subarray(bin + v.byteOffset, bin + v.byteOffset + v.byteLength))); };
    const col = img(json.textures[json.materials[0].pbrMetallicRoughness.baseColorTexture.index].source);
    const nrm = img(json.textures[json.materials[0].normalTexture.index].source);
    // the burned square is darker than the bare wood, and its walls tilt the normals
    const lum = (k) => col.data[k * 4] + col.data[k * 4 + 1] + col.data[k * 4 + 2], c = (col.height >> 1) * col.width + (col.width >> 1);
    expect(lum(c)).toBeLessThan(lum((col.height >> 1) * col.width + 4) * 0.6);
    let tilted = 0;
    for (let k = 0; k < nrm.width * nrm.height; k++) if (nrm.data[k * 4 + 2] < 250) tilted++; // z < 0.96
    expect(tilted).toBeGreaterThan(50);
    // the hole is cut out by the alpha mask
    const hx = Math.round(((8 + 15) / 30) * col.width), hy = col.height >> 1;
    expect(col.data[(hy * col.width + hx) * 4 + 3]).toBe(0);
  });

  it('true_scale keeps metres', async () => {
    const { glb } = await customer_glb({ positions, res, placement, look, mesh_mm: 1, max_texture: 512, true_scale: true }, png);
    const { json } = parse(glb), acc = json.accessors[json.meshes[0].primitives[0].attributes.POSITION];
    expect(acc.max[0] - acc.min[0]).toBeGreaterThan(0.029);
    expect(acc.max[0] - acc.min[0]).toBeLessThan(0.0315);
  });
});

describe('HTTP API', () => {
  let server, url;
  beforeAll(async () => {
    server = make_server({ jobs: 1, timeout_s: 60 });
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    url = `http://127.0.0.1:${server.address().port}/api/laser`;
  });
  afterAll(() => server.close());
  const rgba = new Uint8Array(60 * 40 * 4).map((_, k) => (k % 4 === 3 ? 255 : bitmap()[k >> 2]));
  const job = () => ({
    model: { format: 'stl', data: stl(plate()).toString('base64') },
    image: { data: png(rgba, 60, 40).toString('base64') },
    placement: { cx: 0, cy: 0, angle_deg: 10, width_mm: 24 },
    material: { preset: 'anodized_black' },
    output: { mesh_mm: 1, max_texture: 512 },
  });
  const post = (body) => fetch(`${url}/render`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  it('lists presets and defaults', async () => {
    const info = await (await fetch(`${url}/info`)).json();
    expect(info.presets.super_rosewood).toMatch(/Rosewood/);
    expect(info.laser).toEqual(DEFAULT_LASER);
  });

  it('renders a job to a valid GLB', async () => {
    const r = await post(job());
    expect(r.status).toBe(200);
    expect(r.headers.get('content-type')).toBe('model/gltf-binary');
    const summary = JSON.parse(r.headers.get('x-laser-summary'));
    expect(summary.max_depth_mm).toBeGreaterThan(0);
    const report = await validator.validateBytes(new Uint8Array(await r.arrayBuffer()));
    expect(report.issues.numErrors).toBe(0);
  }, 60000);

  it('rejects bad jobs with 400 and a reason', async () => {
    for (const [patch, msg] of [
      [{ extra: 1 }, /unknown job field 'extra'/],
      [{ laser: { passes: 0 } }, /laser.passes/],
      [{ laser: { invert: 'yes' } }, /laser.invert/],
      [{ material: { preset: 'gold' } }, /unknown material.preset/],
      [{ material: { color: 'red' } }, /material.color/],
      [{ placement: { cx: 500 } }, /does not overlap/],
      [{ model: { format: 'step', data: 'AAAA' } }, /model.format/],
      [{ image: { data: Buffer.from('nope').toString('base64') } }, /PNG or JPEG/],
      [{ output: { max_texture: 10 } }, /output.max_texture/],
    ]) {
      const r = await post({ ...job(), ...patch });
      expect(r.status, JSON.stringify(patch)).toBe(400);
      expect((await r.json()).error).toMatch(msg);
    }
    expect((await fetch(`${url}/render`)).status).toBe(405);
  }, 60000);
});
