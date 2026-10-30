// Laser API: runs an engraving job and answers with the customer GLB (src/relief.js), a single file for a
// three.js viewer that does not carry the solid. Plain node:http; each job runs in its own worker thread.
//
//   GET  /api/laser/info            presets, laser defaults and option defaults
//   POST /api/laser/render          JSON job -> model/gltf-binary (summary in the X-Laser-Summary header)
//   GET  /api/laser/templates       the stored templates (id, format, bytes, upload time)
//   GET  /api/laser/templates/<id>  a template's model file, for the simulator app to load
//
// Job: { model: { format: 'stl' | 'obj', data: base64 } | { template: id }, image: { data: base64 PNG or JPEG },
//   placement?: { cx, cy, angle_deg, width_mm, height_mm }, laser?: { ...DEFAULT_LASER }, supersample?,
//   material?: { preset, color, mark, rough, metal, mark_rough, mark_metal, tex_scale_mm, texture: base64 },
//   output?: { mesh_mm, max_texture, true_scale } }
// Placement is in the simulator frame (model centred in XY, lowest point at Z = 0), as the app shows it.
//
// Rendering is CPU- and memory-heavy: call it from the shop's backend, or set LASER_TOKEN and send
// 'Authorization: Bearer <token>'. Do not expose it to the public unauthenticated.
//
// Unless true_scale is asked for, the file is the faithful render of a stand-in part: the real one (and the job's
// placement) scaled by a factor in [0.9, 1.1] keyed to the part (HMAC of its geometry), engraved with the same laser.
// Every ruler in the file (mesh pitch, texture tiles, pixel grid, spot blur, defocus, depths) then measures the
// stand-in, so the real size is known only to within ±10 %, and rendering the same part again does not narrow that
// down. Set LASER_SECRET to keep the factor across restarts; unset, it is new per process.
//
// Templates are proprietary master parts (a 1911 frame, a Colt Python) kept in LASER_TEMPLATES and engraved by id.
// They go in and are deleted through the MCP server (mcp.mjs). The only way back out is GET /api/laser/templates/<id>,
// which the simulator app loads them through behind the site's sign-in. A render of one is always the stand-in
// (true_scale is refused), and the listing reports no size.
//
// Run: node server.mjs   (env: HOST=127.0.0.1, PORT=8001, LASER_JOBS=2 concurrent jobs, LASER_TIMEOUT_S=300,
//   LASER_MAX_MB=64 request body cap, LASER_TOKEN unset = no auth, LASER_SECRET unset = random per process,
//   LASER_TEMPLATES=templates)
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { Worker, parentPort, workerData } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';
import { pipeline } from 'node:stream/promises';
import { PNG } from 'pngjs';
import jpeg from 'jpeg-js';
import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { DEFAULT_LASER, simulate, plan_grid, beam_diameter } from './src/sim.js';
import { PRESETS, procedural_pixels, triangle_soup, centre_model } from './src/part.js';
import { customer_glb } from './src/relief.js';

const MAX_PIXELS = 25e6; // decoded image cap (the engraving grid is at most 4e6 cells anyway)
const DEFAULTS = { supersample: 2, tex_scale_mm: 80, mesh_mm: 0.5, max_texture: 2048, true_scale: false };
const LASER_RANGES = {
  spot_mm: [0.001, 10], focal_length_mm: [1, 10000], beam_diameter_mm: [0.01, 1000], focus_offset_mm: [-1000, 1000], power_w: [0, 1e6],
  max_power_w: [1e-6, 1e6], depth_per_pass_mm: [0, 100], passes: [1, 100000], threshold: [0, 0.999], gamma: [0.01, 100],
};

const bad = (message) => Object.assign(new Error(message), { status: 400 });
const num = (v, what, [lo, hi]) => {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < lo || v > hi) throw bad(`${what} must be a number in [${lo}, ${hi}]`);
  return v;
};
const only = (obj, allowed, what) => {
  if (obj === undefined) return {};
  if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) throw bad(`${what} must be an object`);
  for (const k of Object.keys(obj)) if (!allowed.includes(k)) throw bad(`unknown ${what} field '${k}'`);
  return obj;
};
const b64 = (v, what) => {
  if (typeof v !== 'string' || !v) throw bad(`${what} must be a base64 string`);
  return Buffer.from(v, 'base64');
};
// the part's size factor: deterministic per (secret, geometry), uniform in [0.9, 1.1]
export const size_jitter = (secret, positions) =>
  0.9 + 0.2 * (createHmac('sha256', secret).update(Buffer.from(positions.buffer, positions.byteOffset, positions.byteLength)).digest().readUInt32BE(0) / 2 ** 32);

// Model bytes -> its triangle soup; a bad or empty model is a 400.
export function load_model(format, data) {
  let root;
  try {
    if (format === 'stl') root = new THREE.Mesh(new STLLoader().parse(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)));
    else if (format === 'obj') root = new OBJLoader().parse(data.toString('utf8'));
  } catch (err) {
    throw bad(`model could not be read: ${err.message}`);
  }
  if (!root) throw bad("model.format must be 'stl' or 'obj'");
  const positions = triangle_soup(root);
  if (!positions.length) throw bad('model has no triangles');
  if (!positions.every(Number.isFinite)) throw bad('model has non-finite vertices');
  return positions;
}

// Templates: <dir>/<id>.stl or .obj, readable only by this user. Ids are checked, so they never leave the directory.
const template_id = (id) => {
  if (typeof id !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id)) throw bad('template id must be 1-64 of a-z, 0-9, _ and -, starting with a letter or digit');
  return id;
};
const template_files = (dir, id) => ['stl', 'obj'].map((format) => ({ format, file: path.join(dir, `${id}.${format}`) })).filter((t) => fs.existsSync(t.file));

// What the templates are, without anything from inside them: id, format, file size, upload time.
export function list_templates(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).map((name) => /^([a-z0-9][a-z0-9_-]{0,63})\.(stl|obj)$/.exec(name)).filter(Boolean).map(([name, id, format]) => {
    const st = fs.statSync(path.join(dir, name));
    return { id, format, bytes: st.size, uploaded: st.mtime.toISOString() };
  }).sort((a, b) => a.id.localeCompare(b.id));
}

// Store a template once it reads as a model. An existing id is kept unless `replace`; the file is swapped in whole.
export function save_template(dir, id, format, data, replace = false) {
  template_id(id);
  const triangles = load_model(format, data).length / 9, old = template_files(dir, id);
  if (old.length && !replace) throw Object.assign(new Error(`template '${id}' already exists (pass replace to overwrite it)`), { status: 409 });
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const tmp = path.join(dir, `.${id}.${randomBytes(6).toString('hex')}.tmp`);
  fs.writeFileSync(tmp, data, { mode: 0o600 });
  fs.renameSync(tmp, path.join(dir, `${id}.${format}`));
  for (const t of old) if (t.format !== format) fs.rmSync(t.file);
  return { id, format, bytes: data.length, triangles, replaced: old.length > 0 };
}

export function delete_template(dir, id) {
  const old = template_files(dir, template_id(id));
  if (!old.length) throw Object.assign(new Error(`unknown template '${id}'`), { status: 404 });
  for (const t of old) fs.rmSync(t.file);
  return { id, deleted: true };
}

const linear = (hex, what) => {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) throw bad(`${what} must be a #rrggbb colour`);
  return new THREE.Color(hex).toArray(); // three's working space is linear sRGB
};

// PNG/JPEG bytes -> RGBA pixels; the size is checked (PNG header / jpeg-js limit) before decoding.
function decode_image(buf, what) {
  try {
    if (buf.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
      if (buf.length < 24 || buf.readUInt32BE(16) * buf.readUInt32BE(20) > MAX_PIXELS) throw bad(`${what} is over ${MAX_PIXELS / 1e6} megapixels`);
      const p = PNG.sync.read(buf);
      return { px: p.data, w: p.width, h: p.height };
    }
    if (buf[0] === 0xff && buf[1] === 0xd8) { const j = jpeg.decode(buf, { useTArray: true, maxResolutionInMP: MAX_PIXELS / 1e6, maxMemoryUsageInMB: 512 }); return { px: j.data, w: j.width, h: j.height }; }
  } catch (err) {
    if (err.status) throw err;
    throw bad(`${what} could not be decoded: ${err.message}`);
  }
  throw bad(`${what} must be a PNG or JPEG`);
}

// The whole job, inside a worker thread: validate, load, simulate, build the GLB.
async function render(body, secret, templates) {
  let job;
  try { job = JSON.parse(Buffer.from(body).toString('utf8')); } catch (err) { throw bad(`body is not JSON: ${err.message}`); }
  only(job, ['model', 'image', 'placement', 'laser', 'supersample', 'material', 'output'], 'job');

  const model = only(job.model, ['format', 'data', 'template'], 'model');
  let positions;
  if (model.template !== undefined) {
    if (model.format !== undefined || model.data !== undefined) throw bad('model takes either a template or format and data, not both');
    if (job.output?.true_scale === true) throw bad('output.true_scale is not available for templates');
    const t = template_files(templates, template_id(model.template))[0];
    if (!t) throw bad(`unknown template '${model.template}'`);
    positions = load_model(t.format, fs.readFileSync(t.file));
  } else positions = load_model(model.format, b64(model.data, 'model.data'));
  centre_model(positions, 'model');
  const jitter = job.output?.true_scale === true ? 1 : size_jitter(secret, positions);
  for (let k = 0; k < positions.length; k++) positions[k] *= jitter; // from here on, the stand-in part

  const img = decode_image(b64(only(job.image, ['data'], 'image').data, 'image.data'), 'image');
  const grey = new Float32Array(img.w * img.h);
  for (let k = 0; k < grey.length; k++) grey[k] = img.px[k * 4 + 3] < 128 ? 255 : 0.299 * img.px[k * 4] + 0.587 * img.px[k * 4 + 1] + 0.114 * img.px[k * 4 + 2];

  const laser = { ...DEFAULT_LASER };
  for (const [k, v] of Object.entries(only(job.laser, Object.keys(DEFAULT_LASER), 'laser'))) {
    if (k === 'invert') { if (typeof v !== 'boolean') throw bad('laser.invert must be true or false'); laser.invert = v; } else laser[k] = num(v, `laser.${k}`, LASER_RANGES[k]);
  }
  // placement defaults as in the app: centred, 1 px = 1 spot, the other side keeping the image aspect. A given
  // placement is on the real part and scales with it; the default one is set on the stand-in, or its pixel grid
  // would be a spot_mm ruler for the real size
  const pl = only(job.placement, ['cx', 'cy', 'angle_deg', 'width_mm', 'height_mm'], 'placement'), R = [-1e5, 1e5];
  const given = (v, what) => (v === undefined ? undefined : num(v, what, [1e-3, 1e5]) * jitter);
  const w_given = given(pl.width_mm, 'placement.width_mm'), h_given = given(pl.height_mm, 'placement.height_mm');
  const width_mm = w_given ?? (h_given === undefined ? img.w * laser.spot_mm : (h_given * img.w) / img.h);
  const placement = {
    cx: num(pl.cx ?? 0, 'placement.cx', R) * jitter, cy: num(pl.cy ?? 0, 'placement.cy', R) * jitter,
    angle: (num(pl.angle_deg ?? 0, 'placement.angle_deg', [-3600, 3600]) * Math.PI) / 180,
    width_mm, height_mm: h_given ?? (width_mm * img.h) / img.w,
  };
  const supersample = num(job.supersample ?? DEFAULTS.supersample, 'supersample', [1, 4]);
  // the defocused spot, in grid cells, sets the blur kernel: refuse placements so small that it would run for ages
  const { grid_w } = plan_grid(img.w, img.h, supersample);
  let zmax = 0;
  for (let k = 2; k < positions.length; k += 3) zmax = Math.max(zmax, positions[k]);
  if (beam_diameter(laser, Math.abs(laser.focus_offset_mm) + zmax) / (placement.width_mm / grid_w) > 400) throw bad('placement is too small for the laser spot (the spot would cover hundreds of image pixels)');

  const mat = only(job.material, ['preset', 'color', 'mark', 'rough', 'metal', 'mark_rough', 'mark_metal', 'tex_scale_mm', 'texture'], 'material');
  const p = PRESETS[mat.preset ?? 'anodized_black'];
  if (!p) throw bad(`unknown material.preset '${mat.preset}' (see GET /api/laser/info)`);
  const custom = mat.texture === undefined ? null : decode_image(b64(mat.texture, 'material.texture'), 'material.texture');
  const look = {
    color: linear(mat.color ?? p.color, 'material.color'), mark: linear(mat.mark ?? p.mark, 'material.mark'),
    rough: num(mat.rough ?? p.rough, 'material.rough', [0, 1]), metal: num(mat.metal ?? p.metal, 'material.metal', [0, 1]),
    mark_rough: num(mat.mark_rough ?? p.mark_rough, 'material.mark_rough', [0, 1]), mark_metal: num(mat.mark_metal ?? p.mark_metal, 'material.mark_metal', [0, 1]),
    mark_full: p.mark_full, clearcoat: p.clearcoat || 0, clearcoat_rough: p.clearcoat_rough ?? 0.03,
    tile: custom || (p.tex ? { px: procedural_pixels(p.tex, 1024), w: 1024, h: 1024 } : null),
    tile_mm: num(mat.tex_scale_mm ?? DEFAULTS.tex_scale_mm, 'material.tex_scale_mm', [1, 1000]),
    laminate: !custom && p.tex?.startsWith('super_'), rough_tile: !custom && p.tex === 'brushed',
  };
  const out = only(job.output, ['mesh_mm', 'max_texture', 'true_scale'], 'output');
  if (out.true_scale !== undefined && typeof out.true_scale !== 'boolean') throw bad('output.true_scale must be true or false');

  let res;
  try {
    res = simulate({ positions, grey, img_w: img.w, img_h: img.h, placement, laser, supersample });
  } catch (err) {
    throw err.message.includes('does not overlap') ? bad(err.message) : err;
  }
  const { glb, summary } = await customer_glb({
    positions, res, placement, look,
    mesh_mm: num(out.mesh_mm ?? DEFAULTS.mesh_mm, 'output.mesh_mm', [0.1, 2]),
    max_texture: Math.round(num(out.max_texture ?? DEFAULTS.max_texture, 'output.max_texture', [256, 8192])),
    true_scale: out.true_scale ?? DEFAULTS.true_scale,
  }, (px, w, h) => PNG.sync.write({ width: w, height: h, data: Buffer.from(px.buffer, px.byteOffset, px.byteLength) }, { colorType: 6 }));
  // nothing in mm that would give the part's size away if the summary reaches a browser
  return { glb, summary: { texture: summary.texture, relief_triangles: summary.relief_triangles, wall_triangles: summary.wall_triangles, max_depth_mm: res.max_depth, bytes: glb.byteLength } };
}

// Inside a job thread: run the job and post the result back.
if (workerData?.laser_job) {
  try {
    const { glb, summary } = await render(workerData.body, workerData.secret, workerData.templates);
    parentPort.postMessage({ ok: true, glb, summary }, [glb.buffer]);
  } catch (err) {
    parentPort.postMessage({ ok: false, status: err.status || 500, message: err.message || String(err) });
  }
}

// Run a job (the JSON body, as bytes it may take over) in its own worker thread -> { glb, summary }. Errors carry the
// HTTP status: 400 for a bad job, 504 past `timeout_s`, 499 once `signal` aborts (the caller has gone away).
export const run_job = (body, { secret, templates, timeout_s = 300, signal }) => new Promise((resolve, reject) => {
  const w = new Worker(fileURLToPath(import.meta.url), { workerData: { laser_job: true, body, secret, templates }, transferList: [body.buffer] });
  const stop = (err) => { clearTimeout(timer); signal?.removeEventListener('abort', gone); w.terminate(); reject(err); };
  const gone = () => stop(Object.assign(new Error('client went away'), { status: 499 }));
  const timer = setTimeout(() => stop(Object.assign(new Error(`job took longer than ${timeout_s} s`), { status: 504 })), timeout_s * 1000);
  signal?.addEventListener('abort', gone);
  w.once('message', (m) => { clearTimeout(timer); signal?.removeEventListener('abort', gone); w.terminate(); m.ok ? resolve(m) : reject(Object.assign(new Error(m.message), { status: m.status })); });
  w.once('error', stop);
  w.once('exit', (code) => stop(new Error(`job thread exited (${code})`))); // no-op once settled
});

// GET /api/laser/info's answer: what a job can choose from
export const info = () => ({ presets: Object.fromEntries(Object.entries(PRESETS).map(([k, p]) => [k, p.label])), laser: DEFAULT_LASER, defaults: DEFAULTS });

// HTTP server; jobs beyond `jobs` concurrent ones get 503. A job's slot is held from before its body is read until
// it ends, and a job whose client has gone away is stopped.
export function make_server({ jobs = 2, timeout_s = 300, max_mb = 64, token = '', secret = randomBytes(32), templates = 'templates' } = {}) {
  const max_body = max_mb * 1024 * 1024, auth = Buffer.from(`Bearer ${token}`);
  let running = 0;
  const run = (body, out) => {
    const gone = new AbortController();
    out.on('close', () => gone.abort());
    return run_job(body, { secret, templates, timeout_s, signal: gone.signal });
  };
  const send = (out, status, body, type, extra = {}) => {
    out.writeHead(status, { 'Content-Type': type, 'Content-Length': Buffer.byteLength(body), 'Cache-Control': 'no-store', ...extra });
    out.end(body);
  };
  const same = (header, want) => { const got = Buffer.from(header || ''); return got.length === want.length && timingSafeEqual(got, want); };
  const fail = (out, status, message) => send(out, status, JSON.stringify({ error: message }), 'application/json');
  return http.createServer(async (req, out) => {
    const path = new URL(req.url, 'http://x').pathname;
    let slot = false;
    try {
      if (req.method === 'GET' && path === '/api/laser/info') return send(out, 200, JSON.stringify(info()), 'application/json');
      const read = /^\/api\/laser\/templates(?:\/([^/]+))?$/.exec(path);
      if (path !== '/api/laser/render' && !read) return fail(out, 404, 'not found');
      if (req.method !== (read ? 'GET' : 'POST')) return fail(out, 405, read ? 'use GET' : 'use POST');
      if (token && !same(req.headers.authorization, auth)) return fail(out, 401, 'missing or wrong bearer token');
      if (read && !read[1]) return send(out, 200, JSON.stringify(list_templates(templates)), 'application/json');
      if (read) {
        const t = template_files(templates, template_id(decodeURIComponent(read[1])))[0];
        if (!t) return fail(out, 404, `unknown template '${read[1]}'`);
        out.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(t.file).size, 'Cache-Control': 'no-store', 'X-Template-Format': t.format });
        return await pipeline(fs.createReadStream(t.file), out);
      }
      if (Number(req.headers['content-length']) > max_body) return fail(out, 413, `body over ${max_mb} MB`);
      if (running >= jobs) return fail(out, 503, 'busy: too many jobs running, retry shortly');
      running++;
      slot = true;
      const parts = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > max_body) { out.setHeader('Connection', 'close'); return fail(out, 413, `body over ${max_mb} MB`); }
        parts.push(chunk);
      }
      const { glb, summary } = await run(new Uint8Array(Buffer.concat(parts)), out); // a copy owns its ArrayBuffer: transferable
      send(out, 200, Buffer.from(glb.buffer, glb.byteOffset, glb.byteLength), 'model/gltf-binary', { 'Content-Disposition': 'attachment; filename="part.glb"', 'X-Laser-Summary': JSON.stringify(summary) });
    } catch (err) {
      const internal = !err.status || err.status >= 500;
      if (internal && err.status !== 504) console.error(err);
      if (!out.headersSent && !out.destroyed) fail(out, err.status || 500, internal && err.status !== 504 ? 'internal error (see the server log)' : err.message);
    } finally {
      if (slot) running--;
    }
  });
}

if (!workerData?.laser_job && process.argv[1] === fileURLToPath(import.meta.url)) {
  const env = process.env, host = env.HOST || '127.0.0.1', port = Number(env.PORT || 8001);
  make_server({ jobs: Number(env.LASER_JOBS || 2), timeout_s: Number(env.LASER_TIMEOUT_S || 300), max_mb: Number(env.LASER_MAX_MB || 64), token: env.LASER_TOKEN || '', templates: env.LASER_TEMPLATES || 'templates', ...(env.LASER_SECRET && { secret: env.LASER_SECRET }) }).listen(port, host, () => console.log(`laser API on http://${host}:${port}/api/laser/info`));
}
