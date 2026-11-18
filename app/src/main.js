// Main process. Owns all file access and blocks every network request.
'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell, session, Menu } = require('electron');
const fs = require('fs');
const path = require('path');
const dataset = require('./dataset');
const srclocate = require('./srclocate');
const collector = require('./collector');
const translate = require('./translate');
const chatTerminal = require('./chat/terminal');
const chatTools = require('./chat/tools');
const chatGuard = require('./chat/guard');
const chatSessions = require('./chat/sessions');
const chatPrompt = require('./chat/prompt');
const { startServer } = require('./chat/server');

// IRV_USER_DATA lets a test run use a throw-away settings folder.
if (process.env.IRV_USER_DATA) app.setPath('userData', process.env.IRV_USER_DATA);

const settingsFile = () => path.join(app.getPath('userData'), 'settings.json');

// The app was "Insurance Rates Viewer" before it was Ratebook, and the user-data folder is named
// after the app. On the first start under the new name, carry across what the old one remembered:
// settings.json (the data folder) and the page's local storage (filters, enabled candidates, news seen).
if (!process.env.IRV_USER_DATA) {
  const oldDir = path.join(app.getPath('appData'), 'Insurance Rates Viewer');
  const newDir = app.getPath('userData');
  const carried = ['settings.json', 'Local Storage'];
  if (oldDir !== newDir && carried.every((n) => !fs.existsSync(path.join(newDir, n)))) {
    for (const n of carried) {
      try { fs.cpSync(path.join(oldDir, n), path.join(newDir, n), { recursive: true }); } catch { /* nothing to carry */ }
    }
  }
}

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsFile(), 'utf8')) || {};
  } catch {
    return {};
  }
}

function writeSettings(s) {
  try {
    fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
    fs.writeFileSync(settingsFile(), JSON.stringify(s, null, 2));
  } catch { /* settings are a convenience only */ }
}

// Order: remembered folder, IRV_DATA_DIR, then the folder that contains app/
// (only meaningful when run from the repo with `npm start`).
function currentRoot() {
  const s = readSettings();
  if (s.dataFolder && fs.existsSync(s.dataFolder)) return s.dataFolder;
  if (process.env.IRV_DATA_DIR) return path.resolve(process.env.IRV_DATA_DIR);
  return path.resolve(app.getAppPath(), '..');
}

let win = null;
let quitConfirmed = false;

// Closing or quitting while the collector runs: ask first, then stop it.
function okToQuit() {
  if ((!collector.isRunning() && !translate.isRunning()) || quitConfirmed) return true;
  const what = collector.isRunning() ? 'A data update' : 'A translation';
  const choice = dialog.showMessageBoxSync(win || undefined, {
    type: 'warning',
    buttons: ['Stop it and quit', 'Keep running'],
    defaultId: 1, cancelId: 1,
    message: what + ' is still running.',
    detail: 'Quitting now stops it. What has already been written is kept.',
  });
  if (choice !== 0) return false;
  quitConfirmed = true;
  if (collector.isRunning()) collector.cancel();
  if (translate.isRunning()) translate.cancel();
  return true;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1000,
    minHeight: 560,
    show: false,
    title: 'Ratebook',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false, // the spellchecker would download dictionaries
    },
  });
  win.once('ready-to-show', () => win.show());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (e) => e.preventDefault());
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.on('close', (e) => { if (!okToQuit()) e.preventDefault(); });
  win.on('closed', () => { win = null; });
}

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{ role: 'appMenu' }] : []),
    {
      label: 'File',
      submenu: [
        { label: 'Choose Data Folder…', accelerator: 'CmdOrCtrl+O', click: () => win && win.webContents.send('menu', 'choose-folder') },
        { label: 'Reload Data', accelerator: 'CmdOrCtrl+R', click: () => win && win.webContents.send('menu', 'reload') },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' }, { role: 'togglefullscreen' },
        { type: 'separator' },
        { label: 'Assistant', accelerator: 'CmdOrCtrl+J', click: () => win && win.webContents.send('menu', 'toggle-chat') },
        { type: 'separator' }, { role: 'toggleDevTools' },
      ],
    },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(async () => {
  // `npm start` runs inside Electron's own app bundle, so give the Dock our icon. A packaged build has it already.
  if (!app.isPackaged && app.dock) {
    try { app.dock.setIcon(path.join(__dirname, '..', 'build', 'icon-mac.png')); } catch { /* cosmetic */ }
  }
  // No outbound traffic of any kind: only the app's own local files load.
  session.defaultSession.webRequest.onBeforeRequest((details, cb) => {
    const ok = details.url.startsWith('file://') || details.url.startsWith('devtools://');
    cb({ cancel: !ok });
  });
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));

  ipcMain.handle('dataset:load', () => dataset.loadDataset(currentRoot()));

  ipcMain.handle('dataset:rates', (_e, productId, listedCsvs) => {
    if (typeof productId !== 'string' || !/^[\w.-]+$/.test(productId)) return [];
    return dataset.readRates(currentRoot(), productId, listedCsvs);
  });

  ipcMain.handle('folder:choose', async () => {
    const r = await dialog.showOpenDialog(win, {
      title: 'Choose the research data folder (the one containing data/ and sources/)',
      defaultPath: currentRoot(),
      properties: ['openDirectory'],
    });
    if (r.canceled || !r.filePaths[0]) return null;
    if (collector.isRunning() || translate.isRunning()) return null; // do not switch folders under a running job
    let chosen = r.filePaths[0];
    // Be forgiving if the data/ folder itself was picked.
    if (!dataset.looksLikeDataFolder(chosen) && dataset.looksLikeDataFolder(path.dirname(chosen))) {
      chosen = path.dirname(chosen);
    }
    writeSettings({ ...readSettings(), dataFolder: chosen });
    if (chatTerm && chatTerm.running) { chatTerm.stop(); win.webContents.send('chat:exit', { code: 0, restart: true }); }
    return chosen;
  });

  ipcMain.handle('file:open', async (_e, relPath) => {
    const abs = dataset.resolveOpenable(currentRoot(), relPath);
    if (!abs) return { ok: false, error: 'File not found inside the data folder' };
    const err = await shell.openPath(abs);
    return err ? { ok: false, error: err } : { ok: true };
  });

  ipcMain.handle('file:reveal', (_e, relPath) => {
    const abs = dataset.resolveOpenable(currentRoot(), relPath);
    if (!abs) return { ok: false, error: 'File not found inside the data folder' };
    shell.showItemInFolder(abs);
    return { ok: true };
  });

  // Embedded PDF viewer: a manifest-listed PDF's bytes, and PDF.js's own
  // support files (character maps, standard fonts, decoders) from the app.
  ipcMain.handle('doc:read', (_e, relPath) => dataset.readManifestPdf(currentRoot(), relPath));
  const PDF_ASSETS = { cMapUrl: 'cmaps', standardFontDataUrl: 'standard_fonts', wasmUrl: 'wasm' };
  ipcMain.handle('pdf:asset', (_e, kind, filename) => {
    const dir = Object.prototype.hasOwnProperty.call(PDF_ASSETS, kind) ? PDF_ASSETS[kind] : null;
    if (!dir || typeof filename !== 'string' || !/^[\w.-]{1,80}$/.test(filename) || filename.includes('..')) return null;
    try {
      return fs.readFileSync(path.join(__dirname, '..', 'node_modules', 'pdfjs-dist', dir, filename));
    } catch {
      return null;
    }
  });

  // Source viewer: the saved page a rate came from, in its own window. Only files inside sources/ of the
  // data folder; scripts off; the session already blocks every network request.
  let srcWin = null;
  let srcText = null; // the text snapshot the Source window is showing: { text, json, find }
  ipcMain.handle('source:text', (e) => (srcWin && !srcWin.isDestroyed() && e.sender === srcWin.webContents ? srcText : null));
  ipcMain.handle('source:open', async (_e, req) => {
    const rel = req && typeof req.snapshot === 'string' ? req.snapshot : '';
    if (!rel) return { ok: false, error: 'No saved page is recorded for this rate.' };
    const abs = dataset.resolveOpenable(currentRoot(), rel);
    if (!abs) {
      // A copy is recorded; say why it cannot be shown rather than that there is none.
      const there = dataset.safeResolve(currentRoot(), rel);
      return { ok: false, error: there ? 'A saved copy is recorded for this rate, but it is a kind of file the app cannot show.'
        : 'A saved copy is recorded for this rate, but the file is not in the data folder.' };
    }
    const ext = path.extname(abs).toLowerCase();
    // The rate's own dimensions pick the right place when one value appears several times on the page.
    const dimStr = (v) => (typeof v === 'string' && v.length <= 60 && /^[\w .+\-–~]*$/.test(v) ? v : '');
    const d = req.dims && typeof req.dims === 'object' ? req.dims : {};
    const dims = { plan: dimStr(d.plan), currency: dimStr(d.currency), term: dimStr(d.term), age: dimStr(d.age), sex: dimStr(d.sex), date: dimStr(d.date) };
    const figure = /^\d{1,3}(\.\d{1,4})?$/.test(String(req.find || '')) ? String(req.find) : '';
    // Saved API responses and text extracts are shown as text by the window itself (see source:text).
    let asText = null;
    if (ext === '.json' || ext === '.txt') {
      try {
        if (fs.statSync(abs).size > 8 * 1024 * 1024) return { ok: false, error: 'A saved copy is recorded for this rate, but it is too large to show.' };
        asText = { text: fs.readFileSync(abs, 'utf8'), json: ext === '.json' };
        // JSON is shown pretty-printed, so places are found in that form.
        if (asText.json) { try { asText.text = JSON.stringify(JSON.parse(asText.text), null, 2); } catch { /* shown as saved */ } }
      } catch { return { ok: false, error: 'A saved copy is recorded for this rate, but the file could not be read.' }; }
    }
    if (ext === '.pdf') { const err = await shell.openPath(abs); return err ? { ok: false, error: err } : { ok: true }; }
    // An HTML copy: when the rate's cell can be told apart, a copy of the page with that cell marked is shown
    // (written to the app's temporary folder, never into sources/); otherwise the saved file itself, with every
    // place the value appears highlighted.
    let pageUrl = asText ? '' : require('url').pathToFileURL(abs).href, hit = '';
    if (!asText && figure && /^\.html?$/.test(ext)) {
      try {
        const buf = fs.readFileSync(abs);
        if (buf.length <= 8 * 1024 * 1024) {
          const html = srclocate.decodeHtml(buf);
          const r = srclocate.locateHtml(html, figure, dims);
          if (r.start != null) {
            const dir = path.join(app.getPath('temp'), 'irv-source-view');
            fs.mkdirSync(dir, { recursive: true });
            const out = path.join(dir, 'view.html');
            fs.writeFileSync(out, srclocate.markedHtml(html, r.start, r.end, require('url').pathToFileURL(path.dirname(abs) + path.sep).href), 'utf8');
            pageUrl = require('url').pathToFileURL(out).href + '?v=' + Date.now() + '#irv-hit';
            hit = 'one';
          }
        }
      } catch { /* show the saved file as it is */ }
    }
    const clip = (s, n) => String(s || '').slice(0, n);
    const url = /^https?:\/\//.test(String(req.url || '')) ? clip(req.url, 800) : '';
    if (!srcWin || srcWin.isDestroyed()) {
      srcWin = new BrowserWindow({ width: 1000, height: 820, parent: win || undefined, title: 'Source',
        webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false } });
      srcWin.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      srcWin.webContents.on('will-navigate', (e) => e.preventDefault());
      // The number of places the value appears, for the window's header line.
      srcWin.webContents.on('found-in-page', (_e, res) => {
        if (res && res.finalUpdate && srcWin && !srcWin.isDestroyed()) srcWin.webContents.send('source:found', res.matches);
      });
      srcWin.on('closed', () => { srcWin = null; });
    }
    let located = null;
    if (asText) { try { located = srclocate.locateText(asText.text, figure, dims); } catch { located = null; } }
    srcText = asText ? { ...asText, find: figure, hits: located ? located.hits : null, exact: !!(located && located.exact) } : null; // set before the window loads and asks for it
    await srcWin.loadFile(path.join(__dirname, 'renderer', 'source.html'), { query: {
      file: pageUrl, mode: asText ? 'text' : 'page', hit, find: hit ? figure : '', path: clip(rel, 400), retrieved: /^\d{4}-\d{2}-\d{2}$/.test(String(req.retrieved || '')) ? req.retrieved : '',
      url, label: clip(req.label, 200),
      // Only a flag and an ISO date cross over; the wording is the window's own.
      unchecked: req.unchecked === true ? '1' : '', verified: /^\d{4}-\d{2}-\d{2}$/.test(String(req.verified || '')) ? req.verified : '' } });
    srcWin.show(); srcWin.focus();
    // No single cell could be told apart: Chromium's find highlights every place the value appears, and the
    // count goes to the header ("3 matches").
    if (figure && !asText && !hit) setTimeout(() => { if (srcWin && !srcWin.isDestroyed()) srcWin.webContents.findInPage(figure); }, 700);
    return { ok: true };
  });

  // Brochure translation (see src/translate.js). Documents are named by their manifest path and a page number.
  const trSend = (ev) => { if (win && !win.isDestroyed()) win.webContents.send('tr:progress', ev); };
  ipcMain.handle('tr:status', (_e, file) => translate.status(currentRoot(), file));
  ipcMain.handle('tr:summary', (_e, files) => translate.summary(currentRoot(), files));
  ipcMain.handle('tr:start', (_e, opts) => {
    // The engine looks for Claude Code itself; naming the app's own copy makes a packaged app work too.
    const claude = chatTerminal.findClaude();
    return translate.start(currentRoot(), opts, trSend, claude && !process.env.REPAINT_CLAUDE_BIN ? { REPAINT_CLAUDE_BIN: claude } : {});
  });
  ipcMain.handle('tr:cancel', () => translate.cancel());
  ipcMain.handle('tr:delete', (_e, file) => translate.remove(currentRoot(), file));
  ipcMain.handle('tr:page', (_e, file, n) => translate.readPage(currentRoot(), file, n));
  ipcMain.handle('tr:checks', (_e, file, n) => translate.checks(currentRoot(), file, n));
  ipcMain.handle('tr:openOutput', async (_e, file) => {
    const p = translate.outputPath(currentRoot(), file);
    if (!p) return { ok: false, error: 'There is no English PDF for this document yet.' };
    const err = await shell.openPath(p);
    return err ? { ok: false, error: err } : { ok: true };
  });

  // Hands a source URL to the default browser. The app itself never fetches it.
  ipcMain.handle('url:open', async (_e, url) => {
    let u;
    try { u = new URL(String(url)); } catch { return { ok: false, error: 'Invalid URL' }; }
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return { ok: false, error: 'Only web links can be opened' };
    await shell.openExternal(u.toString());
    return { ok: true };
  });

  // Data update: runs scripts/collect_all.py from the data folder, only when
  // the page asks. See collector.js for what the page may and may not choose.
  const send = (ev) => { if (win && !win.isDestroyed()) win.webContents.send('collector:progress', ev); };
  ipcMain.handle('collector:info', async () => {
    const i = await collector.info(currentRoot());
    const s = readSettings();
    return { ...i, lastRun: s.lastUpdateRun || null, lastNewsRun: s.lastNewsRun || null };
  });
  ipcMain.handle('collector:start', (_e, opts) => {
    const news = !!(opts && opts.kind === 'news');
    // The summaries step looks for Claude Code itself; naming the app's own copy makes a packaged app work too.
    const claude = news && opts.enrich ? chatTerminal.findClaude() : null;
    return collector.start(currentRoot(), opts, send,
      (summary) => writeSettings({ ...readSettings(), [news ? 'lastNewsRun' : 'lastUpdateRun']: summary }),
      claude && !process.env.NEWS_CLAUDE_BIN ? { NEWS_CLAUDE_BIN: claude } : {});
  });
  ipcMain.handle('collector:cancel', () => collector.cancel());

  await setupChat();

  buildMenu();
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

// ---------- chat assistant (Claude Code in the right-hand pane) ----------
// See src/chat/terminal.js. The window answers the tool calls; this process checks every argument first.
let chatTerm = null;
async function setupChat() {
  const token = chatTerminal.newToken();
  const sendWin = (channel, payload) => { if (win && !win.isDestroyed()) win.webContents.send(channel, payload); };
  let view = null;        // what the window last said it shows
  let nextResume;         // undefined = the app's latest conversation here, false = a new one, an id = that one
  // The data folder is also where the user's own Claude Code sessions are kept, so the app remembers which
  // conversations it started (ids, per folder, in its settings) and opens, lists and deletes only those.
  const ownKey = () => { try { return fs.realpathSync(currentRoot()); } catch { return path.resolve(currentRoot()); } };
  const ownIds = () => { const all = readSettings().chatSessions || {}; return Array.isArray(all[ownKey()]) ? all[ownKey()] : []; };
  const setOwn = (ids) => { const s = readSettings(); s.chatSessions = { ...(s.chatSessions || {}), [ownKey()]: ids.slice(-200) }; writeSettings(s); };
  const ownSessions = () => { const ids = new Set(ownIds()); return chatSessions.listSessions(currentRoot()).filter((c) => ids.has(c.id)); };
  const freshId = () => { const id = require('crypto').randomUUID(); setOwn([...ownIds(), id]); shownSession = id; return id; };
  let shownSession = null;
  const pending = new Map();
  let seq = 0;

  const callWindow = (name, args) => new Promise((resolve) => {
    if (!win || win.isDestroyed()) return resolve({ content: [{ type: 'text', text: 'The app window is not open.' }], isError: true });
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); resolve({ content: [{ type: 'text', text: 'The app did not answer in time.' }], isError: true }); }, 20000);
    pending.set(id, (r) => { clearTimeout(timer); resolve({ content: [{ type: 'text', text: String(r && r.text !== undefined ? r.text : '') }], isError: !(r && r.ok) }); });
    win.webContents.send('chat:tool', { id, name, args });
  });
  ipcMain.on('chat:toolResult', (_e, r) => { const fn = r && pending.get(r.id); if (fn) { pending.delete(r.id); fn(r); } });
  ipcMain.on('chat:view', (_e, v) => { if (v && typeof v === 'object') view = v; });

  let started;
  try {
    started = await startServer(token, {
      tools: () => chatTools.listForMcp(),
      call: async (name, args) => {
        const v = chatTools.validate(name, args);
        if (!v.ok) return { content: [{ type: 'text', text: v.error }], isError: true };
        return callWindow(name, v.args);
      },
      promptLine: () => (view && typeof view.line === 'string' ? '[App view: ' + view.line.slice(0, 600) + ']' : ''),
      pretool: (tool, input) => chatGuard.decide(currentRoot(), tool, input),
    });
  } catch (e) {
    ipcMain.handle('chat:status', () => ({ ok: false, message: 'The assistant could not start its local tool server: ' + e.message }));
    return;
  }
  chatTerm = chatTerminal.createTerminal({ port: started.port, token, send: sendWin });

  const dir = () => currentRoot();
  ipcMain.handle('chat:status', () => chatTerminal.status());
  ipcMain.handle('chat:start', async (_e, size) => {
    const st = await chatTerminal.status();
    if (!st.ok) return { running: false, message: st.message };
    const cols = Math.max(20, Math.min(400, Number(size && size.cols) | 0)) || 80;
    const rows = Math.max(5, Math.min(200, Number(size && size.rows) | 0)) || 24;
    const resume = nextResume; nextResume = undefined;
    if (chatTerm.running) return chatTerm.start({ cols, rows });
    const latest = ownSessions()[0];
    const resumeId = typeof resume === 'string' ? resume : resume === false ? null : (latest ? latest.id : null);
    const sessionId = resumeId ? null : freshId();
    if (resumeId) shownSession = resumeId;
    return chatTerm.start({ dir: dir(), model: process.env.IRV_CHAT_MODEL || 'sonnet', systemPrompt: chatPrompt(), cols, rows,
      resume: resumeId, sessionId, onFresh: freshId });
  });
  // Claude Code treats a line starting with "!" as a shell command typed by the user and "#" as a note to save to a
  // memory file. The pane is read-only, so neither character reaches it: typed or pasted, they become their
  // full-width forms. Escape sequences (keys, mouse reports) pass through untouched.
  const wide = (s) => s.replace(/!/g, '\uff01').replace(/#/g, '\uff03');
  const sanitize = (d) => (d.startsWith('\x1b[200~') ? wide(d) : d[0] === '\x1b' ? d : wide(d));
  ipcMain.on('chat:input', (_e, data) => { if (typeof data === 'string' && data.length <= 100000) chatTerm.input(sanitize(data)); });
  ipcMain.on('chat:resize', (_e, size) => { if (size) chatTerm.resize(Number(size.cols) | 0, Number(size.rows) | 0); });
  const restart = (resume) => { nextResume = resume; chatTerm.stop(); sendWin('chat:exit', { code: 0, restart: true }); };
  ipcMain.handle('chat:new', () => { restart(false); return true; });
  ipcMain.handle('chat:sessions', () => ownSessions().map((c) => ({ ...c, current: c.id === shownSession })));
  ipcMain.handle('chat:openSession', (_e, id) => {
    if (typeof id !== 'string' || !chatSessions.SESSION_ID.test(id) || !ownSessions().some((c) => c.id === id)) return { ok: false, error: 'That conversation is no longer there.' };
    restart(id);
    return { ok: true };
  });
  ipcMain.handle('chat:deleteSession', (_e, id) => {
    try {
      if (!ownIds().includes(id)) return { ok: false, error: 'That conversation was not started in this app.' };
      chatSessions.deleteSession(dir(), id);
      setOwn(ownIds().filter((x) => x !== id));
      if (id === shownSession) restart(false);
      return { ok: true };
    } catch (e) { return { ok: false, error: e.message }; }
  });
}

// Last resort for exits that skip the prompt: never leave the collector running.
process.on('exit', () => { if (collector.isRunning()) collector.cancel(); if (translate.isRunning()) translate.cancel(); if (chatTerm) chatTerm.stop(); });

app.on('before-quit', (e) => { if (!okToQuit()) e.preventDefault(); });

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
