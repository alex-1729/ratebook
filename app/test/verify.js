// Launches the real app, walks through its views and saves screenshots.
// Usage: electron test/verify.js <data-folder> <output-folder> [dark]
'use strict';
const path = require('path');
const fs = require('fs');
const os = require('os');
const [dataDir, outDir, scheme] = process.argv.slice(2);
process.env.IRV_DATA_DIR = path.resolve(dataDir);
// IRV_KEEP_USER_DATA names a settings folder to reuse (to test what survives a relaunch).
process.env.IRV_USER_DATA = process.env.IRV_KEEP_USER_DATA || fs.mkdtempSync(path.join(os.tmpdir(), 'irv-verify-'));
const { app, BrowserWindow, nativeTheme, session } = require('electron');
require(process.env.IRV_MAIN || '../src/main.js'); // IRV_MAIN: test the packaged app.asar's main instead

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
app.whenReady().then(async () => {
  if (scheme === 'dark') nativeTheme.themeSource = 'dark';
  const blocked = [];
  session.defaultSession.webRequest.onErrorOccurred((d) => blocked.push(d.url + ' ' + d.error));
  await sleep(1500);
  const win = BrowserWindow.getAllWindows()[0];
  if (process.env.IRV_SIZE) { const [w, hh] = process.env.IRV_SIZE.split('x').map(Number); win.setMinimumSize(800, 500); win.setSize(w, hh); await sleep(400); }
  const wc = win.webContents;
  // Keep the page animating even if another window covers this one (PDF.js renders on animation frames).
  wc.setBackgroundThrottling(false); win.setAlwaysOnTop(true); win.show(); win.focus();
  const logs = [];
  wc.on('console-message', (e) => logs.push(e.message));
  fs.mkdirSync(outDir, { recursive: true });
  const js = (code) => wc.executeJavaScript(code, true);
  const shot = async (name) => {
    await sleep(500);
    fs.writeFileSync(path.join(outDir, name + '.png'), (await wc.capturePage()).toPNG());
  };
  const report = {};
  report.api = await js('Object.keys(window.api)');
  report.nodeLeak = await js('typeof require + "/" + typeof process');
  report.products = await js('[...document.querySelectorAll(".prod .ja")].map(e => e.textContent)');
  try { await js('fetch("https://example.com").then(() => "REACHED", e => "blocked: " + e.message)').then((r) => { report.fetch = r; }); } catch (e) { report.fetch = 'threw ' + e.message; }
  const steps = (process.env.IRV_STEPS ? JSON.parse(process.env.IRV_STEPS) : []);
  await shot('01-start');
  let i = 2;
  for (const s of steps) {
    try { report['step ' + s.name] = await js(s.js); } catch (e) { report['step ' + s.name] = 'ERROR ' + e.message; }
    await shot(String(i++).padStart(2, '0') + '-' + s.name);
  }
  // Any other window the walk-through opened (the source viewer).
  let k = 0;
  for (const w of BrowserWindow.getAllWindows()) {
    if (w === win) continue;
    await sleep(800);
    fs.writeFileSync(path.join(outDir, `window-${++k}.png`), (await w.webContents.capturePage()).toPNG());
    report['window ' + k] = await w.webContents.executeJavaScript('document.body.innerText.slice(0, 300) + " | frame: " + document.getElementById("src-frame").src.slice(-70)').catch((e) => 'ERR ' + e.message);
  }
  report.console = logs;
  report.failedRequests = blocked;
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  app.exit(0);
});
