// Minimal RFC 4180 CSV parser. Works both in Node (main process, tests) and
// in the renderer as a plain <script>. Returns an array of objects keyed by
// the header row. Handles quoted fields, escaped quotes, CRLF and a UTF-8 BOM.
(function (root) {
  'use strict';

  function parseCsv(text) {
    if (typeof text !== 'string' || text.length === 0) return [];
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const rows = [];
    let row = [];
    let field = '';
    let inQuotes = false;
    const n = text.length;
    for (let i = 0; i < n; i++) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; }
          else inQuotes = false;
        } else field += c;
      } else if (c === '"') {
        inQuotes = true;
      } else if (c === ',') {
        row.push(field); field = '';
      } else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(field); field = '';
        rows.push(row); row = [];
      } else field += c;
    }
    if (field !== '' || row.length > 0) { row.push(field); rows.push(row); }
    if (rows.length === 0) return [];
    const header = rows[0].map((h) => h.trim());
    const out = [];
    for (let r = 1; r < rows.length; r++) {
      const cells = rows[r];
      if (cells.length === 1 && cells[0].trim() === '') continue; // blank line
      const obj = {};
      for (let k = 0; k < header.length; k++) {
        obj[header[k]] = cells[k] === undefined ? '' : cells[k].trim();
      }
      out.push(obj);
    }
    return out;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { parseCsv };
  else root.parseCsv = parseCsv;
})(typeof globalThis !== 'undefined' ? globalThis : this);
