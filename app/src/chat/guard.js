'use strict';
// What the assistant's built-in file tools may touch: data/, reports/ and
// sources/<insurer>/docs/ inside the chosen data folder, and nothing else.
const fs = require('fs');
const path = require('path');

const real = (p) => { try { return fs.realpathSync(p); } catch { return null; } };

function insideAllowed(root, target) {
  // 1. As written, the path must be data/..., reports/..., translations/... or sources/<insurer>/docs/... of the data folder.
  const lex = path.relative(path.resolve(root), path.resolve(target));
  if (!lex || lex.startsWith('..') || path.isAbsolute(lex)) return false;
  const parts = lex.split(path.sep);
  const okShape = parts[0] === 'data' || parts[0] === 'reports' || parts[0] === 'translations' || (parts[0] === 'sources' && parts.length >= 3 && parts[2] === 'docs');
  if (!okShape) return false;
  // 2. Followed through any symlinks, it must still be inside the data folder or inside that top-level folder's
  //    own real location (a data folder may keep sources/ as a link to another disk).
  let probe = path.resolve(target);
  const tail = [];
  while (!fs.existsSync(probe)) {
    const up = path.dirname(probe);
    if (up === probe) return false;
    tail.unshift(path.basename(probe)); probe = up;
  }
  const abs = path.join(real(probe) || probe, ...tail);
  const within = (base) => { if (!base) return false; const r = path.relative(base, abs); return r === '' || (!r.startsWith('..') && !path.isAbsolute(r)); };
  return within(real(root)) || within(real(path.join(root, parts[0])));
}

const WHERE = 'The assistant may read only data/, reports/, translations/ and sources/<insurer>/docs/ inside the data folder.';
const str = (x) => (typeof x === 'string' ? x : '');
const bad = (s) => s.includes('\0') || s.split(/[\\/]/).includes('..') || s.startsWith('~');

// decide(root, toolName, toolInput) -> { allow, reason }
function decide(root, tool, input) {
  const inp = input && typeof input === 'object' ? input : {};
  if (tool === 'Read') {
    const f = str(inp.file_path);
    if (!f || bad(f)) return { allow: false, reason: WHERE };
    return insideAllowed(root, path.resolve(root, f)) ? { allow: true, reason: '' } : { allow: false, reason: WHERE };
  }
  if (tool === 'Glob' || tool === 'Grep') {
    const base = str(inp.path);
    const pattern = tool === 'Glob' ? str(inp.pattern) : str(inp.glob);
    if (bad(base) || bad(pattern)) return { allow: false, reason: WHERE };
    let start = path.resolve(root, base || '.');
    if (tool === 'Glob') {
      // The fixed leading part of the pattern narrows where the search starts.
      const fixed = [];
      for (const seg of pattern.split(/[\\/]/)) { if (/[*?[\]{}!]/.test(seg) || seg === '') break; fixed.push(seg); }
      if (path.isAbsolute(pattern)) start = path.resolve(path.sep, ...fixed);
      else if (fixed.length) start = path.resolve(start, ...(fixed.length === pattern.split(/[\\/]/).length ? fixed.slice(0, -1) : fixed));
    }
    return insideAllowed(root, start) ? { allow: true, reason: '' }
      : { allow: false, reason: WHERE + ' Give a path such as data/products, reports or sources/<insurer>/docs/<product_id>.' };
  }
  return { allow: false, reason: 'That tool is not available to the assistant.' };
}

module.exports = { decide, insideAllowed };
