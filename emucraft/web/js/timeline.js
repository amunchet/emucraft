// Timeline: machine time from left to right, tool bands, issue ticks.
import { toolColor } from './util.js';

export class Timeline {
  constructor(container, onSeek) {
    this.el = container;
    this.canvas = container.querySelector('canvas');
    this.onSeek = onSeek;
    this.player = null;
    this.issues = [];
    this.time = 0;
    let dragging = false;
    const seek = e => {
      if (!this.player) return;
      const r = this.canvas.getBoundingClientRect();
      const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      this.onSeek(x * this.player.duration);
    };
    this.canvas.addEventListener('pointerdown', e => { dragging = true; this.canvas.setPointerCapture(e.pointerId); seek(e); });
    this.canvas.addEventListener('pointermove', e => { if (dragging) seek(e); });
    const stop = () => { dragging = false; };
    this.canvas.addEventListener('pointerup', stop);
    this.canvas.addEventListener('pointercancel', stop);
    this.canvas.addEventListener('lostpointercapture', stop);
    new ResizeObserver(() => this.draw()).observe(this.el);
  }

  setData(player, path, issues) {
    this.player = player;
    this.path = path;
    this.issues = issues;
    this._bands = null;
    this.draw();
  }

  setTime(t) {
    if (Math.abs(t - this.time) < 1e-9) return;
    this.time = t;
    this.draw();
  }

  _toolBands() {
    if (this._bands) return this._bands;
    const bands = [];
    const { tools, n } = this.path;
    let start = 0;
    for (let i = 1; i <= n; i++) {
      if (i === n || tools[i] !== tools[start]) {
        bands.push([this.player.timeOf(start), this.player.timeOf(i), tools[start]]);
        start = i;
      }
    }
    this._bands = bands;
    return bands;
  }

  draw() {
    const c = this.canvas, dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const css = getComputedStyle(document.documentElement);
    g.fillStyle = css.getPropertyValue('--raised');
    g.fillRect(0, h / 2 - 5, w, 10);
    if (!this.player || !this.player.duration) return;
    const T = this.player.duration;
    for (const [a, b, slot] of this._toolBands()) {
      g.fillStyle = toolColor(slot);
      g.globalAlpha = 0.55;
      g.fillRect((a / T) * w, h / 2 - 5, Math.max(1, ((b - a) / T) * w), 10);
    }
    g.globalAlpha = 1;
    for (const issue of this.issues) {
      const x = (this.player.timeOf(issue.moves[0]) / T) * w;
      g.fillStyle = issue.severity === 'error' ? css.getPropertyValue('--err') : css.getPropertyValue('--warn');
      g.fillRect(Math.round(x) - 1, 2, 3, h - 4);
    }
    const x = (this.time / T) * w;
    g.fillStyle = css.getPropertyValue('--text');
    g.fillRect(Math.round(x) - 1, 0, 2, h);
    g.beginPath();
    g.arc(x, h / 2, 5, 0, Math.PI * 2);
    g.fill();
  }
}
