// Finds the place in a saved copy that one rate was read from, using the rate's own dimensions (plan,
// currency, tenor, age band, sex, date) rather than the first place its value appears.
//
//   locateText(text, find, dims) -> { hits: [[start, end], ...], exact }   (text and JSON snapshots)
//   locateHtml(html, find, dims) -> { start, end, count } | { count }        (HTML snapshots)
//
// exact / start..end: the one place the figure was read from. Otherwise every place the value appears is
// returned (text) or counted (HTML), and the window says "n matches" instead of pointing at one of them.
'use strict';

// "5.70" and "5.7": the figure as printed, else without trailing zeros.
function forms(find) {
  return [find, find.replace(/\.?0+$/, '')].filter((x, i, a) => x && a.indexOf(x) === i);
}
const valueRe = (f) => new RegExp('(?<![\\d.])' + f.replace('.', '\\.') + '(?![\\d]|\\.\\d)', 'g');

function allHits(body, find) {
  for (const f of forms(find)) {
    const hits = [...body.matchAll(valueRe(f))].map((k) => [k.index, k.index + f.length]);
    if (hits.length) return hits;
  }
  return [];
}
const sameValue = (s, find) => forms(find).includes(String(s).trim().replace(/%$/, '')) || (Number(s) === Number(find) && !isNaN(Number(s)));

// Age labels as printed in Japanese tables ("34歳以下", "35~39歳", "50歳以上", "40～69歳") and age bands
// as stored ("0-34", "50+", "40-69"), both as [lo, hi] (lo null = "and under", hi Infinity = "and over").
function labelAges(text) {
  const t = String(text).normalize('NFKC');
  const out = [];
  for (const k of t.matchAll(/(\d{1,3})\s*[~〜\-–]\s*(\d{1,3})\s*歳/g)) out.push([+k[1], +k[2]]);
  for (const k of t.matchAll(/(\d{1,3})\s*歳\s*以下/g)) out.push([null, +k[1]]);
  for (const k of t.matchAll(/(\d{1,3})\s*歳\s*以上/g)) out.push([+k[1], Infinity]);
  return out;
}
function bandAges(band) {
  const t = String(band || '').trim();
  let k = /^(\d+)\s*[-–~]\s*(\d+)$/.exec(t);
  if (k) return [+k[1], +k[2]];
  k = /^(\d+)\s*\+$/.exec(t);
  if (k) return [+k[1], Infinity];
  k = /^(\d+)$/.exec(t);
  return k ? [+k[1], +k[1]] : null;
}
const ageMatch = (label, band) => label[1] === band[1] && (label[0] == null ? band[0] === 0 || band[0] == null : label[0] === band[0]);

// ---------- Manulife's condensed text copies (self-described by their "#format" line) ----------
const CUR_JA = { '円': 'JPY', '米ドル': 'USD', '豪ドル': 'AUD', 'ユーロ': 'EUR' };
// "#cols ari_* : 契約通貨 円 / 米ドル / 豪ドル.  nashi_kihon : 第1保険期間 2年/3年/5年/7年/10年."
function parseCols(line) {
  const cols = [];
  for (const k of String(line).replace(/^#cols\s*/, '').matchAll(/([a-z_*]+)\s*:\s*([^.]+)\./g)) {
    const list = k[2].replace(/^[^/]*?\s(?=\S+\s*\/)/, '').split('/').map((x) => x.trim().replace(/,$/, ''));
    const toks = list.flatMap((x) => x.split(',')).map((x) => x.trim()).map((x) => {
      const y = /(\d+)\s*年/.exec(x);
      if (y) return { term: y[1] };
      const c = Object.keys(CUR_JA).find((j) => x.endsWith(j));
      return c ? { currency: CUR_JA[c] } : {};
    });
    cols.push({ re: new RegExp('^' + k[1].replace(/\*/g, '.*') + '$'), toks });
  }
  return (plan) => (cols.find((c) => c.re.test(plan)) || { toks: [] }).toks;
}
const colIndex = (toks, dims) => toks.findIndex((t) => (t.term ? t.term === String(dims.term) : t.currency ? t.currency === dims.currency : false));

function locateCondensed(text, find, dims) {
  const lines = text.split('\n');
  const fmt = (/^#format\s+(\S+)/m.exec(text) || [])[1] || '';
  const colsLine = lines.find((l) => l.startsWith('#cols')) || '';
  const toksFor = parseCols(colsLine);
  const band = bandAges(dims.age);
  if (!band || !dims.plan) return null;
  let off = 0;
  const lineAt = [];
  for (const l of lines) { lineAt.push(off); off += l.length + 1; }
  // Value tokens of a line, with their offsets.
  const tokens = (l, base) => [...l.matchAll(/\S+/g)].map((k) => ({ s: k[0], at: base + k.index }));
  if (fmt === 'mnl-condensed-v1') {
    // R <plan> <currency or ALL> <age as printed> <values>
    for (let i = 0; i < lines.length; i++) {
      const t = tokens(lines[i], lineAt[i]);
      if (t.length < 5 || t[0].s !== 'R' || t[1].s !== dims.plan) continue;
      if (t[2].s !== 'ALL' && t[2].s !== dims.currency) continue;
      const ages = labelAges(t[3].s);
      if (!ages.length || !ageMatch(ages[0], band)) continue;
      const toks = toksFor(dims.plan);
      const ci = toks.length ? colIndex(toks, t[2].s === 'ALL' ? { currency: dims.currency } : { term: dims.term }) : -1;
      const v = t[4 + ci];
      if (ci < 0 || !v || !sameValue(v.s, find)) return null;
      return { hits: [[v.at, v.at + v.s.length]], exact: true };
    }
    return null;
  }
  if (fmt === 'mnl-condensed-v1-past') {
    // Periods (newest first), then "P <PLAN>[_<CUR>]_<age key> [n=N] <one value per period>".
    const pi = lines.indexOf('#periods'), ti = lines.indexOf('#tables');
    if (pi < 0 || ti < 0) return null;
    const starts = lines.slice(pi + 1, ti).map((l) => { const k = /(\d{4})年(\d{2})月(\d{2})日/.exec(l); return k ? `${k[1]}-${k[2]}-${k[3]}` : ''; });
    const period = starts.indexOf(dims.date);
    if (period < 0) return null;
    // "#ages feed age key 30=34歳以下, 35=35~39歳, ..." -> the key for this band (else its lower bound).
    const agesLine = lines.find((l) => l.startsWith('#ages')) || '';
    let key = null;
    for (const k of agesLine.matchAll(/(\d+)=([^,(\s]+)/g)) { const a = labelAges(k[2]); if (a.length && ageMatch(a[0], band)) { key = k[1]; break; } }
    if (key == null && band[0] != null) key = String(band[0]);
    const plan = dims.plan.toUpperCase();
    const toks = toksFor(dims.plan);
    for (let i = ti + 1; i < lines.length; i++) {
      const t = tokens(lines[i], lineAt[i]);
      if (t.length < 3 || t[0].s !== 'P') continue;
      const name = t[1].s;
      const want = [plan + '_' + key, plan + '_' + dims.currency + '_' + key];
      if (!want.includes(name)) continue;
      const vals = t.slice(2).filter((x) => !/^n=/.test(x.s));
      const v = vals[period];
      if (!v) return null;
      if (v.s.includes('/')) {
        // A triple per period, in the column order of "#cols" (円 / 米ドル / 豪ドル).
        // The header names the triple's order ("a triple 円/米ドル/豪ドル") when "#cols" does not.
        let ci = colIndex(toks, { currency: dims.currency });
        if (ci < 0) {
          const k = /triple\s+(\S+)/.exec(text);
          ci = k ? k[1].replace(/[.,]$/, '').split('/').map((x) => CUR_JA[x.trim()]).indexOf(dims.currency) : -1;
        }
        const parts = v.s.split('/');
        if (ci < 0 || !parts[ci] || !sameValue(parts[ci], find)) return null;
        const at = v.at + parts.slice(0, ci).reduce((a, p) => a + p.length + 1, 0);
        return { hits: [[at, at + parts[ci].length]], exact: true };
      }
      // One value per period for every tenor that shows it.
      if (!sameValue(v.s, find)) return null;
      return { hits: [[v.at, v.at + v.s.length]], exact: true };
    }
    return null;
  }
  return null;
}

function locateText(text, find, dims) {
  if (!find) return { hits: [], exact: false };
  if (dims && /^#format\s+mnl-condensed/m.test(text)) {
    try { const r = locateCondensed(text, find, dims); if (r) return r; } catch { /* fall back to every match */ }
  }
  const hits = allHits(text, find);
  return { hits, exact: hits.length === 1 };
}

// ---------- HTML tables ----------
const ENTITY = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', yen: '¥' };
const decodeEntities = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (x, e) => (e[0] === '#' ? String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : +e.slice(1)) : ENTITY[e.toLowerCase()] ?? x));
const norm = (s) => decodeEntities(s).normalize('NFKC').replace(/\s+/g, ' ').trim();

// Tables with their cells: { tag, start, end (inner HTML range), text, colspan, rowspan, row }, and the text just before each table.
function parseTables(html) {
  const tables = [], stack = [];
  let lastText = '';
  const re = /<!--[\s\S]*?-->|<(script|style)\b[\s\S]*?<\/\1\s*>|<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>/g;
  let k, prev = 0;
  const closeCell = (tb, at) => {
    if (tb && tb.cell) { tb.cell.end = at; tb.cell.text = norm(html.slice(tb.cell.start, at).replace(/<[^>]*>/g, ' ')); tb.cell = null; }
  };
  while ((k = re.exec(html))) {
    const between = html.slice(prev, k.index);
    if (!stack.length) lastText = (lastText + ' ' + between.replace(/<[^>]*>/g, ' ')).slice(-400);
    prev = re.lastIndex;
    if (!k[3]) continue;
    const tag = k[3].toLowerCase(), close = k[2] === '/';
    const tb = stack[stack.length - 1];
    if (tag === 'table') {
      if (!close) { closeCell(tb, k.index); const t = { before: norm(lastText), rows: [], cell: null, caption: '' }; stack.push(t); tables.push(t); }
      else { closeCell(tb, k.index); stack.pop(); lastText = ''; }
      continue;
    }
    if (!tb) continue;
    if (tag === 'tr') { closeCell(tb, k.index); if (!close) tb.rows.push([]); continue; }
    if (tag === 'td' || tag === 'th') {
      closeCell(tb, k.index);
      if (close) continue;
      if (!tb.rows.length) tb.rows.push([]);
      const attr = k[4] || '';
      const num = (n) => { const m = new RegExp(n + '\\s*=\\s*["\']?(\\d+)', 'i').exec(attr); return m ? Math.max(1, Math.min(50, +m[1])) : 1; };
      tb.cell = { tag, start: re.lastIndex, end: re.lastIndex, text: '', colspan: num('colspan'), rowspan: num('rowspan') };
      tb.rows[tb.rows.length - 1].push(tb.cell);
      continue;
    }
    if (tag === 'tbody' || tag === 'thead' || tag === 'tfoot') closeCell(tb, k.index);
  }
  // Grid positions, rowspans and colspans taken into account.
  for (const t of tables) {
    const taken = [];
    t.rows.forEach((row, r) => {
      let c = 0;
      for (const cell of row) {
        while (taken[r] && taken[r][c]) c++;
        cell.r = r; cell.c = c;
        for (let dr = 0; dr < cell.rowspan; dr++) for (let dc = 0; dc < cell.colspan; dc++) { (taken[r + dr] = taken[r + dr] || [])[c + dc] = cell; }
        c += cell.colspan;
      }
    });
    t.grid = taken;
  }
  return tables;
}

const isFigure = (s) => /^[-+]?\d{1,3}(?:\.\d+)?\s*%?$/.test(s);
// Header text that applies to one cell: the cells to its left in its row and above it in its columns
// (other figures left out), plus the text just before the table.
function context(t, cell) {
  const parts = new Set();
  const row = t.grid[cell.r] || [];
  for (let c = 0; c < cell.c; c++) if (row[c] && row[c] !== cell && !isFigure(row[c].text)) parts.add(row[c]);
  for (let r = 0; r < cell.r; r++) for (let c = cell.c; c < cell.c + cell.colspan; c++) { const x = (t.grid[r] || [])[c]; if (x && x !== cell && !isFigure(x.text)) parts.add(x); }
  return { near: [...parts].map((x) => x.text).join(' | '), before: t.before };
}

const CUR_WORDS = { USD: /米ドル|米国ドル|\bUSD\b/, AUD: /豪ドル|豪州ドル|オーストラリアドル|\bAUD\b/, JPY: /円建|円\b|日本円|\bJPY\b/, EUR: /ユーロ|\bEUR\b/, NZD: /NZドル|ニュージーランドドル|\bNZD\b/ };
// +1 for each dimension the context names correctly, -1 for each it names otherwise.
function score(ctx, dims) {
  let plus = 0, minus = 0;
  const near = ctx.near, all = ctx.near + ' | ' + ctx.before;
  const sex = String(dims.sex || '').toUpperCase();
  if (sex === 'M' || sex === 'F') {
    const hasM = /男/.test(near), hasF = /女/.test(near);
    const own = sex === 'M' ? hasM : hasF, other = sex === 'M' ? hasF : hasM;
    if (own) plus++; else if (other) minus++;
  }
  if (dims.currency && CUR_WORDS[dims.currency]) {
    const own = CUR_WORDS[dims.currency];
    const others = Object.entries(CUR_WORDS).filter(([c]) => c !== dims.currency).map(([, r]) => r);
    if (own.test(near)) plus++;
    else if (others.some((r) => r.test(near))) minus++;
    else if (own.test(ctx.before) && !others.some((r) => r.test(ctx.before))) plus++;
  }
  const band = bandAges(dims.age);
  if (band) {
    const ages = labelAges(near);
    if (ages.length) { if (ages.some((a) => ageMatch(a, band))) plus++; else minus++; }
  }
  if (dims.term && /^\d+$/.test(String(dims.term))) {
    const terms = [...all.replace(/\d{4}年/g, '').matchAll(/(\d{1,2})\s*年/g)].map((x) => x[1]);
    const nearTerms = [...near.replace(/\d{4}年/g, '').matchAll(/(\d{1,2})\s*年/g)].map((x) => x[1]);
    if (nearTerms.length) { if (nearTerms.includes(String(dims.term))) plus++; else minus++; } else if (terms.includes(String(dims.term))) plus += 0;
  }
  return { plus, minus };
}

function locateHtml(html, find, dims) {
  if (!find) return { count: 0 };
  const tables = parseTables(html);
  const cands = [];
  for (const t of tables) for (const row of t.rows) for (const cell of row) if (isFigure(cell.text) && sameValue(cell.text.replace(/%$/, ''), find)) cands.push({ t, cell });
  if (!cands.length || !dims) return { count: cands.length };
  if (cands.length === 1) return { start: cands[0].cell.start, end: cands[0].cell.end, count: 1 };
  const scored = cands.map((x) => ({ ...x, s: score(context(x.t, x.cell), dims) }));
  const clean = scored.filter((x) => x.s.minus === 0);
  if (!clean.length) return { count: cands.length };
  const best = Math.max(...clean.map((x) => x.s.plus));
  const top = clean.filter((x) => x.s.plus === best);
  if (top.length === 1 && best > 0) return { start: top[0].cell.start, end: top[0].cell.end, count: cands.length };
  return { count: cands.length };
}

// Decoded text of a saved page: UTF-8, else the charset the page declares (Shift_JIS, EUC-JP).
function decodeHtml(buf) {
  const head = buf.slice(0, 4096).toString('latin1');
  const cs = ((/charset\s*=\s*["']?([\w-]+)/i.exec(head) || [])[1] || 'utf-8').toLowerCase();
  const tryDec = (enc) => { try { return new TextDecoder(enc, { fatal: true }).decode(buf); } catch { return null; } };
  return tryDec(/shift|sjis|euc|iso-2022/.test(cs) ? cs : 'utf-8') || tryDec('utf-8') || tryDec('shift_jis') || buf.toString('utf8');
}

// A copy of the page with the one cell marked, to be shown instead of the saved file.
function markedHtml(html, start, end, baseHref) {
  const style = '<style>mark#irv-hit{background:#ffd54a;color:#000;outline:2px solid #c98500;outline-offset:1px;padding:0 2px;scroll-margin-top:35vh}</style>';
  const base = baseHref ? `<base href="${baseHref.replace(/"/g, '%22')}">` : '';
  let out = html.slice(0, start) + '<mark id="irv-hit">' + html.slice(start, end) + '</mark>' + html.slice(end);
  const k = /<head\b[^>]*>/i.exec(out);
  out = k ? out.slice(0, k.index + k[0].length) + '<meta charset="utf-8">' + base + style + out.slice(k.index + k[0].length) : base + style + out;
  return '\ufeff' + out;
}

module.exports = { locateText, locateHtml, decodeHtml, markedHtml, parseTables, labelAges, bandAges };
