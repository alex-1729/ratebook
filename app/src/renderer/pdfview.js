// Embedded PDF viewer on PDF.js (bundled locally under node_modules, worker
// included). The file's bytes and PDF.js's support files arrive through the
// preload API; the page itself fetches nothing.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h } = IRV;
  const BASE = new URL('../../node_modules/pdfjs-dist/', location.href).href;
  const lastPage = new Map(); // file -> page, for this session
  IRV.pdfLastPage = lastPage;

  let libP = null;
  function lib() {
    if (!libP) {
      libP = (async () => {
        const pdfjs = await import(BASE + 'build/pdf.mjs');
        globalThis.pdfjsLib = pdfjs; // the viewer components look for this
        pdfjs.GlobalWorkerOptions.workerSrc = BASE + 'build/pdf.worker.mjs';
        const viewer = await import(BASE + 'web/pdf_viewer.mjs');
        return { pdfjs, viewer };
      })();
    }
    return libP;
  }

  // Character maps (needed for Japanese), standard fonts and decoders come from the app, over IPC.
  class IpcBinaryDataFactory {
    async fetch({ kind, filename }) {
      const data = await window.api.pdfAsset(kind, filename);
      if (!data) throw new Error(`Unable to load ${kind} data: ${filename}`);
      return data instanceof Uint8Array ? data : new Uint8Array(data);
    }
  }

  // Page text for the assistant's read_document and search_document tools: the same engine and the same
  // manifest-only file access as the viewer. Extracted pages are kept for the session.
  const textCache = new Map(); // file -> { pdf, pages: Map(n -> text) }
  async function textDoc(file) {
    if (textCache.has(file)) return textCache.get(file);
    const [{ pdfjs }, res] = await Promise.all([lib(), window.api.readDocument(file)]);
    if (!res || !res.ok) throw new Error(res && res.error ? res.error : 'Could not read the file.');
    const pdf = await pdfjs.getDocument({ data: res.data instanceof Uint8Array ? res.data : new Uint8Array(res.data),
      cMapUrl: 'cmaps/', cMapPacked: true, standardFontDataUrl: 'standard_fonts/', wasmUrl: BASE + 'wasm/',
      useWorkerFetch: false, BinaryDataFactory: IpcBinaryDataFactory, isEvalSupported: false, enableXfa: false }).promise;
    const entry = { pdf, pages: new Map() };
    textCache.set(file, entry);
    if (textCache.size > 4) { const first = textCache.keys().next().value; textCache.get(first).pdf.destroy(); textCache.delete(first); }
    return entry;
  }
  async function pageText(entry, n) {
    if (entry.pages.has(n)) return entry.pages.get(n);
    const page = await entry.pdf.getPage(n);
    const tc = await page.getTextContent();
    let out = '';
    for (const it of tc.items) { if (typeof it.str !== 'string') continue; out += it.str; if (it.hasEOL) out += '\n'; }
    out = out.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    entry.pages.set(n, out);
    return out;
  }
  IRV.pdfText = { doc: textDoc, page: pageText };
  // Opens PDF bytes with the app's settings (used for translated pages).
  IRV.pdfOpen = async (data) => {
    const { pdfjs } = await lib();
    return pdfjs.getDocument({ data: data instanceof Uint8Array ? data : new Uint8Array(data), cMapUrl: 'cmaps/', cMapPacked: true,
      standardFontDataUrl: 'standard_fonts/', wasmUrl: BASE + 'wasm/', useWorkerFetch: false, BinaryDataFactory: IpcBinaryDataFactory,
      isEvalSupported: false, enableXfa: false }).promise;
  };

  // Builds the viewer into `mount` for the document `file` (path relative to the data folder).
  // Returns { destroy }.
  IRV.pdfViewer = function (mount, file, hint) {
    let dead = false, task = null, pdfViewer = null, eventBus = null, pdf = null, io = null, tr = null;
    const say = (msg) => { status.textContent = msg; };
    const btn = (label, title, fn, id) => h('button', { type: 'button', class: 'pv-b', title, 'aria-label': title, 'data-pv': id || null, onclick: fn }, label);

    const pageIn = h('input', { type: 'text', inputmode: 'numeric', class: 'pv-page', 'aria-label': 'Page', value: '' });
    const pageOf = h('span', { class: 'pv-of' }, '');
    const zoomLbl = h('span', { class: 'pv-zoom' }, '');
    const findIn = h('input', { type: 'search', class: 'pv-find', placeholder: 'Find in document', 'aria-label': 'Find in document' });
    const findCnt = h('span', { class: 'pv-cnt' }, '');
    const status = h('div', { class: 'pv-status' }, 'Loading…');
    const railBody = h('div', { class: 'pv-railbody' });
    const railTabs = h('div', { class: 'pv-railtabs' });
    const rail = h('div', { class: 'pv-rail' }, railTabs, railBody);
    const inner = h('div', { class: 'pdfViewer' });
    const container = h('div', { class: 'pv-scroll', tabindex: '0' }, inner);
    const go = (n) => { if (pdfViewer && n >= 1 && n <= pdfViewer.pagesCount) pdfViewer.currentPageNumber = n; };
    const zoom = (f) => { if (pdfViewer) pdfViewer.currentScale = Math.max(0.25, Math.min(5, pdfViewer.currentScale * f)); };
    const fit = (mode) => { if (pdfViewer) { pdfViewer.currentScaleValue = mode; if (tr) tr.afterFit(mode); } };
    const find = (type, back) => {
      if (!eventBus) return;
      // In the English view the search runs over the English pages; otherwise over the original.
      if (tr && tr.usesEnglish(findIn.value)) {
        eventBus.dispatch('find', { source: mount, type: '', query: '', caseSensitive: false, entireWord: false, highlightAll: false, findPrevious: false, matchDiacritics: false });
        void tr.find(type, back);
        return;
      }
      if (tr) void tr.find('', false, true);
      if (!findIn.value) findCnt.textContent = '';
      eventBus.dispatch('find', { source: mount, type, query: findIn.value, caseSensitive: false, entireWord: false,
        highlightAll: true, findPrevious: !!back, matchDiacritics: false });
    };

    const bar = h('div', { class: 'pv-bar' },
      btn('Contents', 'Show or hide the side rail', () => { rail.hidden = !rail.hidden; }, 'rail'),
      h('span', { class: 'pv-grp' }, btn('‹', 'Previous page', () => go(pdfViewer.currentPageNumber - 1), 'prev'), pageIn, pageOf,
        btn('›', 'Next page', () => go(pdfViewer.currentPageNumber + 1), 'next')),
      h('span', { class: 'pv-grp' }, btn('−', 'Zoom out', () => zoom(1 / 1.15), 'out'), zoomLbl, btn('+', 'Zoom in', () => zoom(1.15), 'in'),
        btn('Fit width', 'Fit width', () => fit('page-width'), 'width'), btn('Fit page', 'Fit page', () => fit('page-fit'), 'fit')),
      h('span', { class: 'pv-grp pv-findgrp' }, findIn, findCnt, btn('↑', 'Previous match', () => find('again', true), 'fprev'), btn('↓', 'Next match', () => find('again', false), 'fnext')));
    const body = h('div', { class: 'pv-body' }, rail, h('div', { class: 'pv-stage' }, container, status));
    mount.append(bar, body);

    pageIn.addEventListener('change', () => { const n = parseInt(pageIn.value, 10); if (!isNaN(n)) go(n); pageIn.value = pdfViewer ? String(pdfViewer.currentPageNumber) : ''; });
    findIn.addEventListener('input', () => find(''));
    findIn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); find('again', e.shiftKey); }
      if (e.key === 'Escape') { findIn.value = ''; find(''); container.focus(); }
    });

    const onKey = (e) => {
      if (!pdfViewer || !mount.isConnected) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'f') { e.preventDefault(); findIn.focus(); findIn.select(); return; }
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      if (mod || e.altKey) return;
      if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); go(pdfViewer.currentPageNumber + 1); }
      else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); go(pdfViewer.currentPageNumber - 1); }
      else if (e.key === 'Home') { e.preventDefault(); go(1); }
      else if (e.key === 'End') { e.preventDefault(); go(pdfViewer.pagesCount); }
      else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoom(1.15); }
      else if (e.key === '-') { e.preventDefault(); zoom(1 / 1.15); }
    };
    document.addEventListener('keydown', onKey);

    function outlineList(items, depth) {
      return h('ul', { class: 'pv-outline' }, items.map((it) => h('li', null,
        h('button', { type: 'button', class: 'pv-ol', lang: 'ja', style: { paddingLeft: 6 + depth * 12 + 'px' },
          onclick: () => { if (it.dest) pdfViewer.linkService.goToDestination(it.dest); } }, it.title || '—'),
        it.items && it.items.length && depth < 3 ? outlineList(it.items, depth + 1) : null)));
    }
    // Thumbnails are drawn one at a time, nearest the current page first, so the rail fills in even for a long
    // document without drawing every page at once.
    let thumbRun = 0;
    function thumbs() {
      const wrap = h('div', { class: 'pv-thumbs' });
      const els = [];
      for (let n = 1; n <= pdf.numPages; n++) {
        const t = h('button', { type: 'button', class: 'pv-thumb', 'data-page': String(n), onclick: () => go(n) }, h('span', null, n));
        wrap.append(t); els.push(t);
      }
      const run = ++thumbRun;
      const drawOne = async (t) => {
        const page = await pdf.getPage(+t.dataset.page);
        const vp = page.getViewport({ scale: 120 / page.getViewport({ scale: 1 }).width });
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const canvas = h('canvas', { width: String(Math.floor(vp.width * dpr)), height: String(Math.floor(vp.height * dpr)) });
        canvas.style.width = vp.width + 'px'; canvas.style.height = vp.height + 'px';
        await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
        if (!dead && run === thumbRun) t.prepend(canvas);
      };
      (async () => {
        while (!dead && run === thumbRun) {
          const here = pdfViewer ? pdfViewer.currentPageNumber : 1;
          const next = els.filter((t) => !t.dataset.done).sort((a, b) => Math.abs(a.dataset.page - here) - Math.abs(b.dataset.page - here))[0];
          if (!next) break;
          next.dataset.done = '1';
          try { await drawOne(next); } catch { /* leave the number only */ }
        }
      })();
      return wrap;
    }
    function markThumb(n) {
      for (const t of railBody.querySelectorAll('.pv-thumb.on')) t.classList.remove('on');
      const t = railBody.querySelector(`.pv-thumb[data-page="${n}"]`);
      if (t) { t.classList.add('on'); t.scrollIntoView({ block: 'nearest' }); }
    }

    (async () => {
      try {
        const [{ pdfjs, viewer }, res] = await Promise.all([lib(), window.api.readDocument(file)]);
        if (dead) return;
        if (!res || !res.ok) { say(res && res.error ? res.error : 'Could not read the file.'); return; }
        eventBus = new viewer.EventBus();
        const linkService = new viewer.PDFLinkService({ eventBus });
        const findController = new viewer.PDFFindController({ eventBus, linkService });
        pdfViewer = new viewer.PDFViewer({ container, viewer: inner, eventBus, linkService, findController });
        linkService.setViewer(pdfViewer);
        eventBus.on('pagesinit', () => {
          pdfViewer.currentScaleValue = 'page-width';
          const p = lastPage.get(file);
          if (p && p <= pdfViewer.pagesCount) pdfViewer.currentPageNumber = p;
          pageIn.value = String(pdfViewer.currentPageNumber);
        });
        eventBus.on('pagechanging', (e) => { pageIn.value = String(e.pageNumber); lastPage.set(file, e.pageNumber); markThumb(e.pageNumber); if (IRV.reportView) IRV.reportView(); });
        eventBus.on('scalechanging', (e) => { zoomLbl.textContent = Math.round(e.scale * 100) + '%'; });
        const count = (m) => { if (tr && tr.usesEnglish(findIn.value)) return; findCnt.textContent = !findIn.value ? '' : m && m.total ? `${m.current} / ${m.total}` : 'No match'; };
        eventBus.on('updatefindmatchescount', (e) => count(e.matchesCount));
        eventBus.on('updatefindcontrolstate', (e) => count(e.matchesCount));

        task = pdfjs.getDocument({
          data: res.data instanceof Uint8Array ? res.data : new Uint8Array(res.data),
          cMapUrl: 'cmaps/', cMapPacked: true, standardFontDataUrl: 'standard_fonts/', wasmUrl: BASE + 'wasm/', // absolute: the worker imports its JS fallbacks from here
          useWorkerFetch: false, BinaryDataFactory: IpcBinaryDataFactory, isEvalSupported: false, enableXfa: false,
        });
        task.onPassword = () => { say('This PDF is password-protected.'); };
        pdf = await task.promise;
        if (dead) { task.destroy(); return; }
        pdfViewer.setDocument(pdf);
        linkService.setDocument(pdf, null);
        pageOf.textContent = '/ ' + pdf.numPages;
        status.hidden = true;

        // Side rail: the PDF's own outline when it has one, and page thumbnails.
        const outline = await pdf.getOutline().catch(() => null);
        if (dead) return;
        const views = [];
        if (outline && outline.length) views.push(['Outline', () => outlineList(outline, 0)]);
        views.push(['Pages', thumbs]);
        const show = (i) => {
          thumbRun++;
          railBody.textContent = '';
          railBody.append(views[i][1]());
          [...railTabs.children].forEach((b, k) => b.classList.toggle('on', k === i));
          if (views[i][0] === 'Pages') markThumb(pdfViewer.currentPageNumber);
          if (tr) tr.markRail();
        };
        views.forEach(([name], i) => railTabs.append(h('button', { type: 'button', class: 'tg', onclick: () => show(i) }, name)));
        show(0);
        if (IRV.pdfTranslate && !dead) {
          const { pdfjs: lib2 } = await lib();
          tr = IRV.pdfTranslate({ mount, file, bar, body, container, inner, railBody, pdfViewer, eventBus, pdf, pdfjs: lib2, go, findIn, findCnt,
            refind: () => find('') });
        }
      } catch (e) {
        if (!dead) { status.hidden = false; say('Could not open this PDF. ' + (hint || (e && e.message ? e.message : e))); }
      }
    })();

    // For the test walk-through: lets a script read the viewer's state.
    mount._pv = () => ({ page: pdfViewer ? pdfViewer.currentPageNumber : 0, pages: pdfViewer ? pdfViewer.pagesCount : 0,
      scale: pdfViewer ? pdfViewer.currentScale : 0, find: findCnt.textContent, status: status.hidden ? '' : status.textContent,
      rendered: inner.querySelectorAll('.canvasWrapper canvas').length, pageDivs: inner.querySelectorAll('.page').length, tr: tr ? tr.state() : null });

    return {
      destroy() {
        dead = true;
        if (tr) tr.destroy();
        document.removeEventListener('keydown', onKey);
        if (io) io.disconnect();
        try { if (pdfViewer) pdfViewer.setDocument(null); } catch { /* ignore */ }
        if (task) task.destroy().catch(() => {});
      },
    };
  };
})();
