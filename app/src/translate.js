// Brochure translation: starts the repo's translation engine (scripts/repaint/run.py) for one manifest-listed
// document, relays its progress, and serves the results. The engine does the work and uses the user's Claude Code
// sign-in; the app only starts it, shows progress and displays what it wrote. Everything the page asks for is
// identified by a manifest document and a page number, never by a path or an argument of its own.
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const { StringDecoder } = require('string_decoder');
const dataset = require('./dataset');

let job = null; // { file, child, cancelled, pages: {n: {stage, state, status}}, done, of, stage, log: [] }

const pad = (n) => 'p' + String(n).padStart(3, '0');
const safeName = (s) => /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(s);

function python(root) {
  const p = process.platform === 'win32' ? path.join(root, '.venv', 'Scripts', 'python.exe') : path.join(root, '.venv', 'bin', 'python');
  return fs.existsSync(p) ? p : null;
}
function engine(root) {
  const p = path.join(root, 'scripts', 'repaint', 'run.py');
  return fs.existsSync(p) ? p : null;
}
function availability(root) {
  if (!engine(root)) return { ok: false, message: 'The translation engine (scripts/repaint/run.py) is not in this data folder.' };
  if (!python(root)) return { ok: false, message: 'The Python environment for translation is not set up (no .venv in the data folder). See the README for the setup commands.' };
  return { ok: true, message: '' };
}

// The manifest entry for a document path, with the folders its translation lives in. null if not listed.
function locate(root, relFile) {
  const d = dataset.manifestDoc(root, relFile);
  if (!d) return null;
  const docName = path.basename(d.file).replace(/\.pdf$/i, '');
  if (!safeName(d.insurer_id) || !safeName(d.product_id) || !safeName(docName)) return null;
  const base = path.join(root, 'translations', d.insurer_id, d.product_id);
  return { doc: d, docName, src: path.join(root, d.file),
    work: path.join(root, 'translations', '_work', d.product_id, docName),
    pagesDir: path.join(base, docName, 'pages'), docDir: path.join(base, docName),
    out: path.join(base, docName + '.en.pdf'), outJson: path.join(base, docName + '.en.json'),
    // Left by the app when a run was cancelled: the engine then records the pages it never reached as failed,
    // and they should read as not translated yet.
    cancelMark: path.join(base, docName, 'cancelled') };
}
const readJson = (f) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return null; } };
function pageFiles(dir) {
  const out = [];
  let names = [];
  try { names = fs.readdirSync(dir); } catch { return out; }
  for (const n of names) { const m = /^p(\d{3,4})\.pdf$/.exec(n); if (m) out.push(Number(m[1])); }
  return out.sort((a, b) => a - b);
}

// What exists for a document: which pages have an English version, each page's status from the engine's
// result file, and the running job if it is for this document.
function status(root, relFile) {
  const av = availability(root);
  const loc = locate(root, relFile);
  if (!loc) return { ok: false, error: 'Not a document listed in a manifest of this data folder.' };
  const have = pageFiles(loc.pagesDir);
  const pages = {};
  for (const n of have) pages[n] = { has: true, status: 'ok' };
  const j = readJson(loc.outJson);
  if (j && Array.isArray(j.pages)) {
    for (const p of j.pages) {
      const n = Number(p.page);
      if (!Number.isInteger(n) || n < 1) continue;
      const prob = Array.isArray(p.problems) ? p.problems.map(String).slice(0, 3) : [];
      pages[n] = { has: have.includes(n), status: String(p.status || (have.includes(n) ? 'ok' : 'untranslated')),
        errors: Number(p.errors) || (Array.isArray(p.errors) ? p.errors.length : 0), warnings: Number(p.warnings) || (Array.isArray(p.warnings) ? p.warnings.length : 0),
        reason: prob.join('; ') };
    }
  }
  if (fs.existsSync(loc.cancelMark)) for (const n of Object.keys(pages)) if (!pages[n].has) delete pages[n];
  let date = '';
  try { date = fs.statSync(fs.existsSync(loc.outJson) ? loc.outJson : loc.pagesDir).mtime.toISOString(); } catch { /* none */ }
  const whole = !!(j && Array.isArray(j.pages_requested) && j.pages_requested.length > 1 && fs.existsSync(loc.out));
  return { ok: true, available: av.ok, message: av.message, pages, translated: have.length, hasOutput: fs.existsSync(loc.out),
    whole: whole && !fs.existsSync(loc.cancelMark), date,
    job: job ? { running: true, mine: job.file === loc.doc.file, done: job.done, of: job.of, stage: job.stage, pages: job.file === loc.doc.file ? job.pages : {} } : null };
}

// Short status for the Documents list: { file: { state: 'none'|'partial'|'complete', pages, date } }
function summary(root, files) {
  const out = {};
  for (const f of Array.isArray(files) ? files.slice(0, 200) : []) {
    const s = typeof f === 'string' ? status(root, f) : null;
    if (!s || !s.ok) continue;
    const failed = Object.values(s.pages).some((p) => !p.has);
    out[f] = { state: !s.translated ? 'none' : s.whole && !failed ? 'complete' : 'partial', pages: s.translated, date: s.date, hasOutput: s.hasOutput };
  }
  return out;
}

function killTree(child) {
  const pid = child.pid;
  if (process.platform === 'win32') { try { spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true }); } catch { /* ignore */ } return; }
  const send = (sig) => { try { process.kill(-pid, sig); } catch { try { child.kill(sig); } catch { /* gone */ } } };
  send('SIGTERM');
  setTimeout(() => { if (job && job.child === child) send('SIGKILL'); }, 4000).unref();
}

// opts: { file, mode: 'page' | 'all', page }. emit(event) relays progress to the window.
function start(root, opts, emit, extraEnv) {
  if (job) return { ok: false, error: job.file === (opts && opts.file) ? 'This document is already being translated.' : 'Another document is being translated. Wait for it to finish or cancel it first.' };
  const av = availability(root);
  if (!av.ok) return { ok: false, error: av.message };
  const loc = locate(root, opts && opts.file);
  if (!loc) return { ok: false, error: 'Not a document listed in a manifest of this data folder.' };
  if (!fs.existsSync(loc.src)) return { ok: false, error: 'The document file is missing.' };
  const page = Math.floor(Number(opts.page));
  if (!Number.isInteger(page) || page < 1 || page > 5000) return { ok: false, error: 'Bad page number.' };
  const mode = opts.mode === 'all' ? 'all' : 'page';
  // The engine's translator: Claude Code. IRV_TRANSLATOR=files replays finished work with no model calls (tests).
  const translator = process.env.IRV_TRANSLATOR === 'files' ? 'files' : 'claude';
  const args = [engine(root), loc.src, ...(mode === 'all' ? ['--all', '--first', String(page)] : ['--pages', String(page)]),
    '--work', loc.work, '--out', loc.out, '--translator', translator, '--page-dir-out', loc.pagesDir];
  try { fs.mkdirSync(loc.work, { recursive: true }); fs.mkdirSync(loc.pagesDir, { recursive: true }); fs.rmSync(loc.cancelMark, { force: true }); } catch (e) { return { ok: false, error: e.message }; }

  let child;
  try {
    child = spawn(python(root), args, { cwd: root, shell: false, windowsHide: true, detached: process.platform !== 'win32',
      env: { ...process.env, ...extraEnv, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) { return { ok: false, error: 'Could not start the translation engine: ' + e.message }; }
  const mine = (job = { file: loc.doc.file, child, cancelled: false, pages: {}, done: 0, of: mode === 'all' ? 0 : 1, stage: 'Starting', log: [], mode });
  const send = (ev) => emit({ file: mine.file, ...ev });
  const onLine = (line) => {
    if (line.startsWith('@@progress ')) {
      let p = null;
      try { p = JSON.parse(line.slice(11)); } catch { /* treat as log */ }
      if (p && typeof p === 'object') {
        const n = Number.isInteger(p.page) ? p.page : null;
        if (Number.isInteger(p.of) && p.of > 0) mine.of = p.of;
        const stage = String(p.stage || ''), state = String(p.state || '');
        if (n) {
          const rec = (mine.pages[n] = mine.pages[n] || {});
          rec.stage = stage; rec.state = state;
          if (stage === 'page' && state === 'done') { rec.status = String(p.status || 'ok'); mine.done++; }
          if (state === 'failed') rec.status = 'failed';
          if (p.message && (state === 'failed' || rec.status === 'failed')) rec.reason = String(p.message).slice(0, 300);
        }
        mine.stage = n ? `page ${n}: ${stage === 'page' ? 'finished' : stage}` : stage;
        send({ kind: 'progress', page: n, stage, state, status: p.status ? String(p.status) : undefined,
          message: p.message ? String(p.message).slice(0, 300) : '', done: mine.done, of: mine.of });
        return;
      }
    }
    if (line.trim()) { mine.log.push(line); if (mine.log.length > 200) mine.log.shift(); }
  };
  for (const stream of [child.stdout, child.stderr]) {
    const dec = new StringDecoder('utf8');
    let buf = '';
    stream.on('data', (c) => { buf += dec.write(c); const parts = buf.split(/\r?\n/); buf = parts.pop(); parts.forEach(onLine); });
    stream.on('end', () => { buf += dec.end(); if (buf) onLine(buf); buf = ''; });
  }
  let ended = false;
  const finish = (code, err) => {
    if (ended) return;
    ended = true;
    if (job === mine) job = null;
    if (mine.cancelled) { try { fs.writeFileSync(loc.cancelMark, new Date().toISOString()); } catch { /* only a hint */ } }
    // The engine's own last words explain a failure (for example that Claude Code is not signed in).
    const said = mine.log.find((l) => /^Claude Code is not installed or not signed in/.test(l.trim())) || '';
    const message = mine.cancelled ? 'Cancelled.' : err ? 'The translation engine could not be run: ' + err
      : code === 0 ? '' : (said.trim() || mine.log.slice(-2).join(' ').trim() || 'Nothing usable was produced.').slice(0, 600);
    send({ kind: 'end', code, cancelled: mine.cancelled, ok: code === 0 && !mine.cancelled, message, done: mine.done, of: mine.of });
  };
  child.on('error', (e) => finish(null, e.message));
  child.on('close', (code) => finish(code, null));
  send({ kind: 'start', mode, of: mine.of });
  return { ok: true };
}

function cancel() {
  if (!job) return { ok: false, error: 'No translation is running.' };
  job.cancelled = true;
  killTree(job.child);
  return { ok: true };
}

// Removes the English output and the per-page folder for one document. The engine's work folder and the
// translation memory stay, so translating it again is quick.
function remove(root, relFile) {
  const loc = locate(root, relFile);
  if (!loc) return { ok: false, error: 'Not a document listed in a manifest of this data folder.' };
  if (job && job.file === loc.doc.file) return { ok: false, error: 'This document is being translated. Cancel that first.' };
  const inside = (p) => { const rel = path.relative(path.join(root, 'translations'), p); return rel && !rel.startsWith('..') && !path.isAbsolute(rel); };
  try {
    for (const f of [loc.out, loc.outJson]) if (inside(f) && fs.existsSync(f)) fs.unlinkSync(f);
    if (inside(loc.docDir) && fs.existsSync(loc.docDir)) fs.rmSync(loc.docDir, { recursive: true, force: true });
    return { ok: true };
  } catch (e) { return { ok: false, error: e.message }; }
}

// Bytes of one translated page.
function readPage(root, relFile, n) {
  const loc = locate(root, relFile);
  const page = Math.floor(Number(n));
  if (!loc || !Number.isInteger(page) || page < 1 || page > 5000) return { ok: false, error: 'Bad request' };
  const f = path.join(loc.pagesDir, pad(page) + '.pdf');
  const abs = dataset.safeResolve(root, path.relative(root, f));
  if (!abs) return { ok: false, error: 'That page has no English version.' };
  try { return { ok: true, data: fs.readFileSync(abs) }; } catch (e) { return { ok: false, error: e.message }; }
}

// "What to check" for one page: the engine's findings that are warnings or errors, grouped by message.
function checks(root, relFile, n) {
  const loc = locate(root, relFile);
  const page = Math.floor(Number(n));
  if (!loc || !Number.isInteger(page) || page < 1 || page > 5000) return { ok: false, error: 'Bad request' };
  const c = readJson(path.join(loc.work, pad(page), 'checks.json'));
  if (!c) return { ok: true, items: [], verdict: '' };
  const tally = new Map();
  const add = (f) => {
    if (!f || (f.severity !== 'error' && f.severity !== 'warn' && f.severity !== 'warning')) return;
    const sev = f.severity === 'error' ? 'error' : 'warning';
    const key = sev + '|' + String(f.message || f.check || '');
    const cur = tally.get(key) || { severity: sev, message: String(f.message || f.check || '').slice(0, 300), count: 0 };
    cur.count++; tally.set(key, cur);
  };
  for (const u of Array.isArray(c.units) ? c.units : []) for (const f of Array.isArray(u.findings) ? u.findings : []) add(f);
  for (const f of Array.isArray(c.page) ? c.page : []) add(f);
  const items = [...tally.values()].sort((a, b) => (a.severity === b.severity ? b.count - a.count : a.severity === 'error' ? -1 : 1)).slice(0, 30);
  return { ok: true, items, verdict: String((c.summary && c.summary.verdict) || '') };
}

function outputPath(root, relFile) {
  const loc = locate(root, relFile);
  return loc && fs.existsSync(loc.out) ? loc.out : null;
}

module.exports = { status, summary, start, cancel, remove, readPage, checks, outputPath, availability, isRunning: () => !!job };
