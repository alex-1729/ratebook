// Candidates page: products marked "candidate": true in their product file.
// They are collected and stored like any other product, but stay out of the
// leaderboard, markets, moves, chart, search and the assistant until enabled.
(function () {
  'use strict';
  const IRV = window.IRV;
  const { h } = IRV;

  IRV.views.candidates = function (main) {
    const app = IRV.app, ds = app.ds, m = app.model;
    const waiting = IRV.arr(ds.candidates), enabled = IRV.arr(ds.enabledCandidates);
    const insurers = new Map(IRV.arr(ds.insurers).map((i) => [i.id, i]));
    const typeOf = (p) => (p.product_type && m && m.typeLabel ? m.typeLabel(p.product_type) : IRV.categoryEn(p.category));
    const sortKey = (p) => IRV.insurerEn(insurers.get(p.insurer_id), p.insurer_id) + '|' + IRV.nameEn(p);
    const sorted = (list) => [...list].sort((a, b) => sortKey(a).localeCompare(sortKey(b)));

    main.append(h('h1', { class: 'sm' }, 'Candidates'));
    main.append(h('p', { class: 'sub' },
      'Products that have been collected but not yet added to the app. A candidate does not appear in the leaderboard, markets, moves, chart, search or the assistant until it is enabled. Its rates keep updating either way.'));

    const table = (list, on) => {
      const rows = [];
      let last = null;
      for (const p of sorted(list)) {
        if (p.insurer_id !== last) {
          last = p.insurer_id;
          const group = list.filter((x) => x.insurer_id === p.insurer_id);
          rows.push(h('tr', { class: 'ins' },
            h('td', { colspan: 4 }, IRV.insurerEn(insurers.get(p.insurer_id), p.insurer_id)),
            h('td', { class: 'act' }, group.length > 1 ? h('button', { type: 'button', class: 'btn', 'data-enable-insurer': p.insurer_id,
              onclick: () => app.setCandidates(group.map((x) => x.id), !on) }, on ? 'Disable all ' + group.length : 'Enable all ' + group.length) : null)));
        }
        const files = IRV.arr(ds.rateFiles && ds.rateFiles[p.id]).length;
        const editions = IRV.arr(p.editions).length;
        rows.push(h('tr', { 'data-cand': p.id },
          h('td', null, IRV.nameEn(p), h('span', { class: 'ja' }, IRV.str(p.name_ja)),
            editions ? h('span', { class: 'ja' }, `also sold under ${editions} other name${editions > 1 ? 's' : ''}`) : null),
          h('td', null, typeOf(p)),
          h('td', null, IRV.arr(p.currencies).join(' · ')),
          h('td', null, files ? files + ' rate file' + (files > 1 ? 's' : '') : 'no rates'),
          h('td', { class: 'act' },
            on ? h('button', { type: 'button', class: 'btn', onclick: () => app.go('product', { id: p.id }) }, 'Open') : null,
            on ? ' ' : null,
            h('button', { type: 'button', class: 'btn' + (on ? '' : ' go'), 'data-toggle': p.id,
              onclick: () => app.setCandidates(p.id, !on) }, on ? 'Disable' : 'Enable'))));
      }
      return h('table', { class: 'cand' },
        h('thead', null, h('tr', null, h('th', null, 'Product'), h('th', null, 'Type'), h('th', null, 'Currencies'), h('th', null, 'Data'), h('th', null, ''))),
        h('tbody', null, rows));
    };

    main.append(h('h2', null, 'Waiting', h('span', { class: 'more' }, waiting.length)));
    if (waiting.length) {
      main.append(h('div', { class: 'rowf' }, h('button', { type: 'button', class: 'btn go', id: 'cand-enable-all',
        onclick: () => app.setCandidates(waiting.map((p) => p.id), true) }, 'Enable all ' + waiting.length)));
      main.append(table(waiting, false));
    } else main.append(h('div', { class: 'sub' }, 'None.'));

    main.append(h('h2', null, 'Enabled', h('span', { class: 'more' }, enabled.length)));
    main.append(enabled.length ? table(enabled, true) : h('div', { class: 'sub' }, 'None yet.'));

    main.append(h('p', { class: 'foot' },
      'Enabling is remembered by this app on this computer, not in the data folder. To make a product permanent for everyone using the data folder, remove "candidate": true from its file in data/products/.'));
  };
})();
