// Emucraft viewer: wires the server API, the WebAssembly kernel and the scene.
import * as THREE from 'three';
import { CodeView } from './codeview.js';
import { FLAG_ARC, FLAG_CYCLE, FLAG_RAPID, FLAG_SPINDLE, Kernel, PREVIOUS_OP } from './kernel.js';
import { renderIssues, renderOptimize, renderSetup, renderStats } from './panels.js';
import { Player } from './player.js';
import { StockView } from './stock.js';
import { Timeline } from './timeline.js';
import { buildTool, disposeTool } from './toolmodel.js';
import { ToolpathView } from './toolpath.js';
import { debounce, downloadText, el, fmtClock, fmtLen, hexToRgb, inflateTyped, rampCss, splitLines, toolColor, typed } from './util.js';
import { Viewer } from './viewer.js';

const $ = id => document.getElementById(id);
const EMBED = window.EMUCRAFT_EMBED || null;

async function api(path, body) {
  const resp = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
  if (!resp.ok || data.error) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

class App {
  constructor() {
    this.viewer = new Viewer($('canvas'));
    this.code = new CodeView($('code'), line => this.seekLine(line, true));
    this.timeline = new Timeline($('timeline'), t => this.seekTime(t));
    this.settings = {};
    this.speed = 20;
    this.playing = false;
    this.clock = 0;
    this.seekTarget = null;
    this.selected = -1;
    this.lastUi = 0;
    this.pathMode = 'type';
    this.stockMode = 'material';
    this.optimized = null;
    this.requests = 0;
    this.frame = this.frame.bind(this);
  }

  async init() {
    this.kernel = await Kernel.create(EMBED ? typed(EMBED.wasm, Uint8Array) : 'wasm/emucraft.wasm');
    this.bindUi();
    requestAnimationFrame(this.frame);
    if (EMBED) {
      document.body.classList.add('embedded');
      $('open-btn').hidden = true;
      $('program-select').hidden = true;
      this.source = { name: EMBED.name, text: EMBED.text };
      const initial = EMBED.payload.initial_heights_gz ? await inflateTyped(EMBED.payload.initial_heights_gz, Float32Array) : null;
      this.onAnalysis(EMBED.payload, false, initial);
      return;
    }
    const [info, programs] = await Promise.all([api('/api/info'), api('/api/programs')]);
    this.info = info;
    const select = $('program-select');
    select.append(el('option', { value: '' }, programs.length ? 'Examples & programs…' : 'No programs found'));
    for (const p of programs) select.append(el('option', { value: p.name }, `${p.name}${p.source === 'examples' ? ' (example)' : ''}`));
    const wanted = new URLSearchParams(location.search).get('program');
    const first = programs.find(p => p.name === wanted) || programs.find(p => p.name === 'crash_demo.nc');
    if (first) {
      select.value = first.name;
      this.loadSample(first.name);
    }
  }

  // ------------------------------------------------------------ loading

  async loadSample(name) {
    let text;
    try {
      const resp = await fetch(`/api/programs/${encodeURIComponent(name)}`);
      if (!resp.ok) throw new Error(`Cannot load ${name} (HTTP ${resp.status})`);
      text = await resp.text();
    } catch (err) {
      this.toast(err.message);
      return;
    }
    await this.analyze({ name, text, sample: true }, false, {});
  }

  async loadFile(file) {
    const text = await file.text();
    $('program-select').value = '';
    await this.analyze({ name: file.name, text, sample: false }, false, {});
  }

  // settings: undefined keeps the current ones (re-check), {} starts over
  async analyze(source, keepView = false, settings = undefined) {
    const request = ++this.requests;
    const useSettings = settings === undefined ? this.settings : settings;
    this.setBadge('busy', 'Checking…');
    $('file-name').textContent = source.name;
    this.hideToast();
    let payload, initial = null;
    try {
      payload = await api('/api/analyze', { ...source, settings: useSettings });
      if (payload.initial_heights_gz) initial = await inflateTyped(payload.initial_heights_gz, Float32Array);
    } catch (err) {
      if (request !== this.requests) return;
      // keep showing the program that was loaded before
      this.toast(err.message);
      if (this.report) {
        $('file-name').textContent = this.source.name;
        this.showStatus(this.report);
      } else {
        this.setBadge('fail', 'Error');
      }
      return;
    }
    if (request !== this.requests) return; // a newer load won
    this.source = source;
    this.settings = useSettings;
    // an optimization belongs to one path and one setup
    this.optimized = null;
    $('path-color').querySelector('[value="optimized"]').disabled = true;
    if (this.pathMode === 'optimized') { this.pathMode = 'type'; $('path-color').value = 'type'; }
    this.onAnalysis(payload, keepView, initial);
  }

  showStatus(r) {
    const collisions = r.issues.filter(i => i.severity === 'error').length;
    const warnings = r.issues.length - collisions;
    this.setBadge(r.status, r.status === 'fail' ? `${collisions} collision${collisions === 1 ? '' : 's'}`
      : r.status === 'warn' ? (warnings ? `${warnings} warning${warnings === 1 ? '' : 's'}` : 'Passed with notes') : 'No collisions');
    const count = $('issue-count');
    count.textContent = r.issues.length ? String(r.issues.length) : '✓';
    count.className = `count ${collisions ? '' : r.issues.length ? 'warn' : 'ok'}`;
  }

  // The display grid must fit the GPU's texture size; coarsen it if not.
  fitGrid(display, initial) {
    const max = this.viewer.renderer.capabilities.maxTextureSize;
    if (display.nx <= max && display.ny <= max) return [display, initial];
    const w = (display.x_max ?? display.x0 + display.nx * display.cell) - display.x0;
    const h = (display.y_max ?? display.y0 + display.ny * display.cell) - display.y0;
    const cell = Math.max(display.cell, w / (max - 1), h / (max - 1));
    const grid = { ...display, cell, nx: Math.max(1, Math.ceil(w / cell - 1e-9)), ny: Math.max(1, Math.ceil(h / cell - 1e-9)) };
    if (initial) this.toast('The part is too long for this GPU at full detail; the starting stock is not shown.');
    return [grid, null];
  }

  // ------------------------------------------------------------ analysis

  onAnalysis(payload, keepView = false, initial = null) {
    this.teardown();
    this.payload = payload;
    const r = this.report = payload.report;
    const p = payload.path;
    this.units = r.program.units;
    this.path = {
      n: p.n,
      points: typed(p.points, Float64Array),
      flags: typed(p.flags, Int32Array),
      tools: typed(p.tools, Int32Array),
      feed: typed(p.feed, Float32Array),
      speed: typed(p.speed, Float32Array),
      line: typed(p.line, Int32Array),
    };
    this.lines = splitLines(this.source.text);
    $('empty').hidden = true;
    $('file-name').textContent = r.program.name;
    this.showStatus(r);
    const t = r.timing || {};
    $('timing').textContent = t.total != null ? `checked in ${t.total.toFixed(2)} s` : '';
    $('line-count').textContent = `${r.program.lines.toLocaleString()} lines`;

    const bounds = new THREE.Box3();
    if (r.stock) {
      bounds.set(new THREE.Vector3(r.stock.xmin, r.stock.ymin, r.stock.zmin), new THREE.Vector3(r.stock.xmax, r.stock.ymax, r.stock.zmax));
    } else if (r.stats.bounds) {
      bounds.set(new THREE.Vector3(...r.stats.bounds[0]), new THREE.Vector3(...r.stats.bounds[1]));
    }
    this.bounds = bounds;

    const world = this.viewer.world;
    let display = payload.display;
    if (display && this.path.n) {
      [display, initial] = this.fitGrid(display, initial);
      this.display = display;
      this.kernel.setup({ grid: display, path: this.path, tools: payload.kernel_tools, tolerance: r.stats.tolerance, initial });
      this.stock = new StockView(this.viewer.renderer, display, this.path.n);
      this.stock.setMode(this.stockMode);
      this.stock.upload(this.kernel.heights, this.kernel.marks, null);
      this.kernel.takeDirty();
      this.paintStock();
      this.stock.group.visible = $('show-stock').checked;
      world.add(this.stock.group);
      this.player = new Player(this.kernel, this.path, r.stats.rapid_rate);
    }
    this.toolpath = new ToolpathView(this.path);
    this.toolpath.setShowRapids($('show-rapids').checked);
    this.toolpath.object.visible = $('show-path').checked;
    world.add(this.toolpath.object);
    this.toolModels = payload.kernel_tools.map(kt => buildTool(kt.spec, kt.slot));
    this.toolGroup = new THREE.Group();
    this.toolModels.forEach(m => { m.visible = false; this.toolGroup.add(m); });
    this.toolGroup.visible = $('show-tool').checked;
    world.add(this.toolGroup);
    this.buildMarkers();

    this.viewer.setHelpers(bounds, r.machine.table_z);
    if (!keepView) this.viewer.frame(bounds, 'iso');

    const marks = new Map();
    for (const issue of r.issues) {
      for (let l = issue.lines[0]; l <= issue.lines[1]; l++) {
        if (marks.get(l - 1) !== 'error') marks.set(l - 1, issue.severity);
      }
    }
    this.code.setText(this.lines, marks);
    this.timeline.setData(this.player, this.path, r.issues);
    this.selected = -1;
    this.setPathMode(this.pathMode);
    this.renderPanels();
    this.replayStart = performance.now();
    this.replay = null;
    if (this.player) this.seekMove(this.path.n, 0, true);
    this.updateTool();
    this.viewer.dirty = true;
  }

  teardown() {
    const world = this.viewer.world;
    if (this.stock) { world.remove(this.stock.group); this.stock.dispose(); this.stock = null; }
    if (this.toolpath) { world.remove(this.toolpath.object); this.toolpath.dispose(); this.toolpath = null; }
    if (this.toolGroup) { world.remove(this.toolGroup); this.toolModels.forEach(disposeTool); this.toolGroup = null; }
    for (const m of [...this.viewer.markers.children]) this.viewer.markers.remove(m);
    this.player = null;
    this.display = null;
    this.pick = null;
    this.playing = false;
    document.body.classList.remove('playing');
    this.seekTarget = null;
    this.showProgress(null);
  }

  paintStock() {
    if (!this.stock) return;
    const n = this.path.n;
    const colors = new Uint8Array(Math.max(1, n) * 4);
    const toolRgb = {};
    for (let i = 0; i < n; i++) {
      const slot = this.path.tools[i];
      const c = toolRgb[slot] || (toolRgb[slot] = hexToRgb(toolColor(slot)).map(v => Math.round(v * 255 * 0.85 + 30)));
      colors[4 * i] = c[0]; colors[4 * i + 1] = c[1]; colors[4 * i + 2] = c[2]; colors[4 * i + 3] = 255;
    }
    for (const issue of this.report.issues) {
      if (!['rapid-cut', 'spindle-off-cut', 'link-cut', 'shank', 'holder'].includes(issue.kind)) continue;
      const rgb = issue.severity === 'error' ? [255, 70, 70] : [245, 180, 60];
      for (let m = issue.moves[0]; m <= issue.moves[1] && m < n; m++) {
        colors[4 * m] = rgb[0]; colors[4 * m + 1] = rgb[1]; colors[4 * m + 2] = rgb[2];
      }
    }
    this.stock.setMoveColors(colors);
  }

  buildMarkers() {
    const r = this.report;
    const size = this.bounds ? this.bounds.getSize(new THREE.Vector3()) : new THREE.Vector3(1, 1, 1);
    this.markerSize = Math.max(size.x, size.y) * 0.012 || 0.02;
    if (!this.markerMaterials) {
      const dot = (fill, ring) => {
        const c = document.createElement('canvas');
        c.width = c.height = 64;
        const g = c.getContext('2d');
        g.beginPath(); g.arc(32, 32, 24, 0, Math.PI * 2);
        g.fillStyle = fill; g.fill();
        g.lineWidth = 7; g.strokeStyle = ring; g.stroke();
        const tex = new THREE.CanvasTexture(c);
        tex.colorSpace = THREE.SRGBColorSpace;
        return new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false, transparent: true });
      };
      this.markerMaterials = {
        error: dot('rgba(255,77,77,0.9)', '#ffffff'),
        warning: dot('rgba(245,180,64,0.9)', '#ffffff'),
        selected: dot('rgba(255,45,45,0.25)', '#ff2d2d'),
      };
    }
    r.issues.forEach((issue, i) => {
      const m = new THREE.Sprite(this.markerMaterials[issue.severity] || this.markerMaterials.warning);
      m.position.set(...issue.position);
      m.scale.setScalar(0.028);
      m.renderOrder = 20;
      m.userData.issue = i;
      this.viewer.markers.add(m);
    });
  }

  renderPanels() {
    const r = this.report;
    renderIssues($('tab-issues'), r, {
      selected: this.selected,
      onIssue: i => this.selectIssue(i),
      onLine: line => this.seekLine(line, true),
    });
    renderSetup($('tab-setup'), r, {
      units: this.units, readOnly: !!EMBED, settings: this.settings,
      onApply: changes => {
        let next = {};
        if (changes) {
          next = { ...this.settings, ...changes, tools: { ...(this.settings.tools || {}) } };
          for (const [t, values] of Object.entries(changes.tools || {})) next.tools[t] = { ...(next.tools[t] || {}), ...values };
        }
        this.analyze(this.source, true, next);
      },
    });
    this.renderOptimize();
    renderStats($('tab-stats'), r, { display: this.payload.display, replay: this.replay });
  }

  renderOptimize(busy = false) {
    renderOptimize($('tab-optimize'), {
      materials: this.info?.materials || {},
      defaults: this.info?.optimize_defaults,
      options: this.optimizeOptions,
      result: this.optimized,
      units: this.units,
      readOnly: !!EMBED,
      busy,
      onRun: opts => this.runOptimize(opts),
      onDownload: () => downloadText(this.optimized.name, this.optimized.text),
      onOpen: () => {
        const { text, name } = this.optimized;
        // same setup as the original: its config file and the current settings
        const configOf = this.source.sample ? this.source.name : this.source.configOf;
        this.analyze({ name, text, sample: false, configOf }, true);
      },
    });
  }

  async runOptimize(options) {
    this.optimizeOptions = options;
    this.renderOptimize(true);
    try {
      const result = await api('/api/optimize', { ...this.source, settings: this.settings, options });
      result.moveFeed = typed(result.move_feed, Float32Array);
      this.optimized = result;
      const opt = $('path-color').querySelector('[value="optimized"]');
      opt.disabled = false;
      $('path-color').value = 'optimized';
      this.setPathMode('optimized');
    } catch (err) {
      this.toast(err.message);
    }
    this.renderOptimize(false);
  }

  // ------------------------------------------------------------ playback

  seekTime(t) {
    if (!this.player) return;
    const [m, f] = this.player.at(t);
    this.seekMove(m, f);
  }

  seekMove(move, frac = 0, fromLoad = false) {
    if (!this.player) return;
    this.playing = false;
    document.body.classList.remove('playing');
    this.seekTarget = [Math.max(0, Math.min(move, this.path.n)), frac];
    this.clock = this.player.timeOf(...this.seekTarget);
    this.seekFromLoad = fromLoad;
  }

  // index of the first move whose line is > line (all of `line` executed)
  endOfLine(line) {
    const lines = this.path.line;
    let lo = 0, hi = this.path.n;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (lines[mid] <= line) lo = mid + 1; else hi = mid; }
    return lo;
  }

  seekLine(line, reveal) {
    if (!this.player) return;
    this.seekMove(this.endOfLine(line), 0);
    if (reveal) this.code.setCurrent(line);
  }

  selectIssue(i) {
    const issue = this.report.issues[i];
    if (!issue) return;
    this.selected = i;
    this.seekMove(Math.min(this.path.n, issue.moves[0] + 1), 0);
    const p = new THREE.Vector3(...issue.position);
    this.viewer.focus(p, this.markerSize * 4);
    this.viewer.markers.children.forEach(m => {
      const on = m.userData.issue === i;
      m.scale.setScalar(on ? 0.05 : 0.028);
      m.material = on ? this.markerMaterials.selected : this.markerMaterials[this.report.issues[m.userData.issue].severity] || this.markerMaterials.warning;
    });
    this.code.reveal(issue.lines[0] - 1);
    renderIssues($('tab-issues'), this.report, {
      selected: i, onIssue: j => this.selectIssue(j), onLine: line => this.seekLine(line, true),
    });
    this.viewer.dirty = true;
  }

  togglePlay() {
    if (!this.player) return;
    this.playing = !this.playing;
    if (this.playing) {
      const [m, f] = this.seekTarget || [this.player.move, this.player.frac];
      if (m >= this.path.n) {
        this.player.reset(); // at the end: play again from the start
        this.clock = 0;
      } else {
        this.clock = this.player.timeOf(m, f);
      }
      this.seekTarget = null;
      this.showProgress(null);
    }
    document.body.classList.toggle('playing', this.playing);
  }

  step(dir, byLine) {
    if (!this.player) return;
    const n = this.path.n, lines = this.path.line;
    let m = this.seekTarget ? this.seekTarget[0] : this.player.move;
    if (!byLine) {
      m = Math.max(0, Math.min(n, m + dir));
    } else if (dir > 0) {
      m = m < n ? this.endOfLine(lines[m]) : n;
    } else if (m > 0) {
      m = this.endOfLine(lines[m - 1] - 1);  // undo the current line
    }
    this.seekMove(m, 0);
  }

  frame(now) {
    requestAnimationFrame(this.frame);
    const dt = this.lastFrame ? Math.min(0.1, (now - this.lastFrame) / 1000) : 0;
    this.lastFrame = now;
    const player = this.player;
    if (player) {
      let moved = false;
      if (this.playing) {
        let target;
        if (this.speed === 0) target = [this.path.n, 0];
        else { this.clock += dt * this.speed; target = player.at(this.clock); }
        const done = player.advance(target[0], target[1], this.speed === 0 ? 30 : 12);
        if (!done && this.speed !== 0) this.clock = player.timeOf(player.move, player.frac);
        if (player.move >= this.path.n) { this.playing = false; document.body.classList.remove('playing'); }
        moved = true;
      } else if (this.seekTarget) {
        const [m, f] = this.seekTarget;
        // big steps while seeking: a few slow frames beat many (software GL can be slow)
        const done = player.advance(m, f, this.seekFromLoad ? 150 : 60);
        const total = Math.max(1, m);
        this.showProgress(done ? null : player.move / total, player.move < m ? 'Simulating' : '');
        if (done) {
          this.seekTarget = null;
          if (this.seekFromLoad) {
            this.replay = (performance.now() - this.replayStart) / 1000;
            this.seekFromLoad = false;
            renderStats($('tab-stats'), this.report, { display: this.payload.display, replay: this.replay });
            if (this.pathMode === 'load') this.setPathMode('load');
          }
        }
        moved = true;
      }
      if (moved) {
        const rect = this.kernel.takeDirty();
        if (rect && this.stock) this.stock.upload(this.kernel.heights, this.kernel.marks, rect);
        this.updateTool();
        this.viewer.dirty = true;
      }
      if (moved || now - this.lastUi > 250) this.updateUi(now);
    }
    if (this.viewer.dirty) {
      if (this.stock) this.stock.setCamera(this.viewer.camera);
      this.viewer.render();
    }
  }

  updateTool() {
    if (!this.player || !this.toolGroup) return;
    const { move, frac } = this.player;
    const m = Math.min(move, this.path.n - 1);
    const pos = this.player.position(move, frac);
    const slot = this.path.tools[Math.max(0, m)];
    this.toolModels.forEach((model, i) => { model.visible = this.payload.kernel_tools[i].slot === slot; });
    this.toolGroup.position.set(pos[0], pos[1], pos[2]);
    this.toolpath.setCurrent(move + (frac > 0 ? 1 : 0));
    // "current" is the move being cut, or the last one that finished
    if (this.stock) this.stock.setCurrent(frac > 0 ? move : move - 1);
  }

  updateUi(now) {
    if (now - this.lastUi < 60) return;
    this.lastUi = now;
    const player = this.player;
    const n = this.path.n;
    // the move being cut, or the last one finished
    const i = Math.max(0, Math.min(n - 1, player.frac > 0 ? player.move : player.move - 1));
    const line = n ? this.path.line[i] : 0;
    this.code.setCurrent(line, true);
    this.timeline.setTime(player.timeOf(player.move, player.frac));
    const pos = player.position(player.move, player.frac);
    const u = this.units;
    const f = this.path.flags[i];
    const tool = this.payload.kernel_tools.find(t => t.slot === this.path.tools[i]);
    const feed = f & FLAG_RAPID ? 'rapid' : `F${Math.round(this.path.feed[i] * 10) / 10}`;
    const spin = f & FLAG_SPINDLE ? `S${Math.round(this.path.speed[i])}` : 'S off';
    $('readout').innerHTML = '';
    $('readout').append(
      el('span', { class: 'ln' }, `line ${line + 1}`), ` · ${fmtClock(player.timeOf(player.move, player.frac))} / ${fmtClock(player.duration)}`,
      ` · X${fmtLen(pos[0], u)} Y${fmtLen(pos[1], u)} Z${fmtLen(pos[2], u)} · ${feed} ${spin}${tool ? ` · T${tool.number}` : ''}`);
    const hit = this.report.issues.find(is => i >= is.moves[0] && i <= is.moves[1]);
    const hud = $('hud');
    hud.textContent = '';
    if (hit && player.move > 0) hud.append(el('div', { class: 'hit' }, `⚠ ${hit.message}`));
    if (this.pick) hud.append(el('div', {}, this.pick));
    if (player.move > 0 && player.move < n) {
      const kind = f & FLAG_RAPID ? 'rapid' : f & FLAG_CYCLE ? 'drill cycle' : f & FLAG_ARC ? 'arc' : 'feed';
      hud.append(el('div', { class: 'muted' }, `${kind} · ${this.lines[line]?.trim().slice(0, 60) || ''}`));
    }
  }

  showProgress(fraction, label) {
    const box = $('progress');
    if (fraction === null) { box.hidden = true; return; }
    box.hidden = false;
    box.querySelector('.bar').style.width = `${Math.round(fraction * 100)}%`;
    box.querySelector('span').textContent = `${label} ${Math.round(fraction * 100)}%`;
  }

  // ------------------------------------------------------------ display modes

  setPathMode(mode) {
    this.pathMode = mode;
    if (!this.toolpath) return;
    this.toolpath.setMode(mode, {
      moveVolume: this.player ? this.kernel.fresh().moveVolume : null,
      optimizedFeed: this.optimized?.moveFeed,
    });
    this.renderLegend();
    this.viewer.dirty = true;
  }

  renderLegend() {
    const lg = $('legend');
    lg.textContent = '';
    const legend = this.toolpath?.legend;
    if (!legend || !$('show-path').checked) return;
    const u = this.units === 'mm' ? 'mm' : 'in';
    if (legend.kind === 'keys') {
      for (const [name, color] of legend.keys) lg.append(el('div', { class: 'key' }, el('span', { class: 'sw', style: `background:${color}` }), name));
    } else {
      const fmt = v => (legend.label === 'Feed factor' ? `${Math.round(v * 100)}%` : v >= 100 ? v.toFixed(0) : v.toPrecision(3));
      lg.append(el('div', {}, `${legend.label}${legend.units ? ` (${u}${legend.units})` : ''}`),
        el('div', { class: 'ramp', style: `background:${rampCss()}` }),
        el('div', { class: 'ends' }, el('span', {}, fmt(legend.lo)), el('span', {}, fmt(legend.hi))));
    }
  }

  // ------------------------------------------------------------ picking

  pickAt(x, y) {
    if (!this.payload) return;
    const caster = this.viewer.ray(x, y);
    const hits = caster.intersectObjects(this.viewer.markers.children, false);
    if (hits.length) { this.selectIssue(hits[0].object.userData.issue); return; }
    const hit = this.pickStock(caster.ray);
    if (!hit) { this.pick = null; return; }
    const u = this.units;
    if (hit.mark === 0) {
      this.pick = `Uncut stock at X${fmtLen(hit.x, u)} Y${fmtLen(hit.y, u)} Z${fmtLen(hit.z, u)}`;
    } else if (hit.mark === PREVIOUS_OP) {
      this.pick = `Z${fmtLen(hit.z, u)} at X${fmtLen(hit.x, u)} Y${fmtLen(hit.y, u)}, cut by an earlier program`;
    } else {
      const move = hit.mark - 1;
      const line = this.path.line[move];
      const tool = this.payload.kernel_tools.find(t => t.slot === this.path.tools[move]);
      this.pick = `Z${fmtLen(hit.z, u)} at X${fmtLen(hit.x, u)} Y${fmtLen(hit.y, u)} cut by line ${line + 1}${tool ? ` (T${tool.number})` : ''}`;
      this.code.reveal(line);
      this.code.setCurrent(line, false);
    }
    this.lastUi = 0;
    this.updateUi(performance.now());
  }

  pickStock(ray) {
    const g = this.payload.display;
    if (!g || !this.stock) return null;
    const box = new THREE.Box3(new THREE.Vector3(g.x0, g.y0, g.z_bottom), new THREE.Vector3(g.x0 + g.nx * g.cell, g.y0 + g.ny * g.cell, g.z_top));
    const enter = ray.intersectBox(box, new THREE.Vector3());
    if (!enter) return null;
    const heights = this.kernel.fresh().heights, marks = this.kernel.marks;
    const step = g.cell * 0.5;
    const p = enter.clone();
    const dir = ray.direction;
    const maxSteps = Math.ceil(box.getSize(new THREE.Vector3()).length() / step) + 2;
    for (let s = 0; s < maxSteps; s++) {
      const ix = Math.floor((p.x - g.x0) / g.cell), iy = Math.floor((p.y - g.y0) / g.cell);
      if (ix < 0 || iy < 0 || ix >= g.nx || iy >= g.ny) {
        if (s > 2) return null;
      } else {
        const h = heights[iy * g.nx + ix];
        if (p.z <= h && h > g.z_bottom + 1e-9) return { x: p.x, y: p.y, z: h, mark: marks[iy * g.nx + ix] };
      }
      p.addScaledVector(dir, step);
    }
    return null;
  }

  // ------------------------------------------------------------ chrome

  setBadge(kind, text) {
    const b = $('status-badge');
    b.hidden = false;
    b.className = `badge ${kind}`;
    b.textContent = text;
  }

  toast(message) {
    const t = $('toast');
    t.hidden = false;
    t.textContent = message;
    clearTimeout(this._toast);
    this._toast = setTimeout(() => this.hideToast(), 8000);
  }

  hideToast() { $('toast').hidden = true; }

  bindUi() {
    $('open-btn').addEventListener('click', () => $('file-input').click());
    $('file-input').addEventListener('change', e => { if (e.target.files[0]) this.loadFile(e.target.files[0]); e.target.value = ''; });
    $('program-select').addEventListener('change', e => { if (e.target.value) this.loadSample(e.target.value); });
    $('panel-toggle').addEventListener('click', () => { $('layout').classList.toggle('no-panels'); this.viewer.resize(); });

    for (const btn of document.querySelectorAll('.tabs button')) {
      btn.addEventListener('click', () => {
        for (const b of document.querySelectorAll('.tabs button')) b.classList.toggle('active', b === btn);
        for (const body of document.querySelectorAll('.tab-body')) body.hidden = body.dataset.tabBody !== btn.dataset.tab;
      });
    }
    for (const btn of document.querySelectorAll('[data-view]')) btn.addEventListener('click', () => this.viewer.setView(btn.dataset.view));
    $('show-stock').addEventListener('change', e => { if (this.stock) this.stock.group.visible = e.target.checked; this.viewer.dirty = true; });
    $('show-path').addEventListener('change', e => { if (this.toolpath) this.toolpath.object.visible = e.target.checked; this.renderLegend(); this.viewer.dirty = true; });
    $('show-rapids').addEventListener('change', e => { this.toolpath?.setShowRapids(e.target.checked); this.viewer.dirty = true; });
    $('show-tool').addEventListener('change', e => { if (this.toolGroup) this.toolGroup.visible = e.target.checked; this.viewer.dirty = true; });
    $('stock-color').addEventListener('change', e => { this.stockMode = e.target.value; this.stock?.setMode(this.stockMode); this.viewer.dirty = true; });
    $('path-color').addEventListener('change', e => this.setPathMode(e.target.value));
    $('speed').addEventListener('change', e => { this.speed = Number(e.target.value); });
    $('btn-play').addEventListener('click', () => this.togglePlay());
    $('btn-start').addEventListener('click', () => this.seekMove(0, 0));
    $('btn-end').addEventListener('click', () => this.seekMove(this.path?.n || 0, 0));
    $('btn-back').addEventListener('click', e => this.step(-1, e.shiftKey));
    $('btn-fwd').addEventListener('click', e => this.step(1, e.shiftKey));

    const canvas = $('canvas');
    let down = null;
    canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    canvas.addEventListener('pointerup', e => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 4) this.pickAt(e.clientX, e.clientY);
      down = null;
    });

    window.addEventListener('keydown', e => {
      if (e.target.closest('input, select, textarea')) return;
      const speeds = [...$('speed').options].map(o => Number(o.value));
      switch (e.key) {
        case ' ': e.preventDefault(); this.togglePlay(); break;
        case 'ArrowRight': e.preventDefault(); this.step(1, e.shiftKey); break;
        case 'ArrowLeft': e.preventDefault(); this.step(-1, e.shiftKey); break;
        case 'Home': this.seekMove(0, 0); break;
        case 'End': this.seekMove(this.path?.n || 0, 0); break;
        case 'f': case 'F': this.viewer.setView('fit'); break;
        case '1': this.viewer.setView('iso'); break;
        case '2': this.viewer.setView('top'); break;
        case '3': this.viewer.setView('front'); break;
        case '4': this.viewer.setView('right'); break;
        case '+': case '=': case '-': {
          const i = speeds.indexOf(this.speed);
          const next = speeds[Math.max(0, Math.min(speeds.length - 1, i + (e.key === '-' ? -1 : 1)))];
          this.speed = next; $('speed').value = String(next);
          break;
        }
        default: return;
      }
    });

    if (!EMBED) {
      let depth = 0;
      window.addEventListener('dragenter', e => { e.preventDefault(); depth++; document.body.classList.add('dragging'); });
      window.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; document.body.classList.remove('dragging'); } });
      window.addEventListener('dragover', e => e.preventDefault());
      window.addEventListener('drop', e => {
        e.preventDefault();
        depth = 0;
        document.body.classList.remove('dragging');
        const file = e.dataTransfer.files[0];
        if (file) this.loadFile(file);
      });
    }
    window.addEventListener('resize', debounce(() => this.timeline.draw(), 100));
  }
}

const app = new App();
window.emucraft = app;
app.init().catch(err => {
  console.error(err);
  const t = $('toast');
  t.hidden = false;
  t.textContent = `Could not start the viewer: ${err.message}`;
});
