// Explore (home), Products, Markets, Moves and Chart pages.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h, fmt, pct, fd, cls, md } = IRV;
  const V = (IRV.views = {});
  const MAX_OVERLAY = 8;
  const MEASURES = [['crediting_rate', 'Crediting rate'], ['net_yield', 'Net yield']];

  const A = () => IRV.app;
  const profile = (st) => ({ age: st.age, sex: st.sex });
  const color = (pid) => IRV.cssVar(A().model.color[pid]);
  const chartSeries = (s, label, col) => ({ label, color: col, endT: IRV.endT(s, A().model), ...IRV.periodSpan(A().model, s),
    obs: s.obs.map((o) => ({ t: o.t, v: o.v, wb: o.wb, u: IRV.isUnchecked(A().model, s.pid, o) })) });
  V.chartSeries = chartSeries;
  const insShort = (p) => IRV.insurerShort(A().model.insurers.get(p.insurer_id), p.insurer_id);
  // The board for the type / currency / tenor selected in Explore.
  // Survival-weighted annuities can be ranked on the fixed-annuity board (Explore's switch), marked on each row.
  const SW = 'survival_weighted_annuity';
  const alsoTypes = (st) => (st.type === 'fixed_annuity' && st.withSW ? [SW] : []);
  const caseBoard = (st, at) => IRV.board(A().model, { type: st.type, also: alsoTypes(st), cur: st.cur, term: st.term, metric: st.measure }, profile(st), at);
  // The "as of" day shared by Explore, Markets and Moves: st.asOf is 'YYYY-MM-DD', or empty for today. Kept for the session only.
  const asOfAt = (st) => { const t = st.asOf ? IRV.toEpoch(st.asOf) : null; return t != null && t < IRV.today() ? t : null; };
  const earliestDate = (m) => m.series.reduce((d, s) => (s.obs[0] && (!d || s.obs[0].date < d) ? s.obs[0].date : d), '');
  function asOfPicker(st, earliest) {
    const app = A(), today = IRV.isoDate(IRV.today()), on = asOfAt(st) != null;
    const set = (v) => { st.asOf = v && v < today ? v : ''; app.render(); };
    return h('span', { class: 'grp asof-pick' }, h('span', null, 'As of'),
      h('input', { type: 'date', id: 'asof-date', value: on ? st.asOf : today, min: earliest || null, max: today, 'aria-label': 'Show rates in force on this date',
        onchange: (e) => set(e.target.value) }),
      on ? h('button', { type: 'button', class: 'lnk', id: 'asof-today', onclick: () => set('') }, 'Today') : null);
  }
  const boardTitle = (m, spec) => `${m.typeLabel(spec.type)} · ${spec.cur} · ${IRV.termEn(spec.term)}`;

  // Text toggles: [{ value, label }]
  function toggles(label, options, value, onpick) {
    return h('div', { class: 'grp' }, label ? h('span', { class: 'lbl' }, label) : null,
      options.map((o) => h('button', { type: 'button', class: 'tg' + (o.value === value ? ' on' : ''), onclick: () => onpick(o.value) }, o.label)));
  }
  V.toggles = toggles;
  const measureSwitch = (value, onpick) => h('div', { class: 'grp', 'data-measure': '' }, MEASURES.map(([k, label]) => h('button', {
    type: 'button', class: 'tg' + (k === value ? ' on' : ''), 'data-measure-key': k, onclick: () => onpick(k) }, label)));

  // One line saying which age and sex pick the row where a product prices by them.
  function caseLine(st) {
    const app = A();
    return h('span', { class: 'case' }, `Sample case: age ${st.age}, ${IRV.sexEn(st.sex).toLowerCase()} · `,
      h('button', { type: 'button', class: 'lnk', id: 'case-edit', onclick: () => { st.caseOpen = !st.caseOpen; app.render(); } }, st.caseOpen ? 'Done' : 'Change'),
      st.caseOpen ? h('span', { class: 'grp' },
        h('input', { type: 'number', min: '0', max: '110', value: String(st.age), 'aria-label': 'Age',
          onchange: (e) => { const v = parseInt(e.target.value, 10); if (!isNaN(v)) { st.age = Math.max(0, Math.min(110, v)); app.save(); app.render(); } } }),
        ['F', 'M'].map((x) => h('button', { type: 'button', class: 'tg' + (st.sex === x ? ' on' : ''), onclick: () => { st.sex = x; app.save(); app.render(); } }, IRV.sexEn(x)))) : null);
  }

  // Who sells it: the bank or broker by name when there are one or two, otherwise how many (all listed on hover).
  // The insurer as a coloured chip: one colour per insurer (by its place in the sorted insurer list), so a
  // board shows at a glance which insurers have several plans on it.
  const insChip = (p) => {
    const ids = [...A().model.insurers.keys()].sort();
    const i = Math.max(0, ids.indexOf(p.insurer_id));
    return h('span', { class: 'chip ic' + (i % 12), title: IRV.insurerEn(A().model.insurers.get(p.insurer_id), p.insurer_id) }, insShort(p));
  };
  // A plan's label on a board: without the board's currency (it is in the title), and without the age band or
  // sex when it is the sample case's (every row is picked for that case); both stay in the row's title.
  const boardPlan = (r, cur) => {
    const st = A().state;
    let plan = IRV.planShown(r.s.plan, r.p.id) || '';
    const k = new RegExp('^' + cur + '(?:,\\s*|\\s+)').exec(plan);
    if (k && plan.length > k[0].length) { plan = plan.slice(k[0].length); plan = plan.charAt(0).toUpperCase() + plan.slice(1); }
    const band = [IRV.ageMatches(r.s.age, st.age) ? '' : IRV.ageEn(r.s.age), IRV.sexMatches(r.s.sex, st.sex) ? '' : IRV.sexEn(r.s.sex)].filter(Boolean).join(', ');
    return [plan, band].filter(Boolean).join(', ');
  };
  // Short English names for the banks that sell a product on their own (the data records them in Japanese).
  const DIST_EN = { 楽天銀行: 'Rakuten', 三井住友銀行: 'SMBC', 三井住友信託銀行: 'Sumitomo Mitsui Trust', みずほ銀行: 'Mizuho', みずほ信託銀行: 'Mizuho Trust' };
  function soldBy(p) {
    const names = (Array.isArray(p.distributors) ? p.distributors : [])
      .map((d) => (typeof d === 'string' ? d : d && (d.name_en || d.name_ja || d.name))).filter(Boolean);
    if (!names.length) return h('td', { class: 'l co none' }, 'not recorded');
    if (names.length <= 2) {
      const en = names.map((n) => DIST_EN[n]);
      return en.every(Boolean) ? h('td', { class: 'l co', title: names.join('・') }, [...new Set(en)].join(' · '))
        : h('td', { class: 'l co', lang: 'ja' }, names.join('・'));
    }
    const more = names.length > 40 ? `\n… and ${names.length - 40} more` : '';
    return h('td', { class: 'l co cnt', title: names.slice(0, 40).join('\n') + more }, `${names.length} distributors`);
  }

  // ---------- a board: header sentence, rows, and the plans left out ----------
  // Row: rank (with any rank change), insurer, product and plan, who sells it, rate, change, and the date only
  // where it is not the board's. A row repeating the product above it is set faint but for its plan and rate.
  function boardRows(b, limit) {
    const app = A(), m = app.model;
    const rows = limit ? b.rows.slice(0, limit) : b.rows;
    const noPrevTitle = 'Change on the period immediately before; blank = no rate for that period';
    // The red rate marks the best plan of the board's own type (row 1 unless a survival-weighted plan leads).
    const best = b.ownLeader || rows[0];
    // The As of column only when some row's date is not the board's.
    const anyOdd = rows.some((r) => r.o.date !== b.mainDate);
    // Over the government curve, when there is one for this currency (UST, JGB, ACGB).
    const g = b.govt, anyGovt = rows.some((r) => r.govt);
    const govtTitle = (x) => `${IRV.govtLabel(x)} par yield ${x.y.toFixed(3)}% on ${x.date}, ${x.ae.toFixed(3)}% annual effective`;
    const overCell = (r) => {
      if (!anyGovt) return null;
      if (!r.govt) return h('td', { class: 'num none', title: `No curve tenor: ${r.govtWhy}` }, '');
      const other = !g || r.govt.tenor !== g.tenor;
      return h('td', { class: 'num over', title: `Rate ${pct(r.o.v)} less ${govtTitle(r.govt)}` + (other ? ` (tenor from ${r.govtWhy})` : '') },
        fd(r.over), other ? h('sup', { class: 'eqmark' }, `${r.govt.tenor}Y`) : null);
    };
    return h('table', { class: 'bd' },
      h('thead', null, h('tr', null, h('th', { class: 'rk' }, '#'), h('th', { class: 'l ins' }, 'Insurer'), h('th', { class: 'l' }, 'Product · plan'), h('th', { class: 'l co' }, 'Sold by'), h('th', null, b.spec.metric === 'net_yield' ? 'Net yield' : 'Rate'),
        h('th', { title: noPrevTitle }, 'Chg'),
        anyGovt ? h('th', { title: (g ? `Rate less the ${govtTitle(g)}` : 'Rate less the government yield') + '. Percentage points; one yield per curve and tenor, the latest on or before the board\'s date.' }, g ? `Over ${IRV.govtLabel(g)}` : 'Over govt') : null,
        anyOdd ? h('th', { title: `Blank = the board's date, ${b.mainDate}` }, 'As of') : null)),
      h('tbody', null, rows.map((r, i) => {
        const fullBand = [IRV.ageEn(r.s.age), IRV.sexEn(r.s.sex)].filter(Boolean).join(', ');
        const fullPlan = [IRV.planShown(r.s.plan, r.p.id), fullBand].filter(Boolean).join(', ');
        const plan = boardPlan(r, b.spec.cur);
        const noPrev = `No rate for the previous period (from ${r.prevStart})` + (r.earlier ? `; last point ${r.earlier.date}` : '');
        const rc = r.rankChange == null ? (r.isNew ? h('span', { class: 'rc new', title: 'First appears this period' }, 'new') : null)
          : r.rankChange > 0 ? h('span', { class: 'rc up', title: `Was ${r.rankWas} of the ${r.compared} plans ranked in both periods` }, '▲' + r.rankChange)
            : r.rankChange < 0 ? h('span', { class: 'rc dn', title: `Was ${r.rankWas} of the ${r.compared} plans ranked in both periods` }, '▼' + -r.rankChange) : null;
        const chg = r.d == null
          ? h('td', { class: 'num none', title: r.isNew ? 'First appears this period' : noPrev }, r.isNew && r.rankChange != null ? h('span', { class: 'new' }, 'new') : '')
          : h('td', { class: 'num ' + cls(r.d) }, fd(r.d));
        const odd = r.o.date !== b.mainDate;
        // Faint only next to a row of the same product and type, and never the best row.
        const rep = i > 0 && rows[i - 1].p.id === r.p.id && !rows[i - 1].other === !r.other && r !== best;
        return h('tr', { class: [rep ? 'rep' : '', r.other ? 'swrow' : '', r === best ? 'best' : ''].filter(Boolean).join(' ') || null },
          // Rank and rank change each sit in a fixed-width slot (the change slot even when empty),
          // so a board gaining or losing an arrow does not shift the columns after it.
          h('td', { class: 'rk', title: r.tied ? 'Tied on rate' : null }, h('span', { class: 'rn' }, (r.tied ? '=' : '') + r.rank), rc || h('span', { class: 'rc' })),
          h('td', { class: 'l ins' }, insChip(r.p)),
          h('td', { class: 'l nm', title: [IRV.shortEn(r.p), fullPlan, insShort(r.p)].filter(Boolean).join(' · ') }, h('button', { type: 'button', class: 'pn', onclick: () => app.go('product', { id: r.p.id }) }, IRV.shortEn(r.p)),
            plan ? h('span', { class: 'pl' }, plan) : null,
            r.other ? h('span', { class: 'tag swtag', title: A().model.typeLabel(r.s.type) + ': fixed rate, reduced death benefit, so the rate is not like-for-like' }, 'SW') : null),
          soldBy(r.p),
          h('td', { class: 'num rate' }, h('button', { type: 'button', class: 'src', title: 'Source: the saved page this rate came from',
            onclick: () => IRV.openSource(r.o, `${IRV.shortEn(r.p)} · ${fullPlan || IRV.metricEn(b.spec.metric)}`) }, pct(r.o.v)), IRV.uncheckedMark(m, r.p.id, r.o),
            IRV.netCheckMark(m, r.s, r.o),
            IRV.derivedMark(r.s)),
          chg,
          overCell(r),
          !anyOdd ? null : h('td', { class: 'num asof' + (odd ? ' flag' : ''), title: odd ? `Differs from the board's date, ${b.mainDate}` : null }, odd ? r.o.date : ''));
      })));
  }
  // " · UST 10Y 4.09%, leader +1.25 over": the curve the Over column reads against, annual effective.
  function govtBit(b) {
    const g = b.govt;
    if (!g) return null;
    const lead = b.leader && b.leader.govt && b.leader.govt.tenor === g.tenor ? b.leader.over : null;
    return [' · ', h('span', { title: `${g.curve} ${g.tenor}-year par yield ${g.y.toFixed(3)}% on ${g.date}, shown annual effective` }, `${IRV.govtLabel(g)} `, h('b', null, g.ae.toFixed(2) + '%')),
      lead != null ? [', leader ', h('b', null, fd(lead)), ' over'] : null];
  }
  // "Leader … 5.34% · spread 1.03 · 9 down, 0 up": the answer without arithmetic.
  function boardSummary(b, noDate, noLeader) {
    if (!b.leader) return h('span', { class: 'sum' }, b.spec.metric === 'net_yield' ? 'No plan publishes a net yield for this board.'
      : `No plan has a rate in force ${b.at == null ? 'today' : 'on ' + IRV.isoDate(b.at)}.`);
    const r = b.leader;
    const nm = (x) => `${IRV.shortEn(x.p)}${IRV.planShown(x.s.plan, x.p.id) ? ', ' + IRV.planShown(x.s.plan, x.p.id) : ''}`;
    // The names can be cut short on a board header; the rate after them never is.
    const lead = b.leaders.length === 1 ? ['Leader ', h('b', null, nm(r))]
      : b.leaders.length === 2 && (nm(b.leaders[0]) + nm(b.leaders[1])).length <= 60 ? ['Joint leaders ', h('b', null, `${nm(b.leaders[0])} and ${nm(b.leaders[1])}`)]
        : [h('b', null, `${b.leaders.length} joint leaders`)];
    const n = b.up + b.down;
    const moved = n ? [`${n} rate${n === 1 ? '' : 's'} changed (`, b.down ? h('span', { class: 'dn' }, `${b.down} down`) : null, b.up && b.down ? ', ' : null, b.up ? h('span', { class: 'up' }, `${b.up} up`) : null, ')']
      : 'no rate changed';
    // The leader can be long: on a board header it is the part that gives way (ellipsis, full text on hover),
    // so that spread and movers stay on the same line.
    const leadEl = h('span', { class: 'lead' }, lead);
    leadEl.title = `${leadEl.textContent} ${pct(r.o.v)}`;
    const uncLeader = b.leaders.find((x) => x.unchecked);
    // Where the table under it shows the leader, the header gives only what the rows do not: spread, changes, date.
    if (noLeader) {
      return h('span', { class: 'sum' }, h('span', { class: 'rest' }, b.spread != null ? ['Spread ', h('b', null, b.spread.toFixed(2)), ' · '] : null,
        b.govt ? [govtBit(b).slice(1), ' · '] : null, moved,
        b.rankMoved && !noDate ? ` · ${b.rankMoved} changed rank` : '', b.mainDate && !noDate ? [' · ', h('span', { class: 'maindate' }, 'as of ', h('b', null, b.mainDate))] : null));
    }
    return h('span', { class: 'sum' }, leadEl, h('span', { class: 'rest' }, h('b', null, pct(r.o.v)), uncLeader ? IRV.uncheckedMark(A().model, uncLeader.p.id, uncLeader.o) : null,
      b.spread != null ? [' · spread ', h('b', null, b.spread.toFixed(2))] : null, govtBit(b), ' · ', moved, b.rankMoved && !noDate ? ` · ${b.rankMoved} changed rank` : '',
      b.mainDate && !noDate ? [' · ', h('span', { class: 'maindate' }, 'as of ', h('b', null, b.mainDate))] : null));
  }
  function leftOut(b, key) {
    const app = A(), open = app.state.leftOpen.has(key);
    if (!b.left.length) return null;
    const n = b.left.length;
    return h('div', { class: 'leftout' },
      h('button', { type: 'button', class: 'lnk', 'data-left': key, onclick: () => { if (open) app.state.leftOpen.delete(key); else app.state.leftOpen.add(key); app.render(); } },
        `${n} plan${n === 1 ? '' : 's'} not shown: no rate in force${b.at == null ? '' : ' on ' + IRV.isoDate(b.at)}`),
      open ? h('ul', null, b.left.map((x) => h('li', null, `${IRV.shortEn(x.p)}${IRV.planShown(x.s.plan, x.p.id) ? ', ' + IRV.planShown(x.s.plan, x.p.id) : ''}: ${x.reason}`,
        `, last rate ${x.last.date} at ${pct(x.last.v)}`, IRV.uncheckedMark(app.model, x.p.id, x.last)))) : null);
  }

  // ---------- Products: every product, with what it offers today ----------
  V.products = function (main) {
    const app = A(), m = app.model, st = app.state;
    // Per product: plans, currencies and tenors with a rate in force today (any measure); the last date otherwise.
    const info = (p) => {
      const list = m.byProduct.get(p.id) || [];
      const live = list.filter((s) => { const c = IRV.periodPoints(m, s).cur; return c && c.v != null; });
      const use = live.length ? live : list;
      const last = list.reduce((d, s) => { const o = s.obs[s.obs.length - 1]; return o && o.date > d ? o.date : d; }, '');
      const terms = IRV.sortTerm(new Set(use.map((s) => s.term).filter(Boolean)));
      return { live: live.length > 0, last, types: [...new Set(list.map((s) => s.type))],
        plans: new Set(use.map((s) => s.plan)).size, curs: IRV.sortCur(new Set(use.map((s) => s.currency).filter(Boolean))),
        terms, noTerm: use.some((s) => !s.term) };
    };
    const all = m.products.map((p) => ({ p, i: info(p) }));
    const typeN = (k) => all.filter((x) => (x.i.types.length ? x.i.types : [IRV.str(x.p.product_type) || 'unclassified']).includes(k)).length;
    const types = m.types.filter((t) => typeN(t.key));
    if (st.prodType && !typeN(st.prodType)) st.prodType = '';
    const shown = all.filter((x) => !st.prodType || (x.i.types.length ? x.i.types : [IRV.str(x.p.product_type) || 'unclassified']).includes(st.prodType));
    const insName = (p) => IRV.insurerShort(m.insurers.get(p.insurer_id), p.insurer_id);
    shown.sort((a, b) => insName(a.p).localeCompare(insName(b.p)) || IRV.shortEn(a.p).localeCompare(IRV.shortEn(b.p), 'en', { numeric: true }));

    main.append(h('h1', { class: 'sm' }, 'Products', h('span', null, ` ${shown.length}`)));
    main.append(h('div', { class: 'pick' }, h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Type'),
      [{ key: '', label: 'All', n: all.length }, ...types.map((t) => ({ key: t.key, label: t.label, n: typeN(t.key) }))].map((t) =>
        h('button', { type: 'button', class: 'tg' + (t.key === st.prodType ? ' on' : ''), 'data-prodtype': t.key || 'all',
          onclick: () => { st.prodType = t.key; app.render(); } }, t.label, h('i', { class: 'n' }, t.n))))));
    const rows = [];
    let last = null;
    for (const { p, i } of shown) {
      if (p.insurer_id !== last) { last = p.insurer_id; rows.push(h('tr', { class: 'ins' }, h('td', { colspan: 6 }, insName(p)))); }
      const open = () => app.go('product', { id: p.id });
      const tenors = [i.terms.map((t) => IRV.termEn(t)).join(' · '), i.noTerm ? 'set by plan' : ''].filter(Boolean).join(' · ');
      rows.push(h('tr', { class: 'prow', 'data-prod': p.id, tabindex: 0, onclick: open, onkeydown: (e) => { if (e.key === 'Enter') open(); } },
        h('td', null, h('b', null, IRV.shortEn(p)), h('span', { class: 'ja' }, IRV.str(p.name_ja))),
        h('td', null, i.types.map((k) => m.typeLabel(k)).join(' · ') || m.typeLabel(IRV.str(p.product_type) || 'unclassified')),
        h('td', { class: 'num' }, i.plans || '—'),
        h('td', null, i.curs.join(' · ') || '—'),
        h('td', null, tenors || '—'),
        h('td', { class: i.live ? null : 'old' }, i.live ? 'In force' : i.last ? 'Last ' + i.last : 'No rates')));
    }
    main.append(h('table', { class: 'cand prods' },
      h('thead', null, h('tr', null, h('th', null, 'Product'), h('th', null, 'Type'), h('th', { class: 'num' }, 'Plans'), h('th', null, 'Currencies'), h('th', null, 'Tenors'), h('th', null, 'Rates'))),
      h('tbody', null, rows)));
    main.append(h('p', { class: 'foot' }, 'Plans, currencies and tenors with a rate in force today; for a product with none, those of its last rates. Open a product for every plan, currency and tenor.'));
  };

  // ---------- Explore: one board from three pickers, with all rows and its history ----------
  function countPicker(label, options, value, onpick) {
    return h('div', { class: 'grp' }, h('span', { class: 'lbl' }, label),
      options.map((o) => h('button', { type: 'button', class: 'tg' + (o.value === value ? ' on' : ''), disabled: !o.n, title: o.title || null,
        'data-pick': label.toLowerCase() + ':' + o.value, onclick: () => onpick(o.value) }, o.label, h('i', { class: 'n' }, o.n))));
  }
  V.explore = function (main) {
    const app = A(), m = app.model, st = app.state;
    const at = asOfAt(st);
    const combos = IRV.boardCombos(m, profile(st), st.measure, at);
    // Counts are distinct product-plans with a rate in force (on the as-of day).
    const sum = (list) => new Set(list.flatMap((c) => [...c.plans])).size;
    const typeN = (k) => sum(combos.filter((c) => c.type === k));
    if (!typeN(st.type)) st.type = (m.types.map((t) => t.key).filter(typeN).sort((a, b) => typeN(b) - typeN(a))[0]) || st.type;
    const also = alsoTypes(st);
    const inType = combos.filter((c) => c.type === st.type || also.includes(c.type));
    const curs = IRV.sortCur(new Set(inType.map((c) => c.cur)));
    const curN = (c) => sum(inType.filter((x) => x.cur === c));
    if (!curN(st.cur)) st.cur = [...curs].sort((a, b) => curN(b) - curN(a))[0] || st.cur;
    const inCur = inType.filter((c) => c.cur === st.cur);
    const terms = IRV.sortTerm([...new Set(inCur.map((c) => c.term))]);
    const termN = (t) => sum(inCur.filter((x) => x.term === t));
    if (!termN(st.term)) st.term = [...terms].sort((a, b) => termN(b) - termN(a))[0] || st.term;
    const pick = (k) => (v) => { st[k] = v; app.save(); app.render(); };
    const spec = { type: st.type, cur: st.cur, term: st.term };
    const b = caseBoard(st, at);

    main.append(h('h1', { class: 'sm lb' }, m.typeLabel(st.type), h('span', null, ` ${st.cur} ${IRV.termEn(st.term)}`),
      at != null ? h('span', { class: 'asof-h' }, ` as of ${st.asOf}`) : null));
    const typeRow = countPicker('Type', m.types.filter((t) => typeN(t.key)).map((t) => ({ value: t.key, label: t.label, n: typeN(t.key), title: t.description })), st.type, pick('type'));
    if (st.type === 'fixed_annuity' && typeN(SW)) {
      typeRow.append(h('label', { class: 'swsw', title: 'Rank survival-weighted annuities on this board too, each marked. Their rate buys a reduced death benefit, so it is not like-for-like.' },
        h('input', { type: 'checkbox', id: 'with-sw', checked: !!st.withSW, onchange: (e) => { st.withSW = e.target.checked; app.save(); app.render(); } }),
        'Include survival-weighted'));
    }
    main.append(h('div', { class: 'pick col3' },
      typeRow,
      countPicker('Currency', curs.map((c) => ({ value: c, label: c, n: curN(c) })), st.cur, pick('cur')),
      countPicker('Tenor', terms.map((t) => ({ value: t, label: IRV.termEn(t), n: termN(t) })), st.term, pick('term'))));
    const untracked = m.types.filter((t) => !typeN(t.key) && t.key !== 'unclassified').map((t) => t.label);
    const typeLbl = main.querySelector('.pick.col3 .lbl');
    if (typeLbl && untracked.length) typeLbl.title = 'No plans tracked yet: ' + untracked.join('; ');
    main.append(h('div', { class: 'lbline' }, measureSwitch(st.measure, (k) => { st.measure = k; app.save(); app.render(); }),
      asOfPicker(st, earliestDate(m)), h('span', { class: 'sp' }), caseLine(st)));
    // Small print in one place under the table: what is left out, then one key.
    const anySW = b.rows.some((r) => r.other);
    const key = [`Rates in force ${at == null ? 'today' : 'on ' + st.asOf}. Chg and ▲▼ are against the period before; a blank Chg = no rate then.`,
      anySW ? ' SW = survival-weighted: reduced death benefit, so not like-for-like.' : '',
      b.ownLeader ? ` Red rate = best ${m.typeLabel(st.type).toLowerCase()}.` : '',
      b.rows.some((r) => r.o.date !== b.mainDate) ? ' A date shows only where it is not the board\'s.' : '',
      b.rows.some((r) => r.s.derived === 'crediting_rate') ? ' = the crediting rate, which the insurer states is also the net yield.' : '',
      b.rows.some((r) => r.s.derived === 'model') ? ' m = modelled, not published: the crediting rate less the up-front charge, for the latest period only, so no Chg or history.' : '',
      b.rows.some((r) => IRV.netYieldCheck(m, r.s, r.o)) ? ' ✓ = the value model reproduces this net yield from the crediting rate and the brochure\'s up-front charge (≠ = it does not); no mark = no brochure charge to check against.' : ''].join('');
    main.append(h('section', { class: 'board' }, h('div', { class: 'bhead' }, boardSummary(b, false, b.rows.length > 0)), b.rows.length ? boardRows(b, 0) : null,
      h('div', { class: 'bfoot' }, leftOut(b, 'explore'), h('span', { class: 'nanote' }, key, b.rows.some((r) => r.unchecked) ? [' ', IRV.uncheckedLegend()] : null))));
    // History of the board's plans, eight lines at a time.
    const pages = Math.ceil(b.rows.length / MAX_OVERLAY);
    if (!(st.histPage < pages)) st.histPage = 0;
    const from = st.histPage * MAX_OVERLAY;
    const top = b.rows.slice(from, from + MAX_OVERLAY);
    if (top.length) {
      main.append(h('h2', null, 'History', h('span', { class: 'more' },
        pages > 1 ? [`Ranks ${from + 1}–${from + top.length} of ${b.rows.length} · `, h('button', { type: 'button', class: 'lnk', id: 'hist-next', onclick: () => { st.histPage = (st.histPage + 1) % pages; app.render(); } },
          `Show ${((st.histPage + 1) % pages) * MAX_OVERLAY + 1}–${Math.min(b.rows.length, (((st.histPage + 1) % pages) + 1) * MAX_OVERLAY)}`), ' · '] : null,
        h('button', { type: 'button', class: 'lnk', onclick: () => { st.overlay = top.map((r) => r.s.key); st.ovSlot = {}; st.overlayCleared = false; app.save(); app.go('chart'); } }, 'Open in Chart'))));
      main.append(V.chartBlock(top.map((r, i) => chartSeries(r.s, `${r.tied ? '=' : ''}${r.rank}. ${[IRV.shortEn(r.p), IRV.planShown(r.s.plan, r.p.id)].filter(Boolean).join(' · ')}`, IRV.cssVar('--s' + (i + 1)))), st, 'range'));
    }
  };

  // ---------- Markets: one product type at a time ----------
  V.markets = function (main) {
    const app = A(), m = app.model, st = app.state;
    const at = asOfAt(st);
    const combos = IRV.boardCombos(m, profile(st), 'crediting_rate', at);
    const types = m.types.filter((t) => combos.some((c) => c.type === t.key));
    const typeSize = (k) => new Set(combos.filter((c) => c.type === k).flatMap((c) => [...c.plans])).size;
    if (!types.some((t) => t.key === st.mtype)) st.mtype = (types.slice().sort((a, b) => typeSize(b.key) - typeSize(a.key))[0] || {}).key || '';
    // Currencies and tenors that exist for this type (with a rate in force).
    const mine = combos.filter((c) => c.type === st.mtype);
    const curs = IRV.sortCur(new Set(mine.map((c) => c.cur)));
    if (!curs.includes(st.cur)) st.cur = curs[0] || st.cur;
    const terms = IRV.sortTerm(new Set(mine.filter((c) => c.cur === st.cur).map((c) => c.term)));
    if (!terms.includes(st.term)) st.term = terms.includes('10') ? '10' : terms[terms.length - 1] || st.term;
    const board = (cu, t) => IRV.board(m, { type: st.mtype, cur: cu, term: t, metric: 'crediting_rate' }, profile(st), at);
    const b = board(st.cur, st.term), now = b.rows;
    const caseName = `${st.cur} ${IRV.termEn(st.term)}`;

    // The headline is about the same currency and tenor as the figures under it.
    const movedRows = now.filter((r) => Math.abs(r.d || 0) > 0.00005);
    let head = `${caseName} rates unchanged`;
    if (movedRows.length) {
      const dir = b.down > b.up ? 'fall' : b.up > b.down ? 'rise' : null;
      const n = new Set(movedRows.filter((r) => (b.down > b.up ? r.d < 0 : r.d > 0)).map((r) => r.p.insurer_id)).size;
      head = dir ? `${caseName} rates ${dir} at ${n} insurer${n === 1 ? '' : 's'}` : `${caseName} rates mixed`;
    }
    main.append(h('h1', null, head, at != null ? h('span', { class: 'asof-h' }, ` as of ${st.asOf}`) : null),
      h('div', { class: 'stats' },
        h('div', null, h('b', null, now[0] ? pct(now[0].o.v) : '–', now[0] ? IRV.uncheckedMark(m, now[0].p.id, now[0].o) : null),
          h('span', null, `Best ${caseName}`, now[0] ? ' · ' + (b.leaders.length > 1 ? `${b.leaders.length} joint leaders` : IRV.shortEn(now[0].p)) : '')),
        h('div', null, h('b', null, b.spread != null ? b.spread.toFixed(2) : '–'), h('span', null, `Spread, ${now.length} plan${now.length === 1 ? '' : 's'}`)),
        h('div', null, h('b', null, b.down, h('i', null, '↓'), ' ', b.up, h('i', null, '↑')), h('span', null, `${caseName} plans moved`))));

    main.append(h('h2', null, 'Markets', h('span', { class: 'more' }, m.typeLabel(st.mtype))));
    main.append(h('div', { class: 'pick', style: { marginTop: '0', marginBottom: '12px' } },
      toggles('Type', types.map((t) => ({ value: t.key, label: t.label })), st.mtype, (v) => { st.mtype = v; app.save(); app.render(); }),
      asOfPicker(st, earliestDate(m))));
    // Six small charts, one line per product (its best plan), each product in its own colour.
    let anyUnc = now.some((r) => r.unchecked);
    // Colours are given per chart, among the lines that chart shows (its top plans by current rate).
    const LINES = 6;
    const slot = (i) => IRV.cssVar('--s' + (i + 1));
    const topLines = (rows) => { const seen = new Set(); return rows.filter((r) => (seen.has(r.p.id) ? false : (seen.add(r.p.id), true))); };
    const charted = IRV.sortCur(new Set(mine.map((c) => c.cur))).flatMap((cu) => ['5', '10'].filter((t) => mine.some((c) => c.cur === cu && c.term === t)).map((t) => [cu, t]));
    const cells = charted.map(([cu, t]) => {
      const on = cu === st.cur && t === st.term;
      const rows = board(cu, t).rows;
      const all = topLines(rows), lines = all.slice(0, LINES);
      if (rows[0] && rows[0].unchecked) anyUnc = true;
      return h('button', { type: 'button', class: 'smc' + (on ? ' on' : ''), onclick: () => { st.cur = cu; st.term = t; app.save(); app.render(); } },
        h('h3', null, `${cu} ${t}Y`, h('span', { class: 'num' }, rows[0] ? pct(rows[0].o.v) : '–', rows[0] ? IRV.uncheckedMark(m, rows[0].p.id, rows[0].o) : null)),
        h('p', null, rows[0] ? IRV.shortEn(rows[0].p) : 'No rate in force'),
        IRV.mini(lines.map((r, i) => chartSeries(r.s, '', slot(i))), { w: 320, h: 170 }),
        h('span', { class: 'legend sm' }, lines.map((r, i) => h('span', null, h('span', { class: 'sw', style: { background: slot(i) } }), IRV.shortEn(r.p))),
          all.length > lines.length ? h('span', { class: 'morek' }, `+${all.length - lines.length} more`) : null));
    });
    // The table under the charts uses the selected chart's colours.
    const swatch = new Map(topLines(now).slice(0, LINES).map((r, i) => [r.s.key, slot(i)]));
    main.append(h('div', { class: 'multi' }, cells));
    main.append(h('div', { class: 'pick' },
      toggles('Currency', curs.map((c) => ({ value: c, label: c })), st.cur, (v) => { st.cur = v; app.save(); app.render(); }),
      toggles('Tenor', terms.map((t) => ({ value: t, label: IRV.termEn(t) })), st.term, (v) => { st.term = v; app.save(); app.render(); })));

    const table = h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Product · plan'), h('th', null, 'Rate'), h('th', null, 'Chg'), h('th', null, 'As of'))),
      h('tbody', null, now.map((r) => h('tr', { class: 'go', onclick: () => app.go('product', { id: r.p.id }) },
        h('td', { title: IRV.nameEn(r.p) }, swatch.has(r.s.key) ? h('span', { class: 'sw', style: { background: swatch.get(r.s.key) } }) : h('span', { class: 'sw none' }), IRV.shortEn(r.p),
          h('small', null, [insShort(r.p), IRV.planShown(r.s.plan, r.p.id), IRV.ageEn(r.s.age)].filter(Boolean).join(' · '))),
        h('td', { class: 'num' }, h('button', { type: 'button', class: 'src', title: 'Source: the saved page this rate came from',
          onclick: (e) => { e.stopPropagation(); IRV.openSource(r.o, [IRV.shortEn(r.p), IRV.planShown(r.s.plan, r.p.id)].filter(Boolean).join(' · ')); } }, pct(r.o.v)), IRV.uncheckedMark(m, r.p.id, r.o)),
        h('td', { class: 'num ' + cls(r.d), title: r.d == null && !r.isNew ? `No rate for the previous period (from ${r.prevStart})` : null }, r.d == null ? (r.isNew ? 'new' : 'n/a') : fd(r.d)),
        h('td', { class: 'num asof' + (r.o.date !== b.mainDate ? ' flag' : ''), title: r.o.date !== b.mainDate ? `Differs from the main date, ${b.mainDate}` : null },
          r.o.date)))));
    // Moves of this type, any currency and tenor.
    const mv = IRV.moves(m, 'crediting_rate', at).filter((x) => x.s.type === st.mtype);
    const fresh = mv.filter((x) => x.kind === 'chg');
    if (fresh.slice(0, 8).some((x) => IRV.isUnchecked(m, x.p.id, x.to))) anyUnc = true;
    const right = h('div', null, h('h2', null, 'Moves · all currencies', h('button', { type: 'button', class: 'more lnk', onclick: () => app.go('moves') }, `All ${mv.length}`)),
      fresh.length ? fresh.slice(0, 8).map((x) => h('button', { type: 'button', class: 'mvl', onclick: () => app.go('product', { id: x.p.id, series: x.s.key }) },
        h('span', null, IRV.shortEn(x.p), h('small', null, IRV.seriesLabel(x.s), ' · ', h('span', { class: 'nw' }, pct(x.to.v), IRV.uncheckedMark(m, x.p.id, x.to)))),
        h('span', { class: 'x num ' + cls(x.d) }, fd(x.d)))) : h('div', { class: 'empty' }, 'None this period.'));
    main.append(h('div', { class: 'two' },
      h('div', null, h('h2', null, `${m.typeLabel(st.mtype)} · ${caseName}`, b.mainDate ? h('span', { class: 'more' }, 'as of ' + b.mainDate) : null), now.length ? table : h('div', { class: 'empty' }, 'No plan has a rate in force for this case.')),
      right));
    main.append(h('p', { class: 'foot' }, `Rates in force ${at == null ? 'today' : 'on ' + st.asOf}; change against the period immediately before. Each small chart shows its top ${LINES} products by current rate. `, anyUnc ? IRV.uncheckedLegend() : null));
  };

  // ---------- Moves ----------
  V.moves = function (main) {
    const app = A(), m = app.model, st = app.state;
    const f = st.movesFilter;
    const metrics = [...new Set(m.series.map((s) => s.metric))];
    if (!metrics.includes(f.metric)) f.metric = metrics.includes('crediting_rate') ? 'crediting_rate' : metrics[0];
    const at = asOfAt(st);
    const all = IRV.moves(m, f.metric, at);
    const curs = IRV.sortCur(new Set(all.map((x) => x.s.currency)));
    const inss = [...new Set(all.map((x) => x.p.insurer_id))];
    if (f.cur && !curs.includes(f.cur)) f.cur = '';
    if (f.ins && !inss.includes(f.ins)) f.ins = '';
    const list = all.filter((x) => (!f.cur || x.s.currency === f.cur) && (!f.ins || x.p.insurer_id === f.ins));
    const set = (k) => (v) => { f[k] = v; app.render(); };

    main.append(h('h1', { class: 'sm' }, 'Moves', at != null ? h('span', { class: 'asof-h' }, ` as of ${st.asOf}`) : null),
      h('div', { class: 'pick' },
        toggles('Currency', [{ value: '', label: 'All' }, ...curs.map((c) => ({ value: c, label: c }))], f.cur, set('cur')),
        toggles('Insurer', [{ value: '', label: 'All' }, ...inss.map((i) => ({ value: i, label: IRV.insurerShort(m.insurers.get(i), i) }))], f.ins, set('ins')),
        h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Measure'),
          IRV.menu('Measure', metrics.map((k) => [k, IRV.metricEn(k)]), f.metric, set('metric'))),
        asOfPicker(st, earliestDate(m))));
    const section = (title, rows) => {
      const CAP = 250;
      main.append(h('h2', null, title, h('span', { class: 'more' }, rows.length)));
      if (!rows.length) { main.append(h('div', { class: 'empty' }, 'None.')); return; }
      main.append(h('table', null,
        h('thead', null, h('tr', null, h('th', null, 'Series'), h('th', null, 'Was'), h('th', null, 'Now'), h('th', null, 'Chg'), h('th', null, 'Previous'), h('th', null, 'Effective'))),
        h('tbody', null, rows.slice(0, CAP).map((x) => h('tr', { class: 'go', onclick: () => app.go('product', { id: x.p.id, series: x.s.key }) },
          h('td', { title: IRV.nameEn(x.p) }, IRV.shortEn(x.p), h('small', null, insShort(x.p), ' · ', IRV.seriesLabel(x.s))),
          h('td', { class: 'num' }, x.from.v == null ? '—' : pct(x.from.v), x.from.v == null ? null : IRV.uncheckedMark(m, x.p.id, x.from)),
          h('td', { class: 'num' }, x.to.v == null ? '—' : h('button', { type: 'button', class: 'src', title: 'Source: the saved page this rate came from',
            onclick: (e) => { e.stopPropagation(); IRV.openSource(x.to, `${IRV.shortEn(x.p)} · ${IRV.seriesLabel(x.s)}`); } }, pct(x.to.v)), IRV.uncheckedMark(m, x.p.id, x.to)),
          h('td', { class: 'num ' + cls(x.d) }, x.kind === 'new' ? 'New' : x.kind === 'susp' ? 'Suspended' : fd(x.d)),
          h('td', { class: 'num' }, x.from.date), h('td', { class: 'num' }, x.to.date))))));
      if (rows.length > CAP) main.append(h('p', { class: 'foot' }, `${rows.length - CAP} smaller moves not shown.`));
    };
    section(at == null ? 'This period' : `Period in force on ${st.asOf}`, list.filter((x) => x.kind === 'chg'));
    section('Newly offered or suspended', list.filter((x) => x.kind !== 'chg'));
    main.append(h('p', { class: 'foot' }, 'Each series is compared with the period immediately before. ',
      all.noPrevious ? `${all.noPrevious} series in force ${at == null ? 'today' : 'on ' + st.asOf} have no point for that period and are not compared. ` : '',
      list.some((x) => IRV.isUnchecked(m, x.p.id, x.to) || IRV.isUnchecked(m, x.p.id, x.from)) ? IRV.uncheckedLegend() : null));
  };

  // ---------- Chart (overlay) ----------
  V.chart = function (main) {
    const app = A(), m = app.model, st = app.state;
    st.overlay = st.overlay.filter((k) => m.byKey.has(k));
    if (!st.overlay.length && !st.overlayCleared) st.overlay = caseBoard(st).rows.map((r) => r.s.key).slice(0, MAX_OVERLAY);
    const ad = st.adder;
    if (!m.byId.has(ad.pid)) ad.pid = m.products[0] ? m.products[0].id : '';
    const own = m.byProduct.get(ad.pid) || [];
    const metrics = [...new Set(own.map((s) => s.metric))];
    if (!metrics.includes(ad.metric)) ad.metric = metrics.includes('crediting_rate') ? 'crediting_rate' : metrics[0] || '';
    const cand = own.filter((s) => s.metric === ad.metric);
    if (!cand.some((s) => s.key === ad.key)) ad.key = cand[0] ? cand[0].key : '';
    const full = st.overlay.length >= MAX_OVERLAY;

    main.append(h('h1', { class: 'sm' }, 'Chart'));
    main.append(h('div', { class: 'pick' },
      h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Add'),
        IRV.menu('Product', m.products.map((p) => [p.id, IRV.shortEn(p)]), ad.pid, (v) => { ad.pid = v; app.render(); }),
        metrics.length > 1 ? IRV.menu('Measure', metrics.map((k) => [k, IRV.metricEn(k)]), ad.metric, (v) => { ad.metric = v; app.render(); }) : null,
        IRV.menu('Series', cand.map((s) => [s.key, IRV.seriesLabel(s) || 'Rate']), ad.key, (v) => { ad.key = v; app.render(); }),
        h('button', { type: 'button', class: 'btn', disabled: full || !ad.key || st.overlay.includes(ad.key),
          onclick: () => { st.overlay.push(ad.key); st.overlayCleared = false; app.save(); app.render(); } }, 'Add')),
      h('div', { class: 'grp' },
        h('button', { type: 'button', class: 'lnk', onclick: () => {
          st.overlay = caseBoard(st).rows.map((r) => r.s.key).slice(0, MAX_OVERLAY); st.overlayCleared = false; app.save(); app.render();
        } }, `Current board, ${st.cur} ${st.term}Y`),
        h('button', { type: 'button', class: 'lnk', onclick: () => { st.overlay = []; st.overlayCleared = true; app.save(); app.render(); } }, 'Clear'))));

    // Stable colour per series while it stays on the chart.
    for (const k of Object.keys(st.ovSlot)) if (!st.overlay.includes(k)) delete st.ovSlot[k];
    const used = new Set(Object.values(st.ovSlot));
    for (const k of st.overlay) {
      if (st.ovSlot[k] != null) continue;
      // Prefer the product's own colour (as on the board) when it is free.
      let i = parseInt(m.color[m.byKey.get(k).pid].slice(3), 10);
      if (used.has(i)) { i = 1; while (used.has(i)) i++; }
      st.ovSlot[k] = i; used.add(i);
    }
    const series = st.overlay.map((k) => {
      const s = m.byKey.get(k), p = m.byId.get(s.pid);
      const metricTag = s.metric === 'crediting_rate' ? '' : ' · ' + IRV.metricEn(s.metric);
      return { key: k, ...chartSeries(s, `${IRV.shortEn(p)} · ${IRV.seriesLabel(s)}${metricTag}`, IRV.cssVar('--s' + (((st.ovSlot[k] - 1) % 8) + 1))) };
    });
    main.append(V.chartBlock(series, st, 'range', { onRemove: (k) => { st.overlay = st.overlay.filter((x) => x !== k); app.save(); app.render(); } }));
    main.append(h('p', { class: 'foot' }, `Up to ${MAX_OVERLAY} series. Breaks = not offered. Dashed with hollow points = archive captures. `,
      series.some((x) => x.obs.some((o) => o.u)) ? IRV.uncheckedLegend() : null));
  };

  // Range presets, the plot, and under it one list that names each series once and shows its value at the
  // pointer. opts.onRemove(key) adds a remove button per series (the Chart page).
  V.chartBlock = function (all, st, rangeKey, opts) {
    const app = A(), o = opts || {};
    const wrap = h('div');
    if (!all.length) { wrap.append(h('div', { class: 'empty' }, 'No series selected.')); return wrap; }
    // Only series with at least one rate are drawn and named in the list; the rest get one line under it.
    const series = all.filter((s) => s.obs.some((x) => x.v != null));
    const blank = all.filter((s) => !series.includes(s));
    const blankLine = blank.length ? h('div', { class: 'sub nodraw' }, `No line, not offered at any point: `,
      blank.map((s, i) => [i ? ', ' : '', s.label, o.onRemove ? h('button', { type: 'button', class: 'rm', title: 'Remove', 'aria-label': 'Remove ' + s.label, onclick: () => o.onRemove(s.key) }, '×') : null])) : null;
    if (!series.length) { wrap.append(h('div', { class: 'empty' }, 'Nothing to draw: none of these series has a rate.'), blankLine || ''); return wrap; }
    const box = h('div', { class: 'chart-box' });
    const presets = [['6M', 0.5], ['1Y', 1], ['All', null]];
    const grp = h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Range'), presets.map(([lbl, yrs]) => h('button', {
      type: 'button', class: 'tg' + (st[rangeKey] === yrs ? ' on' : ''), onclick: (e) => {
        st[rangeKey] = yrs; app.save();
        if (app.chart) app.chart.setRange(yrs);
        for (const b of e.target.parentNode.querySelectorAll('.tg')) b.classList.toggle('on', b === e.target);
      } }, lbl)));
    const dateEl = h('span', { class: 'rd-date' }, '');
    const valEls = series.map(() => h('span', { class: 'rd num' }, ''));
    const list = h('div', { class: 'slist' }, h('div', { class: 'rdhead' }, h('span', { class: 'nm' }, 'At the pointer'), dateEl),
      series.map((s, i) => h('div', null, h('span', { class: 'sw', style: { background: s.color } }), h('span', { class: 'nm' }, s.label), valEls[i],
        o.onRemove ? h('button', { type: 'button', class: 'rm', title: 'Remove', 'aria-label': 'Remove ' + s.label, onclick: () => o.onRemove(s.key) }, '×') : null)));
    wrap.append(h('div', { class: 'pick' }, grp), box, list);
    if (blankLine) wrap.append(blankLine);
    // The box must be in the document before uPlot measures it.
    queueMicrotask(() => {
      if (!box.isConnected) return;
      app.setChart(IRV.stepChart(box, series, { legend: false, onCursor: (date, vals) => {
        dateEl.textContent = date || '';
        vals.forEach((v, i) => { if (valEls[i]) valEls[i].textContent = v; });
      } }));
      if (st[rangeKey] != null) app.chart.setRange(st[rangeKey]);
    });
    return wrap;
  };
})();
