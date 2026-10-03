// Virtualized G-code listing: only the visible rows exist in the DOM. Very
// long programs would exceed the browser's maximum element height, so the
// scrollbar is mapped onto a capped height and rows are placed relative to
// the scroll position.
const ROW = 18;
const MAX_PX = 8_000_000;
const WORD = /(\([^)]*\)?|;.*$)|([GgMm][0-9.]+)|([Ff][-+0-9.]+)|([TtSsHhDd][-+0-9.]+)|([XxYyZzIiJjKkRrAaBbCc][-+0-9.#[\]]+)/g;
const CLASS = [null, 'c', null, 'f', 't', 'ax'];

function highlight(text, node) {
  node.textContent = '';
  let last = 0;
  WORD.lastIndex = 0;
  for (let m; (m = WORD.exec(text));) {
    if (m.index > last) node.append(text.slice(last, m.index));
    let cls = null;
    for (let g = 1; g < m.length; g++) if (m[g]) { cls = g === 2 ? (/^[Mm]/.test(m[g]) ? 'm' : 'g') : CLASS[g]; break; }
    const span = document.createElement('span');
    span.className = cls || '';
    span.textContent = m[0];
    node.append(span);
    last = m.index + m[0].length;
    if (m[0].length === 0) WORD.lastIndex++;
  }
  if (last < text.length) node.append(text.slice(last));
}

export class CodeView {
  constructor(container, onLine) {
    this.el = container;
    this.onLine = onLine;
    this.lines = [];
    this.marks = new Map();
    this.current = -1;
    this.follow = true;
    this.rows = document.createElement('div');
    this.rows.className = 'rows';
    this.el.append(this.rows);
    this.pool = new Map();
    this.el.addEventListener('scroll', () => this.render());
    this.el.addEventListener('wheel', () => { this.follow = false; clearTimeout(this._t); this._t = setTimeout(() => { this.follow = true; }, 2500); }, { passive: true });
    this.rows.addEventListener('click', e => {
      const row = e.target.closest('.row');
      if (row) this.onLine(Number(row.dataset.line));
    });
    new ResizeObserver(() => this.render()).observe(this.el);
  }

  setText(lines, marks) {
    this.lines = lines;
    this.marks = marks || new Map();
    this.total = lines.length * ROW;
    this.rows.style.height = `${Math.min(this.total, MAX_PX)}px`;
    for (const node of this.pool.values()) node.remove();
    this.pool.clear();
    this.current = -1;
    this.el.scrollTop = 0;
    this.render();
  }

  // content offset per scrolled pixel (1 unless the program is huge)
  get scale() {
    const view = this.el.clientHeight || 600;
    const capped = Math.min(this.total || 0, MAX_PX);
    return capped > view && this.total > capped ? (this.total - view) / (capped - view) : 1;
  }

  offset() { return this.el.scrollTop * this.scale; }

  scrollToOffset(offset) {
    this.el.scrollTop = Math.max(0, offset / this.scale);
  }

  setCurrent(line, scroll = true) {
    if (line === this.current) return;
    const prev = this.pool.get(this.current);
    if (prev) prev.classList.remove('current');
    this.current = line;
    const node = this.pool.get(line);
    if (node) node.classList.add('current');
    if (scroll && this.follow && line >= 0) {
      const top = line * ROW, view = this.el.clientHeight, at = this.offset();
      if (top < at + ROW || top > at + view - 2 * ROW) this.scrollToOffset(top - view / 3);
    }
  }

  reveal(line) {
    this.scrollToOffset(line * ROW - this.el.clientHeight / 3);
    this.render();
  }

  render() {
    const scroll = this.el.scrollTop, height = this.el.clientHeight || 600;
    const top = scroll * this.scale;
    const first = Math.max(0, Math.floor(top / ROW) - 10);
    const last = Math.min(this.lines.length - 1, Math.ceil((top + height) / ROW) + 10);
    for (const [line, node] of this.pool) {
      if (line < first || line > last) { node.remove(); this.pool.delete(line); }
      else node.style.top = `${scroll + line * ROW - top}px`;
    }
    for (let line = first; line <= last; line++) {
      if (this.pool.has(line)) continue;
      const row = document.createElement('div');
      row.className = 'row';
      row.dataset.line = line;
      row.style.top = `${scroll + line * ROW - top}px`;
      const mark = this.marks.get(line);
      if (mark) row.classList.add('bad', mark);
      if (line === this.current) row.classList.add('current');
      const ln = document.createElement('span');
      ln.className = 'ln';
      ln.textContent = line + 1;
      const tx = document.createElement('span');
      tx.className = 'tx';
      highlight(this.lines[line], tx);
      row.append(ln, tx);
      this.rows.append(row);
      this.pool.set(line, row);
    }
  }
}
