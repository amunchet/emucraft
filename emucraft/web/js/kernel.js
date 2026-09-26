// The C kernel (kernel/src/emucraft.c) compiled to WebAssembly. The browser
// runs the same simulation as the command line, so playback, scrubbing and
// "what cut this surface" never need the server.

export const FLAG_RAPID = 1;
export const FLAG_SPINDLE = 2;
export const FLAG_NOCUT = 4;
export const FLAG_ARC = 8;
export const FLAG_CYCLE = 16;
// Mark of cells cut by an earlier program in a chained check.
export const PREVIOUS_OP = 0xffffffff;

export const EVENT_KINDS = {
  1: 'rapid-cut', 2: 'spindle-off-cut', 3: 'link-cut', 4: 'shank', 5: 'holder',
};

let compiled = null;

function compile(source) {
  if (!compiled) {
    compiled = (async () => {
      if (source instanceof Uint8Array || source instanceof ArrayBuffer) return WebAssembly.compile(source);
      const resp = await fetch(source);
      if (!resp.ok) throw new Error(`Cannot load the simulation kernel (${resp.status})`);
      if (WebAssembly.compileStreaming && resp.headers.get('content-type') === 'application/wasm') {
        try { return await WebAssembly.compileStreaming(resp.clone()); } catch { /* fall through */ }
      }
      return WebAssembly.compile(await resp.arrayBuffer());
    })();
  }
  return compiled;
}

export class Kernel {
  static async create(source) {
    const module = await compile(source);
    const instance = await WebAssembly.instantiate(module, {});
    return new Kernel(instance.exports);
  }

  constructor(exports) {
    this.x = exports;
    this.memory = exports.memory;
    this.eventSize = exports.ec_event_size();
    this.sim = 0;
  }

  // One session per program / stock: previous allocations are dropped wholesale.
  // initial: optional Float32Array height field left by earlier programs.
  setup({ grid, path, tools, tolerance, initial = null, eventsCapacity = 4096 }) {
    this.initial = initial && initial.length === grid.nx * grid.ny ? initial : null;
    const x = this.x;
    x.ec_heap_reset();
    this.grid = grid;
    this.n = path.n;
    const n = Math.max(1, path.n);
    this.sim = x.ec_sim_new(grid.nx, grid.ny, grid.x0, grid.y0, grid.cell, grid.z_top, grid.z_bottom);
    if (!this.sim) throw new Error(`Not enough memory for a ${grid.nx} x ${grid.ny} stock grid`);
    if (tolerance) x.ec_sim_set_tolerance(this.sim, tolerance);
    this.ctx = x.ec_ctx_new(eventsCapacity);
    this.pPoints = x.ec_alloc(8 * 3 * (n + 1));
    this.pFlags = x.ec_alloc(4 * n);
    this.pTools = x.ec_alloc(4 * n);
    this.pVolume = x.ec_alloc(8 * n);
    this.pDepth = x.ec_alloc(4 * n);
    this.pDirty = x.ec_alloc(16);
    if (!this.ctx || !this.pPoints || !this.pFlags || !this.pTools || !this.pVolume || !this.pDepth || !this.pDirty) {
      throw new Error('Not enough memory for the toolpath');
    }
    x.ec_ctx_set_move_outputs(this.ctx, this.pVolume, this.pDepth, this.n);
    this._views();
    this.points.set(path.points);
    this.flags.set(path.flags);
    this.tools.set(path.tools);
    for (const t of tools) {
      if (!t.ok) continue;
      if (!x.ec_sim_set_tool(this.sim, t.slot, t.shape, t.radius, t.corner, t.slope, t.flute)) {
        throw new Error(`Invalid geometry for T${t.number}`);
      }
      (t.bodies || []).forEach((b, j) => x.ec_sim_set_body(this.sim, t.slot, j, b[0], b[1]));
    }
    this.reset();
  }

  _views() {
    const buf = this.memory.buffer;
    const { nx, ny } = this.grid;
    const n = Math.max(1, this.n);
    this._buffer = buf;
    this.heights = new Float32Array(buf, this.x.ec_sim_heights(this.sim), nx * ny);
    this.marks = new Uint32Array(buf, this.x.ec_sim_marks(this.sim), nx * ny);
    this.points = new Float64Array(buf, this.pPoints, 3 * (this.n + 1));
    this.flags = new Int32Array(buf, this.pFlags, n);
    this.tools = new Int32Array(buf, this.pTools, n);
    this.moveVolume = new Float64Array(buf, this.pVolume, n);
    this.moveDepth = new Float32Array(buf, this.pDepth, n);
    this.dirtyView = new Int32Array(buf, this.pDirty, 4);
  }

  // Views go stale if the memory ever grows; everything is allocated in
  // setup(), so this only guards against surprises.
  fresh() {
    if (this.memory.buffer !== this._buffer) this._views();
    return this;
  }

  reset() {
    this.x.ec_sim_reset(this.sim);
    this.x.ec_ctx_reset(this.ctx);
    this.resetPending = true; // every cell changed: the next takeDirty() covers the grid
    this.fresh();
    if (this.initial) {
      // stock left by earlier operations; mark it so it renders as machined
      this.heights.set(this.initial);
      const top = Math.fround(this.grid.z_top);
      for (let i = 0; i < this.initial.length; i++) if (this.initial[i] < top) this.marks[i] = PREVIOUS_OP;
    }
    this.trim();
    if (this.initial) this.x.ec_sim_invalidate(this.sim);
    this.moveVolume.fill(0);
    this.moveDepth.fill(0);
  }

  // The grid overhangs the stock by up to a cell: those cells start empty
  // (same as check.trim_to_stock on the command line).
  trim() {
    const { nx, ny, x0, y0, cell, z_bottom, x_max, y_max } = this.grid;
    if (x_max === undefined || y_max === undefined) return;
    const empty = Math.fround(z_bottom);
    let touched = false;
    for (let ix = Math.max(0, nx - 2); ix < nx; ix++) {
      if (x0 + (ix + 0.5) * cell <= x_max) continue;
      for (let iy = 0; iy < ny; iy++) this.heights[iy * nx + ix] = empty;
      touched = true;
    }
    for (let iy = Math.max(0, ny - 2); iy < ny; iy++) {
      if (y0 + (iy + 0.5) * cell <= y_max) continue;
      this.heights.fill(empty, iy * nx, (iy + 1) * nx);
      touched = true;
    }
    if (touched && !this.initial) this.x.ec_sim_invalidate(this.sim);
  }

  run(begin, end) {
    if (end <= begin) return 0;
    return this.x.ec_sim_run(this.sim, this.ctx, this.pPoints, this.pFlags, this.pTools, begin, end, 0, this.grid.ny);
  }

  segment(a, b, flags, tool, move) {
    this.x.ec_sim_segment(this.sim, this.ctx, a[0], a[1], a[2], b[0], b[1], b[2], flags, tool, move, 0, this.grid.ny);
  }

  takeDirty() {
    this.x.ec_ctx_dirty(this.ctx, this.pDirty);
    this.x.ec_ctx_clear_dirty(this.ctx);
    if (this.resetPending) {
      this.resetPending = false;
      return [0, 0, this.grid.nx, this.grid.ny];
    }
    const d = this.fresh().dirtyView;
    return d[0] < d[2] ? [d[0], d[1], d[2], d[3]] : null;
  }

  takeEvents() {
    const count = this.x.ec_ctx_event_count(this.ctx);
    const base = this.x.ec_ctx_events(this.ctx);
    const view = new DataView(this.memory.buffer);
    const out = [];
    for (let i = 0; i < count; i++) {
      const p = base + i * this.eventSize;
      out.push({
        kind: EVENT_KINDS[view.getInt32(p, true)] || 'collision',
        move: view.getInt32(p + 4, true),
        body: view.getInt32(p + 8, true),
        cells: view.getInt32(p + 12, true),
        x: view.getFloat64(p + 16, true),
        y: view.getFloat64(p + 24, true),
        z: view.getFloat64(p + 32, true),
        depth: view.getFloat64(p + 40, true),
        volume: view.getFloat64(p + 48, true),
      });
    }
    this.x.ec_ctx_clear_events(this.ctx);
    return out;
  }

  volume() { return this.x.ec_sim_volume(this.sim); }
}
