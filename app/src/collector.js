// Runs the repo's Python collectors on request: scripts/collect_all.py (rates and documents) or
// scripts/news.py collect (news lists, optionally followed by the English titles and summaries).
// This is the only place the app starts another program. The command is
// fixed: the page can choose which of the two runs, the insurers or news owners
// (validated against `--list`), and the documents / summaries flag, nothing else. No shell is used and nothing about the user
// or the machine is added to the command.
'use strict';

const { spawn, spawnSync, execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const { StringDecoder } = require('string_decoder');

const MAX_LOG_LINES = 5000;
const ID_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const ENV = () => ({ ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' });

let python; // undefined = not looked for yet, null = not found, else [cmd, ...args]
let child = null;
let run = null; // state of the current or most recent run in this session
let listed = { root: null, ids: [], newsIds: [] };

function scriptPath(root) {
  return path.join(root, 'scripts', 'collect_all.py');
}
function newsScript(root) {
  return path.join(root, 'scripts', 'news.py');
}

// python3, then python, then the Windows launcher. Needs 3.9 or later.
function findPython() {
  if (python !== undefined) return python;
  const candidates = [['python3'], ['python']];
  if (process.platform === 'win32') candidates.push(['py', '-3']);
  python = null;
  for (const c of candidates) {
    try {
      const r = spawnSync(c[0], [...c.slice(1), '-c', 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])'], {
        encoding: 'utf8', timeout: 10000, windowsHide: true,
      });
      if (r.status === 0 && Number(String(r.stdout).trim()) >= 309) { python = c; break; }
    } catch { /* try the next one */ }
  }
  return python;
}

function publicRun() {
  if (!run) return null;
  return { ...run, running: !!child };
}

// What the page needs to draw itself: whether a run is possible, the insurers
// from `--list`, and the state of any run in this session.
function info(root) {
  return new Promise((resolve) => {
    const base = { ok: false, reason: '', message: '', insurers: [], news: [], running: !!child, run: publicRun() };
    if (!fs.existsSync(scriptPath(root))) {
      return resolve({ ...base, reason: 'no-script',
        message: 'scripts/collect_all.py was not found in the chosen data folder, so there is nothing to run.' });
    }
    const py = findPython();
    if (!py) {
      return resolve({ ...base, reason: 'no-python',
        message: 'Python 3.9 or later is required and was not found (tried python3, python' +
          (process.platform === 'win32' ? ', py -3' : '') + '). Install it and restart the app.' });
    }
    execFile(py[0], [...py.slice(1), scriptPath(root), '--list'],
      { cwd: root, env: ENV(), timeout: 30000, windowsHide: true, encoding: 'utf8' }, (err, stdout) => {
        if (err) {
          return resolve({ ...base, reason: 'list-failed', message: 'The collector could not list its insurers: ' + err.message });
        }
        let insurers;
        try {
          insurers = JSON.parse(stdout).filter((x) => x && typeof x.id === 'string' && ID_RE.test(x.id))
            .map((x) => ({ id: x.id, name: String(x.name || x.id), kind: String(x.kind || '') }));
        } catch (e) {
          return resolve({ ...base, reason: 'list-failed', message: 'The collector\'s insurer list could not be read: ' + e.message });
        }
        const parse = (text) => JSON.parse(text).filter((x) => x && typeof x.id === 'string' && ID_RE.test(x.id))
          .map((x) => ({ id: x.id, name: String(x.name || x.id), kind: String(x.kind || '') }));
        const done = (news) => {
          listed = { root, ids: insurers.map((x) => x.id), newsIds: news.map((x) => x.id) };
          resolve({ ...base, ok: true, insurers, news });
        };
        if (!fs.existsSync(newsScript(root))) return done([]);
        execFile(py[0], [...py.slice(1), newsScript(root), '--list'],
          { cwd: root, env: ENV(), timeout: 30000, windowsHide: true, encoding: 'utf8' }, (err2, out2) => {
            let news = [];
            if (!err2) { try { news = parse(out2); } catch { news = []; } }
            done(news);
          });
      });
  });
}

function killTree() {
  if (!child) return;
  const pid = child.pid;
  if (process.platform === 'win32') {
    try { spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true }); } catch { /* ignore */ }
    return;
  }
  // The collector starts one script per insurer, so signal the whole group.
  const send = (sig) => { try { process.kill(-pid, sig); } catch { try { child && child.kill(sig); } catch { /* gone */ } } };
  send('SIGTERM');
  setTimeout(() => { if (child && child.pid === pid) send('SIGKILL'); }, 3000).unref();
}

// opts: { kind: 'rates' | 'news', insurers: [ids], docs: bool, enrich: bool }. extraEnv is added to the
// child's environment (the path of the app's own Claude Code, for the summaries). emit(event) sends progress to the
// page; onEnd(summary) is called once when the process has gone.
async function start(root, opts, emit, onEnd, extraEnv) {
  if (child) return { ok: false, error: 'A run is already in progress.' };
  if (listed.root !== root) {
    const i = await info(root);
    if (!i.ok) return { ok: false, error: i.message };
  }
  const py = findPython();
  const news = !!(opts && opts.kind === 'news');
  if (!py || !fs.existsSync(news ? newsScript(root) : scriptPath(root))) return { ok: false, error: 'The collector is not available.' };
  const known = news ? listed.newsIds : listed.ids;
  const ids = Array.isArray(opts && opts.insurers) ? [...new Set(opts.insurers)] : [];
  if (!ids.length) return { ok: false, error: 'Select at least one insurer.' };
  if (!ids.every((id) => typeof id === 'string' && known.includes(id))) {
    return { ok: false, error: 'Unknown insurer in the selection.' };
  }
  const ordered = known.filter((id) => ids.includes(id));
  const docs = !news && !!(opts && opts.docs);
  const enrich = news && !!(opts && opts.enrich);
  const args = [...py.slice(1), '-u', news ? newsScript(root) : scriptPath(root)];
  if (news) args.push('collect');
  if (docs) args.push('--docs');
  if (enrich) args.push('--enrich');
  args.push('--only', ordered.join(','));
  const rows = enrich ? [...ordered, 'enrich'] : ordered;

  run = {
    startedAt: new Date().toISOString(), finishedAt: null, outcome: 'running', exitCode: null, docs, enrich, kind: news ? 'news' : 'rates',
    insurers: Object.fromEntries(rows.map((id) => [id, { state: 'waiting', message: '', newPoints: 0 }])),
    conflicts: [], log: [],
  };
  let current = null;
  let cancelled = false;
  let ended = false;

  const onLine = (line) => {
    if (line.startsWith('@@status ')) {
      let s = null;
      try { s = JSON.parse(line.slice(9)); } catch { /* fall through as log text */ }
      if (s && run.insurers[s.insurer]) {
        const st = run.insurers[s.insurer];
        st.state = ['running', 'ok', 'failed', 'blocked'].includes(s.state) ? s.state : 'failed';
        st.message = String(s.message || '');
        current = st.state === 'running' ? s.insurer : null;
        emit({ kind: 'status', insurer: s.insurer, ...st });
        return;
      }
    }
    if (run.log.length < MAX_LOG_LINES) run.log.push(line);
    emit({ kind: 'line', text: line });
    if (/^\s*CONFLICT\b/.test(line)) {
      const c = { insurer: current, text: line.trim() };
      run.conflicts.push(c);
      emit({ kind: 'conflict', ...c });
    }
    const m = /(\d+) new\b/.exec(line);
    if (m && current) {
      run.insurers[current].newPoints += Number(m[1]);
      emit({ kind: 'status', insurer: current, ...run.insurers[current] });
    }
  };

  const finish = (code, errorText) => {
    if (ended) return;
    ended = true;
    child = null;
    for (const st of Object.values(run.insurers)) {
      if (st.state === 'waiting' || st.state === 'running') {
        st.state = cancelled ? 'cancelled' : st.state === 'running' ? 'failed' : 'not_run';
      }
    }
    run.exitCode = code;
    run.finishedAt = new Date().toISOString();
    run.outcome = cancelled ? 'cancelled' : errorText ? 'error' : code === 0 ? 'ok' : code === 2 ? 'partial' : 'error';
    if (errorText) { run.log.push(errorText); }
    const summary = {
      startedAt: run.startedAt, finishedAt: run.finishedAt, outcome: run.outcome, exitCode: code, docs, enrich, kind: run.kind,
      insurers: run.insurers, conflicts: run.conflicts.length,
    };
    emit({ kind: 'end', run: publicRun(), summary });
    onEnd(summary);
  };

  try {
    child = spawn(py[0], args, {
      cwd: root, env: { ...ENV(), ...(extraEnv || {}) }, shell: false, windowsHide: true,
      detached: process.platform !== 'win32', // own process group, so cancel reaches the per-insurer scripts
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    child = null; run = null;
    return { ok: false, error: 'Could not start Python: ' + e.message };
  }
  child.cancel = () => { cancelled = true; killTree(); };
  for (const stream of [child.stdout, child.stderr]) {
    const dec = new StringDecoder('utf8');
    let buf = '';
    stream.on('data', (chunk) => {
      buf += dec.write(chunk);
      const parts = buf.split(/\r?\n/);
      buf = parts.pop();
      parts.forEach(onLine);
    });
    stream.on('end', () => { buf += dec.end(); if (buf) onLine(buf); buf = ''; });
  }
  child.on('error', (e) => finish(null, 'Could not run the collector: ' + e.message));
  child.on('close', (code) => finish(code, null));
  emit({ kind: 'start', run: publicRun() });
  return { ok: true };
}

function cancel() {
  if (!child) return { ok: false, error: 'Nothing is running.' };
  child.cancel();
  return { ok: true };
}

const isRunning = () => !!child;

module.exports = { info, start, cancel, isRunning };
