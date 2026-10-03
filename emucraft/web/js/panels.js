// Side panels: issues, setup, optimize, stats.
import { el, fmtClock, fmtLen, toolColor } from './util.js';

const KIND_LABEL = {
  'rapid-cut': 'Rapid into material', 'spindle-off-cut': 'Spindle stopped', 'link-cut': 'Link move cutting',
  shank: 'Shank / flute length', holder: 'Holder collision', table: 'Below table', travel: 'Travel limit',
  tool: 'Missing tool data',
};

function icon(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  return t.content.firstChild;
}

function lines(issue) {
  const [a, b] = issue.lines;
  return a === b ? `line ${a}` : `lines ${a}–${b}`;
}

export function renderIssues(root, report, { onIssue, onLine, selected }) {
  root.textContent = '';
  const issues = report.issues;
  if (!issues.length) {
    root.append(el('div', { class: 'all-clear' },
      icon('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="m7 12.5 3.2 3.2L17 9"/></svg>'),
      'No collisions found',
      el('div', { class: 'note' }, 'Rapids, spindle state, flutes, shank, holder and table were checked on every move.')));
  }
  issues.forEach((issue, i) => {
    const card = el('div', {
      class: `issue ${issue.severity}${selected === i ? ' selected' : ''}`,
      onclick: () => onIssue(i),
    },
    el('span', { class: 'where' }, lines(issue)),
    el('div', { class: 'kind' }, KIND_LABEL[issue.kind] || issue.kind),
    el('div', { class: 'msg' }, issue.message));
    root.append(card);
  });
  const msgs = report.messages.filter(m => m.level !== 'info' || m.code === 'assumed-tool');
  if (msgs.length) {
    root.append(el('h3', {}, 'Program messages'));
    const list = el('ul', { class: 'msg-list' });
    for (const m of msgs) {
      list.append(el('li', { class: m.level, onclick: () => m.line && onLine(m.line - 1) },
        el('span', { class: 'lvl' }, m.level),
        m.line ? el('span', { class: 'muted' }, `line ${m.line}: `) : null,
        m.text, m.count > 1 ? el('span', { class: 'muted' }, ` (×${m.count})`) : null));
    }
    root.append(list);
  }
}

function field(label, name, value, { step = 'any', source, type = 'number', options, wide } = {}) {
  let input;
  if (options) {
    input = el('select', { name }, options.map(([v, t]) => el('option', { value: v, selected: v === value }, t)));
  } else {
    input = el('input', { name, type, step, value: value ?? '', placeholder: value == null ? '—' : null });
  }
  input.dataset.original = input.value;
  return el('label', { class: wide ? 'wide' : null }, label, input, source ? el('span', { class: 'src' }, source) : null);
}

function sourceOf(spec, key) {
  const s = spec.source?.[key];
  if (!s) return null;
  return s.replace('program line', 'line').replace('config ', '');
}

export function renderSetup(root, report, { units, readOnly, onApply, settings }) {
  root.textContent = '';
  const u = units === 'mm' ? 'mm' : 'in';
  if (readOnly) root.append(el('p', { class: 'note' }, 'This is a saved report. Run `emucraft serve` to change the setup and re-check.'));
  const form = el('form', { class: 'setup', onsubmit: e => { e.preventDefault(); onApply(collect(form)); } });

  form.append(el('h3', {}, `Stock (${u})`));
  const s = report.stock || {};
  const stockSrc = s.source ? `from ${s.source}` : null;
  form.append(el('div', { class: 'form' },
    field('X min', 'xmin', s.xmin), field('X max', 'xmax', s.xmax),
    field('Y min', 'ymin', s.ymin), field('Y max', 'ymax', s.ymax),
    field('Z min (bottom)', 'zmin', s.zmin), field('Z max (top)', 'zmax', s.zmax, { source: stockSrc })));

  form.append(el('h3', {}, 'Tools'));
  for (const [number, spec] of Object.entries(report.tools)) {
    const card = el('div', { class: 'tool-card', dataset: { tool: number } });
    const slot = report.program.tools.indexOf(Number(number));
    card.append(el('div', { class: 'title' }, el('span', { class: 'dot', style: `background:${toolColor(slot)}` }),
      `T${number}`, el('span', { class: 'muted small' }, spec.name || '')));
    for (const p of spec.problems || []) card.append(el('div', { class: 'problem' }, p));
    card.append(el('div', { class: 'form' },
      field('Shape', 'shape', spec.shape, { options: [['flat', 'Flat end mill'], ['ball', 'Ball end mill'], ['bull', 'Bull nose'], ['cone', 'Drill / chamfer']], source: sourceOf(spec, 'shape') }),
      field(`Diameter`, 'diameter', spec.diameter, { source: sourceOf(spec, 'diameter') }),
      field('Corner radius', 'corner_radius', spec.corner_radius || null, { source: sourceOf(spec, 'corner_radius') }),
      field('Tip angle (°)', 'tip_angle', spec.shape === 'cone' ? spec.tip_angle : null, { source: sourceOf(spec, 'tip_angle') }),
      field('Flute length', 'flute_length', spec.flute_length, { source: sourceOf(spec, 'flute_length') }),
      field('Stick-out', 'stickout', spec.stickout, { source: sourceOf(spec, 'stickout') }),
      field('Holder diameter', 'holder_diameter', spec.holder_diameter, { source: sourceOf(spec, 'holder_diameter') }),
      field('Shank diameter', 'shank_diameter', spec.shank_diameter, { source: sourceOf(spec, 'shank_diameter') })));
    form.append(card);
  }

  form.append(el('h3', {}, 'Machine & check'));
  form.append(el('div', { class: 'form' },
    field('Rapids', 'rapid_mode', report.machine.rapid_mode, { options: [['linear', 'Linear'], ['dogleg', 'Dog-leg (axes independent)']] }),
    field(`Table / fixture Z`, 'table_z', report.machine.table_z),
    field(`Link feed ≥ (${u}/min)`, 'link_feed', report.stats.link_feed ?? null, { source: 'feed moves this fast must not cut; empty: off' }),
    field(`Resolution (${u})`, 'resolution', report.grid?.cell, { source: report.grid ? `${report.grid.nx} × ${report.grid.ny} cells` : null })));
  if (!readOnly) {
    form.append(el('div', { class: 'actions' },
      el('button', { class: 'btn primary', type: 'submit' }, 'Re-check'),
      el('button', { class: 'btn', type: 'button', onclick: () => onApply(null) }, 'Reset to program')));
  } else {
    for (const input of form.querySelectorAll('input, select')) input.disabled = true;
  }
  root.append(form);
}

function changed(input) {
  return input && input.value !== input.dataset.original;
}

function value(input) {
  if (input.tagName === 'SELECT') return input.value;
  if (input.value === '') return null;
  const n = Number(input.value);
  return Number.isFinite(n) ? n : null;
}

// Only what the user changed, so values keep their provenance.
function collect(form) {
  const out = {};
  const stockInputs = ['xmin', 'ymin', 'zmin', 'xmax', 'ymax', 'zmax'].map(k => form.querySelector(`[name="${k}"]`));
  if (stockInputs.some(changed)) {
    const stock = stockInputs.map(value);
    if (stock.every(v => v !== null)) out.stock = stock;
  }
  for (const card of form.querySelectorAll('.tool-card')) {
    const t = {};
    for (const input of card.querySelectorAll('input, select')) {
      if (changed(input) && value(input) !== null) t[input.name] = value(input);
    }
    if (Object.keys(t).length) (out.tools ||= {})[card.dataset.tool] = t;
  }
  for (const name of ['rapid_mode', 'table_z', 'link_feed', 'resolution']) {
    const input = form.querySelector(`[name="${name}"]`);
    if (!changed(input)) continue;
    // an emptied link feed turns the check off (0); other fields fall back to the program
    out[name] = value(input) ?? (name === 'link_feed' ? 0 : null);
  }
  return out;
}

export function renderOptimize(root, { materials, defaults, result, units, readOnly, busy, onRun, onDownload, onOpen, options }) {
  root.textContent = '';
  const u = units === 'mm' ? 'mm' : 'in';
  root.append(el('p', { class: 'note' },
    'Slows the feed around sharp corners and where the simulation shows the tool removing material faster than its typical cut. Only F words change; the new path is verified against the original.'));
  if (readOnly) {
    root.append(el('p', { class: 'note' }, 'Run `emucraft serve` (or `emucraft optimize`) to optimize this program.'));
    return;
  }
  const opts = { ...(defaults || {}), ...(options || {}) };
  const matOptions = [['', 'Custom'], ...Object.keys(materials || {}).map(m => [m, m[0].toUpperCase() + m.slice(1)])];
  const form = el('form', { class: 'form', onsubmit: e => { e.preventDefault(); onRun(read()); } },
    field('Material preset', 'material', opts.material || '', { options: matOptions, wide: true }),
    field('Corner angle (°)', 'corner_angle', opts.corner_angle),
    field('Feed at corners (×)', 'corner_factor', opts.corner_factor),
    field('Corner zone (× dia)', 'corner_zone', opts.corner_zone),
    field('Removal rate limit (×)', 'load_limit', opts.load_limit),
    field('Minimum feed (×)', 'min_factor', opts.min_factor),
    field('Air-cut feed (×)', 'air_factor', opts.air_factor),
    field(`Max feed (${u}/min)`, 'max_feed', opts.max_feed),
    el('div', { class: 'actions wide' },
      el('button', { class: 'btn primary', type: 'submit', disabled: busy }, busy ? 'Optimizing…' : 'Optimize feeds')));
  const matSelect = form.querySelector('[name="material"]');
  matSelect.addEventListener('change', () => {
    const preset = materials[matSelect.value];
    if (!preset) return;
    for (const [k, v] of Object.entries(preset)) {
      const input = form.querySelector(`[name="${k}"]`);
      if (input) input.value = v;
    }
  });
  function read() {
    const out = { material: matSelect.value || null };
    for (const input of form.querySelectorAll('input')) {
      out[input.name] = input.value === '' ? null : Number(input.value);
    }
    return out;
  }
  root.append(form);

  if (!result) return;
  const st = result.stats;
  const before = st.cutting_seconds_before, after = st.cutting_seconds_after;
  const pct = before ? ((after - before) / before) * 100 : 0;
  root.append(el('h3', {}, 'Result'));
  if (!result.verified) {
    root.append(el('p', { class: 'problem' }, 'The optimized program did not verify; nothing to download.'));
  }
  root.append(el('div', { class: 'opt-summary' },
    el('div', {}, 'Cutting time', el('b', {}, `${fmtClock(before)} → ${fmtClock(after)}`), el('span', { class: 'muted' }, `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`)),
    el('div', {}, 'Corners slowed', el('b', {}, String(st.corners))),
    el('div', {}, 'Lines changed', el('b', {}, String(st.lines_changed)), el('span', { class: 'muted' }, `${st.lines_added} added`)),
    el('div', {}, 'Pieces slowed', el('b', {}, `${st.pieces_slowed}`), el('span', { class: 'muted' }, `of ${st.pieces}`))));
  for (const m of result.messages || []) root.append(el('p', { class: 'note' }, `${m.level}: ${m.text}`));
  if (result.verified) {
    root.append(el('div', { class: 'actions' },
      el('button', { class: 'btn primary', onclick: onDownload }, `Download ${result.name}`),
      el('button', { class: 'btn', onclick: onOpen }, 'Open it here')));
  }
}

export function renderStats(root, report, extra) {
  root.textContent = '';
  const st = report.stats, u = report.program.units, U = u === 'mm' ? 'mm' : 'in';
  const row = (k, v) => el('tr', {}, el('td', {}, k), el('td', {}, v));
  const t = el('table', { class: 'stats' },
    row('Cycle time (estimate)', fmtClock(st.cycle_seconds)),
    row('Cutting', fmtClock(st.cutting_seconds)),
    row('Rapids', fmtClock(st.rapid_seconds)),
    row('Dwell', fmtClock(st.dwell_seconds)),
    row('Tool changes', st.tool_changes),
    row('Feed distance', `${st.feed_length.toFixed(2)} ${U}`),
    row('Rapid distance', `${st.rapid_length.toFixed(2)} ${U}`),
    row('Moves', `${report.program.moves.toLocaleString()} (${st.moves.rapid} rapid, ${st.moves.arc} arc segments)`),
    row('Material removed', st.volume_removed != null ? `${st.volume_removed.toFixed(4)} ${U}³` : '—'),
    row('Stock volume', st.stock_volume != null ? `${st.stock_volume.toFixed(4)} ${U}³` : '—'));
  root.append(el('h3', {}, 'Program'), t);
  const tools = el('table', { class: 'stats' });
  for (const [num, v] of Object.entries(st.by_tool || {})) {
    tools.append(row(num === 'null' ? 'No tool' : `T${num}`, `${fmtClock(v.seconds)} · ${v.feed_length.toFixed(1)} ${U} · ${(st.volume_by_tool?.[num] ?? 0).toFixed(3)} ${U}³`));
  }
  root.append(el('h3', {}, 'By tool'), tools);
  const g = report.grid;
  const timing = report.timing || {};
  root.append(el('h3', {}, 'Check'), el('table', { class: 'stats' },
    row('Check grid', g ? `${g.nx.toLocaleString()} × ${g.ny.toLocaleString()} @ ${fmtLen(g.cell, u)} ${U}` : '—'),
    row('Viewer grid', extra.display ? `${extra.display.nx} × ${extra.display.ny} @ ${fmtLen(extra.display.cell, u)} ${U}` : '—'),
    row('Server time', timing.total != null ? `${timing.total.toFixed(2)} s` : '—'),
    row('Kernel time', timing.simulate != null ? `${timing.simulate.toFixed(2)} s` : '—'),
    row('Browser replay', extra.replay != null ? `${extra.replay.toFixed(2)} s` : '—')));
}
