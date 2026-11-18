// Product page (rates grid, history, documents, notes, distributors) and the Data page.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h, arr, str, fmt, pct, fd, cls, md } = IRV;
  const V = IRV.views;
  const A = () => IRV.app;
  const DIMS = ['plan', 'currency', 'term', 'age', 'sex'];
  const DIM_LABEL = { plan: 'Plan', currency: 'Currency', term: 'Tenor', age: 'Age', sex: 'Sex' };
  const MAX_SERIES = 8;

  function dimLabel(d, v, pid) {
    if (d === 'plan') return IRV.planEn(v, pid) || 'Standard';
    if (d === 'term') return IRV.termEn(v);
    if (d === 'sex') return IRV.sexEn(v) || 'Any';
    if (d === 'age') return v ? IRV.ageEn(v) : 'Any age';
    return v || '—';
  }
  function sortDim(d, vals, order) {
    if (d === 'currency') return IRV.sortCur(vals);
    if (d === 'term') return IRV.sortTerm(vals);
    if (d === 'age') return IRV.sortAge(vals);
    if (d === 'plan') return [...vals].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    return [...vals].sort();
  }
  function link(label, fn) { return h('button', { type: 'button', class: 'lnk', onclick: fn }, label); }
  const openUrl = (url) => async () => { const r = await window.api.openUrl(url); if (!r.ok) A().toast(r.error); };
  const openFile = (rel) => async () => { const r = await window.api.openFile(rel); if (!r.ok) A().toast(r.error); };

  // "Fixed annuity", or the product's type plus any plan-level types.
function typeLine(m, p, own) {
    const base = p.product_type || '';
    const extra = [...new Set(own.map((s) => s.type))].filter((t) => t && t !== base && t !== 'unclassified');
    const baseLabel = base ? m.typeLabel(base) : IRV.categoryEn(p.category);
    if (!extra.length) return baseLabel;
    return `${baseLabel}; some plans are ranked as ${extra.map((t) => m.typeLabel(t)).join(' or ')}`;
  }

    function uiFor(pid) {
    const ui = A().ui;
    if (!ui[pid]) ui[pid] = { tab: 'rates', metric: null, date: null, hist: { metric: null, sel: {} }, slots: {}, range: null, distQ: '' };
    return ui[pid];
  }

  V.product = function (main, params) {
    const app = A(), m = app.model;
    const p = m.byId.get(params.id);
    if (!p) { main.append(h('div', { class: 'empty' }, 'Product not found.')); return; }
    const ui = uiFor(p.id);
    const own = m.byProduct.get(p.id) || [];
    if (params.series && m.byKey.has(params.series)) {
      // Arrived from a table row: show that series' history.
      const s = m.byKey.get(params.series);
      ui.tab = 'history'; ui.hist = { metric: s.metric, sel: Object.fromEntries(DIMS.map((d) => [d, new Set([s[d]])])) };
      delete params.series;
    }
    const ins = m.insurers.get(p.insurer_id);
    const docs = mergedDocs(p);
    const dists = arr(p.distributors);
    if (ui.tab === 'notes' || ui.tab === 'facts' || (ui.tab === 'dist' && !dists.length)) ui.tab = 'rates';
    // The full header on Rates; the other tabs keep only the name and one line of facts, so the chart,
    // document or list starts near the top of the window.
    const full = ui.tab === 'rates';
    main.append(h('h1', { class: 'sm' + (full ? '' : ' compact') }, IRV.nameEn(p), full ? null : h('span', { class: 'ja', lang: 'ja' }, p.name_ja || '')));
    if (full) main.append(h('div', { class: 'ja', lang: 'ja' }, p.name_ja || ''));
    if (full) renderFacts(main, p, ins, own, ui);
    else renderMetaLine(main, p, ins, own, ui);
    const pn = IRV.newsForProduct(p);
    const tabs = [['rates', 'Rates'], ['history', 'History'], ['docs', `Documents ${docs.length}`], ['news', `News ${pn.linked.length + pn.insurer.length}`], ...(dists.length ? [['dist', `Sold by ${dists.length}`]] : [])];
    main.append(h('div', { class: 'tabs' + (full ? '' : ' compact') }, tabs.map(([id, label]) => h('button', { type: 'button', 'data-tab': id,
      class: ui.tab === id ? 'on' : '', onclick: () => { ui.tab = id; app.render(); } }, label))));
    if (ui.tab === 'docs') return renderDocs(main, docs, ui);
    if (ui.tab === 'news') return IRV.renderProductNews(main, p);
    if (ui.tab === 'dist') return renderDist(main, p, ui);
    if (!own.length) { main.append(h('div', { class: 'empty' }, 'No rates collected yet.')); return; }
    if (ui.tab === 'history') renderHistory(main, p, own, ui);
    else renderGrid(main, p, own, ui);
  };

  // ---------- rates: every plan x currency x tenor (x age, sex) for one period ----------
  function renderGrid(main, p, own, ui) {
    const app = A(), m = app.model;
    const metrics = [...new Set(own.map((s) => s.metric))];
    if (!metrics.includes(ui.metric)) ui.metric = metrics.includes('crediting_rate') ? 'crediting_rate' : metrics[0];
    const series = own.filter((s) => s.metric === ui.metric);
    const dates = [...new Set(series.flatMap((s) => s.obs.map((o) => o.date)))].sort().reverse();
    const picked = dates.includes(ui.date) ? ui.date : null;
    // "Today" unless an earlier date is picked: a cell shows the rate in force on that day.
    const T = picked ? IRV.toEpoch(picked) : IRV.today();

    main.append(h('div', { class: 'pick' },
      V.toggles(metrics.length > 1 ? 'Measure' : '', metrics.map((k) => ({ value: k, label: IRV.metricEn(k) })), ui.metric, (v) => { ui.metric = v; ui.date = null; app.render(); }),
      h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Period'),
        IRV.menu('Period', [['', 'Current'], ...dates.map((d) => [d, d])], picked || '', (v) => { ui.date = v || null; app.render(); }))));

    // Rows are plan x age band x sex. A row is live when any of its cells has a rate in force.
    const rows = [], seen = new Map(), at = new Map(), pts = new Map(), liveRows = new Set();
    for (const s of series) {
      const rk = [s.plan, s.age, s.sex].join('|');
      if (!seen.has(rk)) { seen.set(rk, true); rows.push(s); }
      at.set(rk + '|' + s.currency + '|' + s.term, s);
      const pp = IRV.periodPoints(m, s, T);
      pts.set(s.key, pp);
      if (pp.cur && pp.cur.v != null) liveRows.add(rk);
    }
    const rowKey = (r) => [r.plan, r.age, r.sex].join('|');
    const multiPlan = new Set(rows.map((r) => r.plan)).size > 1;
    // A product with age bands (or sexes) shows one row per plan; each cell stacks every band in force, the
    // sample case's band first and emphasised, so nothing needs a click and bands that fill different tenors
    // do not make a staircase.
    const st = app.state;
    const fold = rows.some((r) => r.age || r.sex);
    const allRows = rows.slice();
    const live = (x) => { const pp = pts.get(x.key); return !!(pp && pp.cur && pp.cur.v != null); };
    const fits = (x) => IRV.ageMatches(x.age, st.age) && IRV.sexMatches(x.sex, st.sex);
    const lowAge = (x) => { const k = /^(\d+)/.exec(x.age); return k ? +k[1] : -1; };
    // The series to show in one cell: every band in force (sample band first), else the latest stale one.
    const cellSeries = (r, cu, t) => {
      if (!r.folded) { const one = at.get([r.plan, r.age, r.sex, cu, t].join('|')); return one ? [one] : []; }
      const c = series.filter((x) => x.plan === r.plan && x.currency === cu && x.term === t);
      const on = c.filter(live).sort((a, b) => (fits(b) - fits(a)) || lowAge(a) - lowAge(b) || a.sex.localeCompare(b.sex));
      if (on.length) return on;
      const lastT = (x) => { const pp = pts.get(x.key); return pp && pp.last ? pp.last.t : -1; };
      return c.length ? [c.slice().sort((a, b) => lastT(b) - lastT(a))[0]] : [];
    };
    if (fold) {
      rows.length = 0;
      for (const pl of [...new Set(allRows.map((r) => r.plan))]) {
        rows.push({ plan: pl, age: '', sex: '', folded: true });
        if (allRows.some((r) => r.plan === pl && liveRows.has(rowKey(r)))) liveRows.add(pl + '||');
      }
    }
    // A plan with many age bands (more than four) is laid out the other way round: one table per plan (and
    // sex, where rates differ by sex) with the age bands as rows and currency x tenor as columns, so that no
    // cell stacks a dozen figures.
    // Measured as the most age bands that would be stacked in one cell (bands in force), so a plan whose bands
    // merely differ from tenor to tenor keeps the compact layout.
    const bandsOf = new Map();
    for (const s of series) {
      if (!s.age || !live(s)) continue;
      const k = [s.plan, s.currency, s.term, s.sex].join('|');
      if (!bandsOf.has(s.plan)) bandsOf.set(s.plan, new Map());
      const cells = bandsOf.get(s.plan);
      cells.set(k, (cells.get(k) || 0) + 1);
    }
    const byBand = (pl) => fold && bandsOf.has(pl) && Math.max(...bandsOf.get(pl).values()) > 4;
    const anyByBand = [...bandsOf.keys()].some(byBand);
    const idleBands = allRows.filter((r) => byBand(r.plan) && !liveRows.has(rowKey(r))).length;
    const idleRows = rows.filter((r) => !byBand(r.plan) && !liveRows.has(rowKey(r)));
    const idleCount = idleRows.length + idleBands;
    const showIdle = ui.showIdle || !liveRows.size;
    // Currency x tenor columns with a rate in force somewhere; the others fold with the idle rows.
    const colLive = new Set(series.filter(live).map((s) => s.currency + '|' + s.term));
    const idleCols = liveRows.size ? new Set(series.map((s) => s.currency + '|' + s.term).filter((k) => !colLive.has(k))).size : 0;
    // Each plan's policy form, shown on hover over the plan's name.
    const formOf = new Map();
    for (const f of policyForms(p)) for (const pl of f.plans) formOf.set(pl, f.name);
    let anyOld = false, anyWb = false, anyUnc = false, anyGap = false, anyDash = false, anyDelta = false;
    const cellFor = (r, cu, t, edge) => {
      const list = cellSeries(r, cu, t);
      if (!list.length) { anyGap = true; return h('td', { class: 'gap' + edge, title: 'Not sold in this combination' }, ''); }
      const lines = list.map((s, i) => {
        const pp = pts.get(s.key);
        const o = pp.cur || pp.last;
        if (!o) return null;
        const bandTxt = r.folded ? [IRV.bandEn(s.age), IRV.sexEn(s.sex)].filter(Boolean).join(' ') : '';
        if (o.v == null) anyDash = true;
        if (o.v == null) return h('div', { class: 'cl gap', title: 'Not offered from ' + o.date }, '—', bandTxt ? h('small', null, ' · ' + bandTxt) : null);
        const stale = !pp.cur;
        const d = !stale && pp.prev && pp.prev.v != null ? o.v - pp.prev.v : null;
        if (stale) anyOld = true;
        if (!stale && i === 0 && d != null && Math.abs(d) > 0.00005) anyDelta = true;
        if (o.wb) anyWb = true;
        const unc = IRV.uncheckedMark(m, p.id, o);
        if (unc) anyUnc = true;
        return h('div', { class: 'cl' + (i === 0 ? ' main' : ' more') + (stale ? ' old' : '') },
          h('button', { type: 'button', class: 'src', title: `Source: ${stale ? 'last point ' : 'in force from '}${o.date}${o.wb ? ', archive capture' : ''}`,
            onclick: () => IRV.openSource(o, `${IRV.shortEn(p)} · ${IRV.seriesLabel(s)}`) }, fmt(o.v)), o.wb ? '°' : '', unc,
          bandTxt ? h('small', null, ' · ' + bandTxt) : null,
          stale ? h('i', null, o.date) : i === 0 && d != null && Math.abs(d) > 0.00005 ? h('i', { class: cls(d) }, fd(d)) : null);
      }).filter(Boolean);
      return h('td', { class: 'c' + edge, 'data-col': cu + '|' + t }, lines);
    };
    // Where every rate of a block (or of one column) is unchecked, one mark on the caption (or the column
    // heading) stands for all of them instead of a mark on each figure.
    const foldMarks = (tbl, cap) => {
      const cells = [...tbl.querySelectorAll('td.c')];
      const count = (list) => list.reduce((a, td) => { a.v += td.querySelectorAll('.src').length; a.u += td.querySelectorAll('.unc').length; return a; }, { v: 0, u: 0 });
      const strip = (list) => { for (const td of list) for (const x of td.querySelectorAll('.unc')) x.remove(); };
      const mark = () => h('span', { class: 'unc', title: IRV.uncheckedNote(m, p.id), 'aria-label': 'Unchecked' }, '○');
      const all = count(cells);
      if (all.v > 1 && all.u === all.v) { strip(cells); cap.append(h('span', { class: 'allunc' }, mark(), ' every rate in this table')); return; }
      for (const th of tbl.querySelectorAll('thead th[data-col]')) {
        const col = cells.filter((td) => td.dataset.col === th.dataset.col), c = count(col);
        if (c.v > 1 && c.u === c.v) { strip(col); th.append(mark()); }
      }
    };
    const seriesAt = (r, cu, t) => cellSeries(r, cu, t)[0] || null;
    // One block: a caption, the currency (and tenor) headings, then each plan as a heading row with its age
    // bands as compact rows under it. Rows with no rate in force stay folded unless asked for.
    // A table wider than the column scrolls inside its own frame (headings then stick to the frame).
    const fitWrap = (tbl) => {
      // A table that shows changes gives every rate column the same width, so a change does not widen its column only.
      if (tbl.querySelector('td.c .cl.main i')) tbl.classList.add('chg');
      const wrap = h('div', { class: 'gridwrap' }, tbl);
      requestAnimationFrame(() => { if (wrap.isConnected && tbl.offsetWidth > wrap.clientWidth + 1) wrap.classList.add('wide'); });
      return wrap;
    };
    // Headings of a currency (x tenor) table: currencies over their tenors; the corner names the row axis.
    // With one currency and a tenor axis, the caption names the currency and the table keeps the tenor row only.
    const head = (cs, noTenor, corner) => {
      const span = {};
      for (const [cu] of cs) span[cu] = (span[cu] || 0) + 1;
      if (!noTenor && Object.keys(span).length === 1) {
        return h('thead', null, h('tr', null, h('th', { class: 't' }, corner), cs.map(([cu, t], ci) => h('th', { class: 't' + (ci === 0 ? ' bl' : ''), 'data-col': cu + '|' + t }, IRV.termEn(t)))));
      }
      return h('thead', null,
        h('tr', null, h('th', { class: noTenor ? '' : 't' }, noTenor ? corner : ''), Object.entries(span).map(([cu, n]) => h('th', { class: 'cur' + (noTenor ? '' : ' t'), colspan: String(n), 'data-col': noTenor ? cu + '|' : null }, cu))),
        noTenor ? null : h('tr', null, h('th', null, corner), cs.map(([cu, t], ci) => h('th', { class: ci === 0 || cs[ci - 1][0] !== cu ? 'bl' : '', 'data-col': cu + '|' + t }, IRV.termEn(t)))));
    };
    // A plan with many age bands: one table per sex, age bands down the side, every figure its own cell.
    const bandTables = (pl, cs, noTenor) => {
      const out = [];
      const mine = allRows.filter((r) => r.plan === pl && cs.some(([cu, t]) => at.has([r.plan, r.age, r.sex, cu, t].join('|'))));
      const sexes = [...new Set(mine.map((r) => r.sex))].sort();
      const name = IRV.planEn(pl, p.id) || (multiPlan ? 'Standard' : '');
      for (const sx of sexes) {
        const bands = IRV.sortAge([...new Set(mine.filter((r) => r.sex === sx).map((r) => r.age))]);
        const shown = bands.filter((a) => showIdle || liveRows.has([pl, a, sx].join('|')));
        if (!shown.length) continue;
        const ccs = cs.filter(([cu, t]) => shown.some((a) => at.has([pl, a, sx, cu, t].join('|'))));
        const title = h('div', { class: 'gsub' }, h('button', { type: 'button', class: 'pn', title: 'History of this plan', onclick: () => {
          ui.tab = 'history'; ui.hist = { metric: ui.metric, sel: { plan: new Set([pl]), ...(sx ? { sex: new Set([sx]) } : {}) } }; app.render();
        } }, [name, IRV.sexEn(sx)].filter(Boolean).join(' · ') || 'Rate'));
        const tbl = h('table', { class: 'grid byage' + (noTenor ? ' one' : '') + (ccs.length > 8 ? ' dense' : '') }, head(ccs, noTenor, 'Age'),
          h('tbody', null, shown.map((a) => h('tr', null, h('td', { class: 'age' }, IRV.bandEn(a) || 'Any age'),
            ccs.map(([cu, t], ci) => cellFor({ plan: pl, age: a, sex: sx, folded: false }, cu, t, ci === 0 || ccs[ci - 1][0] !== cu ? ' bl' : ''))))));
        foldMarks(tbl, title);
        out.push(title, fitWrap(tbl));
      }
      return out;
    };
    const block = (caption, csAll, noTenor) => {
      // Columns with no rate in force in any row stay folded with the idle rows.
      const cs = showIdle || !liveRows.size ? csAll : csAll.filter((c) => colLive.has(c.join('|')));
      if (!cs.length) return;
      const rsAll = rows.filter((r) => (showIdle || liveRows.has(rowKey(r))) && cs.some(([cu, t]) => seriesAt(r, cu, t)));
      const rs = rsAll.filter((r) => !byBand(r.plan));
      const bandPlans = [...new Set(rsAll.filter((r) => byBand(r.plan)).map((r) => r.plan))];
      if (!rsAll.length) return;
      const cap = h('h3', { class: 'gcap' }, caption);
      if (!rs.length) {
        main.append(cap);
        for (const pl of bandPlans) main.append(...bandTables(pl, cs, noTenor));
        return;
      }
      const span = {};
      for (const [cu] of cs) span[cu] = (span[cu] || 0) + 1;
      const body = [];
      rs.forEach((r, ri) => {
        const sub = [IRV.ageEn(r.age), IRV.sexEn(r.sex)].filter(Boolean).join(' · ');
        const first = ri === 0 || rs[ri - 1].plan !== r.plan;
        const name = IRV.planEn(r.plan, p.id) || (multiPlan ? 'Standard' : '');
        const banded = !r.folded && rs.some((x) => x.plan === r.plan && (x.age || x.sex));
        const histBtn = (text) => h('button', { type: 'button', class: 'pn', title: (formOf.has(r.plan) ? formOf.get(r.plan) + '\n' : '') + 'History of this plan', onclick: () => {
          ui.tab = 'history'; ui.hist = { metric: ui.metric, sel: { plan: new Set([r.plan]) } }; app.render();
        } }, text);
        if (first && banded) body.push(h('tr', { class: 'ph' + (ri > 0 ? ' sep' : '') }, h('td', { colspan: String(cs.length + 1) }, histBtn(name || 'Rate'))));
        body.push(h('tr', { class: (banded ? 'band' : '') + (first && !banded && ri > 0 && multiPlan ? ' sep' : '') },
          h('td', null, banded ? h('span', { class: 'subrow' }, sub || 'Any age') : histBtn(name || 'Rate')),
          cs.map(([cu, t], ci) => cellFor(r, cu, t, ci === 0 || cs[ci - 1][0] !== cu ? ' bl' : ''))));
      });
      const tbl = h('table', { class: 'grid' + (noTenor ? ' one' : '') + (cs.length > 8 ? ' dense' : '') },
        head(cs, noTenor, multiPlan ? 'Plan' : ''),
        h('tbody', null, body));
      foldMarks(tbl, cap);
      main.append(cap, fitWrap(tbl));
      for (const pl of bandPlans) main.append(...bandTables(pl, cs, noTenor));
    };
    const colsFor = (keep) => {
      const out = [];
      for (const cu of IRV.sortCur(new Set(series.filter(keep).map((s) => s.currency)))) {
        for (const t of IRV.sortTerm(new Set(series.filter((s) => keep(s) && s.currency === cu).map((s) => s.term)))) out.push([cu, t]);
      }
      return out;
    };
    // Plans with a tenor (deferral period) get a currency x tenor grid: one grid when it is dense, one per
    // currency when plans are tied to a single currency each.
    const tenored = colsFor((s) => s.term !== '');
    const tenBlocks = (colsAll) => {
      // Judge density on the columns that will show: folded columns with no rate in force do not count.
      const shownCols = showIdle || !liveRows.size ? colsAll : colsAll.filter((c) => colLive.has(c.join('|')));
      const cols = shownCols.length ? shownCols : colsAll;
      const curs = [...new Set(cols.map((c) => c[0]))];
      const ser = series.filter((s) => s.term !== '' && cols.some(([cu, t]) => s.currency === cu && s.term === t));
      const trows = allRows.filter((r) => cols.some(([cu, t]) => at.has([r.plan, r.age, r.sex, cu, t].join('|'))));
      const fill = ser.length / Math.max(1, trows.length * cols.length);
      // A large grid (over about 100 cells) is also split, one block per currency with its own tenor heading.
      // Plans laid out by age band already have one row per figure, so they keep every currency in one table.
      const big = (ser.length > 100 && !(anyByBand && cols.length <= 16)) || cols.length > (anyByBand ? 16 : 10);
      if ((fill < 0.4 || big) && curs.length > 1) for (const cu of curs) block(`${cu} · by deferral period`, cols.filter((c) => c[0] === cu), false);
      else block(`${curs.join(', ')} · by deferral period`, cols, false);
    };
    if (tenored.length) {
      // Plans sold in separate sets of currencies (yen plans and foreign-currency plans, say) get a block per
      // set, so no table is half empty.
      const setOf = new Map();
      for (const s of series) if (s.term !== '') { if (!setOf.has(s.plan)) setOf.set(s.plan, new Set()); setOf.get(s.plan).add(s.currency); }
      const groups = [];
      for (const cs of setOf.values()) {
        const hit = groups.find((g) => [...cs].some((c) => g.has(c)));
        if (hit) for (const c of cs) hit.add(c); else groups.push(new Set(cs));
      }
      // Merge groups that came to overlap through a later plan.
      for (let i = 0; i < groups.length; i++) for (let j = groups.length - 1; j > i; j--) {
        if ([...groups[j]].some((c) => groups[i].has(c))) { for (const c of groups[j]) groups[i].add(c); groups.splice(j, 1); }
      }
      if (groups.length > 1) {
        const pos = (g) => Math.min(...[...g].map((c) => tenored.findIndex((x) => x[0] === c)));
        for (const g of groups.sort((a, b) => pos(a) - pos(b))) tenBlocks(tenored.filter((c) => g.has(c[0])));
      } else tenBlocks(tenored);
    }
    // Plans whose term is part of the plan itself have no tenor axis.
    const untenored = colsFor((s) => s.term === '');
    if (untenored.length) {
      const urows = new Set(allRows.filter((r) => untenored.some(([cu]) => at.has([r.plan, r.age, r.sex, cu, ''].join('|')))).map((r) => (fold ? r.plan : rowKey(r)))).size;
      if ((urows * untenored.length > 100 || series.filter((s) => s.term === '').length > 100) && untenored.length > 1) for (const c of untenored) block(`${c[0]} · term set by the plan`, [c], true);
      else block(`${untenored.map((c) => c[0]).join(', ')} · term set by the plan`, untenored, true);
    }

    if ((idleCount || idleCols) && liveRows.size) {
      const what = [idleCount ? `${idleCount} row${idleCount === 1 ? '' : 's'}` : '', idleCols ? `${idleCols} column${idleCols === 1 ? '' : 's'}` : ''].filter(Boolean).join(' and ');
      main.append(h('div', { class: 'leftout' }, h('button', { type: 'button', class: 'lnk', id: 'idle-toggle', onclick: () => { ui.showIdle = !ui.showIdle; app.render(); } },
        ui.showIdle ? 'Hide rows and columns with no rate in force' : `Show ${what} with no rate in force`)));
    }
    const foldedShown = fold && allRows.some((r) => !byBand(r.plan) && (r.age || r.sex));
    // The key names only what the tables show.
    const key = [anyDelta ? 'small figure = change on the period before' : '', anyOld ? 'grey with a date = last point, not in force' : '',
      anyWb ? '° = archive capture' : '', anyDash ? '— = not offered' : '', anyGap ? 'blank = plan not sold in that currency or tenor' : ''].filter(Boolean).join('; ');
    main.append(h('p', { class: 'foot' }, `% per year, ${picked ? 'in force on ' + picked : 'in force today'}. `, key ? key.charAt(0).toUpperCase() + key.slice(1) + '. ' : '', 'A rate opens its saved copy. ',
      foldedShown ? `Each cell lists every age band in force, the sample case's (age ${st.age}) first. ` : '',
      anyByBand ? 'Plans with many age bands have one row per band. ' : '',
      anyUnc ? IRV.uncheckedLegend() : null));
  }

  // ---------- history ----------
  function renderHistory(main, p, own, ui) {
    const app = A(), st = app.state;
    const hs = ui.hist;
    const metrics = [...new Set(own.map((s) => s.metric))];
    if (!metrics.includes(hs.metric)) hs.metric = metrics.includes(ui.metric) ? ui.metric : metrics.includes('crediting_rate') ? 'crediting_rate' : metrics[0];
    const all = own.filter((s) => s.metric === hs.metric);
    const planOrder = [...new Set(all.map((s) => s.plan))];
    const pick = h('div', { class: 'pick' });
    if (metrics.length > 1) pick.append(V.toggles('Measure', metrics.map((k) => ({ value: k, label: IRV.metricEn(k) })), hs.metric, (v) => { hs.metric = v; app.render(); }));
    let cur = all;
    for (const d of DIMS) {
      // Each picker offers only what exists for the choices before it.
      const vals = sortDim(d, new Set(cur.map((s) => s[d])), planOrder);
      let sel = hs.sel[d] ? new Set([...hs.sel[d]].filter((v) => vals.includes(v))) : null;
      if (!sel || !sel.size) {
        // Default: every currency; for the rest, the value whose series is in force today (for the sample
        // case where it is an age band or sex), else the first.
        const live = (v) => cur.some((s) => s[d] === v && (() => { const pp = IRV.periodPoints(A().model, s); return pp.cur && pp.cur.v != null; })());
        const fits = d === 'age' ? vals.filter((v) => IRV.ageMatches(v, st.age)) : d === 'sex' ? vals.filter((v) => IRV.sexMatches(v, st.sex)) : vals;
        // A tenor: the one with the most series in force (so that most currencies have a line), then the most
        // with any rate at all; the sample case's when it does as well.
        const liveN = (v) => cur.filter((s) => s[d] === v && (() => { const pp = IRV.periodPoints(A().model, s); return pp.cur && pp.cur.v != null; })()).length;
        const drawnN = (v) => cur.filter((s) => s[d] === v && s.obs.some((o) => o.v != null)).length;
        const bestTerm = d === 'term' ? vals.slice().sort((a, b) => liveN(b) - liveN(a) || drawnN(b) - drawnN(a))[0] : null;
        const pickOne = (d === 'term' && vals.includes(st.term) && liveN(st.term) === liveN(bestTerm) && drawnN(st.term) === drawnN(bestTerm) ? st.term : null) ?? bestTerm ?? fits.find(live) ?? vals.find(live) ?? fits[0] ?? vals[0];
        sel = new Set(d === 'currency' ? vals : [pickOne]);
      }
      hs.sel[d] = sel;
      if (vals.length > 1) {
        pick.append(h('div', { class: 'grp' }, h('span', { class: 'lbl' }, DIM_LABEL[d]), vals.map((v) => h('button', {
          type: 'button', class: 'tg' + (sel.has(v) ? ' on' : ''), 'aria-pressed': String(sel.has(v)), onclick: () => {
            const next = new Set(sel);
            if (next.has(v)) { if (next.size > 1) next.delete(v); } else next.add(v);
            hs.sel[d] = next; app.render();
          } }, dimLabel(d, v, p.id)))));
      }
      cur = cur.filter((s) => sel.has(s[d]));
    }
    main.append(pick);
    const total = cur.length;
    const shown = cur.slice(0, MAX_SERIES);
    if (total > MAX_SERIES) main.append(h('div', { class: 'banner' }, `${total} series selected; showing the first ${MAX_SERIES}.`));

    for (const k of Object.keys(ui.slots)) if (!shown.some((s) => s.key === k)) delete ui.slots[k];
    const used = new Set(Object.values(ui.slots));
    for (const s of shown) {
      if (ui.slots[s.key] != null) continue;
      let i = 1; while (used.has(i)) i++;
      ui.slots[s.key] = i; used.add(i);
    }
    const multi = DIMS.filter((d) => new Set(shown.map((s) => s[d])).size > 1);
    const label = (s) => multi.map((d) => dimLabel(d, s[d], p.id)).join(' · ') || IRV.seriesLabel(s) || IRV.metricEn(s.metric);
    const series = shown.map((s) => V.chartSeries(s, label(s), IRV.cssVar('--s' + (((ui.slots[s.key] - 1) % 8) + 1))));
    main.append(V.chartBlock(series, ui, 'range'));

    const cols = DIMS.filter((d) => shown.some((s) => s[d] !== ''));
    const pts = shown.flatMap((s) => s.obs.map((o) => ({ s, o }))).sort((a, b) => b.o.t - a.o.t);
    const CAP = ui.ptsShown || 40;
    main.append(h('h2', null, 'Points', h('span', { class: 'more' }, pts.length > CAP ? `${CAP} of ${pts.length}` : pts.length)));
    main.append(h('div', { class: 'ptswrap' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Effective'), cols.map((d) => h('th', { class: 'l' }, DIM_LABEL[d])),
        h('th', null, 'Rate'), h('th', { class: 'l' }, 'Origin'), h('th', null, 'Retrieved'), h('th', null, 'Source'), h('th', null, 'Copy'))),
      h('tbody', null, pts.slice(0, CAP).map(({ s, o }) => h('tr', { class: o.v == null ? 'dim' : '' },
        h('td', null, o.date),
        cols.map((d) => h('td', { class: 'l' }, s[d] === '' ? '' : dimLabel(d, s[d], p.id))),
        h('td', { class: 'num' }, o.v == null ? 'Not offered' : h('button', { type: 'button', class: 'src', title: 'Open the saved page this rate came from',
          onclick: () => IRV.openSource(o, `${IRV.shortEn(p)} · ${IRV.seriesLabel(s)}`) }, pct(o.v)), o.v == null ? null : IRV.uncheckedMark(A().model, p.id, o)),
        h('td', { class: 'l' }, o.wb ? 'Archive' : 'Insurer site'),
        h('td', { class: 'num' }, o.retrieved),
        h('td', null, o.src ? link('Page', openUrl(o.src)) : ''),
        h('td', null, o.snapshot ? link('File', openFile(o.snapshot)) : '')))))));
    if (pts.length > CAP) main.append(h('div', { class: 'leftout' }, h('button', { type: 'button', class: 'lnk', id: 'pts-more', onclick: () => { ui.ptsShown = CAP + 200; app.render(); } }, `Show ${Math.min(200, pts.length - CAP)} more`)));
    main.append(h('p', { class: 'foot' }, 'Breaks = not offered. Dashed with hollow points = archive captures, never joined across a gap. A rate opens its saved copy. ',
      shown.some((s) => s.obs.some((o) => o.v != null && IRV.isUnchecked(A().model, p.id, o))) ? IRV.uncheckedLegend() : null));
  }

  // ---------- documents ----------
  function mergedDocs(p) {
    const ds = A().model.ds;
    // Complete live copies first, then archive copies, then truncated captures.
    const rank = (d) => (/^incomplete/i.test(d.status || '') ? 2 : d.via_wayback ? 1 : 0);
    const onDisk = arr(ds.documents[p.id]).slice().sort((a, b) => rank(a) - rank(b));
    const all = Object.values(ds.documents).flat();
    const out = [], taken = new Set(), byName = new Map();
    for (const d of arr(p.documents)) {
      if (!d || typeof d !== 'object') continue;
      if (d.name && !byName.has(d.name)) byName.set(d.name, d);
      // The product's entry describes the best copy on disk with that name (or URL).
      let m = onDisk.find((x) => !taken.has(x) && ((d.name && x.name === d.name) || (d.url && x.url === d.url)));
      if (!m && d.url) m = all.find((x) => x.url === d.url && x.file); // insurer-wide files may sit under another product
      if (m) taken.add(m);
      out.push({ ...d, ...(m || {}), title_en: d.title_en || '', title_ja: (m && m.title_ja) || d.title_ja || '', url: (m && m.url) || d.url || '' });
    }
    // Other copies of a listed document (an older capture, say) keep its English title.
    for (const x of onDisk) {
      if (taken.has(x)) continue;
      const d = byName.get(x.name);
      out.push({ ...x, title_en: d ? d.title_en || '' : '', variant: d ? d.variant : undefined });
    }
    return out.map((d, i) => [d, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map((x) => x[0]);
  }

  // English label from the manifest's document name ("brochure_shinkin" -> "Brochure").
  const DOC_KIND = [[/^brochure/, 'Brochure'], [/^policy_conditions/, 'Policy conditions'], [/^pre_contract/, 'Pre-contract disclosure'],
    [/^attention/, 'Important information'], [/^gaiyo/, 'Product summary'], [/^agency_fee/, 'Agency fee structure'], [/^rate/, 'Rate sheet']];
  function docLabel(d) {
    const name = str(d.name);
    const hit = DOC_KIND.find(([re]) => re.test(name));
    return hit ? hit[1] : IRV.tidy(name) || 'Document';
  }
  function docVariant(d) {
    const v = str(d.variant);
    if (!v || v === 'general') return '';
    if (v === 'insurer-wide') return 'Insurer-wide';
    return /^\d+$/.test(v) ? 'Version ' + v : IRV.tidy(v) + ' edition';
  }

  // "2023年4月版" -> "April 2023 edition"; the data's own edition field first.
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const ORD = (n) => n + (n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th');
  function docEdition(d) {
    const when = (y, mo, day) => (mo >= 1 && mo <= 12 ? `${day ? +day + ' ' : ''}${MONTHS[mo - 1]} ${y} edition` : '');
    const e = str(d.edition).trim();
    let k = /^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$/.exec(e);
    if (k) return when(k[1], +k[2], k[3]);
    if (/^\d{4}$/.test(e)) return e + ' edition';
    if (e && !IRV.hasCjk(e)) return /edition|version|revis|as of|issue/i.test(e) ? e : e + ' edition';
    const t = e + ' ' + str(d.title_ja);
    k = /(\d{4})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?/.exec(t);
    if (k) return when(k[1], +k[2], k[3]);
    k = /令和\s*(\d{1,2})年\s*(\d{1,2})月/.exec(t);
    if (k) return when(2018 + +k[1], +k[2]);
    k = /第\s*(\d{1,2})\s*版/.exec(t);
    return k ? ORD(+k[1]) + ' edition' : '';
  }
  // English titles for a product's documents, made distinct: two rows never read the same.
  function docTitles(docs) {
    const titles = docs.map((d) => (d.title_en ? IRV.cleanLabel(d.title_en) : docLabel(d)));
    const has = (t, x) => t.toLowerCase().includes(String(x).toLowerCase());
    const kindless = (d) => IRV.tidy(str(d.name).replace(/^(brochure|policy_conditions|pre_contract|attention|gaiyo|agency_fee(_structure)?|rate)_?/, ''));
    const when = (d) => (d.via_wayback && /^\d{8}/.test(d.capture_timestamp || '') ? 'captured ' + d.capture_timestamp.replace(/^(\d{4})(\d{2})(\d{2}).*/, '$1-$2-$3') : d.retrieved_date ? 'retrieved ' + d.retrieved_date : '');
    const extras = [(d) => docVariant(d), (d) => docEdition(d), kindless, when];
    for (const extra of extras) {
      const seen = new Map();
      titles.forEach((t, i) => seen.set(t, (seen.get(t) || []).concat(i)));
      for (const [t, idx] of seen) {
        if (idx.length < 2) continue;
        const adds = idx.map((i) => extra(docs[i]));
        if (new Set(adds).size < 2) continue; // tells nothing apart
        idx.forEach((i, n) => { if (adds[n] && !has(t, adds[n].replace(/ edition$/, ''))) titles[i] = `${t} · ${adds[n]}`; });
      }
    }
    const seen = new Map();
    return titles.map((t) => { const n = (seen.get(t) || 0) + 1; seen.set(t, n); return n > 1 ? `${t} · copy ${n}` : t; });
  }
  // The date line of a document row: when it was retrieved if it is on disk, its edition where known.
  function docWhen(d) {
    const ed = docEdition(d);
    const got = d.via_wayback && /^\d{8}/.test(d.capture_timestamp || '') ? 'captured ' + d.capture_timestamp.replace(/^(\d{4})(\d{2})(\d{2}).*/, '$1-$2-$3')
      : d.retrieved_date ? 'retrieved ' + d.retrieved_date : '';
    if (!d.file) return [d.missing_file ? 'File missing' : 'Not downloaded', ed, d.missing_file ? got : '', ed || (d.missing_file && got) ? '' : 'date unknown'];
    return [got || (ed ? '' : 'date unknown'), ed, IRV.bytes(d.bytes)];
  }
  IRV.docEdition = docEdition;
  IRV.docTitles = docTitles;
  IRV.docsFor = mergedDocs;
  IRV.docLabel = docLabel;
  IRV.docVariant = docVariant;
  IRV.productUi = uiFor;

  function renderDocs(main, docs, ui) {
    const app = A();
    if (!docs.length) { main.append(h('div', { class: 'empty' }, 'No documents recorded.')); return; }
    let sel = docs.findIndex((d) => (d.file || d.url) === ui.doc);
    if (sel < 0) sel = Math.max(0, docs.findIndex((d) => d.file));
    const cur = docs[sel];
    ui.doc = cur.file || cur.url;
    const titles = docTitles(docs);
    const list = h('div', { class: 'doclist' }, docs.map((d, i) => h('div', { class: 'docitem' + (i === sel ? ' on' : '') },
      h('button', { type: 'button', class: 'docpick', 'data-doc': String(i), onclick: () => { ui.doc = d.file || d.url; app.render(); } },
        h('span', { class: 't' }, titles[i]),
        h('span', { class: 'j', lang: 'ja' }, d.title_ja || ''),
        h('span', { class: 'm' }, docWhen(d).filter(Boolean).join(' · '),
        d.via_wayback ? h('span', { class: 'tag' }, 'Wayback copy') : null,
        /^incomplete/i.test(d.status || '') ? h('span', { class: 'tag' }, 'Incomplete') : null),
        d.file && d.in_manifest ? h('span', { class: 'en', 'data-en': d.file, hidden: '' }) : null),
      h('div', { class: 'acts' },
        d.file ? link('Open externally', openFile(d.file)) : null,
        d.url ? link('Original URL', openUrl(d.url)) : null,
        d.file && d.in_manifest ? h('span', { class: 'en-acts', 'data-en-acts': d.file }) : null))));
    const view = h('div', { class: 'pv' });
    main.append(h('div', { class: 'docs' }, list, view));
    // Marks for documents that have an English version, with open and delete.
    IRV.refreshDocMarks = async () => {
      if (!list.isConnected) return;
      const files = docs.filter((d) => d.file && d.in_manifest).map((d) => d.file);
      const sum = await window.api.translation.summary(files).catch(() => ({}));
      for (const f of files) {
        const s = sum[f] || { state: 'none' };
        const mark = [...list.querySelectorAll('[data-en]')].find((x) => x.dataset.en === f);
        const acts = [...list.querySelectorAll('[data-en-acts]')].find((x) => x.dataset.enActs === f);
        if (!mark || !acts) continue;
        mark.hidden = s.state === 'none';
        mark.className = 'en' + (s.state === 'partial' ? ' partial' : '');
        mark.textContent = s.state === 'none' ? '' : `English ${s.state === 'complete' ? '' : '(partial) '}· ${s.pages} page${s.pages === 1 ? '' : 's'}${s.date ? ' · ' + s.date.slice(0, 10) : ''}`;
        acts.textContent = '';
        if (s.state === 'none') continue;
        if (s.hasOutput) acts.append(link('English PDF', async () => { const r = await window.api.translation.openOutput(f); if (!r.ok) app.toast(r.error); }));
        let armed = false;
        const del = link('Delete translation', async () => {
          if (!armed) { armed = true; del.textContent = 'Delete the English version?'; return; }
          const r = await window.api.translation.remove(f);
          if (!r.ok) app.toast(r.error);
          await IRV.refreshDocMarks();
          if (IRV.trReload) IRV.trReload();
        });
        del.dataset.del = f;
        acts.append(del);
      }
    };
    void IRV.refreshDocMarks();
    if (cur.file && cur.in_manifest) {
      const v = IRV.pdfViewer(view, cur.file, /^incomplete/i.test(cur.status || '') ? 'The manifest marks this capture as incomplete.' : '');
      app.disposers.push(() => v.destroy());
    } else {
      view.append(h('div', { class: 'empty' }, cur.file ? 'Not listed in the manifest. Use Open externally.' : 'No local copy. Use Original URL.'));
    }
  }

  // ---------- facts at the top of the page ----------
  // The product's policy forms (Japanese), each with the plans it covers where the data says so.
  // A plan entry may be a pattern ("usd_kakutei_p*"): it stands for every rate plan of the product it matches.
  function policyForms(p) {
    const known = [...new Set((A().model.byProduct.get(p.id) || []).map((s) => s.plan))];
    const expand = (pl) => {
      if (!pl.includes('*')) return [pl];
      const re = new RegExp('^' + pl.split('*').map((x) => x.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$');
      return known.filter((k) => re.test(k));
    };
    const out = [], seen = new Set();
    const add = (name, plans) => {
      for (const n of String(name).split('／').map((x) => x.trim()).filter(Boolean)) {
        if (seen.has(n)) { const o = out.find((x) => x.name === n); if (o && plans) o.plans.push(...plans); continue; }
        seen.add(n); out.push({ name: n, plans: plans ? [...plans] : [] });
      }
    };
    for (const x of arr(p.policy_names)) if (x && x.name_ja) add(x.name_ja, str(x.plan).split('/').map((s) => s.trim()).filter(Boolean).flatMap(expand));
    for (const x of arr(p.policy_names_ja)) if (x) add(x);
    if (p.policy_name_ja) add(p.policy_name_ja);
    return out;
  }
  IRV.policyForms = policyForms;
  const notesOf = (p) => arr(p.notes_en).filter((n) => typeof n === 'string' && n.trim());
  const launchNoted = (p) => notesOf(p).some((n) => /launch/i.test(n));
  // Status, with the independent re-check date under it.
  const statusFact = (m, p) => {
    const vt = IRV.verifiedThrough(m, p.id);
    return [IRV.statusEn(p.status) || 'not recorded', h('span', { class: 'sub2' }, vt ? `re-checked through ${vt}` : 'not yet re-checked')];
  };

  function renderFacts(main, p, ins, own, ui) {
    const app = A(), m = app.model;
    const dl = h('dl', { class: 'facts top' });
    const fact = (k, v, attrs) => { if (v != null && v !== '') dl.append(h('div', attrs || null, h('dt', null, k), h('dd', null, v))); };
    const dists = arr(p.distributors);
    fact('Insurer', IRV.insurerEn(ins, p.insurer_id));
    fact('Product type', typeLine(m, p, own));
    fact('Currencies', arr(p.currencies).join(', '));
    fact('Status', statusFact(m, p));
    // An unknown launch date is left to the notes when they explain it.
    // on_sale_date may be a day or a month ("2017-08"); it is shown as stored. A bound only reads "not later than".
    if (p.on_sale_date || p.on_sale_not_later_than || !launchNoted(p))
      fact('Launched', p.on_sale_date || (p.on_sale_not_later_than ? `not later than ${p.on_sale_not_later_than}` : 'not established'));
    if (p.off_sale_date) fact('Sales ended', p.off_sale_date);
    // Distributors have their own tab ("Sold by N"); the facts say so only when there are none.
    if (!dists.length) fact('Sold by', 'none recorded');
    // Policy forms: one short value in the grid; several get a full-width row, one per line, with the plans
    // each covers, instead of a tall narrow column.
    const forms = policyForms(p);
    // Up to three plan names under a form; more become a count, named on hover.
    const planList = (labels) => {
      const u = [...new Set(labels)];
      if (!u.length) return null;
      return u.length <= 3 ? h('span', { class: 'sub2' }, u.join('; ')) : h('span', { class: 'sub2', title: u.join('\n') }, `${u.length} plans`);
    };
    if (forms.length > 1) {
      fact(`Policy forms · ${forms.length}`, h('ul', { class: 'forms' }, forms.map((f) => h('li', null,
        h('span', { lang: 'ja' }, f.name),
        planList(f.plans.map((pl) => IRV.planEn(pl, p.id) || pl))))), { class: 'wide' });
    } else {
      fact('Policy form', forms.length ? h('span', { lang: 'ja' }, forms[0].name) : 'not recorded');
    }
    main.append(dl);
    renderNotes(main, p, ui);
  }

  // Notes: the first three, the rest behind a link.
  function renderNotes(main, p, ui) {
    const notes = notesOf(p);
    if (!notes.length) return;
    const SHOW = 2, open = ui.notesOpen || notes.length <= SHOW + 1;
    main.append(h('div', { class: 'notesblk' }, h('div', { class: 'lbl' }, 'Notes'),
      h('ul', { class: 'notes gaps' }, (open ? notes : notes.slice(0, SHOW)).map((n) => h('li', null, n))),
      notes.length > SHOW + 1 ? h('button', { type: 'button', class: 'lnk', id: 'notes-toggle', onclick: () => { ui.notesOpen = !ui.notesOpen; A().render(); } },
        ui.notesOpen ? 'Fewer notes' : `${notes.length - SHOW} more note${notes.length - SHOW === 1 ? '' : 's'}`) : null));
  }

  // The other tabs: one line of facts under the name, and the notes on request.
  function renderMetaLine(main, p, ins, own, ui) {
    const app = A(), m = app.model;
    const vt = IRV.verifiedThrough(m, p.id);
    const notes = notesOf(p);
    main.append(h('div', { class: 'metaline' },
      [IRV.insurerEn(ins, p.insurer_id), typeLine(m, p, own), arr(p.currencies).join(', '),
        IRV.statusEn(p.status) + (vt ? ` · re-checked through ${vt}` : '')].filter(Boolean).join(' · '),
      notes.length ? h('button', { type: 'button', class: 'lnk', id: 'notes-toggle', onclick: () => { ui.notesOpen = !ui.notesOpen; app.render(); } },
        ui.notesOpen ? 'Hide notes' : `${notes.length} note${notes.length === 1 ? '' : 's'}`) : null));
    if (ui.notesOpen && notes.length) main.append(h('ul', { class: 'notes gaps' }, notes.map((n) => h('li', null, n))));
  }

  function renderDist(main, p, ui) {
    const dists = arr(p.distributors);
    const text = (d) => (typeof d === 'string' ? d : [d.name_en, d.name_ja || d.name, d.product_name_ja ? '「' + d.product_name_ja + '」' : ''].filter(Boolean).join(' '));
    const listEl = h('div', { class: 'dist', lang: 'ja' });
    const fill = () => {
      listEl.textContent = '';
      const q = ui.distQ.trim().toLowerCase();
      const hits = dists.map(text).filter((t) => !q || t.toLowerCase().includes(q));
      for (const t of hits) listEl.append(h('div', null, t));
      if (!hits.length) listEl.append(h('div', { class: 'sub' }, 'No match.'));
    };
    if (dists.length > 12) {
      main.append(h('div', { class: 'pick' }, h('input', { type: 'search', placeholder: 'Filter', value: ui.distQ, 'aria-label': 'Filter distributors',
        oninput: (e) => { ui.distQ = e.target.value; fill(); } })));
    }
    fill();
    main.append(listEl);
  }

  // ---------- Data page: folder, reload, update ----------
  const UPD_SAVED = 'Saved copies re-read';
  const UPD_STATE = { waiting: 'Waiting', running: 'Running', ok: 'Updated', blocked: 'Needs a connection in Japan — not run', failed: 'Failed', cancelled: 'Cancelled', not_run: 'Not run' };
  const UPD_OUTCOME = { ok: 'all updated', partial: 'some not reachable or failed', cancelled: 'cancelled', error: 'collector error', running: 'running' };

  // Collector lines in reader's words: insurer and product names, measure and plan labels; file and page lines dropped.
  // Every product the data folder holds, candidates that are not enabled included: a log never prints an id.
  const allProducts = (m) => {
    const byId = new Map(m ? m.byId : []);
    for (const p of arr(A().ds && A().ds.candidates)) if (p && p.id && !byId.has(p.id)) byId.set(p.id, p);
    return byId;
  };
  // ---------- the collector's log in reader's words ----------
  // Every line becomes one short English sentence: products and insurers by their English short names, no
  // ids, file names, paths or Japanese. A line that only repeats the one before it is dropped. Lines that
  // matter only to someone debugging the collector ("quiet") are kept in the technical log only.
  const CJK = '\\u3000-\\u30ff\\u3400-\\u9fff\\uf900-\\ufaff\\uff00-\\uffef';
  // Japanese text, with any digits or letters glued to it ("ワールド4", "保険v2"), so a name never leaves a stray "4".
  const CJK_RUN = new RegExp(`[0-9A-Za-z]*(?:[${CJK}]+[0-9A-Za-z]*)+`, 'g');
  const isoTs = (ts) => (/^\d{8}/.test(ts) ? ts.replace(/^(\d{4})(\d{2})(\d{2}).*/, '$1-$2-$3') : ts);
  const cap1 = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const sentence = (s) => { const t = cap1(String(s).trim().replace(/\s{2,}/g, ' ')); return !t ? '' : /[.:!?…)]$/.test(t) ? t : t + '.'; };
  const CUR_WORD = { usd: 'USD', aud: 'AUD', jpy: 'JPY', eur: 'EUR', nzd: 'NZD', enka: 'JPY', gaika: 'foreign currency' };
  // A collector's table key or saved-file name in plain words ("ln-baseint-past_2026-11-17.txt" -> "past base rates, copy of 2026-11-17").
  function tableWords(key) {
    const k = String(key);
    const date = (/(\d{4}-\d{2}-\d{2})/.exec(k) || [])[1];
    const cur = (/(?:^|[_-])(usd|aud|jpy|eur|nzd|enka|gaika)(?=$|[_.\s-])/i.exec(k) || [])[1];
    const kind = /baseint|base_?rate/i.test(k) ? 'base rates' : /mva/i.test(k) ? 'MVA rates' : /limit/i.test(k) ? 'rate limits'
      : /rate|int|ritsu|tumitate|fxrate/i.test(k) ? 'rates' : 'table';
    const when = /past/i.test(k) ? 'past' : /current|fxrate/i.test(k) ? 'current' : '';
    return [when, cur ? CUR_WORD[cur.toLowerCase()] : '', kind].filter(Boolean).join(' ') + (date ? ', copy of ' + date : '');
  }
  function plainLine(byId, text) {
    let t = text.replace(/\b([a-z0-9][a-z0-9-]*__[a-z_]+__[A-Za-z0-9_.-]+?\.csv)\b/g, (w) => labelWords(byId, w)).replace(/Wayback Machine/g, 'archive service');
    for (const p of [...byId.values()].sort((a, b) => b.id.length - a.id.length)) t = t.split(p.id).join(IRV.shortEn(p));
    t = t.replace(/(?:\.{0,2}\/)?\b(?:sources|data|scripts|translations|reports)\/[^\s,;)]*/g, 'the data folder');
    t = t.replace(/\b[\w.-]+\.(?:csv|json|html?|txt|py|pdf)\b/g, (w) => tableWords(w));
    t = t.replace(/\b[a-z][a-z0-9]*-?[a-z0-9]*(?:_[a-z0-9-]+)+\b/g, (w) => (/rate|int|mva/.test(w) ? tableWords(w) : w.replace(/_/g, ' ')));
    t = t.replace(/\(--?[a-z-]+\)|\s--[a-z-]+\b/g, '');
    t = t.replace(CJK_RUN, ' ').replace(/\s+([,.:;)])/g, '$1').replace(/\(\s*\)/g, '').replace(/[「」『』]/g, '');
    return t.replace(/[ \t]{2,}/g, ' ').replace(/[\s:,]+$/, '').trim();
  }
  // What a "points read" label refers to: a rate file (product, measure, plan), a saved copy, or a period.
  function labelWords(byId, label) {
    const l = label.trim();
    let k = /^([a-z0-9][a-z0-9-]*)__([a-z_]+)__([^\s.]+)\.csv$/.exec(l);
    if (k) { const p = byId.get(k[1]); return [p ? IRV.shortEn(p) : 'A product', IRV.metricEn(k[2]), IRV.planEn(k[3], k[1])].filter(Boolean).join(' · '); }
    k = /^(\S+\.(?:html?|txt|json))(?:\s+(\S.*))?$/i.exec(l);
    if (k) {
      const date = (/(\d{4}-\d{2}-\d{2})/.exec(k[1]) || [])[1];
      return cap1([tableWords(k[1]).replace(/, copy of .*$/, ''), k[2] ? IRV.tidy(k[2]).toLowerCase() + ' plan' : ''].filter(Boolean).join(', ') + (date ? ` (copy of ${date})` : ''));
    }
    k = /^current\s+(\S+)$/.exec(l);
    if (k && /_/.test(k[1])) return cap1('current ' + tableWords(k[1]).replace(/^current /, ''));
    return cap1(plainLine(byId, l));
  }
  const conflictKey = (byId, key) => plainLine(byId, key.split(/\s+/).map((w) => (/^(crediting_rate|net_yield|base_rate)$/.test(w) ? IRV.metricEn(w).toLowerCase() : w)).join(' '));
  // [regex, (match, ctx) => sentence | null, quiet?]. null drops the line from both logs.
  const LOG_RULES = [
    [/^@@status /, () => null],
    [/^=== (.+?) \(([a-z0-9_-]+)\) ===$/, (k, c) => ({ head: true, text: c.insurer(k[2]) || plainLine(c.byId, k[1]) })],
    [/^no request made to the insurer's site/i, () => 'Re-read saved copies (no live request).'],
    [/^using the browser-pane snapshots/i, () => null],
    [/^Save the page elsewhere and use --file/i, () => null],
    [/^live fragments: answer with a bot-protection page/i, () => 'Live page blocked by bot protection; used saved copies.'],
    [/^live fragments: not readable/i, () => 'Live page not readable; used saved copies.'],
    [/^live fragments answered this time/i, () => 'Live page answered; saved copies still used.'],
    [/^live \S+ answered, but its rates are script-loaded/i, () => 'Live page answered, but its rates load by script; used saved copies.'],
    [/^live \S+ NOT READABLE/i, () => 'Live page not readable; used saved copies.'],
    [/^live rate page NOT REACHABLE/i, () => 'Live rate page not reachable; nothing read from it.'],
    [/^live rate page saved to .* but SKIPPED/i, () => 'Live rate page saved but not read: layout not recognised.'],
    [/^saved copy filed as .* but SKIPPED/i, () => 'Saved copy filed but not read: layout not recognised.'],
    [/^no snapshots in/i, () => 'No saved copies found.'],
    [/^(\S+\.(?:html?|txt|json)) SKIPPED, layout not parsed/i, (k) => `Skipped ${tableWords(k[1])}: layout not recognised.`],
    [/^(.+?)\s+window (\S+) to (\S+),\s+(\d+) points? read,\s+(\d+) new/, (k, c) => `${labelWords(c.byId, k[1])}, ${k[2]} to ${k[3]}: ${k[4]} read, ${k[5]} new.`],
    [/^(\d+) points? read, (\d+) new/, (k) => `${k[1]} rates read, ${k[2]} new.`],
    [/^(.+?)\s+(\d+) points? read,\s+(\d+) new(?:,\s*(\S+) to (\S+)|\s*\((\d+) forms?\))?/, (k, c) => `${labelWords(c.byId, k[1])}: ${k[2]} read, ${k[3]} new${k[4] ? `, ${k[4]} to ${k[5]}` : ''}.`],
    [/^(.+?)\s+(\d+) points? identical, (\d+) different/, (k, c) => `${labelWords(c.byId, k[1])}: ${k[2]} match, ${k[3]} differ.`],
    [/^(.+?)\s+(\d+) identical, (\d+) different, (\d+) not on file/, (k, c) => `${labelWords(c.byId, k[1])}: ${k[2]} match, ${k[3]} differ, ${k[4]} not on file.`],
    [/^(\S+)\s+NO POINTS/, (k, c) => `${labelWords(c.byId, k[1])}: nothing read.`],
    [/^(\S+): nothing read/, (k, c) => `${labelWords(c.byId, k[1])}: nothing read.`],
    [/^CONFLICT (.*?): on file (\S+), (?:source|snapshot|site)(?: now)? shows (\S+)/, (k, c) => `Conflict, ${conflictKey(c.byId, k[1])}: ${k[2]} on file, ${k[3]} at the source.`],
    [/^EDITION DIFFERS (.*?): (?:on file|product) (\S+), edition(?: capture)? (\S+)/, (k, c) => `Edition differs, ${conflictKey(c.byId, k[1])}: ${k[2]} on file, ${k[3]} in the edition.`],
    [/^VALUE DIFFERS (\S+): file (\S+), parse (\S+)/, (k, c) => `Value differs, ${conflictKey(c.byId, k[1].replace(/\|/g, ' '))}: ${k[2]} on file, ${k[3]} read.`],
    [/^(NOT REPRODUCED|PARSED BUT NOT ON FILE) (\S+)/, (k, c) => `${cap1(k[1].toLowerCase())}: ${conflictKey(c.byId, k[2].replace(/\|/g, ' '))}.`],
    [/^MISMATCH current page (.*) vs history (\S+)/, (k, c) => `Current page differs from history (${k[2]}): ${conflictKey(c.byId, k[1])}.`],
    [/^(\d+) new points, (\d+) conflicts?/, (k) => `${k[1]} new rates, ${k[2]} conflict${k[2] === '1' ? '' : 's'}.`],
    [/^(\d+) conflicts?(?: or mismatches)?:/, (k) => `${k[1]} conflict${k[1] === '1' ? '' : 's'}: the source differs from the file.`],
    [/^current period starting (\S+)/, (k) => `Current period from ${k[1]}.`],
    [/^current period (\S+) to (\S+)/, (k) => `Current period ${k[1]} to ${k[2]}.`],
    [/^current contract month (\S+)/, (k) => `Current contract month ${k[1]}.`],
    [/^current window from (\S+): (\d+) points/, (k) => `Current window from ${k[1]}: ${k[2]} rates.`],
    [/^current \S+ window starting (\S+)/, (k) => `Current window from ${k[1]}.`],
    [/^current page agrees with newest history row: (\w+)/, (k) => (k[1] === 'yes' ? 'Current page agrees with history.' : 'Current page does not agree with history.')],
    [/^(\S+): period starting (\S+)/, (k) => `${cap1(tableWords(k[1]))}: period from ${k[2]}.`],
    [/^wayback index/i, () => 'Archive index not reachable.'],
    [/^web\.archive\.org .*waiting (\d+) s/i, (k) => `Archive service is limiting requests; waiting ${k[1]} seconds.`],
    [/^wayback (?:\S+ )?(\d{8,14}) (?:\S+ )?SKIPPED \(?(.*?)\)?$/i, (k) => `Archive capture ${isoTs(k[1])} skipped: ${plainLine(new Map(), k[2]).replace(/^\(|\)$/g, '').toLowerCase()}.`],
    [/^wayback (?:\S+ )?(\d{8,14}) (?:\S+ )?(?:period|window) starting (\S+)/i, (k) => `Archive capture ${isoTs(k[1])}: period from ${k[2]}.`, true],
    [/^wayback (?:\S+ )?(\d{8,14}) (?:\S+ )?contract month (\S+)/i, (k) => `Archive capture ${isoTs(k[1])}: contract month ${k[2]}.`, true],
    [/^wayback (\d{8,14}) saved/i, (k) => `Archive capture ${isoTs(k[1])} saved.`, true],
    [/^wayback (\S+) (?:NOT FETCHED|skipped: HTTP)/i, (k) => `Archive capture ${isoTs(k[1])} not fetched.`],
    [/^wayback/i, (_k, c) => `Archive capture: ${plainLine(c.byId, _k.input.replace(/^wayback\s+/i, ''))}.`, true],
    [/^history (\S+)\s+(\d+) windows, +(\d+) points/, (k) => `History, ${IRV.tidy(k[1]).toLowerCase()}: ${k[2]} periods, ${k[3]} rates.`, true],
    [/^history (\S+)\s+skipped (\d+) windows/, (k) => `History, ${IRV.tidy(k[1]).toLowerCase()}: ${k[2]} periods after the last contract date skipped.`, true],
    [/^page\s+(?:NOT REACHABLE|(\S+) UNREACHABLE)/, () => 'A page was not reachable.'],
    [/^saved\s+[a-z0-9-]+\s+\S+\s+[\d.]+ MB/, () => 'Saved a document.', true],
    [/^(?:page|saved)\s/, () => 'Saved a page copy.', true],
    [/^rate list fragment not saved/, () => 'Rate list not saved.'],
    [/^rate list fragment/, () => 'Saved the rate list.', true],
    [/^doc FAILED/, () => 'A document could not be downloaded.'],
    [/^doc\s+\S+.*SKIPPED|^SKIPPED \(not a PDF\)|^NOT PDF/, () => 'A document was skipped: not a PDF or not archived.'],
    [/^doc\s+\S+ already on file/, () => 'Document already on file.', true],
    [/^doc\s/, () => 'Saved a document.', true],
    [/^FAILED\s+([a-z0-9-]+)\s/, (k, c) => `A document for ${c.byId.has(k[1]) ? IRV.shortEn(c.byId.get(k[1])) : 'a product'} could not be downloaded.`],
    [/^distributors: (\d+) agencies/, (k) => `${k[1]} distributors list this product.`, true],
    [/^\S+ \S+: (\d+) agencies/, (k) => `${k[1]} distributors on this channel.`, true],
    [/^wrote .*\((\d+) rate series\)/, (k) => `Product file updated (${k[1]} rate series).`, true],
    [/^(\d+) cells shown as/, (k) => `${k[1]} rates not yet set by the insurer were not recorded.`],
    [/^\S+ \S+: no rows/, () => null],
    [/^\S+\s+no rate page/, () => 'A channel has no rate page.', true],
    [/^\S+\s+period (\S+) to (\S+), (?:.*?)(match|DIFFER)/, (k) => `Channel period ${k[1]} to ${k[2]}: ${k[3] === 'match' ? 'matches' : 'differs from'} the reference channel.`, true],
    [/^edition \S+ shows month/, () => 'An edition shows another month; not compared.', true],
    [/^edition \S+: (\d+) shared values, (\d+) differ/, (k) => `Edition: ${k[1]} shared rates, ${k[2]} differ.`, true],
    [/^COLUMN WITHOUT A SERIES|^NEW COLUMNS not collected/, () => 'The page has columns that are not collected.'],
    [/^BLOCKED: /, () => 'Blocked by the site.'],
    [/^FAILED to read the pages/, () => 'Could not read the pages.'],
    [/^Traceback|^File "|^\^+$|^\s*~+$/, () => null],
    [/^(\w+(?:Error|Exception)): (.*)$/, (k, c) => `Collector error: ${plainLine(c.byId, k[2]).slice(0, 120)}.`],
  ];
  function logLine(ctx, raw) {
    const t = raw.trim();
    if (!t) return null;
    for (const [re, fn, quiet] of LOG_RULES) {
      const k = re.exec(t);
      if (!k) continue;
      const r = fn(k, ctx);
      if (r == null) return null;
      return typeof r === 'object' ? { ...r, quiet: !!quiet } : { text: r, quiet: !!quiet };
    }
    // A product heading: "<product id>  <Japanese name>".
    const k = /^([a-z0-9][a-z0-9-]*)(?:\s|$)/.exec(t);
    if (k && ctx.byId.has(k[1])) return { text: IRV.shortEn(ctx.byId.get(k[1])), product: true };
    const p = plainLine(ctx.byId, t);
    if (!p || !/[A-Za-z]/.test(p)) return null;
    // Anything still built from file names or code words is collector detail, not news.
    const quiet = /\bthe data folder\b|\btable\b/.test(p) && !/fail|error|conflict|not /i.test(p);
    return { text: sentence(p.length > 140 ? p.slice(0, 137).replace(/\s+\S*$/, '') + '…' : p), quiet };
  }
  function logCtx(m) {
    return { byId: allProducts(m), insurer: (id) => { const i = m && m.insurers.get(id); return i ? IRV.insurerShort(i, id) : ''; } };
  }
  function friendlyLog(m, raw) {
    const ctx = logCtx(m), out = [];
    for (const line of raw) {
      const r = logLine(ctx, line);
      if (!r || r.quiet) continue;
      if (out[out.length - 1] === r.text) continue; // the same sentence twice in a row says nothing new
      out.push(r.text);
    }
    return out;
  }
  // Every line in order, quiet ones too, in the same words; indentation kept so the structure shows.
  function technicalLog(m, raw) {
    const ctx = logCtx(m), out = [];
    for (const line of raw) {
      if (!line.trim()) { out.push(''); continue; }
      const r = logLine(ctx, line);
      if (!r) continue;
      out.push(r.head ? `=== ${r.text} ===` : /^(\s*)/.exec(line)[1] + r.text);
    }
    return out;
  }
  // The insurer table's message for one insurer, as one plain sentence.
  function insurerMessage(m, i, s) {
    if (i.kind === 'saved' && s.state === 'ok') return 'Re-read saved copies (no live request).';
    const r = logLine(logCtx(m), str(s.message));
    return r ? r.text : '';
  }

  V.data = function (main) {
    const app = A(), u = app.upd, info = u.info, m = app.model;
    const running = !!(u.run && u.run.running);
    main.append(h('h1', { class: 'sm' }, 'Data'));
    main.append(h('h2', null, 'Folder'), h('div', { class: 'path' }, app.ds ? app.ds.root : ''),
      h('div', { class: 'rowf' },
        h('button', { type: 'button', class: 'btn', disabled: running, onclick: () => app.chooseFolder() }, 'Choose folder'),
        h('button', { type: 'button', class: 'btn', onclick: () => app.reload(true) }, 'Reload'),
        h('span', { class: 'sub' }, m ? `${m.products.length} products · ${m.series.length} series · latest ${m.latest || '–'}` : '')));
    if (app.ds && app.ds.warnings && app.ds.warnings.length) main.append(h('div', { class: 'banner' }, app.ds.warnings.slice(0, 4).join(' · ')));

    main.append(h('h2', null, 'Update', h('span', { class: 'more' }, running ? 'Running' : '')));
    if (!info) { main.append(h('div', { class: 'empty' }, 'Checking the collector…')); return; }
    if (!info.ok) main.append(h('div', { class: 'banner' }, info.message));
    if (running && u.run.kind) u.kind = u.run.kind;
    const news = u.kind === 'news';
    main.append(h('div', { class: 'pick' }, V.toggles('What', [{ value: 'rates', label: 'Rates and documents' }, { value: 'news', label: 'News' }], u.kind,
      (v) => { if (!running) { u.kind = v; app.render(); } })));
    if (news) return dataNews(main, app, u, info, running);
    const insurers = info.insurers || [];
    if (!u.sel) u.sel = new Set(insurers.map((i) => i.id));
    const restricted = insurers.filter((i) => i.kind === 'restricted');
    // The insurer's short name (the full one on hover) and how it is collected in a column of its own, so
    // that nothing wraps under a tag.
    const insName = (i) => { const x = m && m.insurers.get(i.id); return x ? IRV.insurerShort(x, i.id) : i.name; };
    const KIND = { restricted: 'Japan connection', saved: 'Saved copies' };
    // What to do about insurers that could not be reached is said once, above the table; each row says only that.
    const runIns = u.run && u.run.kind !== 'news' && u.run.insurers ? u.run.insurers : {};
    const nBlocked = insurers.filter((i) => runIns[i.id] && runIns[i.id].state === 'blocked').length;
    if (nBlocked) main.append(h('p', { class: 'banner vpn' }, `${nBlocked === 1 ? 'One insurer was' : nBlocked + ' insurers were'} not reachable from this network. Turn on a Japan connection (VPN) and run again.`));
    main.append(h('table', { class: 'upd' },
      h('thead', null, h('tr', null, h('th', null, 'Insurer'), h('th', { class: 'l' }, 'Source'), h('th', { class: 'l' }, 'Status'), h('th', null, 'New'), h('th', { class: 'l' }, 'Message'))),
      h('tbody', null, insurers.map((i) => {
        const s = u.run && u.run.kind !== 'news' && u.run.insurers ? u.run.insurers[i.id] : null;
        // An insurer whose pages are only ever read from copies saved by hand is not "updated" by a run.
        const savedOk = i.kind === 'saved' && s && s.state === 'ok';
        return h('tr', { 'data-ins': i.id },
          h('td', null, h('label', { class: 'insn', title: i.name }, h('input', { type: 'checkbox', checked: u.sel.has(i.id), disabled: running || !info.ok,
            onchange: (e) => { if (e.target.checked) u.sel.add(i.id); else u.sel.delete(i.id); app.render(); } }), ' ', insName(i))),
          h('td', { class: 'l kind' }, KIND[i.kind] ? h('span', { class: 'tag' }, KIND[i.kind]) : h('span', { class: 'sub' }, 'Live site')),
          h('td', { class: 'l' + (savedOk ? ' st-saved' : s ? ' st-' + s.state : '') }, savedOk ? UPD_SAVED : s ? UPD_STATE[s.state] || s.state : ''),
          h('td', { class: 'num' }, s && (s.state === 'ok' || s.newPoints) ? s.newPoints : ''),
          h('td', { class: 'l' }, s ? (s.state === 'blocked' ? ['Not reachable from this network.',
            h('small', null, sentence(plainLine(allProducts(m), (/:\s*(.+?)\.\s*Connect/.exec(s.message || '') || [null, ''])[1])))]
            : insurerMessage(m, i, s)) : ''));
      }))));
    main.append(h('div', { class: 'rowf' },
      h('button', { type: 'button', class: 'btn go', disabled: running || !info.ok || !u.sel.size, onclick: async () => {
        const r = await window.api.collectorStart([...u.sel], u.docs);
        if (!r.ok) app.toast(r.error);
      } }, running ? 'Running' : 'Run'),
      running ? h('button', { type: 'button', class: 'btn', onclick: async () => { const r = await window.api.collectorCancel(); if (!r.ok) app.toast(r.error); } }, 'Cancel') : null,
      h('label', null, h('input', { type: 'checkbox', checked: u.docs, disabled: running || !info.ok, onchange: (e) => { u.docs = e.target.checked; } }), 'Include documents')));
    const last = u.run && !u.run.running && u.run.kind !== 'news' ? u.run : info.lastRun;
    // Dates are written one way throughout: 2026-11-17 (and 09:47 where the time matters).
    const stamp = (iso) => { const d = new Date(iso), z = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`; };
    // "Reachable insurers updated; 2 need a connection in Japan": restricted insurers are expected, not failures.
    const outcome = (r) => {
      const kindOf = new Map(insurers.map((i) => [i.id, i.kind]));
      const entries = Object.entries(r.insurers || {});
      const reread = entries.filter(([id, x]) => x.state === 'ok' && kindOf.get(id) === 'saved').length;
      const states = entries.filter(([id, x]) => !(x.state === 'ok' && kindOf.get(id) === 'saved')).map(([, x]) => x.state);
      const count = (k) => states.filter((x) => x === k).length;
      if (r.outcome === 'cancelled') return 'cancelled';
      if (count('failed') || r.outcome === 'error') return `${count('failed') || 'the run'} failed${count('ok') ? `, ${count('ok')} updated` : ''}`;
      const parts = [count('ok') ? 'reachable insurers updated' : 'nothing updated'];
      if (reread) parts.push(`${reread} re-read from saved copies`);
      if (count('blocked')) parts.push(`${count('blocked')} need${count('blocked') === 1 ? 's' : ''} a connection in Japan`);
      return parts.join('; ');
    };
    main.append(h('p', { class: 'foot' },
      last && last.finishedAt ? `Last run ${stamp(last.finishedAt)} · ${outcome(last)}${last.docs ? ' · with documents' : ''}.`
        : running ? 'Run in progress. A full run takes 2–4 minutes.' : 'No run yet.',
      restricted.length ? ` ${restricted.map(insName).join(' and ')} need a connection in Japan (VPN) switched on before Run.` : ''));

    runLog(main, u);
  };

  // News: which owners' lists to read, and whether to add English titles and summaries afterwards.
  function dataNews(main, app, u, info, running) {
    const owners = info.news || [];
    const n = app.news;
    if (!owners.length) { main.append(h('div', { class: 'empty' }, 'scripts/news.py was not found in the data folder, or no news sources are configured.')); return; }
    if (!u.newsSel) u.newsSel = new Set(owners.map((i) => i.id));
    const all = owners.every((i) => u.newsSel.has(i.id));
    const lastRead = (id) => (n ? Object.entries(n.state).filter(([k]) => k.startsWith(id + '/')).map(([, r]) => r.last_ok || '').sort().pop() : '') || '–';
    const row = (id, name, tag, box) => {
      const s = u.run && u.run.kind === 'news' && u.run.insurers ? u.run.insurers[id] : null;
      return h('tr', null,
        h('td', null, h('label', null, box, name, tag ? h('span', { class: 'tag' }, tag) : null)),
        h('td', { class: 'l' + (s ? ' st-' + s.state : '') }, s ? UPD_STATE[s.state] || s.state : ''),
        h('td', { class: 'num' }, s && (s.state === 'ok' || s.newPoints) ? s.newPoints : ''),
        h('td', { class: 'num' }, id === 'enrich' ? '' : lastRead(id)),
        h('td', { class: 'l' }, s ? s.message : ''));
    };
    main.append(h('table', null,
      h('thead', null, h('tr', null,
        h('th', null, h('label', null, h('input', { type: 'checkbox', checked: all, disabled: running || !info.ok, 'aria-label': 'Select all',
          onchange: (e) => { u.newsSel = new Set(e.target.checked ? owners.map((i) => i.id) : []); app.render(); } }), 'Source')),
        h('th', { class: 'l' }, 'Status'), h('th', null, 'New'), h('th', null, 'Last read'), h('th', { class: 'l' }, 'Message'))),
      h('tbody', null, owners.map((i) => row(i.id, i.name, { full: '', watch: 'Watch', 'market-wide': 'Market-wide' }[i.kind] || '',
        h('input', { type: 'checkbox', checked: u.newsSel.has(i.id), disabled: running || !info.ok,
          onchange: (e) => { if (e.target.checked) u.newsSel.add(i.id); else u.newsSel.delete(i.id); app.render(); } }))),
      u.run && u.run.kind === 'news' && u.run.insurers && u.run.insurers.enrich ? row('enrich', 'English titles and summaries', '', null) : null)));
    main.append(h('div', { class: 'rowf' },
      h('button', { type: 'button', class: 'btn go', id: 'news-run', disabled: running || !info.ok || !u.newsSel.size, onclick: async () => {
        const r = await window.api.collectorStart([...u.newsSel], false, { kind: 'news', enrich: u.enrich });
        if (!r.ok) app.toast(r.error);
      } }, running ? 'Running' : 'Run'),
      running ? h('button', { type: 'button', class: 'btn', onclick: async () => { const r = await window.api.collectorCancel(); if (!r.ok) app.toast(r.error); } }, 'Cancel') : null,
      h('label', { title: 'Runs Claude Code with your own sign-in to add English titles, topics and short summaries to new items. Counts against your Claude plan.' },
        h('input', { type: 'checkbox', checked: u.enrich, disabled: running || !info.ok, onchange: (e) => { u.enrich = e.target.checked; } }), 'Add English titles and summaries (uses Claude)')));
    const stamp = (iso) => { const d = new Date(iso), z = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`; };
    const last = u.run && !u.run.running && u.run.kind === 'news' ? u.run : info.lastNewsRun;
    main.append(h('p', { class: 'foot' },
      last && last.finishedAt ? `Last news run ${stamp(last.finishedAt)} · ${UPD_OUTCOME[last.outcome] || last.outcome}.`
        : running ? 'Run in progress.' : 'No news run from the app yet.',
      ' Reads the insurers\' own news lists, the regulator and the association. Press coverage is added by the weekly sweep, not by this run.'));
    runLog(main, u);
  }

  function runLog(main, u) {
    const m = A().model;
    if (u.run) {
      const conflicts = u.run.conflicts || [];
      main.append(h('h2', null, 'Conflicts', h('span', { class: 'more' }, conflicts.length)));
      main.append(conflicts.length ? h('ul', { class: 'notes' }, conflicts.map((c) => h('li', { class: 'path' }, (c.insurer ? c.insurer + ': ' : '') + c.text)))
        : h('div', { class: 'sub' }, 'None.'));
      // The collector's output, retold with the names the rest of the app uses; the raw text stays one click away.
      const lines = friendlyLog(m, u.run.log || []);
      main.append(h('h2', null, 'Log', h('span', { class: 'more' }, lines.length)));
      main.append(lines.length ? h('ul', { class: 'notes runlog' }, lines.slice(-200).map((t) => h('li', null, t))) : h('div', { class: 'sub' }, 'Nothing yet.'));
      const pre = h('pre', { class: 'log' }, technicalLog(m, u.run.log || []).join('\n'));
      main.append(h('details', { open: u.logOpen ? '' : null, style: { marginTop: '18px' },
        ontoggle: (e) => { u.logOpen = e.target.open; if (e.target.open) pre.scrollTop = pre.scrollHeight; } },
      h('summary', null, 'Technical log'), pre));
      if (u.logOpen) setTimeout(() => { pre.scrollTop = pre.scrollHeight; }, 0);
    }
  }
})();
