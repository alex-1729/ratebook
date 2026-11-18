// The source viewer window: shows the saved page a rate came from. The main process has already checked that the
// file is inside sources/ of the data folder; the page is shown with scripts off and no network.
(function () {
  'use strict';
  const q = new URLSearchParams(location.search);
  const text = (id, s) => { document.getElementById(id).textContent = s || ''; };
  text('src-label', q.get('label'));
  text('src-path', 'Saved copy' + (q.get('retrieved') ? ' · retrieved ' + q.get('retrieved') : ''));
  text('src-date', '');
  if (q.get('unchecked') === '1') {
    const d = q.get('verified');
    const note = document.createElement('span');
    note.className = 'unc-note';
    note.textContent = ' · ○ Unchecked: ' + (/^\d{4}-\d{2}-\d{2}$/.test(d || '') ? 'collected after the last independent re-check on ' + d : 'this product has had no independent re-check');
    document.getElementById('src-path').append(note);
  }
  document.title = 'Source · ' + (q.get('label') || 'saved copy');
  const url = q.get('url');
  const btn = document.getElementById('src-url');
  if (url) { btn.title = url; btn.addEventListener('click', () => window.api.openUrl(url)); } else btn.hidden = true;
  const frame = document.getElementById('src-frame');
  const say = (s) => { const n = document.createElement('span'); n.className = 'src-hit'; n.textContent = s; document.getElementById('src-path').append(n); };
  // One place: the cell the rate was read from. Several: every place the value appears, none singled out.
  const found = (n) => (n === 1 ? ' · figure marked' : n > 1 ? ` · ${n} matches: the page does not show which one this rate is` : ' · the figure was not found as printed');
  if (q.get('mode') !== 'text') {
    frame.src = q.get('file');
    if (q.get('hit') === 'one') say(' · figure marked');
    else if (window.api.onSourceFound) window.api.onSourceFound((n) => { for (const x of document.querySelectorAll('.src-hit')) x.remove(); say(found(n)); });
    return;
  }
  // A saved API response or text extract: shown as text, with the figure marked and scrolled to.
  frame.hidden = true;
  const pre = document.getElementById('src-text');
  pre.hidden = false;
  window.api.sourceText().then((r) => {
    if (!r) { pre.textContent = 'The saved copy could not be read.'; return; }
    const body = r.text;
    let hits = Array.isArray(r.hits) ? r.hits : [];
    if (!Array.isArray(r.hits) && r.find) {
      // The figure as printed (5.70), else without trailing zeros (5.7); whole numbers only, never part of a longer one.
      const forms = [r.find, r.find.replace(/\.?0+$/, '')].filter((x, i, a) => x && a.indexOf(x) === i);
      for (const f of forms) {
        const re = new RegExp('(?<![\\d.])' + f.replace('.', '\\.') + '(?![\\d]|\\.\\d)', 'g');
        hits = [...body.matchAll(re)].map((k) => [k.index, k.index + f.length]);
        if (hits.length) break;
      }
    }
    let at = 0, first = null;
    for (const [a, b] of hits) {
      pre.append(body.slice(at, a));
      const mark = document.createElement('mark');
      mark.textContent = body.slice(a, b);
      pre.append(mark);
      if (!first) first = mark;
      at = b;
    }
    pre.append(body.slice(at));
    if (first) { if (hits.length === 1) first.className = 'first'; first.scrollIntoView({ block: 'center' }); }
    if (r.find) say(found(hits.length));
  });
})();
