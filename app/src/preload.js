// The only bridge between the page and the main process.
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  loadDataset: () => ipcRenderer.invoke('dataset:load'),
  readRates: (productId, listedCsvs) => ipcRenderer.invoke('dataset:rates', productId, listedCsvs),
  chooseFolder: () => ipcRenderer.invoke('folder:choose'),
  openFile: (relPath) => ipcRenderer.invoke('file:open', relPath),
  revealFile: (relPath) => ipcRenderer.invoke('file:reveal', relPath),
  readDocument: (relPath) => ipcRenderer.invoke('doc:read', relPath),
  pdfAsset: (kind, filename) => ipcRenderer.invoke('pdf:asset', kind, filename),
  openSource: (req) => ipcRenderer.invoke('source:open', req),
  sourceText: () => ipcRenderer.invoke('source:text'),
  onSourceFound: (handler) => ipcRenderer.on('source:found', (_e, n) => handler(Number(n) || 0)),
  openUrl: (url) => ipcRenderer.invoke('url:open', url),
  collectorInfo: () => ipcRenderer.invoke('collector:info'),
  collectorStart: (insurers, docs, more) => ipcRenderer.invoke('collector:start', { insurers, docs, ...(more || {}) }),
  collectorCancel: () => ipcRenderer.invoke('collector:cancel'),
  onCollectorProgress: (handler) => ipcRenderer.on('collector:progress', (_e, ev) => handler(ev)),
  // Brochure translation: a document is named by its manifest path, a page by its number.
  translation: {
    status: (file) => ipcRenderer.invoke('tr:status', file),
    summary: (files) => ipcRenderer.invoke('tr:summary', files),
    start: (file, mode, page) => ipcRenderer.invoke('tr:start', { file, mode, page }),
    cancel: () => ipcRenderer.invoke('tr:cancel'),
    remove: (file) => ipcRenderer.invoke('tr:delete', file),
    readPage: (file, n) => ipcRenderer.invoke('tr:page', file, n),
    checks: (file, n) => ipcRenderer.invoke('tr:checks', file, n),
    openOutput: (file) => ipcRenderer.invoke('tr:openOutput', file),
    onProgress: (fn) => ipcRenderer.on('tr:progress', (_e, ev) => fn(ev)),
  },
  // Chat assistant: the terminal's bytes, the conversations list, and the app tools the assistant calls.
  chat: {
    status: () => ipcRenderer.invoke('chat:status'),
    start: (size) => ipcRenderer.invoke('chat:start', size),
    input: (data) => ipcRenderer.send('chat:input', String(data)),
    resize: (size) => ipcRenderer.send('chat:resize', size),
    onData: (fn) => ipcRenderer.on('chat:data', (_e, d) => fn(d)),
    onExit: (fn) => ipcRenderer.on('chat:exit', (_e, info) => fn(info)),
    newConversation: () => ipcRenderer.invoke('chat:new'),
    sessions: () => ipcRenderer.invoke('chat:sessions'),
    openSession: (id) => ipcRenderer.invoke('chat:openSession', id),
    deleteSession: (id) => ipcRenderer.invoke('chat:deleteSession', id),
    onTool: (fn) => ipcRenderer.on('chat:tool', (_e, call) => fn(call)),
    toolResult: (r) => ipcRenderer.send('chat:toolResult', r),
    view: (v) => ipcRenderer.send('chat:view', v),
  },
  onMenu: (handler) => ipcRenderer.on('menu', (_e, action) => handler(action)),
});
