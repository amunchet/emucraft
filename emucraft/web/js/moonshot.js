// Moonshot page: models, runs and their queued tasks through /api/moonshot, and a planned part in 3D with
// the drum at any point and the robot program played back.
import * as THREE from 'three';
import { el, fmtClock, hexToRgb, ramp, rampCss, toolColor, typed } from './util.js';
import { Viewer } from './viewer.js';

const $ = id => document.getElementById(id);
const PART = 0xb8b5ac, HOLDER = 0x77746d, DRUM = 0x2a78d6;
const BLOCKED_PART = [0.86, 0.26, 0.26], BLOCKED_HOLDER = [0.42, 0.42, 0.42], NONE = [0.5, 0.5, 0.5];

async function api(path, { method = 'GET', body, raw } = {}) {
  const init = { method };
  if (raw !== undefined) init.body = raw;
  else if (body !== undefined) Object.assign(init, { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const resp = await fetch(`/api/moonshot${path}`, init);
  const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
  if (!resp.ok || data.error) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

// Form fields: [key, label, default, options]. Vectors are typed as space-separated numbers; an empty
// optional number leaves the server's default.
const PLAN = [
  ['units', 'Model units', 'in', { choices: ['in', 'mm', 'cm', 'm'] }],
  ['rotate', 'Rotate X Y Z (°)', '0 0 0', { vector: true }],
  ['spacing', 'Grid spacing (in)', 0.01],
  ['radius', 'Drum radius (in)', 0.5],
  ['length', 'Drum length (in)', 10],
  ['holder', 'Holder', 'fixture', { choices: ['fixture', 'table', 'none'] }],
  ['mount', 'Toward the holder', '0 0 -1', { vector: true }],
  ['inset', 'Fixture inset (in)', 0.05],
  ['grain', 'Grain direction', '0 0 1', { vector: true }],
  ['grain_weight', 'Grain weight', 1e6],
  ['track', 'Track along the drum', true],
  ['pass_spacing', 'Pass spacing (in)', 0.1],
  ['track_step', 'Track step (in)', null, { placeholder: 'auto' }],
  ['track_gap', 'Track gap (in)', 0.01],
  ['track_sweep', 'Sweep continuously', false],
  ['track_sweeps', 'Sweeps', 1],
  ['track_slope', 'Sweep slope', 8],
  ['tolerance', 'Tolerance (in)', 0.001],
  ['bins', 'Angle bins / 180°', 180],
  ['margin', 'Margin (bins)', 1],
  ['prefer', 'Tie-break axis', '1 0 0', { vector: true }],
];
const PROGRAM = [
  ['frame', 'Part frame (3, 6 or 9 numbers)', '0 0 0', { vector: true, wide: true }],
  ['name', 'Program name', 'SAND', { text: true }],
  ['setup', 'Robot holds', 'part', { choices: ['part', 'drum'] }],
  ['rtcp', 'Remote TCP', false],
  ['feed', 'Feed (in/min)', 120],
  ['rapid', 'Rapid (in/min)', 240],
  ['max_turn', 'Max turn (°/s)', 90],
  ['swing', 'Swing (°/in)', 480],
  ['lift', 'Lift (in)', 0.25],
  ['approach', 'Approach (in)', 2],
  ['tol', 'Tolerance (in)', 0.001],
  ['angle_tol', 'Angle tolerance (°)', 0.5],
  ['max_step', 'Max step (in)', 0.25],
  ['uframe', 'UFRAME', 1],
  ['utool', 'UTOOL', 1],
  ['config', 'Configuration', 'N U T, 0, 0, 0', { text: true, wide: true }],
];

function form(fields) {
  const node = el('div', { class: 'form' });
  for (const [key, label, value, opt = {}] of fields) {
    let input;
    if (opt.choices) input = el('select', { name: key }, opt.choices.map(c => el('option', { value: c, selected: c === value }, c)));
    else if (typeof value === 'boolean') input = el('input', { name: key, type: 'checkbox', checked: value });
    else input = el('input', { name: key, type: opt.vector || opt.text ? 'text' : 'number', step: 'any', value: value ?? '', placeholder: opt.placeholder });
    node.append(el('label', { class: opt.wide ? 'wide' : null }, label, input));
  }
  return node;
}

function collect(node, fields) {
  const out = {};
  for (const [key, , value, opt = {}] of fields) {
    const input = node.querySelector(`[name="${key}"]`);
    if (input.type === 'checkbox') out[key] = input.checked;
    else if (opt.vector) out[key] = input.value.trim().split(/[\s,]+/).map(Number);
    else if (opt.choices || opt.text) out[key] = input.value;
    else if (input.value !== '') out[key] = Number(input.value);
    else if (value !== null) throw new Error(`${key} needs a value`);
  }
  return out;
}

function size(bytes) {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

function table(rows) {
  return el('table', { class: 'stats' }, rows.filter(Boolean).map(([k, v]) => el('tr', {}, el('td', {}, k), el('td', {}, v))));
}

const active = run => run?.tasks.some(t => t.status === 'queued' || t.status === 'running');
const done = (run, kind) => run?.tasks.some(t => t.kind === kind && t.status === 'done');

class App {
  constructor() {
    this.viewer = new Viewer($('canvas'));
    this.world = new THREE.Group();
    this.viewer.world.add(this.world);
    this.drum = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 48),
      new THREE.MeshStandardMaterial({ color: DRUM, transparent: true, opacity: 0.45, depthWrite: false }));
    this.drum.visible = false;
    this.viewer.world.add(this.drum);
    this.models = [];
    this.model = null;
    this.runId = new URLSearchParams(location.search).get('run');
    this.run = null;
    this.scene = null;
    this.program = null;
    this.step = 0;
    this.playing = false;
  }

  async init() {
    this.bindUi();
    this.renderPlan();
    requestAnimationFrame(t => this.frame(t));
    try {
      const samples = await api('/samples');
      $('sample-select').append(el('option', { value: '' }, 'Add a sample part…'), ...samples.map(s => el('option', { value: s }, s)));
      await this.refresh();
    } catch (err) {
      $('empty').querySelector('h1').textContent = 'Moonshot is not available';
      $('empty').querySelector('p').textContent = err.message;
      return;
    }
    if (this.runId) this.selectRun(this.runId);
    setInterval(() => this.poll(), 2000);
  }

  bindUi() {
    $('open-btn').onclick = () => $('file-input').click();
    $('file-input').onchange = e => e.target.files[0] && this.upload(e.target.files[0]);
    $('sample-select').onchange = async e => {
      const name = e.target.value;
      e.target.value = '';
      if (name) this.busy(`Adding ${name}…`, () => api(`/samples/${name}`, { method: 'POST' }).then(m => this.added(m)));
    };
    document.addEventListener('dragover', e => { e.preventDefault(); document.body.classList.add('dragging'); });
    document.addEventListener('dragleave', e => { if (!e.relatedTarget) document.body.classList.remove('dragging'); });
    document.addEventListener('drop', e => {
      e.preventDefault();
      document.body.classList.remove('dragging');
      if (e.dataTransfer.files[0]) this.upload(e.dataTransfer.files[0]);
    });
    for (const b of document.querySelectorAll('.tabs button')) b.onclick = () => this.tab(b.dataset.tab);
    for (const b of document.querySelectorAll('[data-view]')) b.onclick = () => this.viewer.setView(b.dataset.view);
    $('color-mode').onchange = () => this.colorPoints();
    for (const id of ['show-part', 'show-holder', 'show-points', 'show-axes']) $(id).onchange = () => this.toggles();
    $('show-drum').onchange = () => { this.drum.visible = $('show-drum').checked && this.drumAt !== null; this.viewer.dirty = true; };
    let down = null;
    $('canvas').addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    $('canvas').addEventListener('pointerup', e => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 4) this.pick(e.clientX, e.clientY);
    });
    $('btn-play').onclick = () => this.play(!this.playing);
    $('scrub').oninput = e => { this.play(false); this.showStep(Number(e.target.value)); };
    document.addEventListener('keydown', e => {
      if (e.code === 'Space' && this.program && !e.target.closest('input, select, textarea')) {
        e.preventDefault();
        this.play(!this.playing);
      }
    });
  }

  tab(name) {
    for (const b of document.querySelectorAll('.tabs button')) b.classList.toggle('active', b.dataset.tab === name);
    for (const body of document.querySelectorAll('[data-tab-body]')) body.hidden = body.dataset.tabBody !== name;
  }

  toast(message) {
    const t = $('toast');
    t.textContent = message;
    t.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
  }

  async busy(message, work) {
    const bar = $('progress');
    bar.querySelector('span').textContent = message;
    bar.hidden = false;
    try {
      return await work();
    } catch (err) {
      this.toast(err.message);
    } finally {
      bar.hidden = true;
    }
  }

  // ------------------------------------------------------------ models and runs

  async upload(file) {
    await this.busy(`Uploading ${file.name} (${size(file.size)})…`,
      () => api(`/models/${encodeURIComponent(file.name)}`, { method: 'PUT', raw: file }).then(m => this.added(m)));
  }

  async added(model) {
    await this.refresh();
    this.model = model.model_id;
    this.renderModels();
    this.renderPlan();
    this.tab('plan');
  }

  async refresh() {
    const [models, runs] = await Promise.all([api('/models'), api('/runs')]);
    this.models = models;
    this.runs = runs;
    if (!this.model && models.length) this.model = models[0].model_id;
    this.renderModels();
    this.renderRuns();
  }

  async poll() {
    if (!this.run || !active(this.run)) return;
    const before = this.run;
    this.run = await api(`/runs/${this.run.run_id}`).catch(() => before);
    this.runs = await api('/runs').catch(() => this.runs);
    this.renderRuns();
    this.renderRun();
    if (!done(before, 'plan') && done(this.run, 'plan')) {
      this.renderProgram();
      this.loadScene();
    }
    if (this.run.tasks.filter(t => t.kind === 'fanuc' && t.status === 'done').length >
        before.tasks.filter(t => t.kind === 'fanuc' && t.status === 'done').length) this.loadProgram();
    if (!active(this.run)) this.renderFiles();
  }

  renderModels() {
    $('model-count').textContent = this.models.length || '';
    const root = $('models');
    root.textContent = '';
    if (!this.models.length) root.append(el('p', { class: 'note' }, 'Upload a model or add a sample part.'));
    for (const m of this.models) {
      root.append(el('div', { class: `issue info ms-item${m.model_id === this.model ? ' selected' : ''}`, onclick: () => {
        this.model = m.model_id;
        this.renderModels();
        this.renderPlan();
        this.tab('plan');
      } },
      el('span', { class: 'where' }, size(m.size)),
      el('div', { class: 'msg' }, m.filename),
      el('div', { class: 'note' }, `${m.triangles.toLocaleString()} triangles${m.watertight ? '' : ', not watertight'}`)));
    }
  }

  renderRuns() {
    $('run-count').textContent = this.runs.length || '';
    const root = $('runs');
    root.textContent = '';
    if (!this.runs.length) root.append(el('p', { class: 'note' }, 'No runs yet.'));
    for (const r of this.runs) {
      const state = r.last_status || 'empty';
      root.append(el('div', { class: `issue ${state === 'failed' ? 'error' : 'info'} ms-item${r.run_id === this.run?.run_id ? ' selected' : ''}`,
        onclick: () => this.selectRun(r.run_id) },
      el('span', { class: 'where' }, `${r.last_task || ''} ${state}`),
      el('div', { class: 'msg' }, r.model),
      el('div', { class: 'note' }, `${r.created.replace('T', ' ').slice(0, 16)} · ${r.run_id}`)));
    }
  }

  renderPlan() {
    const root = $('tab-plan');
    root.textContent = '';
    const model = this.models.find(m => m.model_id === this.model);
    if (!model) {
      root.append(el('p', { class: 'note' }, 'Upload a model or add a sample part to plan it.'));
      return;
    }
    const bounds = model.bounds_file_units;
    root.append(el('h3', {}, model.filename),
      el('p', { class: 'note' }, `Bounds in file units: ${bounds[0].map(v => v.toFixed(2)).join(', ')} to ${bounds[1].map(v => v.toFixed(2)).join(', ')}. ` +
        'Lengths below are inches, Z is up. A grid spacing of 0.03–0.05 in gives a quick first look; 0.01 in takes minutes.'));
    const fields = form(PLAN);
    root.append(fields, el('div', { class: 'actions' }, el('button', { class: 'btn primary', onclick: () => this.busy('Queuing…', async () => {
      const queued = await api('/runs', { method: 'POST', body: { model_id: model.model_id, ...collect(fields, PLAN) } });
      await this.refresh();
      await this.selectRun(queued.run_id);
    }) }, 'Plan')));
  }

  async selectRun(runId) {
    try {
      this.run = await api(`/runs/${runId}`);
    } catch (err) {
      this.toast(err.message);
      return;
    }
    history.replaceState(null, '', `?run=${runId}`);
    this.renderRuns();
    this.renderRun();
    this.renderFiles();
    this.tab('run');
    this.clearScene();
    this.setProgram(null);
    if (done(this.run, 'plan')) {
      await this.loadScene();
      if (done(this.run, 'fanuc')) this.loadProgram();
    }
  }

  renderRun() {
    const run = this.run, root = $('tab-run');
    root.textContent = '';
    const badge = $('status-badge');
    $('file-name').textContent = run ? `${run.model} · ${run.run_id}` : '';
    const last = run?.tasks.at(-1);
    badge.hidden = !last;
    if (last) {
      badge.className = `badge ${{ done: 'pass', failed: 'fail', running: 'busy', queued: 'busy' }[last.status]}`;
      badge.textContent = `${last.kind} ${last.status}`;
    }
    if (!run) return;
    for (const t of run.tasks) {
      const took = t.finished && t.started ? ` in ${fmtClock(t.finished - t.started)}` : '';
      root.append(el('div', { class: `issue ${t.status === 'failed' ? 'error' : 'info'}` },
        el('span', { class: 'where' }, `${t.status}${took}`),
        el('div', { class: 'kind' }, t.kind),
        t.log.length ? el('pre', { class: 'ms-log' }, t.log.join('\n')) : null,
        t.error ? el('div', { class: 'msg' }, t.error) : null));
    }
    if (done(run, 'plan')) {
      root.append(el('div', { class: 'actions' },
        el('button', { class: 'btn', disabled: active(run), onclick: () => this.queue('/assets', { steps: true, track: !!this.scene?.band, full_gif: false }) }, 'Render pictures'),
        el('button', { class: 'btn', disabled: active(run), onclick: () => this.queue('/assets', { steps: false, track: false, full_gif: true }),
          title: 'Every pass of the run as one GIF: slow, up to half an hour' }, 'Full tracking GIF')),
      el('p', { class: 'note' }, 'Pictures land in the Files tab. They take a few minutes; the full GIF much longer.'));
      const results = el('div');
      root.append(results);
      this.renderStats(results);
    }
  }

  async renderStats(root) {
    const s = await api(`/runs/${this.run.run_id}/stats`).catch(() => null);
    if (!s) return;
    const turn = s.neighbor_turn_deg.optimized, track = s.tracking, prog = s.program?.stats;
    root.append(el('h3', {}, 'Results'), table([
      ['Grid points', s.points.toLocaleString()],
      ['Reachable', `${s.reachable_pct.toFixed(1)}%`],
      ['Mean neighbor turn', `${turn.mean.toFixed(2)}° (naive ${s.neighbor_turn_deg.naive.mean.toFixed(2)}°)`],
      ['Pairs turning > 10°', `${turn.over_10_pct.toFixed(2)}%`],
      s.grain_tilt_deg && ['Grain tilt mean / p95', `${s.grain_tilt_deg.mean.toFixed(2)}° / ${s.grain_tilt_deg.p95.toFixed(1)}°`],
      track && ['Passes, step', track.mode === 'passes' ? `${track.passes}, ${track.step.toFixed(2)} in` : 'continuous sweep'],
      track && ['Busiest inch of drum', `${track.busiest_inch_pct_untracked.toFixed(0)}% → ${track.busiest_inch_pct.toFixed(1)}% (even ${track.even_inch_pct.toFixed(1)}%)`],
      ['Time', Object.entries(s.times_s).map(([k, v]) => `${k} ${v.toFixed(1)} s`).join(', ')],
      prog && ['Program cycle', `${prog.cycle_s.toFixed(0)} s, ${prog.moves.toLocaleString()} points`],
    ]));
  }

  async queue(path, body) {
    await this.busy('Queuing…', async () => {
      await api(`/runs/${this.run.run_id}${path}`, { method: 'POST', body });
      this.run = await api(`/runs/${this.run.run_id}`);
      this.renderRun();
      this.tab('run');
    });
  }

  async renderFiles() {
    const root = $('tab-files');
    root.textContent = '';
    if (!this.run) return;
    const files = await api(`/runs/${this.run.run_id}/files`).catch(() => []);
    const images = files.filter(f => /\.(png|gif)$/.test(f.path));
    for (const f of images) {
      root.append(el('a', { href: f.url, target: '_blank', class: 'ms-thumb', title: f.path }, el('img', { src: f.url, alt: f.path, loading: 'lazy' }), el('span', {}, f.path)));
    }
    root.append(table(files.map(f => [el('a', { href: f.url, download: f.path.split('/').pop() }, f.path), size(f.size)])));
  }

  // ------------------------------------------------------------ the 3D scene

  clearScene() {
    for (const child of [...this.world.children]) {
      this.world.remove(child);
      child.geometry?.dispose();
      child.material?.dispose();
    }
    this.scene = null;
    this.drumAt = null;
    this.drum.visible = false;
    $('hud').textContent = '';
    $('legend').textContent = '';
    $('empty').hidden = false;
    this.viewer.dirty = true;
  }

  async loadScene() {
    const runId = this.run.run_id;
    const s = await this.busy('Loading the planned part…', () => api(`/runs/${runId}/scene`));
    if (!s || this.run?.run_id !== runId) return;
    this.clearScene();
    for (const key of ['vertices', 'holder', 'point', 'normal', 'axis', 'center', 'feasible_pct', 'grain_deg', 'track', 'change_deg', 'euler']) s[key] = typed(s[key], Float32Array);
    s.faces = typed(s.faces, Uint32Array);
    s.reach = typed(s.reach, Uint8Array);
    s.pass = typed(s.pass, Int32Array);
    this.scene = s;
    const h = s.params.spacing;

    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(s.vertices, 3));
    geom.setIndex(new THREE.BufferAttribute(s.faces, 1));
    geom.computeVertexNormals();
    geom.computeBoundingBox();
    this.part = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ color: PART, roughness: 0.8, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    this.world.add(this.part);

    const held = new THREE.BufferGeometry();
    held.setAttribute('position', new THREE.BufferAttribute(s.holder, 3));
    held.computeVertexNormals();
    this.holder = new THREE.Mesh(held, new THREE.MeshStandardMaterial({ color: HOLDER, roughness: 0.9, side: THREE.DoubleSide, transparent: true, opacity: 0.7 }));
    this.world.add(this.holder);

    // Points sit just off the surface; their size is about the grid spacing on screen.
    const lifted = new Float32Array(s.point.length);
    for (let i = 0; i < lifted.length; i++) lifted[i] = s.point[i] + 0.3 * h * s.normal[i];
    const pts = new THREE.BufferGeometry();
    pts.setAttribute('position', new THREE.BufferAttribute(lifted, 3));
    pts.setAttribute('color', new THREE.BufferAttribute(new Float32Array(lifted.length), 3));
    const fov = this.viewer.camera.fov * Math.PI / 360;
    this.points = new THREE.Points(pts, new THREE.PointsMaterial({ size: 1.1 * h / Math.tan(fov), vertexColors: true }));
    this.world.add(this.points);

    // Axis hairs: the drum's direction at a few thousand points.
    const every = Math.max(1, Math.round(s.points / 4000)), len = Math.max(3 * h, geom.boundingBox.getSize(new THREE.Vector3()).length() / 80);
    const seg = [];
    for (let i = 0; i < s.points; i += every) {
      if (s.reach[i]) continue;
      for (const sign of [-1, 1]) for (let k = 0; k < 3; k++) seg.push(lifted[3 * i + k] + sign * len * s.axis[3 * i + k]);
    }
    const hairs = new THREE.BufferGeometry();
    hairs.setAttribute('position', new THREE.Float32BufferAttribute(seg, 3));
    this.axes = new THREE.LineSegments(hairs, new THREE.LineBasicMaterial({ color: 0x184f95 }));
    this.world.add(this.axes);

    this.drum.scale.set(s.params.radius, s.params.length, s.params.radius);
    const bounds = geom.boundingBox.clone();
    this.viewer.setHelpers(bounds, bounds.min.z);
    this.viewer.frame(bounds);
    $('empty').hidden = true;
    if (!s.band && ['track', 'pass'].includes($('color-mode').value)) $('color-mode').value = 'reach';
    this.colorPoints();
    this.toggles();
  }

  toggles() {
    if (!this.scene) return;
    this.part.visible = $('show-part').checked;
    this.holder.visible = $('show-holder').checked;
    this.points.visible = $('show-points').checked;
    this.axes.visible = $('show-axes').checked;
    this.viewer.dirty = true;
  }

  colorPoints() {
    const s = this.scene;
    if (!s) return;
    const mode = $('color-mode').value, band = s.band || 1;
    const out = this.points.geometry.attributes.color.array;
    const diverge = t => {  // blue, white, red over [-1, 1]
      const a = Math.min(1, Math.abs(t)), w = [0.94, 0.94, 0.93], end = t < 0 ? [0.16, 0.47, 0.84] : [0.89, 0.29, 0.28];
      return w.map((v, k) => v + (end[k] - v) * a);
    };
    for (let i = 0; i < s.points; i++) {
      let c;
      if (mode === 'reach') c = s.reach[i] === 1 ? BLOCKED_PART : s.reach[i] === 2 ? BLOCKED_HOLDER : ramp(s.feasible_pct[i] / 100);
      else if (s.reach[i]) c = NONE;
      else if (mode === 'grain') c = ramp(s.grain_deg[i] / 30);
      else if (mode === 'change') c = ramp(s.change_deg[i] / 10);
      else if (mode === 'track') c = Number.isFinite(s.track[i]) ? diverge(s.track[i] / (band / 2)) : NONE;
      else c = s.pass[i] >= 0 ? hexToRgb(toolColor(s.pass[i])) : NONE;
      out.set(c, 3 * i);
    }
    this.points.geometry.attributes.color.needsUpdate = true;
    const legend = $('legend');
    legend.textContent = '';
    const scale = (title, lo, hi, css = rampCss()) => legend.append(el('div', {}, title), el('div', { class: 'ramp', style: `background:${css}` }),
      el('div', { class: 'ends' }, el('span', {}, lo), el('span', {}, hi)));
    const key = (color, label) => legend.append(el('div', { class: 'key' }, el('span', { class: 'sw', style: `background:rgb(${color.map(v => v * 255).join(',')})` }), label));
    if (mode === 'reach') {
      scale('Collision-free axis angles', '0%', '100%');
      key(BLOCKED_PART, 'unreachable: the part');
      key(BLOCKED_HOLDER, 'unreachable: the holder');
    } else if (mode === 'grain') scale('Tilt toward the grain', '0°', '30°');
    else if (mode === 'change') scale('Mean turn to neighbors', '0°', '10°');
    else if (mode === 'track') scale('Contact along the drum', `−${(band / 2).toFixed(1)} in`, `+${(band / 2).toFixed(1)} in`, 'linear-gradient(90deg,#2a78d6,#f0efec,#e34948)');
    else legend.append(el('div', {}, `${s.passes} passes, colored in turn`));
    this.viewer.dirty = true;
  }

  // Click on the part: the nearest grid point, and the drum touching it.
  pick(x, y) {
    const s = this.scene;
    if (!s) return;
    const hit = this.viewer.ray(x, y).intersectObject(this.part)[0];
    if (!hit) return;
    let best = -1, bestD = Infinity;
    const p = hit.point;
    for (let i = 0; i < s.points; i++) {
      const dx = s.point[3 * i] - p.x, dy = s.point[3 * i + 1] - p.y, dz = s.point[3 * i + 2] - p.z, d = dx * dx + dy * dy + dz * dz;
      if (d < bestD) { bestD = d; best = i; }
    }
    if (best < 0 || Math.sqrt(bestD) > 3 * s.params.spacing) {
      $('hud').textContent = 'No grid point here (the mounting side is not gridded)';
      return;
    }
    const i = best, v = k => s.point[3 * i + k].toFixed(3), deg = k => s.euler[3 * i + k].toFixed(1);
    const why = ['', 'unreachable: the part is in the way', 'unreachable: the holder is in the way'][s.reach[i]];
    $('hud').replaceChildren(...[
      el('div', {}, `point ${i.toLocaleString()} at ${v(0)}, ${v(1)}, ${v(2)}`),
      why ? el('div', { class: 'hit' }, why) : el('div', {}, `rx ${deg(0)}°  ry ${deg(1)}°  rz ${deg(2)}°`),
      why ? null : el('div', {}, `free angles ${s.feasible_pct[i].toFixed(0)}%  grain tilt ${s.grain_deg[i].toFixed(1)}°`),
      why || !Number.isFinite(s.track[i]) ? null : el('div', {}, `pass ${s.pass[i]}  contact ${s.track[i] >= 0 ? '+' : ''}${s.track[i].toFixed(2)} in along the drum`),
    ].filter(Boolean));
    if (why) this.placeDrum(null);
    else this.placeDrum([s.center[3 * i], s.center[3 * i + 1], s.center[3 * i + 2]], [s.axis[3 * i], s.axis[3 * i + 1], s.axis[3 * i + 2]]);
  }

  placeDrum(center, axis) {
    this.drumAt = center;
    this.drum.visible = !!center && $('show-drum').checked;
    if (center) {
      this.drum.position.set(...center);
      this.drum.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(...axis).normalize());
    }
    this.viewer.dirty = true;
  }

  // ------------------------------------------------------------ the robot program

  async loadProgram() {
    const runId = this.run.run_id;
    const p = await api(`/runs/${runId}/program`).catch(err => { this.toast(err.message); return null; });
    if (!p || this.run?.run_id !== runId) return;
    for (const key of ['contact', 'normal', 'axis', 'track', 'time_ms', 'pass', 'line']) p[key] = typed(p[key], Float32Array);
    p.kind = typed(p.kind, Uint8Array);
    p.clock = new Float64Array(p.count);
    for (let i = 1; i < p.count; i++) p.clock[i] = p.clock[i - 1] + p.time_ms[i] / 1000;
    this.setProgram(p);
  }

  setProgram(p) {
    if (this.path) {
      this.world.remove(this.path);
      this.path.geometry.dispose();
      this.path.material.dispose();
      this.path = null;
    }
    this.program = p;
    this.play(false);
    $('btn-play').disabled = $('scrub').disabled = !p;
    $('scrub').max = p ? p.count - 1 : 0;
    $('readout').textContent = '';
    this.renderProgram();
    if (!p) return;
    const seg = [], col = [];
    for (let i = 1; i < p.count; i++) {
      const a = p.contact.subarray(3 * (i - 1), 3 * i), b = p.contact.subarray(3 * i, 3 * i + 3);
      if (!Number.isFinite(a[0]) || !Number.isFinite(b[0])) continue;
      const c = p.kinds[p.kind[i]] === 'bridge' ? [0.95, 0.7, 0.24] : [0.3, 0.76, 1.0];
      seg.push(...a, ...b);
      col.push(...c, ...c);
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.Float32BufferAttribute(seg, 3));
    geom.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    this.path = new THREE.LineSegments(geom, new THREE.LineBasicMaterial({ vertexColors: true, depthTest: false, transparent: true, opacity: 0.85 }));
    this.path.renderOrder = 5;
    this.world.add(this.path);
    this.showStep(0);
  }

  renderProgram() {
    const root = $('tab-program');
    root.textContent = '';
    if (!this.run || !done(this.run, 'plan')) {
      root.append(el('p', { class: 'note' }, 'Plan a run first; the program needs its tracking passes.'));
      return;
    }
    const p = this.program;
    if (p) {
      const st = p.stats;
      root.append(el('h3', {}, `${p.program}.ls`), table([
        ['Cycle', `about ${fmtClock(st.cycle_s)}`],
        ['Points', `${st.moves.toLocaleString()} (${st.contact_points.toLocaleString()} in contact)`],
        ['Passes, pieces', `${st.passes}, ${st.pieces}`],
        ['Bridges, lifts', `${st.bridges}, ${st.lifts}`],
      ]), el('p', { class: 'note' }, 'The path shows the contact (blue sanding, orange bridged gaps). Play or scrub below to move the drum along it.'));
    }
    root.append(el('h3', {}, p ? 'Write again' : 'FANUC program'),
      el('p', { class: 'note' }, 'Positions are the part frame (UTOOL) in the drum frame (UFRAME). The part frame is in the model file\'s own coordinates and units: an origin, an origin and W P R, or three points.'));
    const fields = form(PROGRAM);
    root.append(fields, el('div', { class: 'actions' }, el('button', { class: 'btn primary', onclick: () => {
      try {
        this.queue('/program', collect(fields, PROGRAM));
      } catch (err) {
        this.toast(err.message);
      }
    } }, 'Write program')));
  }

  showStep(i) {
    const p = this.program;
    this.step = i;
    $('scrub').value = i;
    const kind = p.kinds[p.kind[i]], c = p.contact.subarray(3 * i, 3 * i + 3);
    if (Number.isFinite(c[0]) && this.scene) {
      const n = p.normal.subarray(3 * i, 3 * i + 3), a = p.axis.subarray(3 * i, 3 * i + 3), t = p.track[i], r = this.scene.params.radius;
      this.placeDrum([0, 1, 2].map(k => c[k] - t * a[k] + r * n[k]), a);
    }
    $('readout').replaceChildren(el('span', { class: 'ln' }, `line ${p.line[i]}`), ` ${kind}${p.pass[i] >= 0 ? `, pass ${p.pass[i]}` : ''}  ` +
      `${fmtClock(p.clock[i])} / ${fmtClock(p.clock[p.count - 1])}`);
  }

  play(on) {
    const p = this.program;
    if (on && p && this.step >= p.count - 1) this.showStep(0);
    this.playing = on && !!p;
    document.body.classList.toggle('playing', this.playing);
    this.playClock = p ? p.clock[this.step] : 0;
    this.lastFrame = null;
  }

  // Playback runs on the program's own move times, sped up.
  frame(now) {
    if (this.playing) {
      const p = this.program;
      if (this.lastFrame !== null) this.playClock += (now - this.lastFrame) / 1000 * Number($('speed').value);
      this.lastFrame = now;
      let i = this.step;
      while (i < p.count - 1 && p.clock[i + 1] <= this.playClock) i++;
      if (i !== this.step) this.showStep(i);
      if (i >= p.count - 1) this.play(false);
    }
    if (this.viewer.dirty) this.viewer.render();
    requestAnimationFrame(t => this.frame(t));
  }
}

new App().init().catch(err => {
  console.error(err);
  document.body.append(el('pre', { class: 'toast' }, String(err.stack || err)));
});
