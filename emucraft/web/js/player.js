// Playback: machine time -> moves, driving the WebAssembly kernel.
//
// The kernel state always matches a position (move, frac): moves before
// `move` are fully cut and `frac` of move `move` is cut. Going forward only
// sweeps the difference; going back resets the stock and replays.
import { FLAG_RAPID } from './kernel.js';

export class Player {
  constructor(kernel, path, rapidRate) {
    this.kernel = kernel;
    this.path = path;
    const n = path.n;
    this.n = n;
    this.times = new Float64Array(n + 1); // start time of each move, seconds
    const P = path.points;
    let t = 0;
    for (let i = 0; i < n; i++) {
      this.times[i] = t;
      const k = 3 * i;
      const len = Math.hypot(P[k + 3] - P[k], P[k + 4] - P[k + 1], P[k + 5] - P[k + 2]);
      const rate = path.flags[i] & FLAG_RAPID ? rapidRate : path.feed[i];
      t += rate > 0 ? (len / rate) * 60 : 0;
    }
    this.times[n] = t;
    this.duration = t;
    this.move = 0;
    this.frac = 0;
  }

  position(move, frac) {
    const P = this.path.points;
    if (move >= this.n) {
      const k = 3 * this.n;
      return [P[k], P[k + 1], P[k + 2]];
    }
    const k = 3 * move;
    return [
      P[k] + (P[k + 3] - P[k]) * frac,
      P[k + 1] + (P[k + 4] - P[k + 1]) * frac,
      P[k + 2] + (P[k + 5] - P[k + 2]) * frac,
    ];
  }

  timeOf(move, frac = 0) {
    if (move >= this.n) return this.duration;
    return this.times[move] + (this.times[move + 1] - this.times[move]) * frac;
  }

  // (move, frac) at machine time t
  at(t) {
    if (t <= 0) return [0, 0];
    if (t >= this.duration) return [this.n, 0];
    let lo = 0, hi = this.n - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.times[mid] <= t) lo = mid; else hi = mid - 1;
    }
    const span = this.times[lo + 1] - this.times[lo];
    return [lo, span > 0 ? (t - this.times[lo]) / span : 1];
  }

  before(move, frac, m2, f2) {
    return move < m2 || (move === m2 && frac < f2);
  }

  reset() {
    this.kernel.reset();
    this.move = 0;
    this.frac = 0;
  }

  // Sweep towards (m2, f2) for at most budgetMs. Returns true when there.
  advance(m2, f2, budgetMs) {
    if (m2 >= this.n) { m2 = this.n; f2 = 0; }
    if (this.before(m2, f2, this.move, this.frac)) this.reset();
    const k = this.kernel;
    const { flags, tools } = this.path;
    const t0 = performance.now();
    let chunk = 64;
    while (this.before(this.move, this.frac, m2, f2)) {
      if (this.frac > 0 || this.move === m2) {
        // finish (or extend) a partial move
        const end = this.move === m2 ? f2 : 1;
        const a = this.position(this.move, this.frac), b = this.position(this.move, end);
        k.segment(a, b, flags[this.move], tools[this.move], this.move);
        if (end >= 1) { this.move++; this.frac = 0; } else { this.frac = end; }
      } else {
        const stop = Math.min(m2, this.move + chunk);
        const s = performance.now();
        k.run(this.move, stop);
        this.move = stop;
        const dt = performance.now() - s;
        chunk = dt < 2 ? Math.min(chunk * 2, 1 << 20) : Math.max(8, Math.floor(chunk * 0.7));
      }
      if (performance.now() - t0 > budgetMs) break;
    }
    return !this.before(this.move, this.frac, m2, f2);
  }
}
