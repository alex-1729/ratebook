// The assistant pane: Claude Code in an xterm.js terminal, joined to the `claude` process the main process runs in
// the data folder. Adapted from the user's Tessera app. Also: what the window tells the assistant it is showing,
// and the jump cards made from [doc:<product>/<name> p.<n>] markers in its answers.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h } = IRV;
  const api = window.api.chat;
  const A = () => IRV.app;
  const STORE = 'irv-chat-v1';
  const saved = (() => { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch { return {}; } })();
  const pane = document.getElementById('chat');
  const host = document.getElementById('chat-term');
  const msg = document.getElementById('chat-msg');
  const cardsEl = document.getElementById('chat-cards');
  const sessionsEl = document.getElementById('chat-sessions');
  const navBtn = document.getElementById('nav-chat');
  let width = Math.max(320, Math.min(900, saved.width || 460));
  let term = null, fit = null, booted = false, ended = false;

  const persist = () => { try { localStorage.setItem(STORE, JSON.stringify({ open: !pane.hidden, width })); } catch { /* optional */ } };
  const setWidth = (w) => { width = Math.max(320, Math.min(Math.max(320, window.innerWidth - 420), w)); pane.style.width = width + 'px'; };

  // ---------- what the window shows (sent with every message by the prompt hook) ----------
  IRV.chatView = function () {
    const app = A(), m = app.model, st = app.state;
    const v = { screen: { explore: 'Explore', products: 'Products', markets: 'Markets', moves: 'Moves', chart: 'Chart', news: 'News', product: 'Product page', data: 'Data' }[app.view] || app.view,
      sample_case: { age: st.age, sex: st.sex } };
    let line = v.screen;
    if (!m) return { ...v, line };
    if (app.view === 'explore') {
      v.leaderboard = { product_type: st.type, currency: st.cur, tenor_years: Number(st.term), best_plan_per_product: !!st.best };
      if (st.asOf) v.leaderboard.as_of = st.asOf;
      line += `: product_type=${st.type}, currency=${st.cur}, tenor=${st.term}Y${st.best ? ', best plan per product' : ''}${st.asOf ? ', as of ' + st.asOf : ''}`;
    } else if (app.view === 'markets') {
      v.case = { currency: st.cur, tenor_years: Number(st.term) };
      if (st.asOf) v.case.as_of = st.asOf;
      line += `: ${st.cur} ${st.term}Y${st.asOf ? ', as of ' + st.asOf : ''}`;
    } else if (app.view === 'moves' && st.asOf) {
      v.as_of = st.asOf;
      line += `: as of ${st.asOf}`;
    } else if (app.view === 'news') {
      const f = st.news || {}, sid = app.params && app.params.story;
      if (sid) { v.story = sid; line += `: story=${sid}`; }
      else {
        v.news_filter = { insurer: f.ins || null, topic: f.cat || null, source: f.kind || null, days: f.range ? Number(f.range) : null, include_routine: !!f.all };
        line += `: insurer=${f.ins || 'all'}, topic=${f.cat || 'all'}, source=${f.kind || 'all'}, period=${f.range ? f.range + ' days' : 'all'}`;
      }
    } else if (app.view === 'product') {
      const p = m.byId.get(app.params.id);
      if (p) {
        const ui = IRV.productUi(p.id);
        v.product = { product_id: p.id, name_en: IRV.nameEn(p), tab: ui.tab };
        line += `: product_id=${p.id} (${IRV.nameEn(p)}), tab=${ui.tab}`;
        if (ui.tab === 'docs' && ui.doc) {
          const d = IRV.docsFor(p).find((x) => (x.file || x.url) === ui.doc);
          if (d) {
            const page = IRV.pdfLastPage.get(d.file) || 1;
            v.document = { name: d.name, label: IRV.docLabel(d), file: d.file || null, page };
            line += `, open document=${d.name} (${IRV.docLabel(d)}) at page ${page}${d.file ? ', file ' + d.file : ''}`;
          }
        }
      }
    }
    line += `; sample case age ${st.age} ${st.sex}`;
    return { ...v, line };
  };
  let viewTimer = 0;
  IRV.reportView = () => { clearTimeout(viewTimer); viewTimer = setTimeout(() => { try { api.view(IRV.chatView()); } catch { /* not ready */ } }, 120); };

  // Open a document in the embedded viewer at a page (the open_document tool and the jump cards).
  IRV.openDocument = function (pid, name, page) {
    const app = A();
    const p = app.model && app.model.byId.get(pid);
    if (!p) return { ok: false, error: `No product "${pid}".` };
    const all = IRV.docsFor(p).filter((d) => d.name === name);
    const d = all.find((x) => x.file && x.in_manifest) || all[0];
    if (!d) return { ok: false, error: `No document "${name}" for ${pid}. Call list_documents for the names.` };
    if (!d.file || !d.in_manifest) return { ok: false, error: 'That document has no local copy the viewer can open.' };
    const ui = IRV.productUi(p.id);
    ui.tab = 'docs'; ui.doc = d.file;
    const n = Math.max(1, Math.floor(Number(page) || 1));
    IRV.pdfLastPage.set(d.file, n);
    app.go('product', { id: p.id });
    return { ok: true, page: n, note: /^incomplete/i.test(d.status || '') ? 'The manifest marks this capture as incomplete; it may not open.' : d.via_wayback ? 'This is a Wayback Machine copy.' : '' };
  };

  // ---------- jump cards from citation markers ----------
  const cites = [];
  const MARK = /\[doc:\s*([A-Za-z0-9][A-Za-z0-9._-]*)\/([A-Za-z0-9][A-Za-z0-9._-]*)\s*p\.?\s*(\d{1,4})\s*\]/g;
  // Markers are read from what the terminal shows (not the raw stream, where spaces can be cursor moves).
  let scanTimer = 0;
  function scheduleScan() { clearTimeout(scanTimer); scanTimer = setTimeout(scan, 400); }
  function scan() {
    if (!term || !A().model) return;
    const b = term.buffer.active, lines = [];
    for (let i = Math.max(0, b.length - 400); i < b.length; i++) { const l = b.getLine(i); if (l) lines.push(l.translateToString(true)); }
    const text = lines.join('\n').replace(/\s*\n\s*/g, ' ');
    let hit, changed = false;
    MARK.lastIndex = 0;
    while ((hit = MARK.exec(text))) {
      const c = { pid: hit[1], name: hit[2], page: Number(hit[3]) };
      c.key = `${c.pid}/${c.name}/${c.page}`;
      if (!A().model.byId.has(c.pid) || cites.some((x) => x.key === c.key)) continue;
      cites.push(c); changed = true;
    }
    if (changed) { while (cites.length > 8) cites.shift(); drawCards(); }
  }
  function drawCards() {
    cardsEl.textContent = '';
    cardsEl.hidden = !cites.length;
    if (!cites.length) return;
    cardsEl.append(h('span', { class: 'lbl' }, 'Cited'));
    for (const c of cites) {
      const p = A().model.byId.get(c.pid);
      const d = p ? IRV.docsFor(p).find((x) => x.name === c.name) : null;
      cardsEl.append(h('button', { type: 'button', class: 'cite', title: `${p ? IRV.nameEn(p) : c.pid} · ${c.name}`, 'data-cite': c.key,
        onclick: () => { const r = IRV.openDocument(c.pid, c.name, c.page); if (!r.ok) A().toast(r.error); } },
      (d ? IRV.docLabel(d) : c.name) + ' p.' + c.page));
    }
    cardsEl.append(h('button', { type: 'button', class: 'pv-b', title: 'Clear the cited pages', onclick: () => { cites.length = 0; if (term) term.clear(); drawCards(); } }, 'Clear'));
    fitNow();
  }

  // ---------- terminal ----------
  function fitNow() {
    if (!term || pane.hidden || !host.clientWidth || !host.clientHeight) return;
    try { fit.fit(); } catch { return; }
    api.resize({ cols: term.cols, rows: term.rows });
  }
  function say(text) { msg.textContent = text; msg.hidden = !text; host.hidden = !!text; }

  async function launch() {
    ended = false;
    try { fit.fit(); } catch { /* not laid out yet */ }
    const r = await api.start({ cols: term.cols, rows: term.rows }).catch((e) => ({ running: false, message: e.message }));
    if (r && r.running === false) say(r.message || 'Claude Code could not be started.');
  }

  async function boot() {
    const st = await api.status().catch((e) => ({ ok: false, message: String(e.message || e) }));
    if (!st.ok) { booted = false; say(st.message); return; }
    say('');
    if (!window.Terminal || !window.FitAddon) { say('The terminal component could not be loaded.'); return; }
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    term = new window.Terminal({
      fontFamily: 'ui-monospace, Menlo, Consolas, "MS Gothic", monospace', fontSize: 12, lineHeight: 1.15, cursorBlink: true, scrollback: 5000,
      macOptionIsMeta: true, allowProposedApi: false,
      theme: { background: dark ? '#100f0d' : '#1c1b19', foreground: '#ece6da', cursor: '#e0805f', cursorAccent: '#1c1b19', selectionBackground: '#5a4a3f' },
    });
    fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(host);
    term.onData((d) => {
      if (ended) { if (d === '\r') void launch(); return; }
      api.input(d);
    });
    api.onData((d) => { term.write(d, scheduleScan); });
    api.onExit((info) => {
      ended = true;
      if (info && info.restart) { term.reset(); cites.length = 0; drawCards(); void launch(); return; }
      term.write('\r\n\x1b[2mClaude Code has ended. Press Enter to start it again.\x1b[0m\r\n');
    });
    new ResizeObserver(() => fitNow()).observe(host);
    await launch();
    term.focus();
  }

  function show(open) {
    pane.hidden = !open;
    navBtn.classList.toggle('on', open);
    persist();
    if (!open) return;
    setWidth(width);
    if (!booted) { booted = true; void boot(); } else { fitNow(); if (term) term.focus(); }
  }
  IRV.chat = {
    toggle: () => show(pane.hidden),
    open: () => show(true),
    // For the test walk-through: type at the prompt, and read what the terminal shows.
    type: (text, enter) => { api.input(String(text)); if (enter) setTimeout(() => api.input('\r'), 150); },
    screenText: () => {
      if (!term) return msg.hidden ? '' : msg.textContent;
      const b = term.buffer.active, out = [];
      for (let i = 0; i < b.length; i++) { const l = b.getLine(i); if (l) out.push(l.translateToString(true)); }
      return out.join('\n');
    },
    cites: () => cites.map((c) => c.key),
  };

  // ---------- header: new conversation, conversations list, close, resize ----------
  navBtn.addEventListener('click', () => IRV.chat.toggle());
  document.getElementById('chat-close').addEventListener('click', () => show(false));
  document.getElementById('chat-new').addEventListener('click', async () => { sessionsEl.hidden = true; if (term) await api.newConversation(); else show(true); });
  async function drawSessions() {
    const list = await api.sessions().catch(() => []);
    sessionsEl.textContent = '';
    if (!list.length) { sessionsEl.append(h('div', { class: 'sub' }, 'No saved conversations for this data folder.')); return; }
    for (const c of list.slice(0, 40)) {
      let armed = false;
      const del = h('button', { type: 'button', class: 'pv-b', onclick: async () => {
        if (!armed) { armed = true; del.textContent = 'Delete for good?'; return; }
        const r = await api.deleteSession(c.id); if (!r.ok) A().toast(r.error); drawSessions();
      } }, 'Delete');
      sessionsEl.append(h('div', { class: 'sess' + (c.current ? ' on' : '') },
        h('div', { class: 't' }, c.title), h('div', { class: 'm' }, `${c.last ? new Date(c.last).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : ''} · ${c.messages} messages${c.current ? ' · open' : ''}`),
        h('div', { class: 'acts' }, c.current ? null : h('button', { type: 'button', class: 'pv-b', onclick: async () => {
          const r = await api.openSession(c.id); if (!r.ok) A().toast(r.error); sessionsEl.hidden = true;
        } }, 'Open'), del)));
    }
  }
  document.getElementById('chat-list').addEventListener('click', () => { sessionsEl.hidden = !sessionsEl.hidden; if (!sessionsEl.hidden) drawSessions(); else fitNow(); });

  const grip = document.getElementById('chat-grip');
  grip.addEventListener('pointerdown', (e) => {
    grip.setPointerCapture(e.pointerId);
    const move = (ev) => setWidth(window.innerWidth - ev.clientX);
    const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); persist(); fitNow(); };
    grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
  });
  window.addEventListener('resize', () => { if (!pane.hidden) setWidth(width); });

  if (saved.open) show(true);
})();
