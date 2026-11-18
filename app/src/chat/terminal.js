'use strict';
// Claude Code in the chat pane: one `claude` process in a pseudo-terminal, started in the data folder with the app's
// tools (through bridge.js), the app's instructions, and nothing else: built-in tools are limited to reading files
// and asking the user, every read is checked by the app, and the user's own Claude Code settings, plugins and MCP
// servers are not loaded. The sign-in is the user's Claude Code sign-in. Adapted from the user's Tessera app.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFile } = require('child_process');

// Files that another program runs must be real files, not entries inside app.asar.
const unpacked = (p) => p.replace(/app\.asar(?!\.unpacked)(?=[\\/]|$)/, 'app.asar.unpacked');
const APP_DIR = unpacked(path.join(__dirname, '..', '..'));
const BRIDGE = unpacked(path.join(__dirname, 'bridge.js'));
const sh = (s) => (process.platform === 'win32' ? `"${String(s).replace(/"/g, '\\"')}"` : `'${String(s).replace(/'/g, `'\\''`)}'`);

// The claude program that ships with the Agent SDK. IRV_CLAUDE_BIN names another one (for tests).
function findClaude() {
  if (process.env.IRV_CLAUDE_BIN) return fs.existsSync(process.env.IRV_CLAUDE_BIN) ? process.env.IRV_CLAUDE_BIN : null;
  const dir = path.join(APP_DIR, 'node_modules', '@anthropic-ai');
  let names = [];
  try { names = fs.readdirSync(dir); } catch { /* none */ }
  for (const n of names) {
    if (!n.startsWith('claude-agent-sdk-')) continue;
    for (const exe of ['claude', 'claude.exe']) { const f = path.join(dir, n, exe); if (fs.existsSync(f)) return f; }
  }
  return null;
}

function loadPty() {
  // node-pty's helper must be executable; npm does not always keep the bit when install scripts are off
  const helper = path.join(APP_DIR, 'node_modules', 'node-pty', 'prebuilds', `${process.platform}-${process.arch}`, 'spawn-helper');
  try { fs.chmodSync(helper, 0o755); } catch { /* not this platform, or already fine */ }
  return require('node-pty');
}

// An app started from inside another Claude Code session inherits that session's switches. Claude Code in the pane
// must behave as it does when the user starts it themself.
const KEEP = new Set(['CLAUDE_CODE_DISABLE_AUTO_MEMORY', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC']);
function cleanEnv(extra) {
  const env = { ...process.env, ...extra };
  delete env.ELECTRON_RUN_AS_NODE;
  for (const k of Object.keys(env)) {
    if (/^(CLAUDECODE$|CLAUDE_PID$|CLAUDE_EFFORT$|CLAUDE_CODE_|CLAUDE_AGENT_SDK_|CLAUDE_PREVIEW_)/.test(k) && !KEEP.has(k)) delete env[k];
  }
  return env;
}

// Can the pane run? -> { ok, message }
function status() {
  return new Promise((resolve) => {
    const claude = findClaude();
    if (!claude) return resolve({ ok: false, message: 'Claude Code is not installed with the app. Run npm install in app/, then reopen this pane.' });
    try { loadPty(); } catch (e) { return resolve({ ok: false, message: 'The terminal component (node-pty) could not be loaded: ' + e.message }); }
    execFile(claude, ['auth', 'status'], { env: cleanEnv({}), timeout: 15000, windowsHide: true }, (err, stdout) => {
      let signedIn = null;
      try { signedIn = JSON.parse(stdout).loggedIn === true; } catch { /* older output: let Claude Code say so itself */ }
      if (signedIn === false || (err && signedIn === null && /not logged in|login/i.test(String(stdout) + String(err.message)))) {
        return resolve({ ok: false, message: 'Claude Code is not signed in. Run `claude` in a terminal and sign in, then reopen this pane.' });
      }
      resolve({ ok: true, message: '' });
    });
  });
}

function claudeArgs({ port, token, model, systemPrompt, resume, sessionId }) {
  const node = `ELECTRON_RUN_AS_NODE=1 ${sh(process.execPath)} ${sh(BRIDGE)}`;
  const hookCmd = process.platform === 'win32' ? `set ELECTRON_RUN_AS_NODE=1&& ${sh(process.execPath)} ${sh(BRIDGE)}` : node;
  const mcp = { mcpServers: { rates: { command: process.execPath, args: [BRIDGE, 'mcp'],
    env: { ELECTRON_RUN_AS_NODE: '1', IRV_PORT: String(port), IRV_TOKEN: token } } } };
  const settings = { hooks: {
    UserPromptSubmit: [{ hooks: [{ type: 'command', command: `${hookCmd} hook prompt` }] }],
    // Every built-in read goes to the app, which allows only data/, reports/ and sources/<insurer>/docs/.
    PreToolUse: [{ matcher: 'Read|Glob|Grep', hooks: [{ type: 'command', command: `${hookCmd} hook pretool` }] }] } };
  const args = [
    // Never --continue: the data folder is also where the user's own Claude Code sessions live. The pane opens
    // only conversations the app started itself, by id.
    ...(typeof resume === 'string' ? ['--resume', resume] : ['--session-id', sessionId]),
    '--model', model,
    '--append-system-prompt', systemPrompt,
    '--mcp-config', JSON.stringify(mcp), '--strict-mcp-config',
    '--settings', JSON.stringify(settings), '--setting-sources', '',
    '--permission-mode', 'default',
    '--tools', 'Read,Glob,Grep,AskUserQuestion',
    '--allowedTools', 'AskUserQuestion,mcp__rates',
  ];
  // No telemetry, error reports or self-update from the embedded Claude Code: only the conversation itself.
  const env = cleanEnv({ TERM: 'xterm-256color', COLORTERM: 'truecolor', CLAUDE_CODE_DISABLE_AUTO_MEMORY: '1',
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1', DISABLE_TELEMETRY: '1', DISABLE_ERROR_REPORTING: '1', DISABLE_AUTOUPDATER: '1',
    IRV_PORT: String(port), IRV_TOKEN: token });
  return { args, env };
}

// createTerminal({port, token, send(channel, payload)}) -> {start, input, resize, stop, running}
function createTerminal({ port, token, send }) {
  let proc = null, generation = 0;

  function spawnClaude({ dir, model, systemPrompt, cols, rows, resume, sessionId }) {
    const claude = findClaude();
    if (!claude) throw new Error('Claude Code is not installed with the app.');
    const pty = loadPty();
    const { args, env } = claudeArgs({ port, token, model, systemPrompt, resume, sessionId });
    return pty.spawn(claude, args, { name: 'xterm-256color', cols: cols || 80, rows: rows || 24, cwd: dir, env });
  }

  // Start (or keep) the process: opts.resume is one of the app's own conversation ids, or absent for a new
  // conversation with the id opts.sessionId. If the conversation cannot be reopened, a new one is started
  // (opts.onFresh() supplies its id).
  function start(opts) {
    if (proc) { try { proc.resize(opts.cols || 80, opts.rows || 24); } catch { /* ignore */ } return { running: true, already: true }; }
    const gen = ++generation;
    const launch = (resume) => {
      const started = Date.now();
      let p;
      try { p = spawnClaude({ ...opts, resume }); } catch (e) { send('chat:data', `\r\n${e.message}\r\n`); send('chat:exit', { code: 1 }); return; }
      proc = p;
      let held = resume ? '' : null; // output of a --continue attempt is held back until it is clearly running
      const flush = () => { if (held !== null) { if (held) send('chat:data', held); held = null; } };
      const timer = resume ? setTimeout(flush, 2500) : null;
      p.onData((d) => { if (gen !== generation) return; if (held !== null) held += d; else send('chat:data', d); });
      p.onExit(({ exitCode }) => {
        if (timer) clearTimeout(timer);
        if (proc === p) proc = null;
        if (gen !== generation) return;
        if (resume && exitCode !== 0 && Date.now() - started < 8000 && held !== null) { opts.sessionId = opts.onFresh(); return launch(false); }
        flush();
        send('chat:exit', { code: exitCode });
      });
    };
    launch(typeof opts.resume === 'string' ? opts.resume : false);
    return { running: true, already: false };
  }

  return {
    start,
    input(data) { if (proc) proc.write(String(data)); },
    resize(cols, rows) { if (proc && cols > 1 && rows > 1) { try { proc.resize(cols, rows); } catch { /* ignore */ } } },
    stop() { generation++; const p = proc; proc = null; if (p) { try { p.kill(); } catch { /* ignore */ } } },
    get running() { return !!proc; },
  };
}

module.exports = { createTerminal, findClaude, claudeArgs, status, newToken: () => crypto.randomBytes(24).toString('hex') };
