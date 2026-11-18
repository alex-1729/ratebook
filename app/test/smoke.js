// Node-only check of the CSV parser and dataset loader (no Electron needed).
// Usage: node test/smoke.js [data-folder]   (default: the folder above app/)
'use strict';
const assert = require('assert');
const path = require('path');
const { parseCsv } = require('../src/csv');
const dataset = require('../src/dataset');

const rows = parseCsv('﻿a,b,c\r\n1,"x, ""y""",\r\n\r\n2,"line\nbreak",3\n');
assert.deepStrictEqual(rows, [{ a: '1', b: 'x, "y"', c: '' }, { a: '2', b: 'line\nbreak', c: '3' }]);

const root = path.resolve(process.argv[2] || path.join(__dirname, '..', '..'));
const ds = dataset.loadDataset(root);
console.log(`root: ${root}\nvalid: ${ds.valid}, insurers: ${ds.insurers.length}, products: ${ds.products.length}, warnings: ${ds.warnings.length}`);
for (const p of ds.products) {
  const files = dataset.readRates(root, p.id, (p.rate_series || []).map((s) => s.csv));
  const n = files.reduce((a, f) => a + (f.text ? parseCsv(f.text).length : 0), 0);
  console.log(`  ${p.id}: ${files.length} rate files, ${n} rows, ${(ds.documents[p.id] || []).length} local documents`);
}
// The value model's net-yield check: only rows with a brochure charge are loaded, each a match or a difference.
console.log(`net-yield check: ${ds.netYieldCheck.length} rows, ${ds.netYieldCheck.filter((r) => r.status === 'match').length} match`);
assert.ok(ds.netYieldCheck.every((r) => (r.status === 'match' || r.status === 'differs') && r.charge_source && r.effective_date));
// Each of those rows finds its published net-yield point in the renderer's model (the ✓ / ≠ mark on a board).
global.window = { parseCsv };
global.document = { addEventListener() {}, createElement() { throw new Error('no DOM in this test'); } };
require('../src/renderer/model.js');
const IRV = global.window.IRV;
const allFiles = {};
for (const p of ds.products) allFiles[p.id] = dataset.readRates(root, p.id, (p.rate_series || []).map((s) => s.csv));
const model = IRV.buildModel(ds, allFiles);
let marked = 0;
for (const s of model.series) for (const o of s.obs) if (IRV.netYieldCheck(model, s, o)) marked++;
assert.strictEqual(marked, ds.netYieldCheck.length, 'every net-yield check row matches exactly one net-yield point');
// The value model's own yields: plans with no published net yield get one net-yield point each, marked as
// modelled, on boards only (not in a product's own series).
const modelled = model.series.filter((s) => s.derived === 'model');
console.log(`modelled yields: ${ds.modelYields.length} rows, ${modelled.length} series, ${new Set(modelled.map((s) => s.pid)).size} products`);
if (ds.modelYields.length) assert.ok(modelled.length > 0, 'model yields attach to crediting-rate series');
for (const s of modelled) {
  assert.ok(s.metric === 'net_yield' && s.obs.length === 1 && s.obs[0].date === s.modelYield.effective_date && s.obs[0].v === s.modelYield.yield_pct);
  assert.ok(s.obs[0].v <= s.modelYield.crediting_pct + 1e-9, 'a charge never raises the yield');
  assert.ok(!model.byProduct.get(s.pid).includes(s), 'not among the product\'s own series');
  assert.ok(model.series.filter((x) => x.key === s.key).length === 1, 'never beside a published net yield');
}
// News: every item has an id, a date, a link and a title; ids are unique; stories and insurers it names exist.
const news = ds.news;
const insurerIds = new Set(ds.insurers.map((i) => i.id));
const storyIds = new Set(news.stories.map((s) => s.id));
const seen = new Set();
for (const i of news.items) {
  assert.ok(/^https?:\/\//.test(i.url || ''), `news item ${i.id} has no link`);
  assert.ok(i.title_ja || i.title_en, `news item ${i.id} has no title`);
  assert.ok(!seen.has(i.id), `news item id ${i.id} appears twice`); seen.add(i.id);
  for (const x of i.insurer_ids || []) assert.ok(insurerIds.has(x), `news item ${i.id} names unknown insurer ${x}`);
  for (const x of i.story_ids || []) assert.ok(storyIds.has(x), `news item ${i.id} names unknown story ${x}`);
}
const tiers = ds.insurers.reduce((o, i) => { o[i.news_tier || 'none'] = (o[i.news_tier || 'none'] || 0) + 1; return o; }, {});
console.log(`  news: ${news.items.length} items, ${news.stories.length} stories, ${Object.keys(news.state).length} sources checked, tiers ${JSON.stringify(tiers)}`);
assert.strictEqual(dataset.resolveOpenable(root, '../outside.pdf'), null);
assert.strictEqual(dataset.resolveOpenable(root, 'data/products/x.json'), null);
console.log('ok');
