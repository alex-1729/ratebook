// News: insurer releases and notices, regulator and association announcements, press coverage and stories.
// Built from data/news/ (see scripts/news.py). Titles and summaries are data: inserted with textContent only.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h, arr, str } = IRV;
  const V = IRV.views;
  const A = () => IRV.app;

  const CATEGORY_EN = {
    product_launch: 'Product launch', product_change: 'Product change', sales_suspension: 'Sales suspension', rates: 'Rates',
    regulatory_conduct: 'Conduct / enforcement', regulation: 'Regulation', distribution: 'Distribution', results: 'Results', corporate: 'Corporate',
    customer_notice: 'Customer notice', other: 'Other',
  };
  const CATEGORY_ORDER = Object.keys(CATEGORY_EN);
  const ROUTINE = new Set(['customer_notice', 'other']);
  const KIND_EN = { official: 'Company', parent: 'Parent group', regulator: 'Regulator', association: 'Association', press: 'Press' };
  const TIER_EN = { full: 'Full coverage', watch: 'Watch', market: 'Market-wide only' };
  const STALE_AFTER_DAYS = 8;
  const RANGES = [['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['', 'All']];
  const PAGE = 200;
  IRV.newsCategoryEn = (c) => CATEGORY_EN[c] || (c ? IRV.tidy(c) : 'Not yet labelled');

  // ---------- model ----------
  IRV.buildNews = function (ds) {
    const raw = (ds && ds.news) || {};
    const insurers = new Map(arr(ds && ds.insurers).map((i) => [i.id, i]));
    const owners = new Map();
    for (const i of insurers.values()) {
      owners.set(i.id, { id: i.id, name: IRV.insurerEn(i, i.id), tier: ['full', 'watch'].includes(i.news_tier) ? i.news_tier : 'market',
        insurer: true, sources: arr(i.news_sources), group: str(i.group) });
    }
    for (const o of arr(raw.owners)) {
      owners.set(o.id, { id: o.id, name: str(o.name_en) || o.id, tier: '', insurer: false, sources: arr(o.news_sources), group: '' });
    }
    // A release published on two sites (a company and its holding company) is shown once: the copy carries duplicate_of.
    const items = arr(raw.items).filter((i) => i && i.id && i.date && !i.duplicate_of).map((i) => ({ ...i, insurer_ids: arr(i.insurer_ids).filter((x) => insurers.has(x)) }));
    items.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : a.id.localeCompare(b.id)));
    const stories = arr(raw.stories).filter((s) => s && s.id);
    const n = { items, byId: new Map(items.map((i) => [i.id, i])), stories, storyById: new Map(stories.map((s) => [s.id, s])),
      owners, insurers, state: raw.state || {}, latest: items.length ? items[0].date : '' };
    n.lastChecked = Object.values(n.state).map((r) => str(r.last_checked)).sort().pop() || '';
    return n;
  };

  const sourceOf = (n, item) => {
    const o = n.owners.get(item.source_owner);
    return o ? o.sources.find((s) => s && s.key === item.source_key) : null;
  };
  // Who published it, for the reader: the outlet for press, otherwise the owner of the list it came from.
  IRV.newsPublisher = function (n, item) {
    if (item.publisher) return str(item.publisher);
    const o = n.owners.get(item.source_owner);
    return o ? o.name : KIND_EN[item.source_kind] || '';
  };
  // The default view leaves out routine notices and items a market-wide or watch source lists that do not bear on the subject.
  IRV.newsIsKey = function (n, item) {
    if (item.relevant === false) return false;
    if (item.category) return !ROUTINE.has(item.category);
    const o = n.owners.get(item.source_owner);
    return item.added_by === 'research' || !!(o && o.insurer && o.tier === 'full'); // not labelled yet
  };
  const daysAgo = (date) => Math.round((IRV.today() - IRV.toEpoch(date)) / IRV.DAY);

  // Filter used by the screen and by the assistant's news tool.
  IRV.newsFilter = function (n, f) {
    const q = str(f.q).trim().toLowerCase().split(/\s+/).filter(Boolean);
    const since = f.range ? IRV.today() - Number(f.range) * IRV.DAY : null;
    return n.items.filter((i) => {
      if (!f.all && !IRV.newsIsKey(n, i)) return false;
      if (f.ins && !i.insurer_ids.includes(f.ins) && i.source_owner !== f.ins) return false;
      if (f.cat && i.category !== f.cat) return false;
      if (f.kind && i.source_kind !== f.kind) return false;
      if (f.story && !arr(i.story_ids).includes(f.story)) return false;
      if (f.product && !arr(i.product_ids).includes(f.product)) return false;
      if (f.from && i.date < f.from) return false;
      if (f.to && i.date > f.to) return false;
      if (since != null && IRV.toEpoch(i.date) < since) return false;
      if (q.length) {
        const hay = [i.title_en, i.title_ja, i.summary_en, i.publisher, ...i.insurer_ids.map((x) => IRV.insurerEn(n.insurers.get(x), x))].map(str).join(' ').toLowerCase();
        if (!q.every((w) => hay.includes(w))) return false;
      }
      return true;
    });
  };

  // Sources whose last good read is old, or that failed or were blocked on the last run.
  IRV.newsSourceRows = function (n) {
    const rows = [];
    for (const o of n.owners.values()) {
      for (const s of o.sources) {
        if (!s || !s.key) continue;
        const r = n.state[o.id + '/' + s.key] || {};
        const age = r.last_ok ? daysAgo(r.last_ok) : null;
        const state = !r.last_checked ? 'never' : r.state === 'ok' && age != null && age > STALE_AFTER_DAYS ? 'stale' : r.state || 'never';
        rows.push({ owner: o, source: s, rec: r, state, age, attention: state !== 'ok' });
      }
    }
    return rows;
  };

  // ---------- "new since last visit" ----------
  const SEEN = 'irv-news-seen-v1';
  let seenAtStart; // the mark this session compares against; set on the first look
  function seenMark() {
    if (seenAtStart === undefined) { try { seenAtStart = localStorage.getItem(SEEN) || ''; } catch { seenAtStart = ''; } }
    return seenAtStart;
  }
  const isNew = (item) => { const m = seenMark(); return !!m && str(item.retrieved_date) > m; };
  IRV.newsNewCount = function () {
    const n = A().news;
    return n ? n.items.filter((i) => isNew(i) && IRV.newsIsKey(n, i)).length : 0;
  };
  function markSeen(n) {
    seenMark();
    const newest = n.items.reduce((a, i) => (str(i.retrieved_date) > a ? str(i.retrieved_date) : a), '');
    try { if (newest) localStorage.setItem(SEEN, newest); } catch { /* optional */ }
  }

  // ---------- rows ----------
  const open = (url) => window.api.openUrl(url).then((r) => { if (r && !r.ok) A().toast(r.error); });
  const insurerNames = (n, item) => item.insurer_ids.map((x) => IRV.insurerShort(n.insurers.get(x), x));

  function itemRow(n, item, opts) {
    const o = opts || {};
    const m = A().model;
    const title = str(item.title_en) || str(item.title_ja);
    const tags = [];
    if (isNew(item)) tags.push(h('span', { class: 'tag new' }, 'New'));
    if (item.importance === 'high') tags.push(h('span', { class: 'tag' }, 'Key'));
    if (/^snippet only/i.test(str(item.note))) tags.push(h('span', { class: 'tag warn', title: 'Known from a search-result snippet only; the page itself was not read.' }, 'Snippet only'));
    if (item.verified && item.verified.result === 'confirmed') tags.push(h('span', { class: 'tag ok', title: 'Re-checked against the source on ' + str(item.verified.date) }, 'Checked'));
    if (item.verified && item.verified.result && item.verified.result !== 'confirmed') tags.push(h('span', { class: 'tag warn', title: str(item.verified.note) }, { could_not_check: 'Source not re-read', unsupported: 'Unsupported' }[item.verified.result] || 'Check: ' + str(item.verified.result)));
    const who = insurerNames(n, item);
    const stories = arr(item.story_ids).map((id) => n.storyById.get(id)).filter(Boolean);
    const products = arr(item.product_ids).map((id) => m && m.byId.get(id)).filter(Boolean);
    return h('div', { class: 'nwi' + (item.importance === 'high' ? ' key' : ''), 'data-news': item.id },
      h('div', { class: 'd' }, item.date), // dates are written one way throughout the app
      h('div', { class: 'b' },
        h('div', { class: 't' }, h('a', { onclick: () => open(item.url), title: item.url }, title), tags),
        item.title_en && item.title_ja ? h('div', { class: 'ja', lang: 'ja' }, item.title_ja) : null,
        item.summary_en ? h('p', { class: 'sum' }, item.summary_en) : null,
        o.note && item.note ? h('p', { class: 'sub' }, item.note) : null,
        stories.length || products.length ? h('div', { class: 'links' },
          stories.filter((s) => s.id !== o.story).map((s) => h('button', { type: 'button', class: 'lnk', onclick: () => A().go('news', { story: s.id }) }, 'Story: ' + str(s.title_en || s.id))),
          products.filter((p) => p.id !== o.product).map((p) => h('button', { type: 'button', class: 'lnk', onclick: () => A().go('product', { id: p.id }) }, IRV.shortEn(p)))) : null),
      h('div', { class: 'm' },
        o.product ? null : h('b', null, who.length ? who.slice(0, 3).join(', ') + (who.length > 3 ? ` +${who.length - 3}` : '') : 'Market-wide'),
        h('span', null, IRV.newsCategoryEn(item.category)),
        h('small', null, item.source_kind === 'official' && who.length === 1 ? 'Company release' : IRV.newsPublisher(n, item))));
  }
  IRV.newsRow = itemRow;

  const monthLabel = (ym) => new Date(+ym.slice(0, 4), +ym.slice(5, 7) - 1, 1).toLocaleString('en-GB', { month: 'long', year: 'numeric' });

  function feed(main, n, list, opts) {
    let lastMonth = '';
    for (const item of list) {
      const ym = item.date.slice(0, 7);
      if (ym !== lastMonth) {
        lastMonth = ym;
        main.append(h('h2', null, monthLabel(ym), h('span', { class: 'more' }, list.filter((x) => x.date.startsWith(ym)).length)));
      }
      main.append(itemRow(n, item, opts));
    }
  }

  // ---------- News screen ----------
  V.news = function (main, params) {
    const app = A(), n = app.news;
    if (!n) { main.append(h('div', { class: 'empty' }, 'Loading…')); return; }
    if (params && params.story) return storyPage(main, n, params.story);
    const f = (app.state.news = app.state.news || { ins: '', cat: '', kind: '', range: '90', q: '', all: false, shown: PAGE });
    if (params && params.ins !== undefined) { f.ins = params.ins; delete params.ins; }
    const set = (k) => (v) => { f[k] = v; f.shown = PAGE; app.render(); };

    main.append(h('h1', { class: 'sm' }, 'News'));
    if (!n.items.length) {
      main.append(h('div', { class: 'empty' }, 'No news collected yet. Run the news update on the Data page, or python3 scripts/news.py collect.'));
      return;
    }
    const rows = IRV.newsSourceRows(n);
    const bad = rows.filter((r) => r.attention && (r.owner.tier !== 'market'));
    main.append(h('div', { class: 'sub' }, `${n.items.length} items · latest ${n.latest} · sources last checked ${n.lastChecked || '–'}`,
      bad.length ? h('button', { type: 'button', class: 'lnk warn', style: { marginLeft: '14px' },
        onclick: () => { const d = document.getElementById('news-sources'); d.open = true; d.scrollIntoView({ block: 'start' }); } },
      `${bad.length} source${bad.length > 1 ? 's' : ''} need${bad.length > 1 ? '' : 's'} attention`) : null));

    // Stories
    if (n.stories.length) {
      main.append(h('h2', null, 'Stories', h('span', { class: 'more' }, n.stories.length)));
      main.append(h('div', { class: 'multi' }, n.stories.map((s) => {
        const count = n.items.filter((i) => arr(i.story_ids).includes(s.id)).length;
        return h('button', { type: 'button', class: 'smc story', 'data-story': s.id, onclick: () => app.go('news', { story: s.id }) },
          h('h3', null, str(s.title_en || s.id), h('span', null, count)),
          h('p', null, [str(s.status) ? IRV.tidy(s.status) : '', arr(s.insurer_ids).map((x) => IRV.insurerShort(n.insurers.get(x), x)).join(', '),
            s.updated ? 'updated ' + s.updated : ''].filter(Boolean).join(' · ')));
      })));
    }

    // Filters
    const withItems = new Set(n.items.flatMap((i) => [...i.insurer_ids, i.source_owner]));
    const tierOpts = (tier) => [...n.owners.values()].filter((o) => o.insurer && o.tier === tier && withItems.has(o.id))
      .sort((a, b) => a.name.localeCompare(b.name)).map((o) => h('option', { value: o.id, selected: f.ins === o.id }, o.name));
    const others = [...n.owners.values()].filter((o) => !o.insurer && withItems.has(o.id))
      .map((o) => h('option', { value: o.id, selected: f.ins === o.id }, o.name));
    const base = IRV.newsFilter(n, { ...f, cat: '' });
    const cats = CATEGORY_ORDER.filter((c) => base.some((i) => i.category === c));
    if (f.cat && !cats.includes(f.cat)) f.cat = '';
    const kinds = Object.keys(KIND_EN).filter((k) => n.items.some((i) => i.source_kind === k));
    main.append(h('div', { class: 'pick col3 news-pick' },
      h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Insurer'),
        h('select', { 'aria-label': 'Insurer', id: 'news-ins', onchange: (e) => set('ins')(e.target.value) },
          h('option', { value: '', selected: !f.ins }, 'All'),
          ['full', 'watch', 'market'].map((t) => { const o = tierOpts(t); return o.length ? h('optgroup', { label: TIER_EN[t] }, o) : null; }),
          others.length ? h('optgroup', { label: 'Regulator, association, groups' }, others) : null),
        h('span', { class: 'lbl', style: { marginLeft: '26px' } }, 'Source'),
        [{ value: '', label: 'All' }, ...kinds.map((k) => ({ value: k, label: KIND_EN[k] }))].map((o) =>
          h('button', { type: 'button', class: 'tg' + (f.kind === o.value ? ' on' : ''), onclick: () => set('kind')(o.value) }, o.label))),
      h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Topic'),
        [{ value: '', label: 'All' }, ...cats.map((c) => ({ value: c, label: CATEGORY_EN[c] }))].map((o) =>
          h('button', { type: 'button', class: 'tg' + (f.cat === o.value ? ' on' : ''), 'data-cat': o.value, onclick: () => set('cat')(o.value) }, o.label))),
      h('div', { class: 'grp' }, h('span', { class: 'lbl' }, 'Period'),
        RANGES.map(([v, label]) => h('button', { type: 'button', class: 'tg' + (f.range === v ? ' on' : ''), onclick: () => set('range')(v) }, label)),
        h('input', { type: 'search', id: 'news-q', placeholder: 'Find in titles', value: f.q, 'aria-label': 'Find in titles', style: { marginLeft: '26px', width: '200px' },
          onchange: (e) => set('q')(e.target.value) }),
        h('label', { style: { marginLeft: '26px' }, title: 'Also show routine notices (maintenance, awards, campaigns), items not yet labelled, and regulator items that do not concern life insurance.' },
          h('input', { type: 'checkbox', id: 'news-all', checked: f.all, onchange: (e) => set('all')(e.target.checked) }), ' Include routine items'))));

    const list = IRV.newsFilter(n, f);
    const hiddenRoutine = f.all ? 0 : IRV.newsFilter(n, { ...f, all: true }).length - list.length;
    main.append(h('div', { class: 'lbline' }, h('span', null, `${list.length} item${list.length === 1 ? '' : 's'}`),
      hiddenRoutine ? h('span', { class: 'sub' }, `${hiddenRoutine} routine or unlabelled not shown`) : null));
    if (!list.length) main.append(h('div', { class: 'empty' }, 'Nothing matches.'));
    feed(main, n, list.slice(0, f.shown), {});
    if (list.length > f.shown) {
      main.append(h('div', { class: 'rowf' }, h('button', { type: 'button', class: 'btn', onclick: () => { f.shown += PAGE; app.render(); } },
        `Show ${Math.min(PAGE, list.length - f.shown)} more`)));
    }

    // Source health
    const attention = rows.filter((r) => r.attention).length;
    const marketOnly = [...n.owners.values()].filter((o) => o.insurer && !o.sources.length);
    main.append(h('details', { id: 'news-sources', style: { marginTop: '38px' } },
      h('summary', null, `Sources · ${rows.length} lists` + (attention ? ` · ${attention} need attention` : ' · all read')),
      h('table', null,
        h('thead', null, h('tr', null, h('th', null, 'Owner'), h('th', { class: 'l' }, 'List'), h('th', { class: 'l' }, 'Status'),
          h('th', null, 'Last read'), h('th', null, 'Newest item'))),
        h('tbody', null, rows.sort((a, b) => Number(b.attention) - Number(a.attention) || a.owner.name.localeCompare(b.owner.name)).map((r) => h('tr', null,
          h('td', null, r.owner.name, h('small', null, r.owner.insurer ? TIER_EN[r.owner.tier] : 'Market-wide source')),
          h('td', { class: 'l' }, h('a', { onclick: () => open(r.source.url || r.source.archive_url) }, str(r.source.label || r.source.key)),
            r.source.access === 'restricted' ? h('span', { class: 'tag' }, 'Japan connection') : null),
          h('td', { class: 'l ' + (r.attention ? 'st-failed' : 'st-ok') },
            { ok: 'Read', stale: `Not read for ${r.age} days`, blocked: 'Not reachable from this network', failed: 'Failed', never: 'Never read' }[r.state] || r.state,
            r.attention && r.rec.message ? h('small', null, str(r.rec.message).slice(0, 160)) : null),
          h('td', { class: 'num' }, r.rec.last_ok || '–'),
          h('td', { class: 'num' }, r.rec.last_new || '–'))))),
      marketOnly.length ? h('p', { class: 'foot' }, `No list of their own is read for ${marketOnly.length} insurers (${marketOnly.map((o) => o.name.replace(/ Life( Japan)?$/, '')).join(', ')}). `
        + 'They appear when the regulator, the association or the press sweep names them.') : null));

    markSeen(n);
    const nav = document.getElementById('nav-news');
    if (nav) nav.textContent = 'News';
  };

  // ---------- one story ----------
  function storyPage(main, n, id) {
    const app = A(), s = n.storyById.get(id);
    main.append(h('div', { class: 'rowf', style: { marginTop: '18px' } }, h('button', { type: 'button', class: 'lnk', onclick: () => app.go('news') }, '← All news')));
    if (!s) { main.append(h('div', { class: 'empty' }, 'Story not found.')); return; }
    const items = n.items.filter((i) => arr(i.story_ids).includes(id)).sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    main.append(h('h1', { class: 'sm' }, str(s.title_en || s.id)),
      s.title_ja ? h('div', { class: 'ja', lang: 'ja' }, s.title_ja) : null,
      h('div', { class: 'meta' }, [h('b', null, arr(s.insurer_ids).map((x) => IRV.insurerEn(n.insurers.get(x), x)).join(', ')),
        s.status ? IRV.tidy(s.status) : '', s.started ? 'since ' + s.started : '', s.updated ? 'updated ' + s.updated : '', `${items.length} items`]
        .filter(Boolean).flatMap((x, i) => (i ? [' · ', x] : [x]))));
    if (s.summary_en) main.append(h('p', { class: 'story-sum' }, s.summary_en));
    const dates = arr(s.key_dates).filter((d) => d && d.date);
    if (dates.length) {
      main.append(h('h2', null, 'Key dates', h('span', { class: 'more' }, dates.length)));
      main.append(h('table', { class: 'kd' }, h('tbody', null, dates.map((d) => h('tr', null,
        h('td', { class: 'num' }, d.date), h('td', { class: 'l' }, str(d.text_en)),
        h('td', { class: 'l' }, d.url ? h('a', { onclick: () => open(d.url) }, 'Source') : ''))))));
    }
    const qs = arr(s.open_questions).filter(Boolean);
    if (qs.length) main.append(h('h2', null, 'Open questions', h('span', { class: 'more' }, qs.length)), h('ul', { class: 'notes' }, qs.map((q) => h('li', null, str(q)))));
    main.append(h('h2', null, 'Timeline · oldest first', h('span', { class: 'more' }, items.length)));
    if (!items.length) main.append(h('div', { class: 'empty' }, 'No items are linked to this story yet.'));
    for (const item of items) main.append(itemRow(n, item, { year: true, story: id, note: true }));
    const snippets = items.filter((i) => /^snippet only/i.test(str(i.note))).length;
    main.append(h('p', { class: 'foot' }, 'Summaries are the tracker\'s own wording of what each source reports. ',
      snippets ? `${snippets} item${snippets > 1 ? 's are' : ' is'} known from a search snippet only and marked. ` : '', 'Open a title to read the source.'));
  }

  // ---------- product page tab ----------
  IRV.newsForProduct = function (p) {
    const n = A().news;
    if (!n) return { linked: [], insurer: [] };
    const linked = n.items.filter((i) => arr(i.product_ids).includes(p.id));
    const ids = new Set(linked.map((i) => i.id));
    const insurer = n.items.filter((i) => !ids.has(i.id) && i.insurer_ids.includes(p.insurer_id) && IRV.newsIsKey(n, i)
      && (i.importance === 'high' || arr(i.story_ids).length) && daysAgo(i.date) <= 365).slice(0, 15);
    return { linked, insurer };
  };
  IRV.renderProductNews = function (main, p) {
    const n = A().news, { linked, insurer } = IRV.newsForProduct(p);
    main.append(h('h2', null, 'About this product', h('span', { class: 'more' }, linked.length)));
    if (!linked.length) main.append(h('div', { class: 'empty' }, 'No news item names this product.'));
    for (const item of linked) main.append(itemRow(n, item, { year: true, product: p.id }));
    main.append(h('h2', null, 'Key news about the insurer · last 12 months', h('span', { class: 'more' }, insurer.length)));
    if (!insurer.length) main.append(h('div', { class: 'empty' }, 'None.'));
    for (const item of insurer) main.append(itemRow(n, item, { year: true, product: p.id }));
    main.append(h('div', { class: 'rowf' }, h('button', { type: 'button', class: 'lnk', onclick: () => A().go('news', { ins: p.insurer_id }) }, 'All news for this insurer')));
  };
})();
