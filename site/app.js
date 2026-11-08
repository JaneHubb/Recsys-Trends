/* RecSys Trends — stage-1 front end. No build step. Reads only data/summary.json and data/papers/*.json. */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const NS = 'http://www.w3.org/2000/svg';
  const fmt = n => Number(n || 0).toLocaleString('en-US');
  const p1 = x => (x * 100).toFixed(1) + '%';
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const DATA = 'data/';

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids) { if (c == null) continue; e.append(c.nodeType ? c : document.createTextNode(String(c))); }
    return e;
  }
  function sv(tag, attrs) { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v); return e; }
  function activate(node, fn) { node.addEventListener('click', fn); node.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } }); }

  let S = null;            // summary.json
  let VENUES = [];         // [{id, name, color, all_core}]
  let YEARS = [];
  const colorOf = v => 'var(--s' + ((VENUES.find(x => x.id === v) || {}).color || 0) + ')';
  const cnt = (v, y) => (S.counts[v] || {})[String(y)] || { state: 'planned', total: 0, core: 0, related: 0, border: 0, pending: 0 };
  const doneVenues = y => VENUES.filter(v => cnt(v.id, y).state === 'done').map(v => v.id);

  /* ---------------- tooltip ---------------- */
  const tip = $('#tip');
  function showTip(at, title, rows, hint) {
    tip.replaceChildren(el('div', { class: 't', text: title }));
    rows.forEach(r => { const i = el('i'); if (r.c) i.style.background = r.c; tip.append(el('div', { class: 'r' }, i, el('span', { text: r.name }), el('b', { text: r.value }))); });
    if (hint) tip.append(el('div', { class: 'h', text: hint }));
    tip.hidden = false;
    let x, y;
    if (at && at.clientX != null) { x = at.clientX + 14; y = at.clientY + 14; } else { const b = at.getBoundingClientRect(); x = b.right + 8; y = b.top; }
    const w = tip.offsetWidth, h = tip.offsetHeight;
    if (x + w > window.innerWidth - 8) x = Math.max(8, x - w - 28);
    if (y + h > window.innerHeight - 8) y = Math.max(8, window.innerHeight - h - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hideTip() { tip.hidden = true; }
  window.addEventListener('scroll', hideTip, { passive: true });

  /* ---------------- ⓪ ---------------- */
  function renderOverview() {
    const b = S.basis, ov = S.overview || {};
    $('#d-data').textContent = S.data_date || '—';
    $('#d-gen').textContent = (S.generated || '').slice(0, 10) || '—';
    $('#d-next').textContent = S.next_update || '—';
    $('#brand-sub').textContent = 'Recommender-systems papers at ' + VENUES.map(v => v.id).join(' · ');
    $('#f-model').textContent = S.judge_model || 'LLM';

    const basis = $('#basis'); basis.replaceChildren();
    if (!b || !b.year) {
      basis.append(el('span', { class: 'muted', text: 'No venue has completed aggregation yet.' }));
    } else {
      basis.append(el('span', { class: 'yr' }, 'Basis year', el('strong', { text: String(b.year) })));
      basis.append(el('span', { class: 'pill ' + (b.kind === 'final' ? 'final' : 'prov') }, el('span', { class: 'dot' }), (b.kind === 'final' ? 'final' : 'provisional') + ' · ' + b.done + '/' + b.total + ' venues'));
      if (b.missing && b.missing.length) basis.append(el('span', { class: 'note', text: b.missing.join(', ') + ' in progress · included in the ' + (S.next_update || 'next') + ' update' }));
    }

    const tiles = $('#tiles'); tiles.replaceChildren();
    const tile = (l, v, unit, d, dcls, f, hero) => el('div', { class: 'tile' }, el('div', { class: 'l', text: l }), el('div', { class: 'v' + (hero ? ' hero' : '') }, v, unit ? el('small', { text: unit }) : null), d ? el('div', { class: 'd ' + (dcls || '') , text: d }) : null, f ? el('div', { class: 'f', text: f }) : null);
    if (b && b.year) {
      tiles.append(tile('Recommender papers (' + b.year + ', ' + b.done + ' complete venues)', fmt(ov.core), '', null, '', 'core only · ' + fmt(ov.core_related) + ' including related', true));
      if (ov.delta != null) {
        const d = ov.delta;
        tiles.append(tile('Year over year (same ' + ov.compare_venues.length + ' venues)', (d >= 0 ? '+' : '') + (d * 100).toFixed(1), '%', (d >= 0 ? '▲ ' : '▼ ') + fmt(ov.compare_prev_core) + ' → ' + fmt(ov.compare_core), d >= 0 ? 'up' : 'down', ov.compare_venues.join(' · ')));
      } else {
        tiles.append(tile('Year over year', '—', '', null, '', 'No venue is complete in both years yet'));
      }
      if (ov.ratio_median != null) {
        const r1 = ov.ratio_median, r0 = ov.ratio_median_prev;
        tiles.append(tile('Share within venue (median)', (r1 * 100).toFixed(1), '%', r0 != null ? ((r1 >= r0 ? '▲ ' : '▼ ') + Math.abs((r1 - r0) * 100).toFixed(1) + ' pts · previous year ' + (r0 * 100).toFixed(1) + '%') : null, '', 'recommender papers ÷ all papers at the venue · ' + ov.ratio_venues + ' venues (RecSys excluded, all its papers count)'));
      }
      const border = VENUES.reduce((a, v) => a + cnt(v.id, b.year).border, 0);
      tiles.append(tile('Borderline cases (' + b.year + ')', fmt(border), '', null, '', 'Have recommender keywords but judged "not a recommender paper" · toggle in ④'));
    }

    const ul = $('#sentences'); ul.replaceChildren();
    (S.sentences || []).forEach(s => ul.append(el('li', { text: s })));

    const ip = $('#inprog'); ip.replaceChildren();
    const rows = (S.in_progress || []).filter(r => r.done > 0 || Object.values(r.venues).some(s => s !== 'planned'));
    if (!rows.length) ip.append(el('div', { class: 'empty', text: 'No year after the basis year has started aggregation.' }));
    rows.forEach(r => {
      const vs = el('div', { class: 'vs' });
      VENUES.forEach(v => { const st = r.venues[v.id]; vs.append(el('span', {}, el('span', { class: 'sym ' + (st === 'done' ? 'done' : st === 'part' ? 'part' : 'plan') }), v.id)); });
      ip.append(el('div', { class: 'row' }, el('span', { class: 'y', text: r.year + ' · ' + r.done + '/' + r.total }), vs));
    });

    const t = $('#board'); t.replaceChildren();
    const hr = el('tr', {}, el('th', { scope: 'col', text: '' }));
    YEARS.forEach(y => hr.append(el('th', { scope: 'col', text: String(y) })));
    t.append(el('thead', {}, hr));
    const tb = el('tbody');
    VENUES.forEach(v => {
      const tr = el('tr', {}, el('th', { scope: 'row' }, el('span', { class: 'vdot', style: 'background:' + colorOf(v.id) }), v.id));
      YEARS.forEach(y => {
        const c = cnt(v.id, y); const st = c.state === 'done' ? 'done' : c.state === 'part' ? 'part' : 'plan';
        const txt = st === 'done' ? 'complete · core ' + fmt(c.core) + ' of ' + fmt(c.total) : st === 'part' ? 'in progress (' + (c.why || '') + ') · core ' + fmt(c.core) : 'planned';
        const btn = el('button', { type: 'button', class: st, title: v.id + ' ' + y + ' · ' + txt, 'aria-label': v.id + ' ' + y + ' ' + txt }, el('span', { class: 'sym ' + st, 'aria-hidden': 'true' }));
        if (st !== 'plan') btn.addEventListener('click', () => applyFilter({ venue: v.id, year: String(y), track: '', q: '' }));
        else btn.setAttribute('aria-disabled', 'true');
        tr.append(el('td', {}, btn));
      });
      tb.append(tr);
    });
    t.append(tb);
  }

  /* ---------------- ① ---------------- */
  const C = { metric: 'count', related: false, table: false };
  const val = (v, y) => { const c = cnt(v, y); return c.core + (C.related ? c.related : 0); };
  function yearRows() {
    return YEARS.map(y => {
      const done = doneVenues(y);
      const segs = VENUES.filter(v => done.includes(v.id)).map(v => ({ v: v.id, c: colorOf(v.id), val: val(v.id, y) })).filter(s => s.val > 0);
      return { y, segs, tot: segs.reduce((a, s) => a + s.val, 0), done: done.length, prov: done.length > 0 && done.length < VENUES.length };
    }).filter(r => r.done > 0);
  }
  function syncControls() {
    document.querySelectorAll('#c-metric button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === C.metric)));
    $('#c-table').setAttribute('aria-pressed', String(C.table)); $('#c-table').textContent = C.table ? 'Show as chart' : 'Show as table';
  }
  let lastW = 0;
  function renderFig1() {
    const box = $('#f1-svg'); box.replaceChildren(); hideTip();
    const leg = $('#f1-legend'); leg.replaceChildren();
    const relTxt = C.related ? 'core + related' : 'core';
    let title, sub, note;
    if (C.metric !== 'ratio') {
      title = C.metric === 'count' ? 'Recommender papers by venue' : 'Venue mix (recommender papers that year = 100%)';
      sub = 'Stacked bars · ' + relTxt + ' · complete venues only · each paper belongs to one venue';
      note = 'Click a bar to open that venue-year in ④. On faded bars, "n/' + VENUES.length + '" is the number of venues summed.';
      VENUES.forEach(v => leg.append(el('span', {}, el('span', { class: 'sw', style: 'background:' + colorOf(v.id) }), v.id)));
      drawStack(box);
    } else {
      title = 'Share within venue'; sub = 'recommender papers ÷ all papers at the venue (' + (S.counted_tracks || []).join(' · ') + ' tracks) · shared y-axis · RecSys excluded, all its papers count';
      note = 'Removes the effect of venue size. Click a point to open ④.';
      drawMultiples(box);
    }
    $('#f1-title').textContent = title; $('#f1-sub').textContent = sub; $('#f1-note').textContent = note;
    renderTable();
    box.hidden = C.table; $('#f1-table').hidden = !C.table; leg.hidden = C.table;
    lastW = $('#fig1').clientWidth || lastW;
  }
  function chartW(box) { return Math.max(280, Math.floor(box.clientWidth || (box.parentElement.clientWidth - 36))); }
  function niceStep(max, n) { const raw = Math.max(max, 1) / n; const p = Math.pow(10, Math.floor(Math.log10(raw))); const f = raw / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; }
  function niceTicks(max, n) { const st = niceStep(max, n); const top = Math.ceil(max / st) * st || st; const t = []; for (let v = 0; v <= top + 1e-9; v += st) t.push(+v.toFixed(6)); return t; }
  function roundTop(x, y, w, h, r) { r = Math.min(r, h, w / 2); return `M${x},${y + h}L${x},${y + r}Q${x},${y} ${x + r},${y}L${x + w - r},${y}Q${x + w},${y} ${x + w},${y + r}L${x + w},${y + h}Z`; }

  function drawStack(box) {
    const rows = yearRows();
    if (!rows.length) { box.append(el('div', { class: 'empty-state', text: 'No venue-year has completed aggregation yet.' })); return; }
    const W = chartW(box), H = W < 560 ? 276 : 336, m = { l: 44, r: 8, t: 22, b: 42 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, band = iw / rows.length, bw = Math.min(24, band * 0.56);
    const share = C.metric === 'share';
    const ticks = share ? [0, 0.25, 0.5, 0.75, 1] : niceTicks(Math.max(...rows.map(r => r.tot)), 4);
    const top = ticks[ticks.length - 1];
    const yS = v => m.t + ih - v / top * ih;
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'group', 'aria-label': share ? 'Venue mix by year' : 'Recommender papers by venue and year' });
    ticks.forEach(t => { svg.append(sv('line', { x1: m.l, x2: W - m.r, y1: yS(t), y2: yS(t), class: t === 0 ? 'axisl' : 'gridl' })); const tx = sv('text', { x: m.l - 8, y: yS(t) + 4, 'text-anchor': 'end', class: 'tick' }); tx.textContent = share ? Math.round(t * 100) + '%' : fmt(t); svg.append(tx); });
    rows.forEach((r, ri) => {
      const cx = m.l + band * (ri + 0.5); let cum = 0;
      r.segs.forEach((s, k) => {
        const v = share ? s.val / r.tot : s.val; const y0 = yS(cum), y1 = yS(cum + v); cum += v;
        const gap = k > 0 ? 2 : 0; const h = (y0 - gap) - y1; if (h <= 0.3) return;
        const g = sv('g', { class: 'seg-hit', tabindex: '0', role: 'button', 'aria-label': s.v + ' ' + r.y + ': ' + fmt(s.val) + ' papers' + (r.prov ? ' (provisional)' : '') });
        g.append(sv('rect', { x: cx - band / 2 + 1, y: y1, width: band - 2, height: h + gap, style: 'fill:transparent' }));
        const isTop = k === r.segs.length - 1;
        const mk = isTop ? sv('path', { d: roundTop(cx - bw / 2, y1, bw, h, 4) }) : sv('rect', { x: cx - bw / 2, y: y1, width: bw, height: h });
        mk.setAttribute('style', 'fill:' + s.c + ';' + (r.prov ? 'fill-opacity:.5' : ''));
        g.append(mk);
        const tipFn = e => showTip(e && e.clientX != null ? e : g, r.y + ' · ' + s.v + (r.prov ? ' (provisional)' : ''), [{ c: s.c, name: s.v, value: fmt(s.val) }, { name: 'share that year', value: p1(s.val / r.tot) }, { name: 'total that year', value: fmt(r.tot) }], 'Click to open the list in ④');
        g.addEventListener('pointermove', tipFn); g.addEventListener('pointerleave', hideTip); g.addEventListener('focus', () => tipFn(null)); g.addEventListener('blur', hideTip);
        activate(g, () => applyFilter({ venue: s.v, year: String(r.y), track: '', q: '' }));
        svg.append(g);
      });
      if (!share) { const cap = sv('text', { x: cx, y: yS(r.tot) - 6, 'text-anchor': 'middle', class: 'capv' }); cap.textContent = fmt(r.tot); svg.append(cap); }
      if (r.prov) { const pt = sv('text', { x: cx, y: H - 6, 'text-anchor': 'middle', class: 'provtag' }); pt.textContent = r.done + '/' + VENUES.length; svg.append(pt); }
      const xl = sv('text', { x: cx, y: H - 23, 'text-anchor': 'middle', class: 'tick' }); xl.textContent = (W < 520 || rows.length > 10) ? "'" + String(r.y).slice(2) : String(r.y); svg.append(xl);
    });
    box.append(svg);
  }

  function drawMultiples(box) {
    const grid = el('div', { class: 'multiples' }); box.append(grid);
    const vs = VENUES.filter(v => !v.all_core);
    const ratio = (v, y) => { const c = cnt(v, y); if (c.state === 'planned' || !c.total) return null; return (c.core + (C.related ? c.related : 0)) / c.total; };
    const years = YEARS.filter(y => vs.some(v => ratio(v.id, y) != null));
    if (!years.length) { box.append(el('div', { class: 'empty-state', text: 'No data yet.' })); return; }
    const max = Math.max(...vs.flatMap(v => years.map(y => ratio(v.id, y) || 0)), 0.01);
    const ticks = niceTicks(max, 2), top = ticks[ticks.length - 1];
    const cells = vs.map(v => {
      const last = years.map(y => ({ y, r: ratio(v.id, y), st: cnt(v.id, y).state })).filter(x => x.r != null).pop();
      const txt = last ? last.y + ' ' + p1(last.r) + (last.st !== 'done' ? ' in progress' : '') : 'no data';
      const c = el('div', { class: 'mult' }, el('h4', {}, el('span', {}, el('span', { class: 'vdot', style: 'background:' + colorOf(v.id) }), v.id), el('span', { text: txt })));
      grid.append(c); return [v, c];
    });
    cells.forEach(([v, c]) => {
      const W = Math.max(160, c.clientWidth - 24), H = 104, m = { l: 34, r: 10, t: 10, b: 18 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const n = years.length;
      const xS = i => m.l + (n > 1 ? iw * i / (n - 1) : iw / 2), yS = r => m.t + ih - r / top * ih;
      const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'group', 'aria-label': v.id + ' share of recommender papers over time' });
      ticks.forEach(t => { svg.append(sv('line', { x1: m.l, x2: W - m.r, y1: yS(t), y2: yS(t), class: t === 0 ? 'axisl' : 'gridl' })); const tx = sv('text', { x: m.l - 6, y: yS(t) + 4, 'text-anchor': 'end', class: 'tick', style: 'font-size:10px' }); tx.textContent = Math.round(t * 100) + '%'; svg.append(tx); });
      [0, n - 1].forEach(i => { const xl = sv('text', { x: xS(i), y: H - 4, 'text-anchor': i ? 'end' : 'start', class: 'tick', style: 'font-size:10px' }); xl.textContent = String(years[i]); svg.append(xl); });
      const pts = years.map((y, i) => { const r = ratio(v.id, y); return r == null ? null : { x: xS(i), y: yS(r), r, yr: y, done: cnt(v.id, y).state === 'done' }; });
      const have = pts.filter(Boolean);
      if (!have.length) { c.append(svg); return; }
      const solid = have.filter(p => p.done);
      if (solid.length) {
        const area = 'M' + solid[0].x + ',' + yS(0) + solid.map(p => 'L' + p.x.toFixed(1) + ',' + p.y.toFixed(1)).join('') + 'L' + solid[solid.length - 1].x + ',' + yS(0) + 'Z';
        svg.append(sv('path', { d: area, style: 'fill:' + colorOf(v.id) + ';fill-opacity:.1' }));
        svg.append(sv('path', { d: solid.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(''), style: 'fill:none;stroke:' + colorOf(v.id) + ';stroke-width:2;stroke-linejoin:round;stroke-linecap:round' }));
      }
      for (let i = 1; i < have.length; i++) if (!have[i].done || !have[i - 1].done) svg.append(sv('path', { d: `M${have[i - 1].x},${have[i - 1].y}L${have[i].x},${have[i].y}`, style: 'fill:none;stroke:' + colorOf(v.id) + ';stroke-width:2;stroke-dasharray:4 4;stroke-linecap:round' }));
      const lastP = have[have.length - 1];
      svg.append(sv('circle', { cx: lastP.x, cy: lastP.y, r: 4, style: 'fill:' + colorOf(v.id) + ';stroke:var(--surface);stroke-width:2' }));
      have.forEach(p => {
        const h = sv('circle', { cx: p.x, cy: p.y, r: 12, style: 'fill:transparent;cursor:pointer', tabindex: '0', role: 'button', 'aria-label': v.id + ' ' + p.yr + ' ' + p1(p.r) });
        const cc = cnt(v.id, p.yr);
        const fn = e => showTip(e && e.clientX != null ? e : h, v.id + ' · ' + p.yr + (p.done ? '' : ' (in progress)'), [{ c: colorOf(v.id), name: 'share within venue', value: p1(p.r) }, { name: 'rec / all', value: fmt(cc.core + (C.related ? cc.related : 0)) + ' / ' + fmt(cc.total) }], 'Click to open the list in ④');
        h.addEventListener('pointermove', fn); h.addEventListener('pointerleave', hideTip); h.addEventListener('focus', () => fn(null)); h.addEventListener('blur', hideTip);
        activate(h, () => applyFilter({ venue: v.id, year: String(p.yr), track: '', q: '' }));
        svg.append(h);
      });
      c.append(svg);
    });
  }

  function renderTable() {
    const box = $('#f1-table'); box.replaceChildren();
    const years = YEARS.filter(y => VENUES.some(v => cnt(v.id, y).state !== 'planned'));
    const t = el('table', { class: 'data' }); const head = el('tr', {}, el('th', { text: 'Venue' }));
    years.forEach(y => head.append(el('th', { text: doneVenues(y).length < VENUES.length ? y + ' (prov.)' : String(y) }))); t.append(el('thead', {}, head));
    const tb = el('tbody');
    VENUES.forEach(v => {
      const tr = el('tr', {}, el('td', { text: v.id }));
      years.forEach(y => {
        const c = cnt(v.id, y); let txt;
        if (c.state === 'planned') txt = '—';
        else if (C.metric === 'count') txt = fmt(val(v.id, y)) + (c.state !== 'done' ? '*' : '');
        else if (C.metric === 'share') { const tot = doneVenues(y).reduce((a, x) => a + val(x, y), 0); txt = (c.state === 'done' && tot) ? p1(val(v.id, y) / tot) : '—'; }
        else txt = (v.all_core || !c.total) ? '—' : p1(val(v.id, y) / c.total) + (c.state !== 'done' ? '*' : '');
        tr.append(el('td', { text: txt }));
      });
      tb.append(tr);
    });
    if (C.metric === 'count') { const tr = el('tr', {}, el('td', { text: 'Total (complete venues)', style: 'font-weight:600' })); years.forEach(y => tr.append(el('td', { text: fmt(doneVenues(y).reduce((a, x) => a + val(x, y), 0)), style: 'font-weight:600' }))); tb.append(tr); }
    t.append(tb); box.append(t);
    box.append(el('p', { class: 'note', text: '* current value of a venue-year still in progress' }));
  }

  /* ---------------- ④ ---------------- */
  const F = { venue: '', year: '', track: '', sort: 'new', q: '', related: false, border: false, demo: false, shown: 30 };
  const cache = new Map();   // "venue-year" → papers json | null
  async function loadPapers(v, y) {
    const k = v + '-' + y;
    if (cache.has(k)) return cache.get(k);
    const p = fetch(DATA + 'papers/' + k + '.json').then(r => r.ok ? r.json() : null).catch(() => null);
    cache.set(k, p);
    const res = await p; cache.set(k, res); return res;
  }
  function fillSelects() {
    const svn = $('#f-venue'); svn.append(el('option', { value: '', text: 'All' })); VENUES.forEach(v => svn.append(el('option', { value: v.id, text: v.id })));
    const sy = $('#f-year'); sy.append(el('option', { value: '', text: 'All' })); [...YEARS].reverse().forEach(y => sy.append(el('option', { value: String(y), text: doneVenues(y).length < VENUES.length && doneVenues(y).length > 0 ? y + ' (provisional)' : String(y) })));
    const st = $('#f-track'); st.append(el('option', { value: '', text: 'All counted tracks' })); (S.counted_tracks || ['main', 'short', 'applied', 'resource']).forEach(t => st.append(el('option', { value: t, text: t })));
    const bind = (id, key, ev) => $(id).addEventListener(ev || 'change', e => { F[key] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; F.shown = 30; renderList(); });
    bind('#f-venue', 'venue'); bind('#f-year', 'year'); bind('#f-track', 'track'); bind('#f-sort', 'sort'); bind('#f-q', 'q', 'input'); bind('#f-related', 'related'); bind('#f-border', 'border'); bind('#f-demo', 'demo');
    $('#f-reset').addEventListener('click', () => { Object.assign(F, { venue: '', year: S.basis && S.basis.year ? String(S.basis.year) : '', track: '', q: '', related: false, border: false, demo: false, shown: 30 }); syncFilters(); renderList(); });
    $('#f-more').addEventListener('click', () => { F.shown += 30; renderList(); });
  }
  function syncFilters() { $('#f-venue').value = F.venue; $('#f-year').value = F.year; $('#f-track').value = F.track; $('#f-sort').value = F.sort; $('#f-q').value = F.q; $('#f-related').checked = F.related; $('#f-border').checked = F.border; $('#f-demo').checked = F.demo; }
  function readUrl() {
    const u = new URLSearchParams(location.search);
    for (const k of ['venue', 'year', 'track', 'sort', 'q']) if (u.has(k)) F[k] = u.get(k);
    for (const k of ['related', 'border', 'demo']) F[k] = u.get(k) === '1';
    if (!u.has('year') && !u.has('venue') && !u.has('q') && S.basis && S.basis.year) F.year = String(S.basis.year);
    if (F.sort && !['new', 'cite', 'title'].includes(F.sort)) F.sort = 'new';
  }
  function writeUrl() {
    const qs = new URLSearchParams();
    for (const k of ['venue', 'year', 'track', 'q']) if (F[k]) qs.set(k, F[k]);
    for (const k of ['related', 'border', 'demo']) if (F[k]) qs.set(k, '1');
    if (F.sort !== 'new') qs.set('sort', F.sort);
    const s = qs.toString();
    history.replaceState(null, '', location.pathname + (s ? '?' + s : '') + location.hash);
    $('#f-url').textContent = location.host + location.pathname + (s ? '?' + s : '');
  }
  function applyFilter(part) {
    Object.assign(F, part, { shown: 30 });
    syncFilters(); renderList();
    $('#papers').scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
  }
  let listSeq = 0;
  async function renderList() {
    const seq = ++listSeq;
    writeUrl();
    const vs = F.venue ? [F.venue] : VENUES.map(v => v.id);
    const ys = F.year ? [Number(F.year)] : YEARS;
    const combos = []; vs.forEach(v => ys.forEach(y => { if (cnt(v, y).state !== 'planned') combos.push([v, y]); }));
    const box = $('#plist');
    box.style.opacity = '.6';
    const files = await Promise.all(combos.map(([v, y]) => loadPapers(v, y)));
    if (seq !== listSeq) return;
    box.style.opacity = '';
    let all = [];
    files.forEach((f, i) => {
      if (!f) return;
      const [v, y] = combos[i]; const prov = f.state !== 'done';
      const tag = p => ({ ...p, v, y, prov });
      f.papers.forEach(p => { if (p.label === 'related' && !F.related) return; all.push(tag(p)); });
      if (F.border && !F.track) (f.border || []).forEach(p => all.push({ ...tag(p), border: true }));
      if (F.demo && !F.track) (f.demo || []).forEach(p => all.push({ ...tag(p), demo: true }));
    });
    if (F.track) all = all.filter(p => p.track === F.track);
    if (F.q) { const q = F.q.toLowerCase(); all = all.filter(p => (p.title || '').toLowerCase().includes(q) || (p.authors || []).some(a => a.toLowerCase().includes(q))); }
    const vOrder = Object.fromEntries(VENUES.map((v, i) => [v.id, i]));
    const order = { new: (a, b) => b.y - a.y || vOrder[a.v] - vOrder[b.v] || (a.title || '').localeCompare(b.title || ''), cite: (a, b) => (b.cites ?? -1) - (a.cites ?? -1), title: (a, b) => (a.title || '').localeCompare(b.title || '') };
    all.sort(order[F.sort] || order.new);
    const nb = all.filter(p => p.border).length, nd = all.filter(p => p.demo).length;
    $('#f-count').textContent = fmt(all.length - nb - nd) + ' papers' + (nb ? ' + ' + nb + ' borderline' : '') + (nd ? ' + ' + nd + ' demo' : '');
    box.replaceChildren();
    if (!all.length) {
      const partial = combos.some(([v, y]) => cnt(v, y).state === 'part');
      box.append(el('div', { class: 'empty-state', text: partial ? 'Classification is not finished yet, or nothing matches. Venues in progress fill in with the next update.' : 'Nothing matches these filters. Try fewer filters or reset them.' }));
    }
    all.slice(0, F.shown).forEach(p => box.append(paperNode(p)));
    $('#f-more').hidden = all.length <= F.shown;
  }
  function paperNode(p) {
    const n = el('article', { class: 'paper' + (p.border ? ' border' : '') });
    const meta = el('div', { class: 'meta' }, el('span', { class: 'ven' }, el('span', { class: 'vdot', style: 'background:' + colorOf(p.v) }), p.v + ' ' + p.y));
    if (p.border) meta.append(el('span', { class: 'rel', text: 'borderline · not a recommender paper' }));
    else {
      meta.append(el('span', { text: p.track || '' }));
      if (p.demo) meta.append(el('span', { class: 'rel', text: 'demo' }));
      if (p.label === 'related') meta.append(el('span', { class: 'rel', text: 'related' }));
      if (p.badge) meta.append(el('span', { class: 'badge' + (p.badge === 'spotlight' ? ' sp' : ''), text: p.badge }));
      if (p.prov) meta.append(el('span', { text: 'in progress' }));
    }
    const href = p.url || (p.doi ? 'https://doi.org/' + p.doi : null);
    const ti = el('div', { class: 'ti' }); ti.append(href ? el('a', { href, target: '_blank', rel: 'noopener', text: p.title }) : document.createTextNode(p.title || ''));
    n.append(meta, ti);
    const au = p.authors || [];
    n.append(el('div', { class: 'au', text: au.length > 6 ? au.slice(0, 6).join(' · ') + ' and ' + (au.length - 6) + ' more' : au.join(' · ') }));
    if (p.reason) n.append(el('div', { class: 'why', text: 'AI reasoning: ' + p.reason + (p.rejudged ? ' (re-judged by the larger model)' : '') }));
    const links = el('div', { class: 'links' });
    if (p.doi) links.append(el('a', { href: 'https://doi.org/' + p.doi, target: '_blank', rel: 'noopener', text: 'DOI' }));
    if (p.arxiv) links.append(el('a', { href: 'https://arxiv.org/abs/' + p.arxiv, target: '_blank', rel: 'noopener', text: 'arXiv' }));
    if (p.openreview) links.append(el('a', { href: 'https://openreview.net/forum?id=' + p.openreview, target: '_blank', rel: 'noopener', text: 'OpenReview' }));
    if (p.cites != null) links.append(el('span', { class: 'muted', text: fmt(p.cites) + ' citations' }));
    n.append(links);
    return n;
  }

  /* ---------------- start ---------------- */
  async function main() {
    try {
      const r = await fetch(DATA + 'summary.json', { cache: 'no-cache' });
      if (!r.ok) throw new Error('summary.json ' + r.status);
      S = await r.json();
    } catch (e) {
      $('#loading').hidden = true;
      const err = $('#load-error'); err.hidden = false;
      err.textContent = 'No data yet. The site fills in once the first update (Actions → update) has finished. (' + e.message + ')';
      return;
    }
    VENUES = S.venues || []; YEARS = S.years || [];
    $('#loading').hidden = true;
    ['#overview', '#trend', '#papers'].forEach(s => { $(s).hidden = false; });
    // derive the repository from the GitHub Pages URL (https://<owner>.github.io/<repo>/) for the Issues link
    const m = location.host.match(/^([^.]+)\.github\.io$/); const seg = location.pathname.split('/').filter(Boolean)[0];
    $('#f-issue').href = (m && seg) ? 'https://github.com/' + m[1] + '/' + seg + '/issues/new/choose' : '#';
    renderOverview();
    document.querySelectorAll('#c-metric button').forEach(b => b.addEventListener('click', () => { C.metric = b.dataset.v; syncControls(); renderFig1(); }));
    $('#c-related').addEventListener('change', e => { C.related = e.target.checked; renderFig1(); });
    $('#c-table').addEventListener('click', () => { C.table = !C.table; syncControls(); renderFig1(); });
    syncControls(); renderFig1();
    fillSelects(); readUrl(); syncFilters(); renderList();
    let rafId = 0;
    new ResizeObserver(() => { cancelAnimationFrame(rafId); rafId = requestAnimationFrame(() => { const w = $('#fig1').clientWidth; if (Math.abs(w - lastW) > 4) { lastW = w; if (!C.table) renderFig1(); } }); }).observe($('#fig1'));
  }
  main();
})();
