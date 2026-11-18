// English versions in the document viewer: the JP / EN / side-by-side switch, the Translate control, the progress
// strip, per-page marks and the review popover. The original document stays the scrolling structure; an English
// page is drawn over (or beside) its original, so switching, zooming and pages arriving never move the reader.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h } = IRV;
  const api = window.api.translation;
  const pageKey = (file, n) => file + '|' + n;
  const enCache = new Map(); // file|n -> Promise<{ page, text }>
  let active = null;         // the viewer on screen, if any
  IRV.viewMode = 'jp';

  // One translated page (a single-page PDF), loaded through the app's guarded access.
  function enPage(file, n) {
    const k = pageKey(file, n);
    if (!enCache.has(k)) {
      enCache.set(k, (async () => {
        const res = await api.readPage(file, n);
        if (!res || !res.ok) throw new Error((res && res.error) || 'No English version.');
        const pdf = await IRV.pdfOpen(res.data);
        const page = await pdf.getPage(1);
        const tc = await page.getTextContent();
        let text = '';
        for (const it of tc.items) { if (typeof it.str !== 'string') continue; text += it.str; text += it.hasEOL ? '\n' : ''; }
        return { page, tc, text: text.replace(/[ \t]+\n/g, '\n').trim() };
      })());
      enCache.get(k).catch(() => enCache.delete(k));
      if (enCache.size > 80) enCache.delete(enCache.keys().next().value);
    }
    return enCache.get(k);
  }
  IRV.enText = async (file, n) => (await enPage(file, n)).text;
  IRV.translationStatus = (file) => api.status(file);

  api.onProgress((ev) => {
    if (ev.kind === 'progress' && ev.page && ev.stage === 'page') enCache.delete(pageKey(ev.file, ev.page));
    if (active) active.onProgress(ev);
    if (ev.kind === 'end' && IRV.refreshDocMarks) IRV.refreshDocMarks();
  });
  IRV.trReload = () => { if (active) active.reload(true); };

  // ctx: { mount, file, bar, body, container, inner, railBody, pdfViewer, eventBus, pdf, pdfjs, go, findIn, findCnt }
  IRV.pdfTranslate = function (ctx) {
    const { file, pdfViewer, inner, container } = ctx;
    const total = ctx.pdf.numPages;
    let dead = false, st = { available: false, message: '', pages: {}, job: null }, mode = IRV.viewMode;
    let jobPages = {}, jobOn = false, jobMine = false, jobDone = 0, jobOf = 0, jobStage = '', choice = 'page', note = '';
    const visible = new Set();

    const info = (n) => st.pages[n] || { has: false, status: '' };
    const jobState = (n) => {
      if (!jobOn || !jobMine) return '';
      const j = jobPages[n];
      if (j && j.stage === 'page' && j.state === 'done') return '';
      if (j && j.state === 'failed') return '';
      if (j) return 'translating';
      return jobAll && !info(n).has ? 'queued' : '';
    };
    let jobAll = false;

    // ---------- toolbar: view switch, Translate ----------
    const modeBtns = [['jp', 'Japanese', 'Original'], ['en', 'English', 'English where translated'], ['sbs', 'Side by side', 'Original left, English right']]
      .map(([id, label, title]) => h('button', { type: 'button', class: 'tg', title, 'data-pv': 'mode-' + id, onclick: () => setMode(id) }, label));
    const trBtn = h('button', { type: 'button', class: 'pv-b', title: 'Translate this document into English', 'data-pv': 'translate', onclick: () => togglePanel() }, 'Translate');
    const grp = h('span', { class: 'pv-grp pv-lang' }, h('span', { class: 'pv-seg' }, modeBtns), trBtn);
    ctx.bar.insertBefore(grp, ctx.bar.querySelector('.pv-findgrp'));
    const panel = h('div', { class: 'pv-panel', hidden: '' });
    const strip = h('div', { class: 'pv-strip', hidden: '' });
    ctx.mount.insertBefore(panel, ctx.body);
    ctx.mount.insertBefore(strip, ctx.body);

    function drawPanel() {
      panel.textContent = '';
      const n = pdfViewer.currentPageNumber;
      if (!st.available) { panel.append(h('span', null, st.message || 'Translation is not available.')); return; }
      const mins = Math.max(3, Math.round(total * 10 / 24));
      const line = choice === 'page'
        ? `Translates page ${n} with your Claude sign-in: about 2–3 minutes, seconds if it was done before.`
        : `Translates all ${total} pages with your Claude sign-in, page ${n} first: about ${mins} minutes the first time, less when pages are cached.`;
      panel.append(
        h('span', { class: 'grp' }, [['page', 'This page'], ['all', 'Whole document']].map(([id, label]) => h('button', { type: 'button', class: 'tg' + (choice === id ? ' on' : ''),
          'data-pv': 'choice-' + id, onclick: () => { choice = id; drawPanel(); } }, label))),
        h('span', { class: 'line' }, line, ' A working machine translation for your own use.'),
        h('button', { type: 'button', class: 'btn go', 'data-pv': 'start', disabled: jobOn, onclick: start }, 'Start'),
        h('button', { type: 'button', class: 'pv-b', onclick: () => togglePanel(false) }, 'Close'));
    }
    function togglePanel(open) {
      panel.hidden = open === undefined ? !panel.hidden : !open;
      if (!panel.hidden) drawPanel();
    }
    async function start() {
      const r = await api.start(file, choice, pdfViewer.currentPageNumber);
      if (!r.ok) { note = r.error; drawStrip(); return; }
      togglePanel(false);
      if (mode === 'jp') setMode('en');
    }

    // ---------- progress strip ----------
    function drawStrip() {
      strip.textContent = '';
      if (jobOn && jobMine) {
        strip.append(h('span', null, h('b', null, 'Translating'), ` · ${jobDone} / ${jobOf || '…'} pages`, jobStage ? ' · ' + jobStage : ''),
          h('button', { type: 'button', class: 'pv-b', 'data-pv': 'cancel', onclick: async () => { const r = await api.cancel(); if (!r.ok) { note = r.error; drawStrip(); } } }, 'Cancel'));
      } else if (jobOn) {
        strip.append(h('span', null, 'Another document is being translated.'));
      } else if (note) {
        strip.append(h('span', null, note), h('button', { type: 'button', class: 'pv-b', onclick: () => { note = ''; drawStrip(); } }, 'Dismiss'));
      }
      strip.hidden = !strip.firstChild;
    }

    // ---------- pages: English overlay and marks ----------
    const pageDiv = (n) => inner.querySelector(`.page[data-page-number="${n}"]`);
    function badge(div, n) {
      let b = div.querySelector(':scope > .tr-badge');
      const i = info(n), js = jobState(n);
      let text = '', kind = '';
      if (mode !== 'jp') {
        if (js === 'translating') { text = 'Translating…'; kind = 'busy'; }
        else if (js === 'queued') { text = 'Queued'; kind = 'busy'; }
        else if (i.has && i.status === 'review') { const pl = (k, w) => `${k} ${w}${k === 1 ? '' : 's'}`;
          text = `Review · ${pl(i.errors || 0, 'error')}, ${pl(i.warnings || 0, 'warning')}`; kind = 'review'; }
        else if (!i.has && (i.status === 'failed' || i.status === 'untranslated')) { text = 'Not translated' + (i.reason ? ': ' + i.reason : ' (failed)'); kind = 'failed'; }
        else if (!i.has) { text = 'Not translated yet'; kind = 'none'; }
      }
      if (!text) { if (b) b.remove(); return; }
      if (!b) { b = h('div', { class: 'tr-badge' }); div.append(b); }
      if (b.dataset.text === text && b.dataset.mode === mode) return;
      b.dataset.text = text; b.dataset.mode = mode;
      b.className = 'tr-badge ' + kind + (mode === 'sbs' ? ' side' : '');
      b.textContent = '';
      if (kind === 'review') {
        b.append(h('button', { type: 'button', 'data-pv': 'review', title: 'What to check on this page', onclick: (e) => { e.stopPropagation(); popover(b, n); } }, text));
      } else b.append(text);
    }
    async function popover(b, n) {
      const old = b.querySelector('.tr-pop');
      if (old) { old.remove(); return; }
      const pop = h('div', { class: 'tr-pop' }, 'Loading…');
      b.append(pop);
      const r = await api.checks(file, n);
      pop.textContent = '';
      pop.append(h('div', { class: 'k' }, `Page ${n}: what to check`));
      if (!r.ok || !r.items.length) pop.append(h('div', null, 'The checks left no notes for this page.'));
      else pop.append(h('ul', null, r.items.map((x) => h('li', { class: x.severity }, h('b', null, x.severity === 'error' ? 'Error' : 'Warning', x.count > 1 ? ' ×' + x.count : ''), ' ', x.message))));
      pop.append(h('div', { class: 'f' }, 'The page is shown as translated; these are places to compare with the original.'));
    }
    const closePops = (e) => { if (!e.target.closest || !e.target.closest('.tr-badge')) for (const p of inner.querySelectorAll('.tr-pop')) p.remove(); };
    document.addEventListener('click', closePops);

    let query = '';
    function highlight(ov) {
      for (const s of ov.querySelectorAll('.textLayer .highlight')) s.classList.remove('highlight', 'selected');
      if (!query) return;
      for (const s of ov.querySelectorAll('.textLayer span')) if (s.textContent.toLowerCase().includes(query)) s.classList.add('highlight');
    }
    async function ensure(n) {
      const div = pageDiv(n);
      if (!div || dead) return;
      badge(div, n);
      let ov = div.querySelector(':scope > .en-ov');
      const want = mode !== 'jp' && (info(n).has || mode === 'sbs');
      if (!want) { if (ov) ov.remove(); return; }
      if (!ov) { ov = h('div', { class: 'en-ov' }); div.append(ov); }
      ov.classList.toggle('side', mode === 'sbs');
      const w = div.clientWidth, hh = div.clientHeight;
      const key = `${w}x${hh}:${info(n).has ? 'en' : 'none'}`;
      if (ov.dataset.key === key) { highlight(ov); return; }
      ov.dataset.key = key;
      if (!info(n).has) { ov.textContent = ''; ov.classList.add('blank'); return; }
      ov.classList.remove('blank');
      try {
        const { page, tc } = await enPage(file, n);
        if (dead || ov.dataset.key !== key || !ov.isConnected) return;
        const vp = page.getViewport({ scale: w / page.getViewport({ scale: 1 }).width });
        const dpr = Math.min(2.5, window.devicePixelRatio || 1);
        const canvas = h('canvas', { width: String(Math.floor(vp.width * dpr)), height: String(Math.floor(vp.height * dpr)) });
        await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
        if (dead || ov.dataset.key !== key) return;
        const tl = h('div', { class: 'textLayer' });
        ov.textContent = '';
        ov.append(canvas, tl);
        try { await new ctx.pdfjs.TextLayer({ textContentSource: tc, container: tl, viewport: vp }).render(); } catch { /* the page still shows; it is just not selectable */ }
        highlight(ov);
      } catch { ov.dataset.key = ''; }
    }
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        const n = Number(en.target.dataset.pageNumber);
        if (en.isIntersecting) { visible.add(n); void ensure(n); } else {
          visible.delete(n);
          const ov = en.target.querySelector(':scope > .en-ov');   // keep memory down: far pages drop their drawing
          if (ov) { ov.textContent = ''; ov.dataset.key = ''; }
        }
      }
    }, { root: container, rootMargin: '900px 0px' });
    for (const d of inner.querySelectorAll('.page')) io.observe(d);
    let refreshTimer = 0;
    const refresh = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => { for (const n of visible) void ensure(n); markRail(); }, 120); };
    ctx.eventBus.on('pagerendered', refresh);
    ctx.eventBus.on('scalechanging', refresh);
    const ro = new ResizeObserver(refresh);
    ro.observe(container);

    function markRail() {
      for (const t of ctx.railBody.querySelectorAll('.pv-thumb')) {
        const n = Number(t.dataset.page), i = info(n), js = jobState(n);
        const text = js === 'translating' ? 'translating' : js === 'queued' ? 'queued' : i.has ? (i.status === 'review' ? 'EN · review' : 'EN')
          : i.status === 'failed' || i.status === 'untranslated' ? 'failed' : '';
        let m = t.querySelector('.tm');
        if (!text) { if (m) m.remove(); continue; }
        if (!m) { m = h('i', { class: 'tm' }); t.append(m); }
        m.textContent = text;
        m.className = 'tm ' + text.replace(/[^a-z]/gi, '').toLowerCase();
      }
    }

    function setMode(m) {
      const was = mode;
      mode = IRV.viewMode = m;
      modeBtns.forEach((b) => b.classList.toggle('on', b.dataset.pv === 'mode-' + m));
      inner.classList.toggle('sbs', m === 'sbs');
      if ((m === 'sbs') !== (was === 'sbs')) {
        const page = pdfViewer.currentPageNumber;
        pdfViewer.currentScaleValue = 'page-width';
        afterFit('page-width');
        pdfViewer.currentPageNumber = page;
      }
      if (ctx.findIn.value) ctx.refind();
      refresh();
    }
    // In side-by-side two pages share the width.
    function afterFit(kind) {
      if (mode !== 'sbs') return;
      const first = inner.querySelector('.page');
      if (!first) return;
      const room = container.clientWidth - 44;
      if (kind === 'page-width' || first.clientWidth * 2 + 14 > room) pdfViewer.currentScale = pdfViewer.currentScale * (room - 14) / (2 * first.clientWidth);
    }

    // ---------- search in the English pages ----------
    let matches = [], cur = -1, lastQ = '';
    const usesEnglish = (q) => mode === 'en' || (mode === 'sbs' && /^[\x00-\x7f]*$/.test(q));
    async function find(type, back, clearOnly) {
      if (clearOnly) { query = ''; matches = []; cur = -1; lastQ = ''; refresh(); return; }
      const q = ctx.findIn.value.trim().toLowerCase();
      query = q;
      if (!q) { matches = []; cur = -1; lastQ = ''; ctx.findCnt.textContent = ''; refresh(); return; }
      if (type !== 'again' || q !== lastQ) {
        lastQ = q; matches = [];
        const pages = Object.keys(st.pages).map(Number).filter((n) => info(n).has).sort((a, b) => a - b);
        for (const n of pages) {
          let text = '';
          try { text = (await IRV.enText(file, n)).toLowerCase(); } catch { continue; }
          if (ctx.findIn.value.trim().toLowerCase() !== q || dead) return;
          let i = text.indexOf(q);
          while (i >= 0) { matches.push(n); i = text.indexOf(q, i + q.length); }
        }
        const here = pdfViewer.currentPageNumber;
        cur = matches.findIndex((n) => n >= here);
        if (cur < 0) cur = 0;
      } else if (matches.length) cur = (cur + (back ? -1 : 1) + matches.length) % matches.length;
      ctx.findCnt.textContent = matches.length ? `${cur + 1} / ${matches.length}` : 'No match';
      if (matches.length) ctx.go(matches[cur]);
      refresh();
    }

    // ---------- state from the engine ----------
    async function reload(redraw) {
      const s = await api.status(file).catch(() => null);
      if (dead || !s || !s.ok) return;
      st = s;
      jobOn = !!(s.job && s.job.running); jobMine = !!(s.job && s.job.mine);
      if (jobMine) { jobPages = s.job.pages || {}; jobDone = s.job.done; jobOf = s.job.of; jobStage = s.job.stage; }
      trBtn.classList.toggle('on', jobOn && jobMine);
      drawStrip();
      if (!panel.hidden) drawPanel();
      if (redraw) for (const k of [...enCache.keys()]) if (k.startsWith(file + '|')) enCache.delete(k);
      if (redraw) for (const ov of inner.querySelectorAll('.en-ov')) ov.dataset.key = '';
      refresh();
    }
    function onProgress(ev) {
      if (dead) return;
      if (ev.file !== file) { if (ev.kind === 'start') { jobOn = true; jobMine = false; } if (ev.kind === 'end') jobOn = false; drawStrip(); return; }
      if (ev.kind === 'start') {
        jobOn = true; jobMine = true; jobPages = {}; jobDone = 0; jobOf = ev.of || 0; jobStage = 'starting'; jobAll = ev.mode === 'all'; note = '';
      } else if (ev.kind === 'progress') {
        jobDone = ev.done; jobOf = ev.of;
        if (ev.page) {
          jobPages[ev.page] = { stage: ev.stage, state: ev.state };
          jobStage = `page ${ev.page}: ${ev.stage === 'page' ? 'finished' : ev.stage}`;
          if (ev.stage === 'page' && ev.state === 'done') {
            // The page is ready: show it now, and pick up its check counts when the run's result file is written.
            const ok = ev.status !== 'failed' && ev.status !== 'untranslated';
            st.pages[ev.page] = { ...info(ev.page), has: ok, status: ev.status || 'ok', reason: ok ? '' : ev.message };
            const ov = pageDiv(ev.page) && pageDiv(ev.page).querySelector(':scope > .en-ov');
            if (ov) ov.dataset.key = '';
          } else if (ev.state === 'failed') st.pages[ev.page] = { ...info(ev.page), status: 'failed', reason: ev.message };
        } else jobStage = ev.stage;
      } else if (ev.kind === 'end') {
        jobOn = false; jobMine = false; jobAll = false;
        note = ev.ok ? `Translation finished: ${ev.done} page${ev.done === 1 ? '' : 's'}.` : ev.message || 'The translation stopped.';
        if (ev.cancelled) for (const n of Object.keys(st.pages)) if (!st.pages[n].has) delete st.pages[n];
        void reload(false);
      }
      trBtn.classList.toggle('on', jobOn && jobMine);
      drawStrip(); refresh();
    }

    const self = { onProgress, reload, find, usesEnglish, afterFit, markRail,
      state: () => ({ mode, overlays: inner.querySelectorAll('.en-ov canvas').length, blanks: inner.querySelectorAll('.en-ov.blank').length,
        badges: [...inner.querySelectorAll('.tr-badge')].slice(0, 6).map((b) => b.dataset.text), strip: strip.hidden ? '' : strip.innerText.replace(/\s+/g, ' '),
        translated: Object.values(st.pages).filter((p) => p.has).length, rail: [...ctx.railBody.querySelectorAll('.tm')].slice(0, 30).map((m) => m.textContent),
        available: st.available, panel: panel.hidden ? '' : panel.innerText.replace(/\s+/g, ' '), pop: (inner.querySelector('.tr-pop') || {}).innerText || '' }),
      destroy() {
        dead = true; if (active === self) active = null;
        io.disconnect(); ro.disconnect(); clearTimeout(refreshTimer);
        document.removeEventListener('click', closePops);
      } };
    active = self;
    modeBtns.forEach((b) => b.classList.toggle('on', b.dataset.pv === 'mode-' + mode));
    inner.classList.toggle('sbs', mode === 'sbs');
    void reload(false).then(() => { if (mode === 'sbs') { pdfViewer.currentScaleValue = 'page-width'; afterFit('page-width'); } });
    return self;
  };
})();
