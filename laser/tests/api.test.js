import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as THREE from 'three';
import { PNG } from 'pngjs';
import validator from 'gltf-validator';
import { DEFAULT_LASER, simulate } from '../src/sim.js';
import { PRESETS, procedural_pixels, triangle_soup, centre_model } from '../src/part.js';
import { customer_glb } from '../src/relief.js';
import { make_server, size_jitter, save_template } from '../server.mjs';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

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

// a texture of the relief material, decoded: 'nrm' (normal map) or 'orm'
const texture = (glb, json, which) => {
  const m = json.materials[0], t = which === 'nrm' ? m.normalTexture : m.pbrMetallicRoughness.metallicRoughnessTexture;
  const v = json.bufferViews[json.images[json.textures[t.index].source].bufferView], bin = 20 + new DataView(glb.buffer, glb.byteOffset).getUint32(12, true) + 8;
  return PNG.sync.read(Buffer.from(glb.subarray(bin + v.byteOffset, bin + v.byteOffset + v.byteLength)));
};
// the wall texture period in file units: u runs along the wall at 1 / tile_mm per mm
const wall_tile_mm = (json, floats) => {
  const wall = json.meshes[0].primitives[1], pos = floats(wall.attributes.POSITION), uv = floats(wall.attributes.TEXCOORD_0);
  let best = 0, ratio = 0;
  for (let q = 0; q < pos.length / 3; q += 4) { // quads: (p, 0), (q, 0), (q, zq), (p, zp)
    const len = Math.hypot(pos[(q + 1) * 3] - pos[q * 3], pos[(q + 1) * 3 + 2] - pos[q * 3 + 2]);
    if (len > best) { best = len; ratio = len / Math.abs(uv[(q + 1) * 2] - uv[q * 2]); }
  }
  return ratio;
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

  it('refuses huge images and too-small placements before doing the work', async () => {
    const ihdr = png(new Uint8Array(4 * 4 * 4), 4, 4);
    ihdr.writeUInt32BE(20000, 16); // the header claims 20000 x 20000
    ihdr.writeUInt32BE(20000, 20);
    let r = await post({ ...job(), image: { data: ihdr.toString('base64') } });
    expect(r.status).toBe(400);
    expect((await r.json()).error).toMatch(/megapixels/);
    r = await post({ ...job(), placement: { width_mm: 0.002 } });
    expect(r.status).toBe(400);
    expect((await r.json()).error).toMatch(/too small/);
  }, 60000);

  it('holds the job slot while a body uploads, and keeps mm out of the summary', async () => {
    const [a, b] = await Promise.all([post(job()), post(job())]);
    expect([a.status, b.status].sort()).toEqual([200, 503]);
    const summary = JSON.parse((a.status === 200 ? a : b).headers.get('x-laser-summary'));
    expect(Object.keys(summary)).not.toContain('scale');
    expect(Object.keys(summary)).not.toContain('texel_mm');
  }, 60000);

  it('keys the size factor to the part: same file scale on a re-render, within ±10 %', async () => {
    const tile = async (srv) => {
      const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/laser/render`, { method: 'POST', body: JSON.stringify(job()) });
      expect(r.status).toBe(200);
      const { json, floats } = parse(new Uint8Array(await r.arrayBuffer()));
      return 80 / wall_tile_mm(json, floats); // the stand-in's longest side, as the 80 mm tile period implies it
    };
    const a = make_server({ secret: 'one' }), b = make_server({ secret: 'two' });
    await Promise.all([a, b].map((srv) => new Promise((r) => srv.listen(0, '127.0.0.1', r))));
    const [a1, a2, b1] = [await tile(a), await tile(a), await tile(b)];
    a.close();
    b.close();
    expect(a1).toBeCloseTo(a2, 6);
    for (const v of [a1, b1]) { expect(v).toBeGreaterThanOrEqual(27); expect(v).toBeLessThanOrEqual(33); } // 30 mm ± 10 %
    expect(Math.abs(a1 - b1)).toBeGreaterThan(1e-3);
  }, 120000);

  it('sets the default placement on the stand-in, so the bitmap pixel grid is no ruler for the real part', async () => {
    // no width or height: 1 px = 1 spot (0.05 mm) on the stand-in. Two stripes 30 px apart must measure 1.5 mm against
    // the stand-in's size as the tile period shows it (centre to centre, so the burn's spread cancels)
    const stripes = new Uint8Array(60 * 40 * 4).map((_, k) => (k % 4 === 3 ? 255 : [13, 14, 15, 16, 17, 43, 44, 45, 46, 47].includes((k >> 2) % 60) ? 0 : 255));
    const srv = make_server({ secret: 'one' });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    const body = { ...job(), image: { data: png(stripes, 60, 40).toString('base64') }, placement: {}, output: { max_texture: 2048 } };
    const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/laser/render`, { method: 'POST', body: JSON.stringify(body) });
    srv.close();
    expect(r.status).toBe(200);
    const glb = new Uint8Array(await r.arrayBuffer()), { json, floats } = parse(glb), relief = json.meshes[0].primitives[0];
    const size = 80 / wall_tile_mm(json, floats); // the stand-in's longest side in mm
    expect(Math.abs(size / 30 - 1)).toBeGreaterThan(0.02); // the secret gives a factor that tells real and stand-in apart
    const pos = floats(relief.attributes.POSITION), uv = floats(relief.attributes.TEXCOORD_0), orm = texture(glb, json, 'orm');
    // file units per texel: relief u runs across the texture as x does across the file
    const span = (arr, n, o) => { let lo = Infinity, hi = -Infinity; for (let k = o; k < arr.length; k += n) { lo = Math.min(lo, arr[k]); hi = Math.max(hi, arr[k]); } return hi - lo; };
    const texel = span(pos, 3, 0) / span(uv, 2, 0) / orm.width, row = orm.height >> 1, mid = orm.width / 2;
    // mark-weighted centre of each stripe (R = 1 - mark), within 2.5 mm of the centre, clear of the plate's hole
    const centre = (side) => {
      let w = 0, x = 0;
      for (let i = 0; i < orm.width; i++) {
        const d = (i + 0.5 - mid) * side;
        if (d > 0 && d * texel * size < 2.5) { const m = 255 - orm.data[(row * orm.width + i) * 4]; w += m; x += m * (i + 0.5); }
      }
      return x / w;
    };
    expect((centre(1) - centre(-1)) * texel * size).toBeCloseTo(1.5, 2);
  }, 120000);

  it('renders the stand-in faithfully: the same file as engraving the scaled part and placement directly', async () => {
    const srv = make_server({ secret: 'one' });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    const r = await fetch(`http://127.0.0.1:${srv.address().port}/api/laser/render`, { method: 'POST', body: JSON.stringify(job()) });
    srv.close();
    expect(r.status).toBe(200);
    const got = new Uint8Array(await r.arrayBuffer());
    // the stand-in, built as render() reads the job: the real part and placement scaled by the factor, the same laser
    const real = triangle_soup(new THREE.Mesh(new STLLoader().parse(new Uint8Array(stl(plate())).buffer)));
    centre_model(real, 'model');
    const f = size_jitter('one', real), stand_in = real.map((v) => v * f);
    expect(Math.abs(f - 1)).toBeGreaterThan(0.02);
    const grey = Float32Array.from({ length: 60 * 40 }, (_, k) => 0.299 * rgba[k * 4] + 0.587 * rgba[k * 4 + 1] + 0.114 * rgba[k * 4 + 2]);
    const placement = { cx: 0, cy: 0, angle: (10 * Math.PI) / 180, width_mm: 24 * f, height_mm: (24 * f * 40) / 60 };
    const res = simulate({ positions: stand_in, grey, img_w: 60, img_h: 40, placement, laser: { ...DEFAULT_LASER }, supersample: 2 });
    const p = PRESETS.anodized_black, lin = (hex) => new THREE.Color(hex).toArray();
    const look = {
      color: lin(p.color), mark: lin(p.mark), rough: p.rough, metal: p.metal, mark_rough: p.mark_rough, mark_metal: p.mark_metal,
      mark_full: p.mark_full, clearcoat: 0, clearcoat_rough: 0.03, tile: null, tile_mm: 80, laminate: false, rough_tile: false,
    };
    const { glb: want } = await customer_glb({ positions: stand_in, res, placement, look, mesh_mm: 1, max_texture: 512 }, png);
    expect(Buffer.compare(Buffer.from(got), Buffer.from(want))).toBe(0);
  }, 120000);

  it('lists templates and serves their files to the simulator app, behind the token', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'laser-templates-')), data = stl(plate());
    save_template(dir, 'colt_python', 'stl', data);
    const api = make_server({ templates: dir, token: 'sesame' }), auth = { headers: { Authorization: 'Bearer sesame' } };
    await new Promise((r) => api.listen(0, '127.0.0.1', r));
    const at = `http://127.0.0.1:${api.address().port}/api/laser/templates`;
    expect((await fetch(at)).status).toBe(401);
    expect((await (await fetch(at, auth)).json()).map((t) => [t.id, t.format, t.bytes])).toEqual([['colt_python', 'stl', data.length]]);
    const got = await fetch(`${at}/colt_python`, auth);
    expect(got.headers.get('x-template-format')).toBe('stl');
    expect(Buffer.compare(Buffer.from(await got.arrayBuffer()), data)).toBe(0);
    expect((await fetch(`${at}/nope`, auth)).status).toBe(404);
    expect((await fetch(`${at}/..%2Fsecret`, auth)).status).toBe(400);
    expect((await fetch(`${at}/colt_python`, { ...auth, method: 'DELETE' })).status).toBe(405);
    api.close();
    fs.rmSync(dir, { recursive: true });
  });

  it('checks the bearer token when one is set', async () => {
    const locked = make_server({ token: 'sesame' });
    await new Promise((r) => locked.listen(0, '127.0.0.1', r));
    const at = `http://127.0.0.1:${locked.address().port}/api/laser/render`;
    expect((await fetch(at, { method: 'POST', body: '{}' })).status).toBe(401);
    expect((await fetch(at, { method: 'POST', body: '{}', headers: { Authorization: 'Bearer sesame' } })).status).toBe(400);
    locked.close();
  });
});
