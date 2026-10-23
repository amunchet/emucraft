// Toolpath lines: one draw call; progress and rapid visibility are uniforms.
import * as THREE from 'three';
import { FLAG_ARC, FLAG_CYCLE, FLAG_NOCUT, FLAG_RAPID } from './kernel.js';
import { hexToRgb, ramp, toolColor } from './util.js';

const vertexShader = /* glsl */`
  in vec3 color;
  in float aMove;            // move index; negative for rapids (-index - 1)
  uniform float uCurrent;    // moves before this are done
  uniform float uShowRapids;
  uniform float uTodoAlpha;
  out vec4 vColor;
  void main() {
    bool rapid = aMove < 0.0;
    float move = rapid ? -aMove - 1.0 : aMove;
    float alpha = move < uCurrent ? 1.0 : uTodoAlpha;
    if (rapid) alpha *= 0.85;
    vColor = vec4(color, alpha);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    gl_Position.z -= 2e-4 * gl_Position.w;       // stay in front of the machined floor
    if (rapid && uShowRapids < 0.5) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
  }`;

const fragmentShader = /* glsl */`
  in vec4 vColor;
  layout(location = 0) out highp vec4 fragColor;
  void main() { fragColor = vColor; }`;

export const TYPE_COLORS = {
  rapid: '#ff6b3d', feed: '#4cc2ff', arc: '#5ee0c0', plunge: '#f5d547', cycle: '#c792ff', link: '#ff4fa3',
};

export class ToolpathView {
  constructor(path) {
    this.path = path;
    const n = path.n;
    const pos = new Float32Array(n * 6);
    const move = new Float32Array(n * 2);
    const P = path.points;
    for (let i = 0; i < n; i++) {
      const a = 3 * i, k = 6 * i;
      pos[k] = P[a]; pos[k + 1] = P[a + 1]; pos[k + 2] = P[a + 2];
      pos[k + 3] = P[a + 3]; pos[k + 4] = P[a + 4]; pos[k + 5] = P[a + 5];
      const code = path.flags[i] & FLAG_RAPID ? -i - 1 : i;
      move[2 * i] = code; move[2 * i + 1] = code;
    }
    this.colors = new Float32Array(n * 6);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geom.setAttribute('color', new THREE.BufferAttribute(this.colors, 3));
    geom.setAttribute('aMove', new THREE.BufferAttribute(move, 1));
    geom.computeBoundingSphere();
    this.uniforms = {
      uCurrent: { value: 0 },
      uShowRapids: { value: 1 },
      uTodoAlpha: { value: 0.22 },
    };
    this.material = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      uniforms: this.uniforms,
      vertexShader,
      fragmentShader,
      transparent: true,
      depthWrite: false,
    });
    this.object = new THREE.LineSegments(geom, this.material);
    this.object.name = 'toolpath';
    this.object.renderOrder = 2;
    this.legend = null;
  }

  setCurrent(move) { this.uniforms.uCurrent.value = move; }
  setShowRapids(show) { this.uniforms.uShowRapids.value = show ? 1 : 0; }

  _paint(fn) {
    const c = this.colors;
    for (let i = 0; i < this.path.n; i++) {
      const [r, g, b] = fn(i);
      const k = 6 * i;
      c[k] = c[k + 3] = r; c[k + 1] = c[k + 4] = g; c[k + 2] = c[k + 5] = b;
    }
    this.object.geometry.attributes.color.needsUpdate = true;
  }

  // mode: type | feed | load | optimized. extra: { moveVolume, optimizedFeed }
  setMode(mode, extra = {}) {
    const { flags, feed, points, tools } = this.path;
    const rapid = hexToRgb(TYPE_COLORS.rapid);
    if (mode === 'feed' || mode === 'load' || mode === 'optimized') {
      let values, lo = Infinity, hi = -Infinity, label, unitsLabel;
      const n = this.path.n;
      values = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        if (flags[i] & FLAG_RAPID) { values[i] = NaN; continue; }
        let v;
        if (mode === 'feed') v = feed[i];
        else if (mode === 'optimized') v = feed[i] > 0 && extra.optimizedFeed ? extra.optimizedFeed[i] / feed[i] : NaN;
        else {
          const k = 3 * i;
          const len = Math.hypot(points[k + 3] - points[k], points[k + 4] - points[k + 1], points[k + 5] - points[k + 2]);
          v = extra.moveVolume && len > 0 ? extra.moveVolume[i] / len * feed[i] : NaN;
        }
        values[i] = v;
        if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      }
      if (mode === 'optimized') { lo = Math.min(lo, 1); hi = 1; }
      const unchanged = mode === 'optimized' && lo >= 1;
      if (!(hi > lo)) { hi = lo + 1; }
      const neutral = [0.45, 0.5, 0.56];
      this._paint(i => {
        if (flags[i] & FLAG_RAPID) return rapid;
        const v = values[i];
        if (!Number.isFinite(v)) return neutral;
        if (mode === 'optimized') return v >= 0.999 ? [0.35, 0.55, 0.75] : ramp(1 - (1 - v) / Math.max(1e-6, 1 - lo));
        return ramp((v - lo) / (hi - lo));
      });
      label = mode === 'feed' ? 'Feed rate' : mode === 'load' ? 'Removal rate' : 'Feed factor';
      unitsLabel = mode === 'feed' ? '/min' : mode === 'load' ? '³/min' : '';
      this.legend = unchanged
        ? { kind: 'keys', keys: [['No feed changed', '#5a8cbf']] }
        : { kind: 'ramp', label, lo, hi, units: unitsLabel, reverse: mode === 'optimized' };
      return;
    }
    const colors = {};
    for (const [k, v] of Object.entries(TYPE_COLORS)) colors[k] = hexToRgb(v);
    const multiTool = new Set(tools).size > 1;
    this._paint(i => {
      const f = flags[i];
      if (f & FLAG_RAPID) return colors.rapid;
      if (f & FLAG_NOCUT) return colors.link;
      if (f & FLAG_CYCLE) return colors.cycle;
      const k = 3 * i;
      if (points[k + 3] === points[k] && points[k + 4] === points[k + 1] && points[k + 5] < points[k + 2]) return colors.plunge;
      if (multiTool) return hexToRgb(toolColor(tools[i]));
      return f & FLAG_ARC ? colors.arc : colors.feed;
    });
    this.legend = { kind: 'keys', keys: multiTool
      ? [['Rapid', TYPE_COLORS.rapid], ['Plunge', TYPE_COLORS.plunge], ['Feed (by tool)', toolColor(0)]]
      : [['Rapid', TYPE_COLORS.rapid], ['Feed', TYPE_COLORS.feed], ['Arc', TYPE_COLORS.arc], ['Plunge', TYPE_COLORS.plunge], ['Cycle', TYPE_COLORS.cycle]] };
  }

  dispose() {
    this.object.geometry.dispose();
    this.material.dispose();
  }
}
