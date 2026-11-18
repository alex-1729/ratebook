// Shared helpers and the in-memory model built from the data files.
// Everything hangs off window.IRV. No Node access here.
(function () {
  'use strict';
  const IRV = (window.IRV = {});

  // ---------- DOM ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'style') Object.assign(el.style, v); // CSSOM, allowed by the CSP
        else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
        else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'value') el[k] = v;
        else el.setAttribute(k, v);
      }
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  // The app's own dropdown (no native select popups): a text button that opens a list. options: [[value, text]].
  function menu(label, options, value, onpick, attrs) {
    const cur = options.find((o) => o[0] === value) || options[0] || ['', ''];
    const list = h('div', { class: 'ddl', role: 'listbox', hidden: '' }, options.map(([v, t]) => h('button', { type: 'button', role: 'option',
      class: 'ddo' + (v === cur[0] ? ' on' : ''), 'data-v': v, onclick: (e) => { e.stopPropagation(); list.hidden = true; if (v !== cur[0]) onpick(v); } }, t)));
    const btn = h('button', { type: 'button', class: 'ddb', 'aria-haspopup': 'listbox', 'aria-label': label, title: label, ...(attrs || {}),
      onclick: (e) => {
        e.stopPropagation();
        const open = list.hidden;
        for (const l of document.querySelectorAll('.ddl')) l.hidden = true;
        list.hidden = !open;
        if (open) { const on = list.querySelector('.on'); if (on) on.scrollIntoView({ block: 'nearest' }); }
      } }, h('span', null, cur[1]), h('span', { class: 'car', 'aria-hidden': 'true' }, '▾'));
    return h('span', { class: 'dd' }, btn, list);
  }
  document.addEventListener('click', () => { for (const l of document.querySelectorAll('.ddl')) l.hidden = true; });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') for (const l of document.querySelectorAll('.ddl')) l.hidden = true; });
  IRV.menu = menu;
  const arr = (x) => (Array.isArray(x) ? x : x == null || x === '' ? [] : [x]);
  const str = (x) => (x == null ? '' : String(x));
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  Object.assign(IRV, { h, arr, str, cssVar });

  // ---------- formatting ----------
  IRV.fmt = (v) => (v == null ? '–' : v.toFixed(2));
  IRV.pct = (v) => (v == null ? '–' : v.toFixed(2) + '%');
  IRV.fd = (d) => (d == null ? '—' : Math.abs(d) < 0.00005 ? '±0.00' : (d > 0 ? '+' : '−') + Math.abs(d).toFixed(2));
  IRV.cls = (d) => (d == null || Math.abs(d) < 0.00005 ? '' : d > 0 ? 'up' : 'dn');
  IRV.md = (s) => (s ? s.slice(5).replace('-', '/') : '');
  IRV.bytes = (b) => (b == null || isNaN(b) ? '' : b < 1048576 ? Math.max(1, Math.round(b / 1024)) + ' KB' : (b / 1048576).toFixed(1) + ' MB');
  IRV.toEpoch = (d) => {
    const m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(str(d));
    return m ? new Date(+m[1], +m[2] - 1, +m[3]).getTime() / 1000 : null;
  };
  IRV.isoDate = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  IRV.today = () => {
    const n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime() / 1000;
  };
  const DAY = 86400;
  IRV.STALE_DAYS = 20;

  // ---------- English labels ----------
  const METRIC_EN = {
    crediting_rate: 'Crediting rate', net_yield: 'Net yield', mva_rate: 'MVA rate',
    periodic_payment_rate: 'Periodic payment rate', periodic_accumulation_rate: 'Periodic accumulation rate',
    base_rate: 'Base rate', assumed_rate: 'Assumed rate',
  };
  const PLAN_EN = {
    kihon: 'Basic', manki: 'Maturity-focused', manki_100: 'Maturity-focused, 100% death benefit',
    manki_50: 'Maturity-focused, 50% death benefit', shisu: 'Index', standard: '',
    no_target: 'No target', target: 'With target', jpy_nenkin: 'JPY, annuity-focused', jpy_kaigo: 'JPY, care cover',
    fx_no_rider: 'FX, no guarantee rider', fx_rider: 'FX, guarantee rider',
    nenkin_genshi_jujitsu: 'Annuity-focused (tontine)', tsumitate: 'Accumulation', teiki_shiharai: 'Periodic payout',
    tontine: 'Tontine', tontine_pre2410: 'Tontine, pre-Oct 2024 contracts',
  };
  const CATEGORY_EN = {
    fixed_annuity: 'Fixed annuity', fixed_indexed_annuity: 'Indexed annuity', whole_life: 'Whole life', tontine_annuity: 'Tontine annuity',
  };
  const STATUS_EN = { on_sale: 'On sale', off_sale: 'Off sale', discontinued: 'Discontinued', suspended: 'Suspended', unconfirmed: 'Status unconfirmed' };
  const SEX_EN = { f: 'Female', m: 'Male', female: 'Female', male: 'Male' };
  // "usd_kakutei_p10" -> "USD kakutei p10": readable, and never Japanese.
  function tidy(key) {
    const s = str(key).split(/[_\s]+/).filter(Boolean)
      .map((w) => (/^(usd|aud|jpy|eur|nzd|fx|mva|a\d)$/i.test(w) ? w.toUpperCase() : w)).join(' ');
    return s ? s[0].toUpperCase() + s.slice(1) : '';
  }
  IRV.metricEn = (m) => METRIC_EN[m] || tidy(m);
  // A label from the data, made safe to print: no Japanese, no field names, no snake_case.
  const CJK = /[\u3000-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]/;
  const FIELD_WORDS = { term_years: 'tenor', age_band: 'age band', effective_date: 'effective date', value_pct: 'rate', not_offered: 'not offered' };
  function cleanLabel(text) {
    let t = str(text);
    if (!CJK.test(t) && !/_/.test(t)) return t;
    // A bracketed aside that is Japanese or explains a field goes entirely.
    t = t.replace(/\s*[(（][^()（）]*[)）]/g, (g) => (CJK.test(g) || /[a-z]_[a-z]/.test(g) ? '' : g));
    t = t.replace(new RegExp(CJK.source + '+(?:\\d+' + CJK.source + '+)*', 'g'), ' ');
    for (const [k, w] of Object.entries(FIELD_WORDS)) t = t.split(k).join(w);
    t = t.replace(/([A-Za-z0-9])_(?=[A-Za-z0-9])/g, '$1 ');
    return t.replace(/\s+([,;.])/g, '$1').replace(/\s{2,}/g, ' ').replace(/^[\s,;·]+|[\s,;·]+$/g, '');
  }
  IRV.cleanLabel = cleanLabel;
  IRV.hasCjk = (t) => CJK.test(str(t));
  // Plan label: the data's own plan_en, then the built-in map, then a tidied key.
  IRV.planMeta = new Map(); // "productId|plan" -> { en, type }
  IRV.planEn = (plan, pid) => {
    const meta = pid ? IRV.planMeta.get(pid + '|' + plan) : null;
    if (meta && meta.en) return cleanLabel(meta.en);
    return plan in PLAN_EN ? PLAN_EN[plan] : tidy(plan);
  };
  // The plan label where a row names a product and plan (boards, Moves, legends): empty for a product with one
  // plan only, whose label ("Standard", "All contracts") says nothing; the product page's grid does the same.
  IRV.singlePlan = new Set(); // product ids with one plan, filled by buildModel
  IRV.planShown = (plan, pid) => (pid && IRV.singlePlan.has(pid) ? '' : IRV.planEn(plan, pid));
  IRV.tidy = tidy;
  IRV.categoryEn = (c) => (c ? CATEGORY_EN[c] || tidy(c) : '');
  IRV.statusEn = (s) => (s ? STATUS_EN[s] || tidy(s) : '');
  // "age 0–69": one way of writing an age band everywhere.
  // A band that is not a plain range is written out rather than printed as a code.
  IRV.bandEn = (a) => {
    const t = str(a).trim();
    if (!t) return '';
    let k = /^(\d+)\s*[-–~]\s*(\d+)$/.exec(t);
    if (k) return `${k[1]}–${k[2]}`;
    if (/^\d+$/.test(t)) return t;
    k = /^(\d+)\s*\+$/.exec(t);
    if (k) return `${k[1]} and over`;
    return cleanLabel(t).split(/\s*;\s*/).map((part) => part.replace(/\s*=\s*/g, ' ').replace(/_/g, ' ').replace(/(\d)-(?=\d)/g, '$1–').replace(/(\d)\+/g, '$1 and over')).join(', ');
  };
  IRV.ageEn = (a) => { const b = IRV.bandEn(a); return !b ? '' : /^\d/.test(b) ? 'age ' + b : b; };
  IRV.sexEn = (s) => (s ? SEX_EN[s.toLowerCase()] || s : '');
  // The tenor is the deferral period in years. Blank = the plan has none; 0 = payments start at once.
  IRV.termEn = (t) => (t === '' || t == null ? 'No fixed term' : parseFloat(t) === 0 ? 'Immediate' : /^[\d.]+$/.test(t) ? t + 'Y' : t);
  IRV.nameEn = (p) => cleanLabel(p.name_en || tidy(p.id));
  // Where space is tight: the data's own short name; else the full name without "Rakuten Bank Edition",
  // and without a bracketed English gloss of a Japanese name ("Ashita no Yorokobi 2 (Tomorrow's Joy 2)").
  IRV.shortEn = (p) => (p && p.name_short_en ? cleanLabel(p.name_short_en) : IRV.nameEn(p).replace(/ \((?:[A-Z][a-z]|without )[^()]{6,}\)$/, '')
    .replace(' Rakuten Bank Edition', ' (Rakuten)').replace(/ \(fixed-rate increase plan\)/i, ''));
  IRV.insurerEn = (i, id) => (i && cleanLabel(i.name_en || '')) || str(id).toUpperCase();
  // The insurer's own short name where the data gives one; otherwise in full ("Sumitomo Life" is not
  // "Mitsui Sumitomo Primary Life").
  IRV.insurerShort = (i, id) => (i && i.name_short_en ? cleanLabel(i.name_short_en) : IRV.insurerEn(i, id));

  // ---------- model ----------
  const CUR_ORDER = ['USD', 'AUD', 'JPY'];
  const curRank = (c) => (CUR_ORDER.indexOf(c) < 0 ? 9 : CUR_ORDER.indexOf(c));
  IRV.sortCur = (list) => [...list].sort((a, b) => curRank(a) - curRank(b) || a.localeCompare(b));
  IRV.sortTerm = (list) => [...list].sort((a, b) => (a === '' ? -1 : parseFloat(a)) - (b === '' ? -1 : parseFloat(b)) || a.localeCompare(b));
  const ageLow = (a) => { const m = /^(\d+)/.exec(a); return m ? +m[1] : -1; };
  IRV.sortAge = (list) => [...list].sort((a, b) => ageLow(a) - ageLow(b) || a.localeCompare(b, 'en', { numeric: true }));

  function parseRows(pid, files) {
    const rows = [];
    for (const f of files) {
      if (!f.text) continue;
      const parts = f.file.replace(/\.csv$/i, '').split('__');
      for (const r of window.parseCsv(f.text)) {
        const raw = str(r.value_pct).replace('%', '').trim();
        const num = raw === '' ? NaN : Number(raw);
        const status = str(r.status) || (isNaN(num) ? 'not_offered' : 'published');
        const t = IRV.toEpoch(r.effective_date);
        if (t == null) continue;
        rows.push({
          pid, metric: str(r.metric) || parts[1] || 'rate',
          plan: str(r.plan) || (r.plan === undefined ? parts[2] || '' : ''),
          currency: str(r.currency), term: str(r.term_years), age: str(r.age_band), sex: str(r.sex),
          date: str(r.effective_date).slice(0, 10), t,
          v: status === 'published' && !isNaN(num) ? num : null, status,
          src: str(r.source_url), retrieved: str(r.retrieved_date), snapshot: str(r.snapshot),
          wb: /^(true|1|yes)$/i.test(str(r.via_wayback)),
        });
      }
    }
    return rows;
  }

  // ds: result of api.loadDataset(); rateFiles: { productId: [{file, text}] }
  IRV.buildModel = function (ds, rateFiles) {
    const insurers = new Map(ds.insurers.map((i) => [i.id, i]));
    const products = [...ds.products].sort((a, b) =>
      str(a.insurer_id).localeCompare(str(b.insurer_id)) || IRV.nameEn(a).localeCompare(IRV.nameEn(b)));
    const m = { ds, insurers, products, byId: new Map(products.map((p) => [p.id, p])), series: [], byKey: new Map(), byProduct: new Map(), color: {}, latest: '', govt: ds.govtYields || {} };
    // Product types: the taxonomy file's order, plus any type seen only on a product.
    m.types = arr(ds.productTypes).map((t) => ({ key: t.key, label: str(t.label) || tidy(t.key), group: str(t.group), description: str(t.description) }));
    const addType = (k) => { if (k && !m.types.some((t) => t.key === k)) m.types.push({ key: k, label: tidy(k), group: '', description: '' }); };
    IRV.planMeta = new Map();
    IRV.singlePlan = new Set();
    for (const p of products) {
      addType(p.product_type);
      for (const rs of arr(p.rate_series)) {
        if (!rs || rs.plan == null) continue;
        const k = p.id + '|' + rs.plan;
        const meta = IRV.planMeta.get(k) || { en: '', type: '' };
        if (rs.plan_en && !meta.en) meta.en = str(rs.plan_en);
        if (rs.product_type && (!meta.type || rs.metric === 'crediting_rate')) meta.type = str(rs.product_type);
        IRV.planMeta.set(k, meta);
        addType(rs.product_type);
      }
    }
    addType('unclassified');
    m.typeLabel = (k) => (m.types.find((t) => t.key === k) || { label: tidy(k) }).label;
    products.forEach((p, i) => { m.color[p.id] = '--s' + ((i % 8) + 1); });
    const modelYields = new Map();
    for (const y of arr(ds.modelYields)) { if (!modelYields.has(y.product_id)) modelYields.set(y.product_id, []); modelYields.get(y.product_id).push(y); }
    for (const p of products) {
      const planOrder = arr(p.rate_series).map((s) => s && s.plan);
      const map = new Map();
      for (const r of parseRows(p.id, rateFiles[p.id] || [])) {
        const key = [p.id, r.metric, r.plan, r.currency, r.term, r.age, r.sex].join('|');
        let s = map.get(key);
        if (!s) {
          // A plan may carry its own type; otherwise it has the product's.
          const meta = IRV.planMeta.get(p.id + '|' + r.plan);
          s = { key, pid: p.id, metric: r.metric, plan: r.plan, currency: r.currency, term: r.term, age: r.age, sex: r.sex, obs: [],
            type: (meta && meta.type) || str(p.product_type) || 'unclassified' };
          map.set(key, s);
        }
        s.obs.push(r);
      }
      // "net_yield_from": "crediting_rate": the insurer states the net yield is the crediting rate, so no
      // net-yield file exists; each crediting-rate series stands in for it, marked `derived`.
      // "net_yield_plans": [...] limits it to those plans, where the insurer says so for some plans only.
      if (p.net_yield_from === 'crediting_rate') {
        const only = Array.isArray(p.net_yield_plans) ? new Set(p.net_yield_plans) : null;
        for (const c of [...map.values()]) {
          if (c.metric !== 'crediting_rate' || (only && !only.has(c.plan))) continue;
          const key = [p.id, 'net_yield', c.plan, c.currency, c.term, c.age, c.sex].join('|');
          if (!map.has(key)) map.set(key, { ...c, key, metric: 'net_yield', derived: 'crediting_rate', obs: c.obs.map((o) => ({ ...o, metric: 'net_yield' })) });
        }
      }
      // The value model's yield (ds.modelYields) for a plan with no net yield of its own: one point, on the
      // date the model ran, standing on the crediting-rate point it was computed from. Marked `derived: 'model'`.
      // No history: the model knows today's up-front charge only.
      for (const y of modelYields.get(p.id) || []) {
        for (const c of [...map.values()]) {
          // A payout plan (_p<years>) runs past its term, so a yield to the end of the term is not its yield.
          if (/_p\d+$/.test(c.plan)) continue;
          if (c.metric !== 'crediting_rate' || c.plan !== y.plan || c.currency !== y.currency || c.age !== y.age_band || parseFloat(c.term) !== y.term_years) continue;
          const o = c.obs.find((x) => x.date === y.effective_date);
          if (!o || o.v == null || Math.abs(o.v - y.crediting_pct) > 1e-6) continue;
          const key = [p.id, 'net_yield', c.plan, c.currency, c.term, c.age, c.sex].join('|');
          if (!map.has(key)) map.set(key, { ...c, key, metric: 'net_yield', derived: 'model', modelYield: y, obs: [{ ...o, metric: 'net_yield', v: y.yield_pct }] });
        }
      }
      const rank = (pl) => (planOrder.indexOf(pl) < 0 ? 999 : planOrder.indexOf(pl));
      const list = [...map.values()];
      for (const s of list) {
        s.obs.sort((a, b) => a.t - b.t);
        const last = s.obs[s.obs.length - 1];
        if (last.date > m.latest) m.latest = last.date;
      }
      list.sort((a, b) => rank(a.plan) - rank(b.plan) || a.plan.localeCompare(b.plan, 'en', { numeric: true }) ||
        curRank(a.currency) - curRank(b.currency) || (parseFloat(a.term) || 0) - (parseFloat(b.term) || 0) ||
        ageLow(a.age) - ageLow(b.age) || a.sex.localeCompare(b.sex));
      // Modelled yields rank on boards only; the product page and the rate tools show what the insurer publishes.
      m.byProduct.set(p.id, list.filter((s) => s.derived !== 'model'));
      if (list.length && new Set(list.map((s) => s.plan)).size === 1) IRV.singlePlan.add(p.id);
      for (const s of list) { m.series.push(s); m.byKey.set(s.key, s); }
    }
    return m;
  };

  // ---------- government yields: the rate over UST / JGB / ACGB ----------
  // Same rules as scripts/rate_spreads.py ("now" rankings): the series' tenor (term, or what the plan implies),
  // the nearest published curve tenor (ties to the longer), the latest yield on or before the date within
  // 14 days, converted from semi-annual par to annual effective, the basis of a credited rate.
  const GOVT_STALE_DAYS = 14;
  IRV.govtTenor = (s) => {
    const plan = str(s.plan), term = str(s.term);
    if (/_(shushin|atsu)$/.test(plan)) return { t: null, why: 'life annuity: rate fixed for life, no single tenor' };
    let x = /_p(\d+)$/.exec(plan);
    if (x && term) return { t: parseFloat(term) + parseInt(x[1], 10) / 2, why: 'deferral + half the payout period' };
    if (term && term !== '0') return { t: parseFloat(term), why: 'term' };
    if (term === '0') return { t: null, why: 'immediate payout, no term' };
    if ((x = /_h(\d+)-(\d+)$/.exec(plan))) return { t: (+x[1] + +x[2]) / 2, why: 'plan judgement period, midpoint' };
    if ((x = /_h(\d+)$/.exec(plan))) return { t: +x[1], why: 'plan period' };
    if ((x = /_hle(\d+)$/.exec(plan))) return { t: +x[1], why: 'plan period, upper bound' };
    if ((x = /_(?:hge(\d+)|h(\d+)-life)$/.exec(plan))) return { t: +(x[1] || x[2]), why: 'plan period, lower bound' };
    return { t: null, why: 'no term in data' };
  };
  // { curve, tenor, date, y (as published), ae (annual effective) } for a currency and tenor at an epoch, or null.
  IRV.govtYield = (m, cur, tenor, at) => {
    const c = m.govt && m.govt[cur];
    if (!c || !(tenor > 0)) return null;
    const avail = Object.keys(c.tenors).map(Number);
    if (!avail.length) return null;
    const ct = avail.reduce((b, t) => (Math.abs(t - tenor) < Math.abs(b - tenor) || (Math.abs(t - tenor) === Math.abs(b - tenor) && t > b) ? t : b));
    const day = IRV.isoDate(at == null ? IRV.today() : at), list = c.tenors[String(ct)];
    let lo = 0, hi = list.length - 1, i = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (list[mid][0] <= day) { i = mid; lo = mid + 1; } else hi = mid - 1; }
    if (i < 0 || (IRV.toEpoch(day) - IRV.toEpoch(list[i][0])) / DAY > GOVT_STALE_DAYS) return null;
    const y = list[i][1];
    return { curve: c.curve, cur, tenor: ct, date: list[i][0], y, ae: ((1 + y / 200) ** 2 - 1) * 100, source: c.source };
  };
  IRV.govtLabel = (g) => (g ? `${g.curve} ${g.tenor}Y` : '');

  IRV.last = (s) => s.obs[s.obs.length - 1];
  IRV.prev = (s) => (s.obs.length > 1 ? s.obs[s.obs.length - 2] : null);
  IRV.delta = (s) => { const a = IRV.prev(s), b = IRV.last(s); return a && a.v != null && b.v != null ? +(b.v - a.v).toFixed(4) : null; };
  IRV.gapDays = (s) => { const a = IRV.prev(s), b = IRV.last(s); return a ? Math.round((b.t - a.t) / DAY) : null; };
  // The line is carried to today only when the latest point is recent.
  IRV.DAY = DAY;
  // One period's length and the longest step between two points that still counts as consecutive, in seconds.
  IRV.periodSpan = function (m, s) {
    const per = m ? IRV.periodOf(m, s) : { type: 'semi' };
    const days = per.type === 'days' ? per.length : per.type === 'month' ? 31 : 15;
    return { cover: days * DAY, gap: Math.max(20, Math.ceil(days * 1.3)) * DAY };
  };
  IRV.endT = (s, m) => { const l = IRV.last(s).t, now = IRV.today(); return now > l && now - l <= IRV.periodSpan(m, s).gap ? now : l; };

  IRV.ageMatches = function (band, age) {
    if (band === '') return true;
    if (age == null || isNaN(age)) return false;
    let m = /^(\d+)\s*[-–~]\s*(\d+)$/.exec(band);
    if (m) return age >= +m[1] && age <= +m[2];
    m = /^(\d+)$/.exec(band);
    if (m) return age === +m[1];
    m = /^(\d+)\s*\+$/.exec(band);
    return m ? age >= +m[1] : false;
  };
  IRV.sexMatches = (s, sex) => s === '' || !sex || s.toLowerCase()[0] === sex.toLowerCase()[0];

  // Every currency/term pair that has a crediting rate.
  IRV.cases = function (m) {
    const cur = new Map();
    for (const s of m.series) {
      if (s.metric !== 'crediting_rate' || !(parseFloat(s.term) > 0) || !s.currency) continue;
      if (!cur.has(s.currency)) cur.set(s.currency, new Set());
      cur.get(s.currency).add(s.term);
    }
    return cur;
  };

  // Series of one product that fit a case { cur, term, age, sex }.
  IRV.caseSeries = function (m, pid, c) {
    return (m.byProduct.get(pid) || []).filter((s) => s.metric === 'crediting_rate' && s.currency === c.cur && s.term === c.term &&
      IRV.ageMatches(s.age, c.age) && IRV.sexMatches(s.sex, c.sex));
  };
  // One headline series per product: the chosen plan, else the first plan on sale.
  IRV.headline = function (m, pid, c, plan) {
    const list = IRV.caseSeries(m, pid, c);
    if (!list.length) return null;
    return (plan != null && list.find((s) => s.plan === plan)) || list.find((s) => IRV.last(s).v != null) || list[0];
  };
  IRV.ranked = function (m, c, planPick) {
    const out = [];
    for (const p of m.products) {
      const s = IRV.headline(m, p.id, c, planPick && planPick[p.id + '|' + c.cur + '|' + c.term]);
      if (s) out.push({ p, s, plans: [...new Set(IRV.caseSeries(m, p.id, c).map((x) => x.plan))] });
    }
    const val = (x) => { const v = IRV.last(x.s).v; return v == null ? -1 : v; };
    return out.sort((a, b) => val(b) - val(a));
  };

  // ---------- rate periods: what is in force today, and the period before ----------
  // Each insurer sets rates for a period: half-months (1st-15th, 16th-end), calendar months, or fixed windows
  // of N days that start on the row's effective date. A product may override its insurer's period.
  // A row is in force when today falls inside its period.
  const halfStart = (t) => { const d = new Date(t * 1000); return new Date(d.getFullYear(), d.getMonth(), d.getDate() < 16 ? 1 : 16).getTime() / 1000; };
  const monthStart = (t) => { const d = new Date(t * 1000); return new Date(d.getFullYear(), d.getMonth(), 1).getTime() / 1000; };
  const startOf = (per, t) => (per.type === 'month' ? monthStart(t) : halfStart(t));
  IRV.periodOf = function (m, s) {
    const p = m.byId.get(s.pid), ins = p && m.insurers.get(p.insurer_id), rp = (p && p.rate_period) || (ins && ins.rate_period);
    if (rp && rp.type === 'days' && Number(rp.length) > 0) return { type: 'days', length: Number(rp.length) };
    if (rp && rp.type === 'monthly') return { type: 'month' };
    return { type: 'semi' };
  };
  IRV.inForceAt = (per, o, at) => (per.type === 'days' ? at >= o.t && at < o.t + per.length * DAY : startOf(per, o.t) === startOf(per, at));
  // { cur: the point in force today (or null), prev: the point of the period immediately before, last, earlier }
  IRV.periodPoints = function (m, s, at) {
    const now = at == null ? IRV.today() : at;
    const per = IRV.periodOf(m, s);
    let li = -1;
    for (let i = s.obs.length - 1; i >= 0; i--) if (s.obs[i].t <= now) { li = i; break; }
    const last = li >= 0 ? s.obs[li] : null;
    const cur = last && IRV.inForceAt(per, last, now) ? last : null;
    let prev = null;
    if (per.type === 'days') {
      const to = cur ? cur.t : now, from = to - per.length * DAY;
      for (let i = s.obs.length - 1; i >= 0; i--) { const o = s.obs[i]; if (o.t < to && o.t >= from) { prev = o; break; } if (o.t < from) break; }
    } else {
      const prevStart = startOf(per, startOf(per, now) - DAY);
      for (let i = s.obs.length - 1; i >= 0; i--) { const o = s.obs[i]; if (startOf(per, o.t) === prevStart) { prev = o; break; } if (o.t < prevStart) break; }
    }
    const before = cur ? li - 1 : li;
    const prevStart = per.type === 'days' ? (cur ? cur.t : now) - per.length * DAY : startOf(per, startOf(per, now) - DAY);
    const iso = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
    return { cur, prev, last, earlier: before >= 0 ? s.obs[before] : null, prevStart: iso(prevStart) };
  };

  // The data's free-text notes are written for the data's maintainers and name things by their keys. For the
  // reader, keys become the labels used everywhere else: plan keys -> plan names, metric keys -> measure names,
  // product ids -> product names, field names -> plain words, file paths -> "the saved files".
  IRV.plainText = function (m, p, text) {
    let t = String(text);
    const esc = (k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const swap = (key, label) => { if (key && label) t = t.replace(new RegExp('(^|[^A-Za-z0-9_-])' + esc(key) + '(?![A-Za-z0-9_-])', 'g'), (_m, a) => a + label); };
    t = t.replace(/\(?via_wayback\s*=\s*true\)?/g, '(archive capture)').replace(/\(?via_wayback\s*=\s*false\)?/g, '(live page)');
    t = t.replace(/(?:\b(?:sources|data|scripts|translations)\/[\w.\/<>*-]*|\b[\w.-]+\.(?:csv|json|py|html))/g, 'the saved files');
    for (const q of [...m.products].sort((a, b) => b.id.length - a.id.length)) swap(q.id, IRV.nameEn(q));
    const plans = [...new Set((m.byProduct.get(p.id) || []).map((s) => s.plan))].filter((k) => /[_\d]/.test(k)).sort((a, b) => b.length - a.length);
    for (const k of plans) swap(k, '“' + (IRV.planEn(k, p.id) || 'Standard') + '”');
    for (const k of Object.keys(METRIC_EN)) swap(k, METRIC_EN[k].toLowerCase());
    const WORDS = { not_offered: 'not offered', term_years: 'tenor', age_band: 'age band', effective_date: 'effective date', retrieved_date: 'retrieval date',
      on_sale_date: 'launch date', off_sale_date: 'end-of-sale date', value_pct: 'rate', has_mva: 'MVA flag', verified_through: 're-check date', plan_en: 'plan name', plan_ja: 'plan name' };
    for (const [k, w] of Object.entries(WORDS)) swap(k, w);
    // Anything else in snake_case reads better as words.
    return t.replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (w) => w.replace(/_/g, ' '));
  };

  // ---------- independent re-check ----------
  // A row retrieved after its product's verified_through date (or a product with none) has not been re-checked.
  IRV.verifiedThrough = (m, pid) => { const p = m.byId.get(pid); return (p && p.verification && str(p.verification.verified_through)) || ''; };
  // Which kinds of unchecked value the page being drawn carries: 'never' (no re-check at all) or 'after'.
  IRV.uncKinds = new Set();
  IRV.isUnchecked = (m, pid, o) => {
    const vt = IRV.verifiedThrough(m, pid);
    const u = !vt || !o || !o.retrieved || o.retrieved > vt;
    if (u) IRV.uncKinds.add(vt ? 'after' : 'never');
    return u;
  };
  IRV.uncheckedNote = (m, pid) => { const vt = IRV.verifiedThrough(m, pid); return vt ? `Unchecked: collected after the last independent re-check on ${vt}.` : 'Unchecked: this product has had no independent re-check.'; };
  IRV.uncheckedMark = (m, pid, o) => (IRV.isUnchecked(m, pid, o) ? h('span', { class: 'unc', title: IRV.uncheckedNote(m, pid), 'aria-label': 'Unchecked' }, '○') : null);
  // Net yield reproduced (or not) by the value model from the brochure's up-front charge, for the period checked.
  // Only published net yields with a brochure charge are checked; a charge backed out of the net yield proves nothing.
  const nycKey = (pid, plan, cur, term, age, sex, date) => [pid, plan, cur, term, age, sex, date].join('|');
  IRV.netYieldCheck = (m, s, o) => {
    if (!s || s.metric !== 'net_yield' || s.derived || !o) return null;
    if (!m.nyCheck) m.nyCheck = new Map(arr(m.ds.netYieldCheck).map((r) => [nycKey(r.product_id, r.plan, r.currency, String(r.term_years), r.age_band || '', r.sex || '', r.effective_date), r]));
    return m.nyCheck.get(nycKey(s.pid, s.plan, s.currency, s.term, s.age, s.sex, o.date)) || null;
  };
  IRV.netCheckMark = (m, s, o) => {
    const c = IRV.netYieldCheck(m, s, o);
    if (!c) return null;
    const how = `${c.crediting_pct.toFixed(2)}% for ${c.term_years} years less the brochure's ${+c.charge_pct.toFixed(2)}% up-front charge gives ${c.model_net_pct.toFixed(4)}%`;
    return c.status === 'match'
      ? h('span', { class: 'nyc ok', title: `Matches the value model: ${how}; published ${c.published_net_pct}% (the insurer truncates). Charge: ${c.charge_source}`, 'aria-label': 'Matches the value model' }, '✓')
      : h('span', { class: 'nyc bad', title: `Differs from the value model by ${c.diff_pp >= 0 ? '+' : ''}${c.diff_pp.toFixed(4)} points: ${how}; published ${c.published_net_pct}%. Charge: ${c.charge_source}`, 'aria-label': 'Differs from the value model' }, '≠');
  };
  // The mark after a net yield that is not a published figure: "=" the crediting rate, which the insurer states
  // is also the net yield; "m" the value model's yield, from the crediting rate and the up-front charge.
  IRV.modelYieldNote = (s) => {
    const y = s && s.modelYield;
    if (!y) return '';
    const how = y.charge_pct > 0
      ? `${y.crediting_pct.toFixed(2)}% for ${y.term_years} years less the ${+y.charge_pct.toFixed(2)}% up-front charge gives ${y.yield_pct.toFixed(4)}%`
      : `no up-front charge, so the crediting rate of ${y.crediting_pct.toFixed(2)}%${y.pays_interest ? ', paid out each year' : ''}`;
    return `Modelled, not published by the insurer: ${how}. Charge: ${y.charge_source}`;
  };
  IRV.derivedMark = (s) => (!s || !s.derived ? null
    : s.derived === 'model' ? h('sup', { class: 'eqmark', title: IRV.modelYieldNote(s), 'aria-label': 'Modelled' }, 'm')
      : h('sup', { class: 'eqmark', title: 'The insurer states the net yield equals the crediting rate; this is the crediting rate.' }, '='));
  IRV.uncheckedLegend = () => {
    const k = IRV.uncKinds, never = k.has('never'), after = k.has('after') || !never;
    return h('span', { class: 'unc-key' }, h('span', { class: 'unc' }, '○'),
      never && after ? ' unchecked: not independently re-checked, or collected after the last re-check.'
        : never ? ' unchecked: not independently re-checked.' : ' unchecked: collected after the last independent re-check.');
  };

  // ---------- a board: one product type, currency, tenor and rate measure ----------
  // rows: plans with a rate in force today, highest first, each with its change and rank change against the
  // period immediately before. left: plans of the board with no rate in force (never ranked).
  // `at` (epoch seconds, optional) puts the board on another day: rates in force then, against the period before it.
  IRV.board = function (m, spec, profile, at) {
    const metric = spec.metric || 'crediting_rate';
    const now = at == null ? IRV.today() : at;
    // spec.also: other product types ranked on the same board (each such row is marked `other`).
    const also = new Set(spec.also || []);
    const list = IRV.entries(m, profile, metric, now).filter((s) => (s.type === spec.type || also.has(s.type)) && s.currency === spec.cur && s.term === String(spec.term));
    const name = (s) => IRV.shortEn(m.byId.get(s.pid)) + ' ' + IRV.planEn(s.plan, s.pid);
    const rows = [], left = [];
    for (const s of list) {
      const pp = IRV.periodPoints(m, s, now), p = m.byId.get(s.pid);
      if (pp.cur && pp.cur.v != null) rows.push({ s, p, o: pp.cur, prev: pp.prev, earlier: pp.earlier, prevStart: pp.prevStart });
      else {
        // A plan that has never published a rate for this currency and tenor is not part of the board at all.
        // The others are listed with their last real rate ("not offered" markers are not rates).
        let lastRate = null;
        for (let i = s.obs.length - 1; i >= 0; i--) if (s.obs[i].t <= now && s.obs[i].v != null) { lastRate = s.obs[i]; break; }
        if (lastRate) left.push({ s, p, last: lastRate, reason: pp.cur ? 'not offered this period' : 'no rate for the current period' });
      }
    }
    const order = (a, b) => b.v - a.v || a.n.localeCompare(b.n);
    rows.sort((a, b) => order({ v: a.o.v, n: name(a.s) }, { v: b.o.v, n: name(b.s) }));
    // Equal rates share a rank (standard competition ranking: 1, =2, =2, 4).
    const eq = (a, b) => Math.abs(a - b) < 0.00005;
    const compRank = (list) => { // list: [{ key, v }] sorted high to low -> Map key -> rank
      const out = new Map();
      list.forEach((x, i) => out.set(x.key, i > 0 && eq(list[i - 1].v, x.v) ? out.get(list[i - 1].key) : i + 1));
      return out;
    };
    const rankNow = compRank(rows.map((r) => ({ key: r.s.key, v: r.o.v })));
    // Rank change is measured only among the plans ranked in both periods: that common set is ranked once on
    // today's rates and once on the previous period's, and a plan's change is the difference. A plan with no
    // rate in the previous period has no rank change and never shifts anyone else's.
    const both = rows.filter((r) => r.prev && r.prev.v != null);
    const nowIn = compRank(both.map((r) => ({ key: r.s.key, v: r.o.v })));
    const wasIn = compRank(both.map((r) => ({ key: r.s.key, v: r.prev.v, n: name(r.s) })).sort(order));
    const dates = new Map();
    rows.forEach((r) => {
      r.rank = rankNow.get(r.s.key);
      r.tied = rows.some((x) => x !== r && eq(x.o.v, r.o.v));
      r.d = r.prev && r.prev.v != null ? +(r.o.v - r.prev.v).toFixed(4) : null;
      const inBoth = nowIn.has(r.s.key);
      r.rankWas = inBoth ? wasIn.get(r.s.key) : null;           // among the plans ranked in both periods
      r.rankChange = inBoth ? wasIn.get(r.s.key) - nowIn.get(r.s.key) : null;
      r.compared = both.length;
      r.isNew = !r.earlier;                                      // nothing at all before the current period
      r.unchecked = IRV.isUnchecked(m, r.s.pid, r.o);
      r.other = r.s.type !== spec.type;                          // one of spec.also's types
      // Over the government curve: one yield per curve and tenor, as of the board's date (not each row's).
      const gt = IRV.govtTenor(r.s);
      r.govt = gt.t != null ? IRV.govtYield(m, spec.cur, gt.t, now) : null;
      r.govtWhy = gt.why;
      r.over = r.govt ? +(r.o.v - r.govt.ae).toFixed(4) : null;
      dates.set(r.o.date, (dates.get(r.o.date) || 0) + 1);
    });
    const mainDate = [...dates.entries()].sort((a, b) => b[1] - a[1] || b[0].localeCompare(a[0]))[0];
    // The curve point most rows are read against (shown in the header); rows on another tenor say so.
    const gs = new Map();
    rows.forEach((r) => { if (r.govt) { const k = r.govt.tenor; gs.set(k, { g: r.govt, n: ((gs.get(k) || {}).n || 0) + 1 }); } });
    const govt = [...gs.values()].sort((a, b) => b.n - a.n)[0];
    return { spec: { ...spec, metric }, at: at == null ? null : now, rows, left, mainDate: mainDate ? mainDate[0] : '', govt: govt ? govt.g : null,
      leader: rows[0] || null, leaders: rows.filter((r) => r.rank === 1),
      // The best row of the board's own type, when another type leads it.
      ownLeader: rows[0] && rows[0].other ? rows.find((r) => !r.other) || null : null, spread: rows.length > 1 ? +(rows[0].o.v - rows[rows.length - 1].o.v).toFixed(4) : null,
      unchecked: rows.some((r) => r.unchecked),
      up: rows.filter((r) => r.d > 0.00005).length, down: rows.filter((r) => r.d < -0.00005).length,
      rankMoved: rows.filter((r) => r.rankChange).length };
  };
  // Every type / currency / tenor that has at least one rate in force, with how many: [{ type, cur, term, n }]
  IRV.boardCombos = function (m, profile, metric, at) {
    const tallyMap = new Map();
    for (const s of IRV.entries(m, profile, metric, at)) {
      const pp = IRV.periodPoints(m, s, at);
      if (!pp.cur || pp.cur.v == null) continue;
      const k = [s.type, s.currency, s.term].join('|');
      if (!tallyMap.has(k)) tallyMap.set(k, new Set());
      tallyMap.get(k).add(s.pid + '|' + s.plan);
    }
    return [...tallyMap.entries()].map(([k, plans]) => { const [type, cur, term] = k.split('|'); return { type, cur, term, n: plans.size, plans }; });
  };

  // What changed between the period before and the current one, for every series with a point in both.
  // Series with no point for the previous period are counted (list.noPrevious), never compared.
  IRV.moves = function (m, metric, at) {
    const out = [];
    let noPrevious = 0;
    for (const s of m.series) {
      if (metric && s.metric !== metric) continue;
      const pp = IRV.periodPoints(m, s, at);
      if (!pp.cur) continue;
      if (!pp.prev) { noPrevious++; continue; }
      const a = pp.prev, b = pp.cur;
      let kind = null;
      if (a.v != null && b.v != null && Math.abs(a.v - b.v) > 0.00005) kind = 'chg';
      else if (a.v == null && b.v != null) kind = 'new';
      else if (a.v != null && b.v == null) kind = 'susp';
      if (!kind) continue;
      out.push({ s, p: m.byId.get(s.pid), kind, d: kind === 'chg' ? +(b.v - a.v).toFixed(4) : null, from: a, to: b });
    }
    out.sort((x, y) => Math.abs(y.d || 0) - Math.abs(x.d || 0));
    out.noPrevious = noPrevious;
    return out;
  };

  // Leaderboard entries: one crediting-rate series per product-plan, currency
  // and tenor, picked for the sample profile where rates vary by age or sex.
  IRV.entries = function (m, profile, metric, at) {
    // Several series can fit one product-plan, currency and tenor for the sample age (an old capture with no
    // age band beside today's age-banded rows). The one in force today wins, then the most recent.
    const best = new Map();
    const now = at == null ? IRV.today() : at;
    for (const s of m.series) {
      if (s.metric !== (metric || 'crediting_rate') || !s.currency || !(parseFloat(s.term) > 0)) continue;
      if (!IRV.ageMatches(s.age, profile.age) || !IRV.sexMatches(s.sex, profile.sex)) continue;
      const k = [s.pid, s.plan, s.currency, s.term].join('|');
      const pp = IRV.periodPoints(m, s, now);
      const score = [pp.cur ? 1 : 0, pp.last ? pp.last.t : -1, s.age ? 1 : 0];
      const cur = best.get(k);
      if (!cur || score[0] > cur.score[0] || (score[0] === cur.score[0] && (score[1] > cur.score[1] || (score[1] === cur.score[1] && score[2] > cur.score[2])))) best.set(k, { s, score });
    }
    return [...best.values()].map((x) => x.s);
  };
  // Leaderboard order: live rates first, highest first; suspended entries last. `best` keeps one row per product.
  IRV.rankEntries = function (list, best) {
    const val = (s) => { const v = IRV.last(s).v; return v == null ? -1 : v; };
    let out = [...list].sort((a, b) => val(b) - val(a));
    if (best) { const seen = new Set(); out = out.filter((s) => (seen.has(s.pid) ? false : (seen.add(s.pid), true))); }
    return out;
  };
  // What a leaderboard row shows for one entry (the screen and the assistant's tool both use this).
  IRV.entryInfo = function (s) {
    const last = IRV.last(s), prev = IRV.prev(s), g = IRV.gapDays(s);
    return { last, prev, live: last.v != null, d: IRV.delta(s),
      stale: g != null && g > IRV.STALE_DAYS, old: last.t < IRV.halfMonthStart() };
  };

  // First day of the half-month that contains today (rates are set on the 1st and 16th).
  IRV.halfMonthStart = () => {
    const n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate() < 16 ? 1 : 16).getTime() / 1000;
  };

  // "Basic · USD 10Y · age 0-65"
  IRV.seriesLabel = function (s, opts) {
    const o = opts || {};
    const plan = o.plan === false ? '' : IRV.planShown(s.plan, s.pid);
    return [plan,
      o.case === false ? '' : [new RegExp('\\b' + s.currency + '\\b').test(plan) ? '' : s.currency,
        s.term === '' ? '' : IRV.termEn(s.term)].filter(Boolean).join(' '),
      IRV.ageEn(s.age), IRV.sexEn(s.sex)].filter(Boolean).join(' · ');
  };
})();
