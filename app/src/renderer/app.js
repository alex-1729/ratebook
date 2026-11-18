// Shell: state, navigation, search, loading. No Node access: everything
// comes through window.api (preload). Data text is inserted with textContent.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h } = IRV;
  const api = window.api;
  const STORE = 'irv-state-v3';

  // Remembered between launches (the app's own storage, not the repo).
  const saved = (() => { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch { return {}; } })();
  const state = {
    type: saved.type || '', best: !!saved.best, withSW: !!saved.withSW, caseOpen: false, searchType: '',
    measure: saved.measure === 'net_yield' ? 'net_yield' : 'crediting_rate', mtype: saved.mtype || '',
    leftOpen: new Set(), prodType: '',
    cur: saved.cur || '', term: saved.term || '',
    age: Number.isFinite(saved.age) ? saved.age : 60, sex: saved.sex === 'M' ? 'M' : 'F',
    planPick: saved.planPick || {}, overlay: Array.isArray(saved.overlay) ? saved.overlay : [], overlayCleared: false,
    range: saved.range === undefined ? null : saved.range,
    ovSlot: {}, adder: { pid: '', metric: '', key: '' }, movesFilter: { cur: '', ins: '', metric: 'crediting_rate' },
  };

  // Candidate products ("candidate": true in the product file) stay out of every
  // view until enabled here. The choice is kept in the app's own storage.
  const ENABLED = 'irv-enabled-candidates-v1';
  const enabledIds = () => { try { return new Set(JSON.parse(localStorage.getItem(ENABLED)) || []); } catch { return new Set(); } };

  const app = (IRV.app = {
    state, ui: {}, model: null, news: null, ds: null, chart: null, view: 'explore', params: {},
    upd: { info: null, sel: null, newsSel: null, kind: 'rates', docs: false, enrich: true, run: null, logOpen: false },
    save() {
      const { type, best, withSW, cur, term, age, sex, planPick, overlay, range, measure, mtype } = state;
      try { localStorage.setItem(STORE, JSON.stringify({ type, best, withSW, cur, term, age, sex, planPick, overlay, range, measure, mtype })); } catch { /* optional */ }
    },
    // Things to tear down before the page is redrawn (e.g. the PDF viewer).
    disposers: [],
    setChart(c) { if (app.chart) app.chart.destroy(); app.chart = c; },
    toast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg; t.hidden = false;
      clearTimeout(app._toast); app._toast = setTimeout(() => { t.hidden = true; }, 3500);
    },
    go(view, params) {
      app.view = view; app.params = params || {};
      document.getElementById('page').scrollTop = 0;
      app.render();
    },
    render() {
      app.setChart(null);
      IRV.uncKinds.clear();
      for (const d of app.disposers.splice(0)) { try { d(); } catch { /* already gone */ } }
      const main = document.getElementById('main');
      const page = document.getElementById('page');
      const y = page.scrollTop;
      main.textContent = '';
      for (const b of document.querySelectorAll('#nav .nv[data-view]')) b.classList.toggle('on', b.dataset.view === app.view);
      if (IRV.reportView) IRV.reportView();
      const waiting = app.ds && app.ds.candidates ? app.ds.candidates.length : 0;
      document.getElementById('nav-cand').textContent = 'Candidates' + (waiting ? ' · ' + waiting : '');
      document.getElementById('nav-data').classList.toggle('busy', !!(app.upd.run && app.upd.run.running));
      const fresh = app.view === 'news' || !IRV.newsNewCount ? 0 : IRV.newsNewCount();
      document.getElementById('nav-news').textContent = 'News' + (fresh ? ' · ' + fresh : '');
      document.getElementById('asof').textContent = app.model && app.model.latest ? 'Latest ' + app.model.latest : '';
      const ds = app.ds;
      if (!ds) { main.append(h('div', { class: 'empty' }, 'Loading…')); return; }
      if (app.view === 'data') { IRV.views.data(main); page.scrollTop = y; return; }
      if (!ds.valid) {
        main.append(h('h1', { class: 'sm' }, 'No data found'), h('div', { class: 'path' }, ds.root),
          h('div', { class: 'rowf' }, h('button', { type: 'button', class: 'btn go', onclick: () => app.chooseFolder() }, 'Choose folder')),
          h('p', { class: 'foot' }, 'Choose the folder that contains data/ and sources/.'));
        return;
      }
      if (app.view === 'candidates') { IRV.views.candidates(main); page.scrollTop = y; return; }
      if (app.view === 'news') { IRV.views.news(main, app.params); page.scrollTop = y; return; }
      if (!app.model.products.length) { main.append(h('div', { class: 'empty' }, 'No products in data/products.')); return; }
      if (app.view !== 'explore' && app.view !== 'products') {
        const cases = IRV.cases(app.model);
        if (!cases.has(state.cur)) state.cur = IRV.sortCur(cases.keys())[0] || state.cur;
        if (cases.has(state.cur) && !cases.get(state.cur).has(state.term)) {
          const t = IRV.sortTerm(cases.get(state.cur));
          state.term = t.includes('10') ? '10' : t[t.length - 1];
        }
      }
      (IRV.views[app.view] || IRV.views.explore)(main, app.params);
      page.scrollTop = y;
    },
    async reload(announce) {
      let ds;
      try { ds = await api.loadDataset(); } catch (e) {
        ds = { root: '', valid: false, products: [], insurers: [], documents: {}, warnings: [String(e.message || e)] };
      }
      // Split off the candidates that are not enabled: nothing else sees them.
      const on = enabledIds();
      const everything = ds.products;
      ds.candidates = everything.filter((p) => p.candidate === true && !on.has(p.id));
      ds.enabledCandidates = everything.filter((p) => p.candidate === true && on.has(p.id));
      ds.products = everything.filter((p) => !(p.candidate === true && !on.has(p.id)));
      const files = {};
      await Promise.all(ds.products.map(async (p) => {
        const listed = IRV.arr(p.rate_series).map((s) => s && s.csv).filter(Boolean);
        try { files[p.id] = await api.readRates(p.id, listed); } catch { files[p.id] = []; }
      }));
      app.ds = ds;
      app.model = IRV.buildModel(ds, files);
      app.news = IRV.buildNews(ds);
      app.render();
      // The search list is rebuilt from the new set of products (or closed), never left as it was.
      if (state.searchType && !app.model.types.some((t) => t.key === state.searchType)) state.searchType = '';
      if (IRV.refreshSearch) IRV.refreshSearch();
      refreshCollector();
      if (announce) app.toast('Data reloaded');
    },
    // ids: one id or a list; on: true to enable, false to move back to candidates.
    async setCandidates(ids, on) {
      const set = enabledIds();
      for (const id of [].concat(ids)) { if (on) set.add(id); else set.delete(id); }
      try { localStorage.setItem(ENABLED, JSON.stringify([...set])); } catch { /* optional */ }
      await app.reload();
    },
    async chooseFolder() {
      const chosen = await api.chooseFolder();
      if (chosen) { app.ui = {}; app.upd.sel = null; app.view = 'explore'; await app.reload(true); }
    },
  });

  // One click from a rate to the saved page it came from.
  IRV.openSource = async function (o, label) {
    if (!o || !o.snapshot) { app.toast('No saved page is recorded for this rate.'); return; }
    // Whether this rate has passed the independent re-check travels as a flag and a date, never as text.
    const unchecked = !!(app.model && o.pid && IRV.isUnchecked(app.model, o.pid, o));
    const r = await api.openSource({ snapshot: o.snapshot, retrieved: o.retrieved, url: o.src, label, find: o.v == null ? '' : o.v.toFixed(2),
      unchecked, verified: app.model && o.pid ? IRV.verifiedThrough(app.model, o.pid) : '',
      // The row's own dimensions, so the window can point at this rate rather than the first equal figure.
      dims: { plan: o.plan || '', currency: o.currency || '', term: o.term || '', age: o.age || '', sex: o.sex || '', date: o.date || '' } });
    if (!r.ok) app.toast(r.error);
  };

  // ---------- collector progress (Data page) ----------
  async function refreshCollector() {
    const u = app.upd;
    try {
      const info = await api.collectorInfo();
      u.info = info;
      if (info.run && !u.run) u.run = info.run;
      if (u.sel) for (const id of [...u.sel]) if (!info.insurers.some((i) => i.id === id)) u.sel.delete(id);
      if (u.newsSel) for (const id of [...u.newsSel]) if (!(info.news || []).some((i) => i.id === id)) u.newsSel.delete(id);
    } catch (e) {
      u.info = { ok: false, message: String(e.message || e), insurers: [] };
    }
    if (app.view === 'data') app.render();
  }
  let timer = 0;
  api.onCollectorProgress((ev) => {
    const u = app.upd;
    if (ev.kind === 'start') u.run = ev.run;
    else if (!u.run) return;
    else if (ev.kind === 'line') { if (u.run.log.length < 5000) u.run.log.push(ev.text); }
    else if (ev.kind === 'conflict') u.run.conflicts.push({ insurer: ev.insurer, text: ev.text });
    else if (ev.kind === 'status') { if (u.run.insurers[ev.insurer]) u.run.insurers[ev.insurer] = { state: ev.state, message: ev.message, newPoints: ev.newPoints }; }
    else if (ev.kind === 'end') { u.run = ev.run; app.reload().then(() => app.toast('Update finished · data reloaded')); return; }
    // A timer, not requestAnimationFrame: rAF stops while the window is hidden.
    if (!timer) timer = setTimeout(() => { timer = 0; if (app.view === 'data') app.render(); else document.getElementById('nav-data').classList.add('busy'); }, 150);
  });

  // ---------- navigation and search ----------
  for (const b of document.querySelectorAll('#nav .nv[data-view]')) b.addEventListener('click', () => app.go(b.dataset.view));
  const search = document.getElementById('search'), results = document.getElementById('results');
  const typesOf = (p) => new Set([p.product_type, ...(app.model.byProduct.get(p.id) || []).map((s) => s.type)].filter(Boolean));
  function matches(p, q) {
    if (state.searchType && !typesOf(p).has(state.searchType)) return false;
    const ins = app.model.insurers.get(p.insurer_id) || {};
    const hay = [p.name_en, p.name_ja, p.policy_name_ja, p.id, ins.name_en, ins.name_ja, IRV.categoryEn(p.category), ...[...typesOf(p)].map(app.model.typeLabel), ...IRV.arr(p.currencies)]
      .map(IRV.str).join(' ').toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
  }
  function showResults() {
    if (!app.model) return;
    const q = search.value.trim();
    const hits = app.model.products.filter((p) => matches(p, q));
    results.textContent = '';
    // Filter by product type; mousedown keeps the focus in the search box.
    const used = app.model.types.filter((t) => app.model.products.some((p) => typesOf(p).has(t.key)));
    results.append(h('div', { class: 'types' }, [{ key: '', label: 'All types' }, ...used].map((t) => h('span', {
      class: 'tg' + (state.searchType === t.key ? ' on' : ''), role: 'button', 'data-type': t.key,
      onmousedown: (e) => { e.preventDefault(); state.searchType = t.key; showResults(); } }, t.label))));
    let lastIns = null;
    hits.forEach((p, i) => {
      if (p.insurer_id !== lastIns) {
        lastIns = p.insurer_id;
        results.append(h('div', { class: 'grp' }, IRV.insurerEn(app.model.insurers.get(p.insurer_id), p.insurer_id)));
      }
      results.append(h('button', { type: 'button', class: i === 0 && q ? 'cur' : '', 'data-id': p.id,
        title: IRV.nameEn(p), onmousedown: (e) => { e.preventDefault(); pick(p.id); } }, IRV.shortEn(p), h('small', null, p.product_type ? app.model.typeLabel(p.product_type) : IRV.categoryEn(p.category))));
    });
    if (!hits.length) results.append(h('div', { class: 'grp' }, 'No match'));
    results.hidden = false;
  }
  function pick(id) { results.hidden = true; search.value = ''; search.blur(); app.go('product', { id }); }
  IRV.refreshSearch = () => { if (!results.hidden && document.activeElement === search) showResults(); else { results.hidden = true; results.textContent = ''; } };
  search.addEventListener('focus', showResults);
  search.addEventListener('input', showResults);
  search.addEventListener('blur', () => { results.hidden = true; });
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { search.value = ''; search.blur(); }
    if (e.key === 'Enter') { const first = results.querySelector('button[data-id]'); if (first) pick(first.dataset.id); }
  });

  api.onMenu((action) => { if (action === 'reload') app.reload(true); if (action === 'choose-folder') app.chooseFolder(); if (action === 'toggle-chat' && IRV.chat) IRV.chat.toggle(); });
  // Chart colours come from CSS variables, so redraw when the system theme flips.
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => app.render());

  app.reload();
})();
