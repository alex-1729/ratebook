// All file-system access for the dataset lives here (main process only).
// Every path is resolved against the chosen data root and checked to stay
// inside it. Every field in the data is treated as optional.
'use strict';

const fs = require('fs');
const path = require('path');
const { parseCsv } = require('./csv');

const OPENABLE_EXT = new Set(['.pdf', '.html', '.htm', '.json', '.txt']);

function isInside(root, target) {
  const rel = path.relative(root, target);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

// Resolve a path relative to the root; returns null if it escapes the root
// (including through symlinks) or does not exist.
function safeResolve(root, relPath) {
  if (typeof relPath !== 'string' || relPath === '' || relPath.includes('\0')) return null;
  const abs = path.resolve(root, relPath);
  if (!isInside(root, abs)) return null;
  try {
    const real = fs.realpathSync(abs);
    return insideRealRoot(root, real) ? real : null;
  } catch {
    return null;
  }
}

// The real locations a data folder covers: the folder itself and, where one of its top-level folders is a
// symlink (sources/ kept on another disk, say), that link's target. A real path outside all of them is refused.
const TOP = ['data', 'sources', 'reports', 'translations'];
function realRoots(root) {
  const out = [];
  try { out.push(fs.realpathSync(root)); } catch { return out; }
  for (const name of TOP) { try { out.push(fs.realpathSync(path.join(root, name))); } catch { /* not there */ } }
  return out;
}
function insideRealRoot(root, real) {
  return realRoots(root).some((r) => isInside(r, real));
}

function listDir(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

function readJson(file, warnings) {
  try {
    let text = fs.readFileSync(file, 'utf8');
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    return JSON.parse(text);
  } catch (e) {
    // A collector may be half-way through writing this file.
    warnings.push(`Could not read ${path.basename(file)}: ${e.message}`);
    return null;
  }
}

function toPosix(p) {
  return p.split(path.sep).join('/');
}

function looksLikeDataFolder(root) {
  try {
    return fs.statSync(path.join(root, 'data', 'products')).isDirectory();
  } catch {
    return false;
  }
}

// Find where a manifest's `file` value actually lives. The column has been
// seen (or could plausibly be written) relative to the repo root, to the
// manifest's folder, or as a bare file name inside the product's folder.
function locateDoc(root, docsDir, productId, file) {
  if (!file) return null;
  const candidates = [
    path.resolve(root, file),
    path.resolve(docsDir, file),
    path.resolve(docsDir, productId || '', file),
    path.resolve(docsDir, productId || '', path.basename(file)),
  ];
  for (const c of candidates) {
    if (!isInside(root, c)) continue;
    try {
      const st = fs.statSync(c);
      if (st.isFile()) return { abs: c, bytes: st.size };
    } catch { /* try next */ }
  }
  return null;
}

function loadDocuments(root, warnings) {
  // productId -> [{ name, title_ja, url, file (root-relative, posix), bytes,
  //                 sha256, retrieved_date, insurer_id, in_manifest }]
  const byProduct = {};
  const add = (pid, doc) => { (byProduct[pid] = byProduct[pid] || []).push(doc); };
  const sourcesDir = path.join(root, 'sources');

  for (const ins of listDir(sourcesDir)) {
    if (!ins.isDirectory()) continue;
    const docsDir = path.join(sourcesDir, ins.name, 'docs');
    const seen = new Set(); // absolute paths already listed via the manifest

    const manifestPath = path.join(docsDir, 'manifest.csv');
    if (fs.existsSync(manifestPath)) {
      let rows = [];
      try {
        rows = parseCsv(fs.readFileSync(manifestPath, 'utf8'));
      } catch (e) {
        warnings.push(`Could not read ${ins.name}/docs/manifest.csv: ${e.message}`);
      }
      for (const r of rows) {
        const pid = r.product_id || '';
        const found = locateDoc(root, docsDir, pid, r.file);
        if (found) seen.add(found.abs);
        add(pid, {
          insurer_id: ins.name,
          product_id: pid,
          name: r.name || '',
          title_ja: r.title_ja || '',
          url: r.url || '',
          file: found ? toPosix(path.relative(root, found.abs)) : '',
          missing_file: !found && !!r.file,
          bytes: found ? found.bytes : (Number(r.bytes) || null),
          sha256: r.sha256 || '',
          retrieved_date: r.retrieved_date || '',
          // Optional columns (present in some insurers' manifests only).
          via_wayback: /^(true|1|yes)$/i.test(r.via_wayback || ''),
          wayback_url: r.wayback_url || '',
          status: r.status || '',
          capture_timestamp: r.capture_timestamp || '',
          in_manifest: true,
        });
      }
    }

    // Files on disk that the manifest does not (yet) mention.
    for (const prod of listDir(docsDir)) {
      if (!prod.isDirectory()) continue;
      for (const f of listDir(path.join(docsDir, prod.name))) {
        if (!f.isFile() || path.extname(f.name).toLowerCase() !== '.pdf') continue;
        const abs = path.join(docsDir, prod.name, f.name);
        if (seen.has(abs)) continue;
        let bytes = null;
        try { bytes = fs.statSync(abs).size; } catch { /* ignore */ }
        const m = /^(.*?)_(\d{4}-\d{2}-\d{2})\.pdf$/i.exec(f.name);
        add(prod.name, {
          insurer_id: ins.name,
          name: m ? m[1] : path.basename(f.name, path.extname(f.name)),
          title_ja: '',
          url: '',
          file: toPosix(path.relative(root, abs)),
          missing_file: false,
          bytes,
          sha256: '',
          retrieved_date: m ? m[2] : '',
          in_manifest: false,
        });
      }
    }
  }
  return byProduct;
}

// News: data/news/items/*.jsonl (one item per line), data/news/stories/*.json, data/news/sources/*.json (owners
// of news sources that are not insurers) and data/news/state.json (when each source was last checked).
function loadNews(root, warnings) {
  const news = { items: [], stories: [], owners: [], state: {} };
  const dir = path.join(root, 'data', 'news');
  const seen = new Set();
  for (const e of listDir(path.join(dir, 'items'))) {
    if (!e.isFile() || !e.name.toLowerCase().endsWith('.jsonl')) continue;
    let text;
    try { text = fs.readFileSync(path.join(dir, 'items', e.name), 'utf8'); } catch (err) {
      warnings.push(`Could not read news/items/${e.name}: ${err.message}`); continue;
    }
    let bad = 0;
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      let j;
      try { j = JSON.parse(line); } catch { bad++; continue; }
      if (!j || typeof j !== 'object' || typeof j.id !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(j.date || '') || seen.has(j.id)) { bad += j && seen.has(j.id) ? 0 : 1; continue; }
      seen.add(j.id);
      news.items.push(j);
    }
    // A collector may be half-way through writing this file.
    if (bad) warnings.push(`news/items/${e.name}: ${bad} line(s) could not be read`);
  }
  for (const [sub, list] of [['stories', news.stories], ['sources', news.owners]]) {
    for (const e of listDir(path.join(dir, sub))) {
      if (!e.isFile() || !e.name.toLowerCase().endsWith('.json')) continue;
      const j = readJson(path.join(dir, sub, e.name), warnings);
      if (j && typeof j === 'object' && !Array.isArray(j)) {
        if (!j.id) j.id = path.basename(e.name, '.json');
        list.push(j);
      }
    }
  }
  const stateFile = path.join(dir, 'state.json');
  if (fs.existsSync(stateFile)) {
    const j = readJson(stateFile, warnings);
    if (j && j.sources && typeof j.sources === 'object') news.state = j.sources;
  }
  return news;
}

function loadDataset(root) {
  const warnings = [];
  const result = {
    root,
    valid: looksLikeDataFolder(root),
    insurers: [],
    products: [],
    documents: {},
    rateFiles: {},
    productTypes: [],
    news: { items: [], stories: [], owners: [], state: {} },
    warnings,
    loadedAt: new Date().toISOString(),
  };
  if (!result.valid) return result;

  for (const e of listDir(path.join(root, 'data', 'insurers'))) {
    if (!e.isFile() || !e.name.toLowerCase().endsWith('.json')) continue;
    const j = readJson(path.join(root, 'data', 'insurers', e.name), warnings);
    if (j && typeof j === 'object' && !Array.isArray(j)) {
      if (!j.id) j.id = path.basename(e.name, '.json');
      result.insurers.push(j);
    }
  }

  for (const e of listDir(path.join(root, 'data', 'products'))) {
    if (!e.isFile() || !e.name.toLowerCase().endsWith('.json')) continue;
    const j = readJson(path.join(root, 'data', 'products', e.name), warnings);
    if (j && typeof j === 'object' && !Array.isArray(j)) {
      if (!j.id) j.id = path.basename(e.name, '.json');
      result.products.push(j);
    }
  }

  // Rate CSVs present on disk, grouped by the product id in the file name
  // (<product_id>__<metric>__<plan>.csv).
  for (const e of listDir(path.join(root, 'data', 'rates'))) {
    if (!e.isFile() || !e.name.toLowerCase().endsWith('.csv')) continue;
    const pid = e.name.split('__')[0];
    (result.rateFiles[pid] = result.rateFiles[pid] || []).push(e.name);
  }

  // Product-type taxonomy (optional): data/product_types.json
  const typesFile = path.join(root, 'data', 'product_types.json');
  if (fs.existsSync(typesFile)) {
    const j = readJson(typesFile, warnings);
    if (j && Array.isArray(j.types)) result.productTypes = j.types.filter((t) => t && typeof t.key === 'string');
  }

  result.documents = loadDocuments(root, warnings);
  result.govtYields = loadGovtYields(root, warnings);
  const value = loadValueReport(root, warnings);
  result.netYieldCheck = value.check;
  result.modelYields = value.yields;
  result.news = loadNews(root, warnings);
  return result;
}

// The value model's report (reports/like_for_like_value.json, written by scripts/value/run.py). Optional.
// check: its independent check of each published net yield, the brochure's up-front charge rolled forward
//   against the 実質利回り.
// yields: its own yield to the buyer for plans with no published net yield, from the crediting rate and the
//   up-front charge, for the period the model ran on. Only crediting-rate plans; payout-rate plans are left out.
function loadValueReport(root, warnings) {
  const out = { check: [], yields: [] };
  const file = path.join(root, 'reports', 'like_for_like_value.json');
  if (!fs.existsSync(file)) return out;
  const j = readJson(file, warnings);
  const rows = j && j.validation && j.validation.net_yield_check;
  if (Array.isArray(rows)) out.check = rows.filter((r) => r && (r.status === 'match' || r.status === 'differs'));
  const num = (v) => typeof v === 'number' && Number.isFinite(v);
  for (const r of (j && Array.isArray(j.accumulation) ? j.accumulation : [])) {
    if (!r || r.published_net_yield_pct != null || r.rate_metric !== 'crediting_rate') continue;
    if (!num(r.irr_contract_currency_pct) || !num(r.crediting_rate_pct) || !num(r.term_years) || !num(r.initial_load_pct)) continue;
    if (typeof r.product_id !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(r.effective_date || '')) continue;
    out.yields.push({ product_id: r.product_id, plan: String(r.plan || ''), currency: String(r.currency || ''), term_years: r.term_years,
      age_band: String(r.age_band || ''), effective_date: r.effective_date, crediting_pct: r.crediting_rate_pct,
      charge_pct: r.initial_load_pct, charge_source: String(r.initial_load_source || ''), yield_pct: r.irr_contract_currency_pct,
      pays_interest: !!r.pays_interest });
  }
  return out;
}

// Government par yields (data/markets/govt_yield__<CCY>.csv, written by the collectors):
// { USD: { curve: 'UST', source: <url of the latest row>, tenors: { '5': [[date, pct], ...] } } }, dates ascending.
function loadGovtYields(root, warnings) {
  const out = {};
  for (const e of listDir(path.join(root, 'data', 'markets'))) {
    if (!e.isFile() || !/^govt_yield__[A-Z]{3}\.csv$/.test(e.name)) continue;
    let rows;
    try { rows = parseCsv(fs.readFileSync(path.join(root, 'data', 'markets', e.name), 'utf8')); } catch (err) { warnings.push(`${e.name}: ${err.message}`); continue; }
    for (const r of rows) {
      const v = parseFloat(r.value_pct), t = String(r.tenor_years || '');
      if (!r.currency || !t || !/^\d{4}-\d{2}-\d{2}$/.test(r.date || '') || !Number.isFinite(v)) continue;
      const c = (out[r.currency] = out[r.currency] || { curve: r.curve, source: '', tenors: {} });
      (c.tenors[t] = c.tenors[t] || []).push([r.date, v]);
      c.source = r.source_url || c.source;
    }
  }
  for (const c of Object.values(out)) for (const list of Object.values(c.tenors)) list.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return out;
}

// Returns [{ file, text }] for every rate CSV of a product: the ones named
// in its rate_series plus any on disk that match its id.
function readRates(root, productId, listedCsvs) {
  const ratesDir = path.join(root, 'data', 'rates');
  const names = new Set();
  for (const e of listDir(ratesDir)) {
    if (e.isFile() && e.name.toLowerCase().endsWith('.csv') && e.name.split('__')[0] === productId) {
      names.add(e.name);
    }
  }
  for (const n of Array.isArray(listedCsvs) ? listedCsvs : []) {
    if (typeof n === 'string' && n.toLowerCase().endsWith('.csv')) names.add(path.basename(n));
  }
  const out = [];
  for (const name of [...names].sort()) {
    const abs = safeResolve(root, path.join('data', 'rates', name));
    if (!abs) { out.push({ file: name, text: null, error: 'missing' }); continue; }
    try {
      out.push({ file: name, text: fs.readFileSync(abs, 'utf8') });
    } catch (e) {
      out.push({ file: name, text: null, error: e.message });
    }
  }
  return out;
}

// A local file the UI may hand to the OS: must be inside <root>/sources and
// be a PDF or HTML snapshot.
function resolveOpenable(root, relPath) {
  const abs = safeResolve(root, relPath);
  if (!abs) return null;
  // Asked for as sources/..., and really inside the sources folder (wherever that folder really is).
  const rel = path.relative(root, path.resolve(root, relPath));
  if (rel.split(path.sep)[0] !== 'sources') return null;
  let realSources;
  try { realSources = fs.realpathSync(path.join(root, 'sources')); } catch { return null; }
  if (!isInside(realSources, abs)) return null;
  if (!OPENABLE_EXT.has(path.extname(abs).toLowerCase())) return null;
  return abs;
}

// The manifest entry for a document path (relative to the data folder), or null when no manifest lists it.
function manifestDoc(root, relPath) {
  if (typeof relPath !== 'string' || !relPath || relPath.includes('\0')) return null;
  const wanted = toPosix(path.normalize(relPath));
  for (const docs of Object.values(loadDocuments(root, []))) {
    for (const d of docs) if (d.in_manifest && d.file === wanted) return d;
  }
  return null;
}

// Bytes of a PDF for the embedded viewer. Only files that a
// sources/*/docs/manifest.csv under the data folder lists are served, and the
// resolved path must stay inside that folder.
const MAX_PDF_BYTES = 300 * 1024 * 1024;
function readManifestPdf(root, relPath) {
  if (typeof relPath !== 'string' || !relPath || relPath.includes('\0')) return { ok: false, error: 'Bad request' };
  const wanted = toPosix(path.normalize(relPath));
  const listed = new Set();
  for (const docs of Object.values(loadDocuments(root, []))) {
    for (const d of docs) if (d.in_manifest && d.file) listed.add(d.file);
  }
  if (!listed.has(wanted)) return { ok: false, error: 'Not a document listed in a manifest of this data folder' };
  const abs = safeResolve(root, wanted);
  if (!abs || path.extname(abs).toLowerCase() !== '.pdf') return { ok: false, error: 'File not found inside the data folder' };
  try {
    const st = fs.statSync(abs);
    if (!st.isFile() || st.size > MAX_PDF_BYTES) return { ok: false, error: 'File is not readable or too large' };
    return { ok: true, data: fs.readFileSync(abs) };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

module.exports = { loadDataset, readRates, resolveOpenable, looksLikeDataFolder, safeResolve, readManifestPdf, manifestDoc, insideRealRoot };
