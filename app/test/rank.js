// Rank change on a board (no Electron needed): node test/rank.js
// The model is the renderer's own file, loaded here with the few browser globals it touches at load time.
'use strict';
const assert = require('assert');
const path = require('path');
global.window = { parseCsv: require('../src/csv').parseCsv };
global.document = { addEventListener() {}, createElement() { throw new Error('no DOM in this test'); } };
require(path.join('..', 'src', 'renderer', 'model.js'));
const IRV = global.window.IRV;

// Dates relative to today: the current half-month and the one before.
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const now = new Date();
const curStart = new Date(now.getFullYear(), now.getMonth(), now.getDate() < 16 ? 1 : 16);
const dayBefore = new Date(curStart.getTime() - 86400000);
const prevStart = new Date(dayBefore.getFullYear(), dayBefore.getMonth(), dayBefore.getDate() < 16 ? 1 : 16);
const old = new Date(curStart.getFullYear() - 1, 0, 1);
const CUR = iso(curStart), PREV = iso(prevStart), OLD = iso(old);

const HEAD = 'product_id,metric,plan,currency,term_years,age_band,sex,effective_date,value_pct,status,source_url,retrieved_date,snapshot,via_wayback';
const csv = (pid, plan, points) => HEAD + '\n' + points.map(([date, v]) =>
  `${pid},crediting_rate,${plan},USD,10,,,${date},${v},published,https://example.invalid/,${CUR},,false`).join('\n') + '\n';
const product = (id, plans) => ({ id, insurer_id: 'ins', name_en: id, product_type: 'fixed_annuity', currencies: ['USD'],
  verification: { verified_through: CUR }, rate_series: plans.map((p) => ({ metric: 'crediting_rate', plan: p, plan_en: p })) });

// Three plans have a rate in both periods; six appear above them with no rate in the previous period.
// "target" is last in both periods at an unchanged rate.
const ds = { insurers: [{ id: 'ins', name_en: 'Insurer', rate_period: { type: 'semi_monthly' } }],
  products: [product('steady', ['a', 'b', 'target']), product('gappy', ['g1', 'g2', 'g3', 'g4', 'g5']), product('fresh', ['n1'])],
  documents: {}, productTypes: [{ key: 'fixed_annuity', label: 'Fixed annuity' }] };
const files = {
  steady: [{ file: 'steady__crediting_rate__a.csv', text: csv('steady', 'a', [[PREV, 5.10], [CUR, 5.00]]) },
    { file: 'steady__crediting_rate__b.csv', text: csv('steady', 'b', [[PREV, 4.90], [CUR, 4.80]]) },
    { file: 'steady__crediting_rate__target.csv', text: csv('steady', 'target', [[PREV, 4.50], [CUR, 4.50]]) }],
  // earlier data exists, but none for the previous period
  gappy: ['g1', 'g2', 'g3', 'g4', 'g5'].map((p, i) => ({ file: `gappy__crediting_rate__${p}.csv`, text: csv('gappy', p, [[OLD, 3.0], [CUR, 6.0 - i * 0.1]]) })),
  // no earlier data at all
  fresh: [{ file: 'fresh__crediting_rate__n1.csv', text: csv('fresh', 'n1', [[CUR, 5.45]]) }],
};
const m = IRV.buildModel(ds, files);
const b = IRV.board(m, { type: 'fixed_annuity', cur: 'USD', term: '10' }, { age: 60, sex: 'F' });
const row = (plan) => b.rows.find((r) => r.s.plan === plan);

assert.strictEqual(b.rows.length, 9);
assert.strictEqual(row('target').rank, 9, 'target is last today');
assert.strictEqual(row('target').rankChange, 0, 'last in both periods among the plans ranked in both: no change (was shown as down 6)');
assert.strictEqual(row('a').rankChange, 0);
assert.strictEqual(row('b').rankChange, 0);
assert.strictEqual(row('g1').rankChange, null, 'no rate in the previous period: no rank change');
assert.strictEqual(row('g1').isNew, false, 'earlier data exists, so not "new"');
assert.strictEqual(row('n1').rankChange, null);
assert.strictEqual(row('n1').isNew, true, 'no earlier data at all: new');
assert.strictEqual(b.rankMoved, 0, 'header count of plans that changed rank');
assert.strictEqual(row('target').d, 0);
assert.strictEqual(row('a').d, -0.1);

// A real overtake inside the common set is still seen.
files.steady[1].text = csv('steady', 'b', [[PREV, 4.90], [CUR, 5.20]]);
const b2 = IRV.board(IRV.buildModel(ds, files), { type: 'fixed_annuity', cur: 'USD', term: '10' }, { age: 60, sex: 'F' });
const row2 = (plan) => b2.rows.find((r) => r.s.plan === plan);
assert.strictEqual(row2('b').rankChange, 1, 'b overtook a');
assert.strictEqual(row2('a').rankChange, -1);
assert.strictEqual(row2('target').rankChange, 0);
assert.strictEqual(b2.rankMoved, 2);

// Ties: equal rates share the lowest rank (1, =2, =2, 4), and plans that stay tied show no rank change.
const tie = { ...ds, products: [product('tie', ['p', 'q', 'r', 's'])] };
const tieFiles = { tie: [
  { file: 'tie__crediting_rate__p.csv', text: csv('tie', 'p', [[PREV, 5.50], [CUR, 5.40]]) },
  { file: 'tie__crediting_rate__q.csv', text: csv('tie', 'q', [[PREV, 5.20], [CUR, 5.10]]) },
  { file: 'tie__crediting_rate__r.csv', text: csv('tie', 'r', [[PREV, 5.20], [CUR, 5.10]]) },
  { file: 'tie__crediting_rate__s.csv', text: csv('tie', 's', [[PREV, 4.00], [CUR, 4.00]]) }] };
const b3 = IRV.board(IRV.buildModel(tie, tieFiles), { type: 'fixed_annuity', cur: 'USD', term: '10' }, { age: 60, sex: 'F' });
const row3 = (plan) => b3.rows.find((r) => r.s.plan === plan);
assert.deepStrictEqual(b3.rows.map((r) => r.s.plan), ['p', 'q', 'r', 's'], 'tied rows ordered by product then plan name');
assert.deepStrictEqual(b3.rows.map((r) => r.rank), [1, 2, 2, 4], 'competition ranking');
assert.deepStrictEqual(b3.rows.map((r) => r.tied), [false, true, true, false]);
assert.deepStrictEqual(b3.rows.map((r) => r.rankChange), [0, 0, 0, 0], 'plans that stay tied show no change');
assert.strictEqual(b3.leaders.length, 1);
// Joint leaders: the top rate is shared.
tieFiles.tie[0].text = csv('tie', 'p', [[PREV, 5.50], [CUR, 5.10]]);
const b4 = IRV.board(IRV.buildModel(tie, tieFiles), { type: 'fixed_annuity', cur: 'USD', term: '10' }, { age: 60, sex: 'F' });
assert.deepStrictEqual(b4.rows.map((r) => r.rank), [1, 1, 1, 4]);
assert.strictEqual(b4.leaders.length, 3, 'three joint leaders');
// p fell into a tie with q and r: q and r each move from joint 2nd to joint 1st, p stays 1st.
assert.deepStrictEqual(b4.rows.map((r) => [r.s.plan, r.rankChange]), [['p', 0], ['q', 1], ['r', 1], ['s', 0]]);
console.log('rank change ok');

// ---------- Over column: tenor, nearest curve tenor, staleness (model.js govtTenor / govtYield) ----------
// Fixed dates in June, clear of any daylight-saving change, so the 14-day cut-off is exact.
{
  const at = (d) => IRV.toEpoch(d);
  const gm = IRV.buildModel({ ...ds, govtYields: { USD: { curve: 'UST', source: '', tenors: {
    5: [['2026-06-20', 4.00]], 10: [['2026-06-01', 3.50], ['2026-06-20', 4.50]] } } } }, files);
  // Tenors: the term, a payout plan's deferral plus half the payout, a judgement period's midpoint;
  // none for a life annuity or an immediate payout.
  assert.deepStrictEqual(IRV.govtTenor({ plan: 'a', term: '10' }), { t: 10, why: 'term' });
  assert.strictEqual(IRV.govtTenor({ plan: 'x_p10', term: '5' }).t, 10, 'deferral 5 + half of a 10-year payout');
  assert.strictEqual(IRV.govtTenor({ plan: 'x_h3-7', term: '' }).t, 5, 'judgement period midpoint');
  const life = IRV.govtTenor({ plan: 'x_shushin', term: '10' });
  assert.strictEqual(life.t, null, 'life annuity: no tenor, even with a term');
  assert.match(life.why, /life annuity/);
  assert.strictEqual(IRV.govtTenor({ plan: 'x', term: '0' }).t, null, 'immediate payout: no tenor');
  assert.strictEqual(IRV.govtYield(gm, 'USD', null, at('2026-06-22')), null, 'no tenor: no yield');
  // Nearest curve tenor; a tie goes to the longer one.
  assert.strictEqual(IRV.govtYield(gm, 'USD', 9, at('2026-06-22')).tenor, 10);
  assert.strictEqual(IRV.govtYield(gm, 'USD', 6, at('2026-06-22')).tenor, 5);
  assert.strictEqual(IRV.govtYield(gm, 'USD', 7.5, at('2026-06-22')).tenor, 10, 'equidistant: the longer tenor');
  assert.strictEqual(IRV.govtYield(gm, 'USD', 30, at('2026-06-22')).tenor, 10, 'beyond the curve: its longest tenor');
  assert.strictEqual(IRV.govtYield(gm, 'AUD', 10, at('2026-06-22')), null, 'no curve for the currency');
  // The latest yield on or before the date, converted from semi-annual par to annual effective.
  const g10 = IRV.govtYield(gm, 'USD', 10, at('2026-06-22'));
  assert.deepStrictEqual([g10.curve, g10.date, g10.y], ['UST', '2026-06-20', 4.50]);
  assert.ok(Math.abs(g10.ae - 4.550625) < 1e-9, 'annual effective of 4.50% semi-annual');
  assert.strictEqual(IRV.govtLabel(g10), 'UST 10Y');
  // Staleness: a yield is used up to 14 days after its date, not 15.
  assert.strictEqual(IRV.govtYield(gm, 'USD', 10, at('2026-06-15')).date, '2026-06-01', '14 days on: still used');
  assert.strictEqual(IRV.govtYield(gm, 'USD', 10, at('2026-06-16')), null, '15 days on: stale');
  assert.strictEqual(IRV.govtYield(gm, 'USD', 10, at('2026-05-31')), null, 'before the first yield');
  console.log('govt yields ok');
}

// ---------- Net-yield check marks (model.js netYieldCheck) ----------
{
  const NY = 'product_id,metric,plan,currency,term_years,age_band,sex,effective_date,value_pct,status,source_url,retrieved_date,snapshot,via_wayback';
  const nyFiles = { ny: [{ file: 'ny__net_yield__a.csv', text: NY + '\n' + [[PREV, 3.10], [CUR, 3.20]].map(([d, v]) =>
    `ny,net_yield,a,USD,10,,,${d},${v},published,https://example.invalid/,${CUR},,false`).join('\n') + '\n' },
  { file: 'ny__crediting_rate__a.csv', text: csv('ny', 'a', [[CUR, 4.00]]) }] };
  const nyDs = { ...ds, products: [product('ny', ['a'])], netYieldCheck: [
    { product_id: 'ny', plan: 'a', currency: 'USD', term_years: 10, age_band: '', sex: '', effective_date: CUR, status: 'match',
      crediting_pct: 4.0, charge_pct: 7.0, model_net_pct: 3.2049, published_net_pct: 3.20, diff_pp: 0.0049, charge_source: 'brochure' },
    { product_id: 'ny', plan: 'a', currency: 'USD', term_years: 10, age_band: '', sex: '', effective_date: PREV, status: 'differs',
      crediting_pct: 4.0, charge_pct: 7.0, model_net_pct: 3.2049, published_net_pct: 3.10, diff_pp: 0.1049, charge_source: 'brochure' }] };
  const nm = IRV.buildModel(nyDs, nyFiles);
  const ny = nm.series.find((s) => s.metric === 'net_yield'), cr = nm.series.find((s) => s.metric === 'crediting_rate');
  assert.strictEqual(IRV.netYieldCheck(nm, ny, IRV.last(ny)).status, 'match', 'matched on product, plan, currency, term, band, sex and date');
  assert.strictEqual(IRV.netYieldCheck(nm, ny, ny.obs[0]).status, 'differs', 'the earlier period has its own row');
  assert.strictEqual(IRV.netYieldCheck(nm, cr, IRV.last(cr)), null, 'only net yields are checked');
  assert.strictEqual(IRV.netYieldCheck(nm, { ...ny, derived: 'crediting_rate' }, IRV.last(ny)), null, 'a net yield stated to equal the crediting rate is not checked');
  assert.strictEqual(IRV.netYieldCheck(nm, { ...ny, term: '5' }, IRV.last(ny)), null, 'another tenor: no row');
  assert.strictEqual(IRV.netYieldCheck(nm, ny, { ...IRV.last(ny), date: OLD }), null, 'another date: no row');
  console.log('net-yield check ok');

  // A plan with no published net yield takes the value model's yield: one point, on the model's date.
  const row = { product_id: 'my', plan: 'a', currency: 'USD', term_years: 10, age_band: '', effective_date: CUR, crediting_pct: 4.0,
    charge_pct: 3.0, charge_source: 'brochure', yield_pct: 3.6837, pays_interest: false };
  const myFiles = { my: [{ file: 'my__crediting_rate__a.csv', text: csv('my', 'a', [[PREV, 3.90], [CUR, 4.00]]) }], ny: nyFiles.ny };
  const mm = IRV.buildModel({ ...nyDs, products: [product('my', ['a']), product('ny', ['a'])],
    modelYields: [row, { ...row, product_id: 'ny' }, { ...row, plan: 'zz' }] }, myFiles);
  const mod = mm.series.filter((s) => s.derived === 'model');
  assert.strictEqual(mod.length, 1, 'only where no net yield is published and the plan exists');
  assert.deepStrictEqual([mod[0].pid, mod[0].metric, mod[0].obs.length, mod[0].obs[0].date, mod[0].obs[0].v], ['my', 'net_yield', 1, CUR, 3.6837]);
  assert.ok(!mm.byProduct.get('my').some((s) => s.metric === 'net_yield'), 'not on the product page');
  assert.strictEqual(IRV.netYieldCheck(mm, mod[0], mod[0].obs[0]), null, 'a modelled yield is not checked against itself');
  assert.ok(/3% up-front charge/.test(IRV.modelYieldNote(mod[0])));
  const stale = IRV.buildModel({ ...nyDs, products: [product('my', ['a'])], modelYields: [{ ...row, crediting_pct: 3.5 }] }, myFiles);
  assert.strictEqual(stale.series.filter((s) => s.derived === 'model').length, 0, 'dropped when the crediting rate has moved since the model ran');
  console.log('modelled yields ok');
}

// ---------- Plan labels: a product with one plan shows none on boards, Moves and legends ----------
{
  const pm = IRV.buildModel({ ...ds, products: [product('steady', ['a', 'b', 'target']), product('fresh', ['n1'])] }, files);
  const fresh = pm.series.find((s) => s.pid === 'fresh'), a = pm.series.find((s) => s.plan === 'a');
  assert.strictEqual(IRV.planEn('n1', 'fresh'), 'n1', 'the label itself is kept');
  assert.strictEqual(IRV.planShown('n1', 'fresh'), '', 'single plan: no label');
  assert.strictEqual(IRV.planShown('a', 'steady'), 'a', 'several plans: the label');
  assert.strictEqual(IRV.seriesLabel(fresh), 'USD 10Y');
  assert.strictEqual(IRV.seriesLabel(a), 'a · USD 10Y');
  console.log('plan labels ok');
}
