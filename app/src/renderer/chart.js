// Charts. Two kinds:
//  - IRV.mini: a small static SVG step chart (home-page small multiples).
//  - IRV.stepChart: an interactive chart on uPlot (axes, cursor, drag-zoom,
//    read-out); the lines are drawn here so that a rate is held flat from its
//    effective date to the next one, not_offered is a real break, and points
//    recovered from the Wayback Machine are hollow markers on dashed lines.
//
// series: [{ label, color, endT, obs: [{ t (epoch s), v (number|null), wb }] }]
(function () {
  'use strict';
  const IRV = window.IRV;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  };

  // Path segments for one series: [{ d, dashed }]. x/y map values to pixels.
  // Where the flat segment that starts at obs[j] ends. A rate is only known to
  // hold until the next point when that point is close (about one period); a
  // capture followed by a long gap covers one period and is not joined on.
  // How long one point covers and how far apart two points may be depends on the series' own rate period
  // (s.cover and s.gap, in seconds: a half-month, a month, or N days); half-months when not given.
  const GAP = 20 * 86400, COVER = 15 * 86400;
  function coverEnd(s, j) {
    const o = s.obs[j], n = s.obs.length;
    const next = j + 1 < n ? s.obs[j + 1].t : Math.max(s.endT == null ? o.t : s.endT, o.t);
    return next - o.t > (s.gap || GAP) ? { t: o.t + (s.cover || COVER), gap: true } : { t: next, gap: false };
  }
  IRV.coverEnd = coverEnd;

  function stepSegments(s, x, y) {
    const out = [];
    const n = s.obs.length;
    let prevY = null;
    for (let j = 0; j < n; j++) {
      const o = s.obs[j];
      if (o.v == null) { prevY = null; continue; }
      const end = coverEnd(s, j);
      const x0 = x(o.t), x1 = x(end.t), yy = y(o.v);
      let d = '';
      if (prevY != null) d += `M${x0.toFixed(1)},${prevY.toFixed(1)}L${x0.toFixed(1)},${yy.toFixed(1)}`;
      else d += `M${x0.toFixed(1)},${yy.toFixed(1)}`;
      d += `L${x1.toFixed(1)},${yy.toFixed(1)}`;
      out.push({ d, dashed: !!o.wb, x0, x1, y: yy, prevY, wb: !!o.wb, gap: end.gap, t: o.t });
      prevY = end.gap ? null : yy;
    }
    return out;
  }

  IRV.mini = function (series, o) {
    const W = o.w || 320, H = o.h || 170, m = { l: 28, r: 6, t: 8, b: 20 };
    const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
    const ts = series.flatMap((s) => s.obs.map((p) => p.t).concat(s.endT == null ? [] : [s.endT]));
    const vs = series.flatMap((s) => s.obs.map((p) => p.v).filter((v) => v != null));
    if (!vs.length) return root;
    const start = Math.min(...ts), end = Math.max(...ts);
    let lo = Math.floor(Math.min(...vs) * 2) / 2, hi = Math.ceil(Math.max(...vs) * 2) / 2;
    if (hi === lo) hi = lo + 0.5;
    const x = (t) => m.l + ((t - start) / (end - start || 1)) * (W - m.l - m.r);
    const y = (v) => m.t + ((hi - v) / (hi - lo)) * (H - m.t - m.b);
    const grid = IRV.cssVar('--grid'), ink = IRV.cssVar('--faint');
    const step = hi - lo > 3 ? 1 : 0.5;
    for (let v = lo; v <= hi + 1e-9; v += step) {
      root.append(svg('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: grid }));
      const t = svg('text', { x: m.l - 6, y: y(v) + 3.5, 'text-anchor': 'end', 'font-size': 10, fill: ink });
      t.textContent = v.toFixed(1);
      root.append(t);
    }
    for (let yr = new Date(start * 1000).getFullYear(); yr <= new Date(end * 1000).getFullYear() + 1; yr++) {
      const t = new Date(yr, 0, 1).getTime() / 1000;
      if (t <= start || t >= end) continue;
      root.append(svg('line', { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, stroke: grid, 'stroke-dasharray': '2 3' }));
      const tx = svg('text', { x: x(t) + 4, y: H - 6, 'font-size': 10, fill: ink });
      tx.textContent = yr;
      root.append(tx);
    }
    for (const s of series) {
      const segs = stepSegments(s, x, y);
      for (const dashed of [false, true]) {
        const d = segs.filter((g) => g.dashed === dashed).map((g) => g.d).join('');
        if (d) root.append(svg('path', { d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 1.4, 'stroke-dasharray': dashed ? '4 3' : '' }));
      }
      // Isolated points (a long gap after them) get a marker: hollow for Wayback.
      for (const g of segs) {
        if (!g.gap && segs.length > 1) continue;
        root.append(svg('circle', { cx: g.x0, cy: g.y, r: 2.2, fill: g.wb ? IRV.cssVar('--paper') : s.color, stroke: s.color, 'stroke-width': 1 }));
      }
    }
    return root;
  };

  // Tiny trend line for a table row (last year of points, gaps not joined).
  IRV.spark = function (s, color) {
    const W = 96, H = 26;
    const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, 'aria-hidden': 'true' });
    const lastT = s.obs.length ? s.obs[s.obs.length - 1].t : 0;
    const from = lastT - 365 * 86400;
    const obs = s.obs.filter((o) => o.t >= from);
    const vs = obs.map((o) => o.v).filter((v) => v != null);
    if (!vs.length) return root;
    const cut = { ...s, obs };
    const end = Math.max(lastT, s.endT == null ? lastT : s.endT);
    const lo = Math.min(...vs), hi = Math.max(...vs), r = hi - lo || 1;
    const x = (t) => 2 + ((t - from) / (end - from || 1)) * (W - 4);
    const y = (v) => H - 3 - ((v - lo) / r) * (H - 6);
    const segs = stepSegments(cut, x, y);
    for (const dashed of [false, true]) {
      const d = segs.filter((g) => g.dashed === dashed).map((g) => g.d).join('');
      if (d) root.append(svg('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.3, 'stroke-dasharray': dashed ? '3 2' : '' }));
    }
    for (const g of segs) if (g.gap || segs.length === 1) root.append(svg('circle', { cx: g.x0, cy: g.y, r: 1.8, fill: g.wb ? IRV.cssVar('--paper') : color, stroke: color, 'stroke-width': 1 }));
    return root;
  };

  function buildData(series) {
    const xsSet = new Set();
    for (const s of series) {
      s.obs.forEach((o, j) => { xsSet.add(o.t); const e = coverEnd(s, j); if (e.gap) xsSet.add(e.t); });
      if (s.endT != null) xsSet.add(s.endT);
    }
    const xs = [...xsSet].sort((a, b) => a - b);
    const data = [xs];
    for (const s of series) {
      // Value in force at each x (for the read-out and the y range).
      const col = new Array(xs.length).fill(null), flags = new Array(xs.length).fill(false);
      const lastT = s.obs.length ? Math.max(s.obs[s.obs.length - 1].t, s.endT == null ? -Infinity : s.endT) : -Infinity;
      let j = -1;
      for (let i = 0; i < xs.length; i++) {
        while (j + 1 < s.obs.length && s.obs[j + 1].t <= xs[i]) j++;
        // Nothing is read out inside a gap: the rate there is not known.
        const on = j >= 0 && xs[i] <= lastT && (xs[i] === s.obs[j].t || xs[i] < coverEnd(s, j).t || !coverEnd(s, j).gap);
        col[i] = on ? s.obs[j].v : null;
        flags[i] = on && !!s.obs[j].u;
      }
      s.flags = flags;
      data.push(col);
    }
    return data;
  }

  function draw(u, series) {
    const ctx = u.ctx, dpr = window.devicePixelRatio || 1;
    const { left, top, width, height } = u.bbox;
    const paper = IRV.cssVar('--paper');
    const X = (t) => u.valToPos(t, 'x', true), Y = (v) => u.valToPos(v, 'y', true);
    ctx.save();
    ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
    ctx.lineJoin = 'round';
    series.forEach((s, si) => {
      if (!u.series[si + 1].show || !s.obs.length) return;
      ctx.strokeStyle = s.color; ctx.fillStyle = s.color; ctx.lineWidth = 2 * dpr;
      const segs = stepSegments(s, X, Y);
      for (const g of segs) {
        ctx.setLineDash(g.dashed ? [5 * dpr, 4 * dpr] : []);
        ctx.beginPath();
        if (g.prevY != null) { ctx.moveTo(g.x0, g.prevY); ctx.lineTo(g.x0, g.y); } else ctx.moveTo(g.x0, g.y);
        ctx.lineTo(g.x1, g.y);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      const pts = s.obs.filter((o) => o.v != null);
      const span = pts.length > 1 ? Math.abs(X(pts[pts.length - 1].t) - X(pts[0].t)) : 0;
      const gapped = new Set(segs.filter((g) => g.gap).map((g) => g.t));
      for (const o of pts) {
        const sparse = pts.length <= 1 || span / pts.length > 16 * dpr || gapped.has(o.t);
        const x = X(o.t), y = Y(o.v);
        if (o.wb) {
          ctx.beginPath(); ctx.arc(x, y, 4 * dpr, 0, Math.PI * 2);
          ctx.fillStyle = paper; ctx.fill(); ctx.lineWidth = 1.6 * dpr; ctx.stroke();
          ctx.fillStyle = s.color; ctx.lineWidth = 2 * dpr;
        } else if (sparse) {
          ctx.beginPath(); ctx.arc(x, y, 3 * dpr, 0, Math.PI * 2); ctx.fill();
        }
      }
    });
    ctx.restore();
  }

  // opts.legend === false hides the plot's own legend; opts.onCursor(date, values[]) receives the read-out instead.
  IRV.stepChart = function (container, series, opts2) {
    const o2 = opts2 || {};
    container.textContent = '';
    const data = buildData(series);
    const axis = IRV.cssVar('--faint'), grid = IRV.cssVar('--grid');
    const opts = {
      width: Math.max(320, container.clientWidth),
      height: Math.max(220, container.clientHeight - 44),
      padding: [12, 14, 0, 0],
      cursor: { points: { size: 7 }, drag: { x: true, y: false } },
      legend: { show: o2.legend !== false, live: true, markers: { width: 0, fill: (_u, i) => (i > 0 ? series[i - 1].color : 'transparent') } },
      scales: {
        // At least about two months wide: a series with one date (or a zoom into a few days) still gets a
        // date axis rather than hours.
        x: { time: true, range: (_u, min, max) => {
          const pad = Math.max((max - min) * 0.015, 43200), MIN = 60 * 86400;
          if (max - min + 2 * pad >= MIN) return [min - pad, max + pad];
          const mid = (min + max) / 2;
          return [mid - MIN / 2, mid + MIN / 2];
        } },
        y: { range: (_u, min, max) => {
          if (min == null || max == null) return [0, 1];
          const pad = Math.max((max - min) * 0.1, 0.05);
          return [min >= 0 ? Math.max(0, min - pad) : min - pad, max + pad];
        } },
      },
      axes: [
        { stroke: axis, grid: { show: false }, ticks: { stroke: grid, width: 1 }, font: '11px system-ui' },
        { stroke: axis, size: 46, font: '11px system-ui', grid: { stroke: grid, width: 1 }, ticks: { show: false },
          values: (_u, vals) => vals.map((v) => v.toFixed(2)) },
      ],
      series: [
        { label: 'Date', value: '{YYYY}-{MM}-{DD}' },
        ...series.map((s) => ({ label: s.label, stroke: s.color, scale: 'y', paths: () => null, points: { show: false },
          // ○ after a value: that point has not been independently re-checked.
          value: (_u, v, _si, idx) => (v == null ? '–' : v.toFixed(2) + '%' + (idx != null && s.flags && s.flags[idx] ? ' ○' : '')) })),
      ],
      hooks: { draw: [(u) => draw(u, series)],
        setCursor: [(u) => {
          if (!o2.onCursor) return;
          const idx = u.cursor.idx;
          if (idx == null) { o2.onCursor('', series.map(() => '')); return; }
          const d = new Date(data[0][idx] * 1000);
          const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
          o2.onCursor(date, series.map((s, i) => { const v = data[i + 1][idx]; return v == null ? '–' : v.toFixed(2) + '%' + (s.flags && s.flags[idx] ? ' ○' : ''); }));
        }] },
    };
    const u = new uPlot(opts, data, container);
    const fit = () => {
      const lg = u.root.querySelector('.u-legend');
      const w = container.clientWidth;
      if (w > 0) u.setSize({ width: w, height: Math.max(220, container.clientHeight - (lg ? lg.offsetHeight : 44)) });
    };
    const ro = new ResizeObserver(fit);
    ro.observe(container);
    const xs = data[0];
    return {
      setRange(years) {
        if (!xs.length) return;
        const max = xs[xs.length - 1];
        const min = years == null ? xs[0] : Math.max(xs[0], max - years * 365.25 * 86400);
        // Same two-month floor as the scale's own range.
        if (max - min < 60 * 86400) { const mid = (min + max) / 2; u.setScale('x', { min: mid - 30 * 86400, max: mid + 30 * 86400 }); return; }
        u.setScale('x', { min, max });
      },
      destroy() { ro.disconnect(); u.destroy(); },
    };
  };
})();
