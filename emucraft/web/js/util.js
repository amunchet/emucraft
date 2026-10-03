// Small shared helpers.

export function decodeBase64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function typed(b64, Type) {
  const bytes = decodeBase64(b64);
  return new Type(bytes.buffer, bytes.byteOffset, bytes.byteLength / Type.BYTES_PER_ELEMENT);
}

// base64 of gzip -> typed array, using the browser's own decompressor
export async function inflateTyped(b64, Type) {
  const stream = new Blob([decodeBase64(b64)]).stream().pipeThrough(new DecompressionStream('gzip'));
  const buf = await new Response(stream).arrayBuffer();
  return new Type(buf);
}

// Same line breaks as Python's str.splitlines(), so line numbers match the
// interpreter: \r\n, \r, \n, and the other Unicode line boundaries.
const LINE_BREAK = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
export function splitLines(text) {
  const lines = text.split(LINE_BREAK);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

export function fmtLen(v, units, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const d = digits ?? (units === 'mm' ? 3 : 4);
  return v.toFixed(d);
}

export function fmtClock(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Perceptual ramp (dark blue -> teal -> yellow), t in [0, 1] -> [r, g, b] 0..1
const RAMP = [
  [0.18, 0.20, 0.55], [0.13, 0.42, 0.72], [0.10, 0.62, 0.70],
  [0.36, 0.78, 0.45], [0.86, 0.85, 0.25], [0.99, 0.62, 0.20],
];
export function ramp(t) {
  t = Math.min(1, Math.max(0, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(t));
  const f = t - i, a = RAMP[i], b = RAMP[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
export function rampCss() {
  const stops = RAMP.map((c, i) => `rgb(${c.map(v => Math.round(v * 255)).join(',')}) ${Math.round(i / (RAMP.length - 1) * 100)}%`);
  return `linear-gradient(90deg, ${stops.join(',')})`;
}

// Distinct tool colors
const TOOL_COLORS = ['#4cc2ff', '#ffb84c', '#a78bfa', '#4ade80', '#f472b6', '#facc15', '#22d3ee', '#fb923c', '#94a3b8', '#e879f9'];
export function toolColor(slot) {
  return slot < 0 ? '#8a96a6' : TOOL_COLORS[slot % TOOL_COLORS.length];
}
export function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}

export function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

export function debounce(fn, ms) {
  let t = 0;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function downloadText(name, text) {
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
