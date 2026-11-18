// The assistant's app tools, answered in the window from the same model the screens are drawn from.
// Arguments have already been checked in the main process (src/chat/tools.js).
(function () {
  'use strict';
  const IRV = window.IRV;
  const A = () => IRV.app;
  const fail = (msg) => { throw new Error(msg); };
  const r2 = (v) => (v == null ? null : Math.round(v * 10000) / 10000);
  const termStr = (t) => String(t);

  function product(id) {
    const p = A().model.byId.get(id);
    return p || fail(`No product "${id}". Call list_products for the ids.`);
  }
  function insurerName(p) { return IRV.insurerEn(A().model.insurers.get(p.insurer_id), p.insurer_id); }
  function typesOf(p) {
    return [...new Set([p.product_type, ...(A().model.byProduct.get(p.id) || []).map((s) => s.type)].filter(Boolean))];
  }
  function point(o) {
    return o ? { effective_date: o.date, value_pct: r2(o.v), status: o.v == null ? 'not_offered' : 'published',
      via_wayback: o.wb, retrieved_date: o.retrieved, source_url: o.src, unchecked: o.pid ? IRV.isUnchecked(A().model, o.pid, o) : undefined } : null;
  }
  function seriesHead(s) {
    return { metric: s.metric, plan: s.plan, plan_label: IRV.planEn(s.plan, s.pid) || 'Standard', product_type: s.type,
      currency: s.currency, term_years: s.term === '' ? null : Number(s.term), age_band: s.age || null, sex: s.sex || null };
  }
  function docs(p) {
    return IRV.docsFor(p).map((d) => ({ name: d.name, label: IRV.docLabel(d), edition: IRV.docVariant(d) || null, title_ja: d.title_ja || null,
      file: d.file || null, viewable_in_app: !!(d.file && d.in_manifest), bytes: d.bytes || null,
      retrieved_date: d.retrieved_date || null, wayback_copy: !!d.via_wayback,
      captured: d.via_wayback && /^\d{8}/.test(d.capture_timestamp || '') ? d.capture_timestamp.replace(/^(\d{4})(\d{2})(\d{2}).*/, '$1-$2-$3') : null,
      incomplete_capture: /^incomplete/i.test(d.status || ''), manifest_status: d.status || null, original_url: d.url || null }));
  }

  async function docText(product_id, name) {
    const p = product(product_id);
    const all = IRV.docsFor(p).filter((x) => x.name === name);
    const d = all.find((x) => x.file && x.in_manifest) || all[0];
    if (!d) fail(`No document "${name}" for ${product_id}. Call list_documents for the names.`);
    if (!d.file || !d.in_manifest) fail('That document has no local copy listed in the manifest.');
    let entry;
    try { entry = await IRV.pdfText.doc(d.file); } catch (e) {
      fail(/^incomplete/i.test(d.status || '') ? 'This capture is marked incomplete in the manifest and cannot be opened.' : 'The document could not be opened: ' + e.message);
    }
    return { d, entry };
  }
  // Page numbers of a document that have an English version.
  async function enPages(d) {
    const s = await IRV.translationStatus(d.file);
    return new Set(s && s.ok ? Object.entries(s.pages).filter(([, p]) => p.has).map(([n]) => Number(n)) : []);
  }
  function docHead(product_id, d, entry) {
    return { product_id, name: d.name, label: IRV.docLabel(d), title_ja: d.title_ja || null, total_pages: entry.pdf.numPages,
      wayback_copy: !!d.via_wayback, incomplete_capture: /^incomplete/i.test(d.status || ''),
      cite_as: `[doc:${product_id}/${d.name} p.<page>]` };
  }

  const tools = {
    list_products({ product_type, insurer }) {
      const m = A().model;
      const out = m.products.filter((p) => (!insurer || p.insurer_id === insurer) && (!product_type || typesOf(p).includes(product_type)))
        .map((p) => ({ product_id: p.id, name_en: IRV.nameEn(p), name_ja: p.name_ja || null, insurer_id: p.insurer_id, insurer: insurerName(p),
          product_types: typesOf(p), currencies: IRV.arr(p.currencies), status: p.status || null,
          plans: [...new Set((m.byProduct.get(p.id) || []).map((s) => s.plan))] }));
      return { count: out.length, product_type_keys: m.types.map((t) => ({ key: t.key, label: t.label })), products: out };
    },

    get_product({ product_id }) {
      const p = product(product_id), m = A().model;
      const own = m.byProduct.get(p.id) || [];
      const plans = [...new Set(own.map((s) => s.plan))].map((pl) => {
        const ss = own.filter((s) => s.plan === pl);
        return { plan: pl, label: IRV.planEn(pl, p.id) || 'Standard', product_type: ss[0].type, product_type_label: m.typeLabel(ss[0].type),
          metrics: [...new Set(ss.map((s) => s.metric))], currencies: IRV.sortCur(new Set(ss.map((s) => s.currency))),
          terms_years: IRV.sortTerm(new Set(ss.map((s) => s.term))), age_bands: IRV.sortAge(new Set(ss.map((s) => s.age).filter(Boolean))) };
      });
      const facts = {};
      for (const [k, v] of Object.entries(p)) {
        if (['notes', 'metrics', 'pages', 'rate_series', 'documents', 'distributors'].includes(k) || v == null) continue;
        if (typeof v !== 'object' || Array.isArray(v)) facts[k] = v;
      }
      return { ...facts, name_en: IRV.nameEn(p), insurer: insurerName(p), product_type_label: p.product_type ? m.typeLabel(p.product_type) : null,
        notes: IRV.arr(p.notes).filter((n) => n && n !== 'placeholder'), metrics: Object.keys(p.metrics || {}).map((k) => ({ key: k, label: IRV.metricEn(k), label_ja: p.metrics[k] })),
        plans, distributors_count: IRV.arr(p.distributors).length, source_pages: IRV.arr(p.pages).map((x) => (x && x.url) || x).filter((x) => typeof x === 'string'),
        documents: docs(p) };
    },

    get_rates({ product_id, metric, plan, currency, term_years, age, sex, date, from, to }) {
      const p = product(product_id);
      let list = (A().model.byProduct.get(p.id) || []).filter((s) => s.metric === (metric || 'crediting_rate') &&
        (plan === undefined || s.plan === plan) && (!currency || s.currency === currency) &&
        (term_years === undefined || s.term === termStr(term_years)) && (age === undefined || IRV.ageMatches(s.age, age)) && (!sex || IRV.sexMatches(s.sex, sex)));
      if (!list.length) {
        const own = A().model.byProduct.get(p.id) || [];
        return { product_id: p.id, series: [], note: 'No series matches.', available: { metrics: [...new Set(own.map((s) => s.metric))],
          plans: [...new Set(own.map((s) => s.plan))], currencies: [...new Set(own.map((s) => s.currency))], terms_years: IRV.sortTerm(new Set(own.map((s) => s.term))) } };
      }
      const MAX_SERIES = 60, MAX_POINTS = 400;
      const total = list.length;
      list = list.slice(0, MAX_SERIES);
      let points = 0, truncated = total > MAX_SERIES;
      const series = list.map((s) => {
        const head = seriesHead(s);
        if (from || to) {
          const a = from ? IRV.toEpoch(from) : -Infinity, b = to ? IRV.toEpoch(to) : Infinity;
          let pts = s.obs.filter((o) => o.t >= a && o.t <= b);
          if (points + pts.length > MAX_POINTS) { pts = pts.slice(Math.max(0, pts.length - Math.max(0, MAX_POINTS - points))); truncated = true; }
          points += pts.length;
          return { ...head, points: pts.map(point) };
        }
        if (date) {
          const T = IRV.toEpoch(date);
          let idx = -1;
          for (let i = s.obs.length - 1; i >= 0; i--) if (s.obs[i].t <= T) { idx = i; break; }
          return { ...head, in_force_on: date, point: idx < 0 ? null : point(s.obs[idx]) };
        }
        const info = IRV.entryInfo(s);
        return { ...head, latest: point(info.last), previous: point(info.prev), change_pct_points: r2(info.d),
          previous_point_is_older_than_20_days: info.stale, not_refreshed_this_half_month: info.old };
      });
      return { product_id: p.id, name_en: IRV.nameEn(p), unit: 'percent per year', series_matching: total, series,
        ...(truncated ? { truncated: `Showing ${list.length} series${from || to ? ' and at most ' + MAX_POINTS + ' points (the most recent)' : ''}; narrow the filters for the rest.` } : {}) };
    },

    leaderboard({ product_type, currency, tenor, metric }) {
      const m = A().model, st = A().state;
      if (!m.types.some((t) => t.key === product_type)) fail(`Unknown product type "${product_type}". Keys: ${m.types.map((t) => t.key).join(', ')}.`);
      const b = IRV.board(m, { type: product_type, cur: currency, term: termStr(tenor), metric: metric || 'crediting_rate' }, { age: st.age, sex: st.sex });
      const rows = b.rows.map((r) => ({ rank: r.rank, tied: r.tied, rank_previous_period: r.rankWas, rank_change: r.rankChange == null ? 'new' : r.rankChange,
        product_id: r.p.id, product: IRV.shortEn(r.p), insurer: insurerName(r.p), plan: r.s.plan, plan_label: IRV.planEn(r.s.plan, r.p.id) || 'Standard',
        age_band: r.s.age || null, sex: r.s.sex || null, rate_pct: r2(r.o.v), change_vs_previous_period: r.d == null ? null : r2(r.d),
        as_of: r.o.date, via_wayback: r.o.wb, unchecked: r.unchecked,
        basis: r.s.derived === 'model' ? IRV.modelYieldNote(r.s) : r.s.derived ? 'The crediting rate, which the insurer states is also the net yield.' : undefined,
        over_govt_pct_points: r.over == null ? null : r2(r.over), govt_curve: r.govt ? IRV.govtLabel(r.govt) : null, govt_tenor_note: r.govt ? null : r.govtWhy }));
      return { product_type, product_type_label: m.typeLabel(product_type), currency, tenor_years: Number(tenor), measure: IRV.metricEn(b.spec.metric),
        rule: 'Only plans whose rate is in force today (inside the insurer\'s current rate period) are ranked. Change and rank change are against the period immediately before; null change = no rate for that period.',
        sample_case: { age: st.age, sex: st.sex, note: 'Used only where a product prices by age band or sex.' },
        leader: b.leader ? `${IRV.shortEn(b.leader.p)} ${IRV.planEn(b.leader.s.plan, b.leader.p.id)} ${b.leader.o.v}%` : null, spread_pct_points: b.spread,
        government_yield: b.govt ? { curve: IRV.govtLabel(b.govt), date: b.govt.date, par_yield_pct: b.govt.y, annual_effective_pct: r2(b.govt.ae), source: b.govt.source,
          rule: 'over_govt = rate less the annual-effective par yield of the nearest curve tenor, latest on or before the board date (within 14 days).' } : null,
        rates_up: b.up, rates_down: b.down, entries: rows.length, rows,
        not_ranked_no_current_rate: b.left.map((x) => ({ product: IRV.shortEn(x.p), plan_label: IRV.planEn(x.s.plan, x.p.id) || 'Standard', reason: x.reason, last_point: x.last ? x.last.date : null })),
        unchecked_note: 'unchecked = retrieved after the product\'s last independent re-check.' };
    },

    moves({ currency, insurer, metric }) {
      const m = A().model;
      const all = IRV.moves(m, metric || 'crediting_rate');
      const list = all.filter((x) => (!currency || x.s.currency === currency) && (!insurer || x.p.insurer_id === insurer));
      const row = (x) => ({ product_id: x.p.id, product: IRV.shortEn(x.p), insurer: insurerName(x.p), ...seriesHead(x.s),
        from_pct: r2(x.from.v), to_pct: r2(x.to.v), change_pct_points: r2(x.d), kind: x.kind === 'chg' ? 'changed' : x.kind === 'new' ? 'newly_offered' : 'suspended',
        previous_period_date: x.from.date, current_period_date: x.to.date, unchecked: IRV.isUnchecked(m, x.p.id, x.to) });
      const CAP = 60;
      const chg = list.filter((x) => x.kind === 'chg');
      return { latest_data_date: m.latest, measure: IRV.metricEn(metric || 'crediting_rate'),
        rule: 'Each series is compared only with the period immediately before the current one.',
        this_period: { count: chg.length, up: chg.filter((x) => x.d > 0).length, down: chg.filter((x) => x.d < 0).length, largest: chg.slice(0, CAP).map(row) },
        newly_offered_or_suspended: list.filter((x) => x.kind !== 'chg').slice(0, CAP).map(row),
        series_with_no_previous_period_point: all.noPrevious };
    },

    current_view() { return IRV.chatView(); },

    list_documents({ product_id }) {
      const p = product(product_id);
      return { product_id: p.id, name_en: IRV.nameEn(p), read_with: 'Read tool on "file" (relative to the data folder), using the pages parameter for ranges.', documents: docs(p) };
    },

    async read_document({ product_id, name, pages, language }) {
      const { d, entry } = await docText(product_id, name);
      const en = language === 'en' ? await enPages(d) : null;
      const [a, b] = pages.split('-').map(Number);
      const from = Math.max(1, a), to = Math.min(entry.pdf.numPages, b || a);
      if (from > entry.pdf.numPages) fail(`That document has ${entry.pdf.numPages} pages.`);
      if (to - from + 1 > 15) fail('Ask for at most 15 pages at a time.');
      const out = [];
      let chars = 0;
      for (let n = from; n <= to; n++) {
        let text = en ? (en.has(n) ? await IRV.enText(d.file, n).catch(() => '') : '') : await IRV.pdfText.page(entry, n);
        if (en && !en.has(n)) { out.push({ page: n, text: '(this page has no English version; read it with language "ja")' }); continue; }
        if (chars + text.length > 60000) { text = text.slice(0, Math.max(0, 60000 - chars)) + ' …[cut]'; }
        chars += text.length;
        out.push({ page: n, text: text || '(no text on this page: a figure or a scan)' });
      }
      return { ...docHead(product_id, d, entry), language: en ? 'en (machine-made working translation)' : 'ja', pages: out };
    },

    async search_document({ product_id, name, query, language }) {
      const q = String(query).trim().slice(0, 60);
      if (!q) fail('Give some text to find.');
      const { d, entry } = await docText(product_id, name);
      const en = language === 'en' ? await enPages(d) : null;
      const needle = q.toLowerCase().replace(/\s+/g, en ? ' ' : '');
      const hits = [];
      let total = 0;
      for (let n = 1; n <= entry.pdf.numPages; n++) {
        // Line breaks and spacing inside Japanese text are layout, not words: match with them removed.
        if (en && !en.has(n)) continue;
        const flat = en ? (await IRV.enText(d.file, n).catch(() => '')).replace(/\s+/g, ' ') : (await IRV.pdfText.page(entry, n)).replace(/\s+/g, '');
        const low = flat.toLowerCase();
        let i = low.indexOf(needle), onPage = 0;
        while (i >= 0) {
          total++; onPage++;
          if (hits.length < 40 && onPage <= 3) hits.push({ page: n, snippet: flat.slice(Math.max(0, i - 50), i + needle.length + 70) });
          i = low.indexOf(needle, i + needle.length);
        }
      }
      return { ...docHead(product_id, d, entry), language: en ? `en (machine-made working translation; ${en.size} of ${entry.pdf.numPages} pages translated)` : 'ja', query: q, matches: total, pages_with_matches: [...new Set(hits.map((x) => x.page))], hits,
        ...(total ? {} : { note: 'No match. Try another wording or the Japanese term; figures and scanned pages are not searchable.' }) };
    },

    async document_translation({ product_id, name }) {
      const { d, entry } = await docText(product_id, name);
      const s = await IRV.translationStatus(d.file);
      if (!s || !s.ok) fail((s && s.error) || 'The translation status could not be read.');
      const pages = Object.entries(s.pages).map(([n, p]) => ({ page: Number(n), english: !!p.has, status: p.status || null,
        ...(p.errors || p.warnings ? { check_errors: p.errors || 0, check_warnings: p.warnings || 0 } : {}), ...(p.reason ? { reason: p.reason } : {}) }));
      return { ...docHead(product_id, d, entry), has_english: s.translated > 0, pages_translated: s.translated,
        whole_document_translated: s.translated >= entry.pdf.numPages, translation_running: !!(s.job && s.job.mine), last_updated: s.date ? s.date.slice(0, 10) : null, pages,
        note: 'English versions are machine-made working translations; "review" means the layout or wording checks flagged something on that page. Read them with read_document or search_document and language "en".' };
    },

    news({ insurer, product_id, topic, source, story, from, to, query, include_routine, limit }) {
      const n = A().news;
      if (!n || !n.items.length) fail('No news has been collected in this data folder.');
      if (insurer && !n.owners.has(insurer)) fail(`No insurer or source "${insurer}". Call list_insurers for the ids.`);
      if (story && !n.storyById.has(story)) fail(`No story "${story}". Call list_stories for the ids.`);
      if (product_id) product(product_id);
      const all = IRV.newsFilter(n, { ins: insurer, product: product_id, cat: topic, kind: source, story, from, to, q: query, all: !!include_routine || !!story });
      const max = limit || 30;
      const owner = insurer ? n.owners.get(insurer) : null;
      return {
        matched: all.length, returned: Math.min(max, all.length), newest_first: true,
        sources_last_checked: n.lastChecked || null,
        ...(owner && owner.insurer ? { coverage: owner.tier, coverage_note: { full: 'Every item from this insurer\'s own lists is collected.',
          watch: 'Watch tier: its lists are read, but routine items are not shown; no press sweep for this insurer.',
          market: 'Market-wide only: no list of its own is read. It appears only when the regulator, the association or press coverage names it.' }[owner.tier] } : {}),
        ...(all.length > max ? { note: `${all.length - max} older items not returned; narrow the dates or raise limit.` } : {}),
        items: all.slice(0, max).map((i) => ({
          id: i.id, date: i.date, title_en: i.title_en || null, title_ja: i.title_ja || null, topic: i.category || 'not yet labelled',
          importance: i.importance || null, summary: i.summary_en || null,
          insurers: i.insurer_ids.map((x) => ({ id: x, name: IRV.insurerEn(n.insurers.get(x), x) })),
          source_kind: i.source_kind || null, publisher: IRV.newsPublisher(n, i), url: i.url,
          products: IRV.arr(i.product_ids), stories: IRV.arr(i.story_ids), retrieved_date: i.retrieved_date || null,
          ...(/^snippet only/i.test(i.note || '') ? { snippet_only: true } : {}), ...(i.note ? { note: i.note } : {}),
          ...(i.verified ? { checked: i.verified.result === 'confirmed', check: i.verified } : {}),
        })),
      };
    },

    list_stories() {
      const n = A().news;
      return { stories: (n ? n.stories : []).map((s) => ({ id: s.id, title_en: s.title_en || null, title_ja: s.title_ja || null, status: s.status || null,
        insurers: IRV.arr(s.insurer_ids), started: s.started || null, updated: s.updated || null, overview: s.summary_en || null,
        key_dates: IRV.arr(s.key_dates), open_questions: IRV.arr(s.open_questions),
        linked_items: n.items.filter((i) => IRV.arr(i.story_ids).includes(s.id)).length })),
        note: 'Overviews and summaries are the tracker\'s own wording of what the sources report.' };
    },

    list_insurers() {
      const n = A().news;
      if (!n) fail('The app has no data loaded.');
      const rows = IRV.newsSourceRows(n);
      const count = new Map();
      for (const i of n.items) for (const x of i.insurer_ids) count.set(x, (count.get(x) || 0) + 1);
      const lists = (id) => rows.filter((r) => r.owner.id === id).map((r) => ({ list: r.source.label || r.source.key, status: r.state,
        last_read: r.rec.last_ok || null, newest_item: r.rec.last_new || null, ...(r.attention && r.rec.message ? { message: String(r.rec.message).slice(0, 200) } : {}) }));
      return {
        insurers: [...n.owners.values()].filter((o) => o.insurer).map((o) => ({ id: o.id, name: o.name, name_ja: (n.insurers.get(o.id) || {}).name_ja || null,
          coverage: o.tier, group: o.group || null, news_items: count.get(o.id) || 0, tracked_products: A().model.products.filter((p) => p.insurer_id === o.id).length, lists: lists(o.id) })),
        market_wide_sources: [...n.owners.values()].filter((o) => !o.insurer).map((o) => ({ id: o.id, name: o.name, lists: lists(o.id) })),
      };
    },

    open_news({ story, insurer }) {
      const n = A().news;
      if (story && !(n && n.storyById.has(story))) fail(`No story "${story}".`);
      if (insurer && !(n && n.owners.has(insurer))) fail(`No insurer or source "${insurer}".`);
      A().go('news', story ? { story } : insurer ? { ins: insurer } : {});
      return { opened: story ? 'story' : 'news', ...(story ? { story } : {}), ...(insurer ? { insurer } : {}) };
    },

    open_product({ product_id }) {
      const p = product(product_id);
      A().go('product', { id: p.id });
      return { opened: 'product page', product_id: p.id };
    },

    open_leaderboard({ product_type, currency, tenor }) {
      const m = A().model, st = A().state;
      const has = IRV.entries(m, { age: st.age, sex: st.sex }).some((s) => s.type === product_type && s.currency === currency && s.term === termStr(tenor));
      if (!has) fail('The leaderboard has no entries for that product type, currency and tenor.');
      st.type = product_type; st.cur = currency; st.term = termStr(tenor);
      A().save(); A().go('explore');
      return { opened: 'leaderboard', product_type, currency, tenor_years: tenor };
    },

    open_document({ product_id, name, page }) {
      const r = IRV.openDocument(product_id, name, page);
      if (!r.ok) fail(r.error);
      return { opened: 'document viewer', product_id, name, page: r.page, ...(r.note ? { note: r.note } : {}) };
    },
  };

  IRV.chatToolHandlers = tools; // for the test walk-through (the assistant reaches them only through the main process)

  window.api.chat.onTool(async ({ id, name, args }) => {
    let ok = true, text;
    try {
      if (!A().model) fail('The app has no data loaded.');
      if (!tools[name]) fail('Unknown tool.');
      text = JSON.stringify(await tools[name](args || {}));
    } catch (e) { ok = false; text = String((e && e.message) || e); }
    window.api.chat.toolResult({ id, ok, text });
  });
})();
