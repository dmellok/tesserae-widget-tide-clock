/* tide_clock: a tide-clock dial, the tide curve and the day's highs and lows.

   The server hands over everything in one frame: minutes from the start of
   the curve's window, in the place's own time zone, with every time already
   formatted. This file only draws. The dial is a fixed 1000-unit viewBox;
   the curve is drawn in pixels after the cell has laid out, so its type and
   strokes are real sizes rather than scaled ones.

   Colour comes from the Spectra tokens only: the ring, the words and the
   curve's line in the text colour, the hand and the now marker in accent-1,
   the water under the curve in the soft teal. On a black and white panel the
   soft fill goes white and the line still carries the curve. */

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const fmtH = (v, units) => {
  if (v == null || !Number.isFinite(Number(v))) return '';
  const n = Number(v);
  return (units === 'ft' ? n.toFixed(1) : n.toFixed(1)) + ' ' + (units === 'ft' ? 'ft' : 'm');
};

const polar = (r, deg) => {
  const a = (deg - 90) * Math.PI / 180;
  return [r * Math.cos(a), r * Math.sin(a)];
};

/* ---- the dial ------------------------------------------------------- */

function dialSvg(d, compact) {
  const R = 500;
  const parts = [];
  parts.push(`<circle class="d-face" cx="0" cy="0" r="${R - 12}"/>`);
  parts.push(`<circle class="d-outer" cx="0" cy="0" r="${R - 18}"/>`);
  parts.push(`<circle class="d-inner" cx="0" cy="0" r="${R - 108}"/>`);

  /* Words on the band between the rings. The top arc runs clockwise so the
     text reads left to right over the crown; the bottom arc runs the other
     way so it reads upright under the foot. */
  const rt = R - 90;
  parts.push(`<defs>
    <path id="tc-top" d="M ${-rt} 0 A ${rt} ${rt} 0 0 1 ${rt} 0"/>
    <path id="tc-bot" d="M ${-rt} 0 A ${rt} ${rt} 0 0 0 ${rt} 0"/>
  </defs>`);
  parts.push(`<text class="d-word"><textPath href="#tc-top" startOffset="50%" text-anchor="middle">HIGH TIDE</textPath></text>`);
  parts.push(`<text class="d-word d-word-b"><textPath href="#tc-bot" startOffset="50%" text-anchor="middle">LOW TIDE</textPath></text>`);

  /* Twelve divisions, a tide clock's nominal hour each. The numerals count
     down to the next turn: 5 to 1 on the right toward low water, 5 to 1 on
     the left back up toward high. */
  const ri = R - 108;
  for (let k = 0; k < 12; k += 1) {
    const deg = k * 30;
    const main = k === 0 || k === 6;
    const quarter = k === 3 || k === 9;
    const len = main ? 46 : quarter ? 34 : 24;
    const [x1, y1] = polar(ri - 6, deg);
    const [x2, y2] = polar(ri - 6 - len, deg);
    parts.push(`<line class="d-tick${main ? ' d-tick-main' : quarter ? ' d-tick-q' : ''}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`);
    if (quarter) {
      /* The quarter marks also cross the band, as on a printed dial. */
      const [bx1, by1] = polar(R - 108, deg);
      const [bx2, by2] = polar(R - 18, deg);
      parts.push(`<line class="d-tick-main" x1="${bx1.toFixed(1)}" y1="${by1.toFixed(1)}" x2="${bx2.toFixed(1)}" y2="${by2.toFixed(1)}"/>`);
    }
    if (!main && !compact) {
      const n = 6 - (k % 6);
      const [nx, ny] = polar(ri - 112, deg);
      parts.push(`<text class="d-num" x="${nx.toFixed(1)}" y="${(ny + 26).toFixed(1)}">${n}</text>`);
    }
  }

  /* The state, in words: the hand says it, but words survive a glance on a
     panel. It sits under the hub unless the hand points down, when it moves
     above so the pointer never crosses it. */
  const state = d.state === 'rising' ? 'RISING' : 'FALLING';
  const ang = Number(d.hand && d.hand.angle) || 0;
  const wordY = ang > 100 && ang < 260 ? -172 : R - 268;
  if (!compact) parts.push(`<text class="d-state" x="0" y="${wordY}">${state}</text>`);

  /* The hand: a pointer in accent-1 with a short counterweight, rotated
     from the top. Drawn last so it sits over everything. */
  parts.push(`<g class="d-hand" transform="rotate(${ang.toFixed(2)})">
    <path d="M 0 118 L -22 118 L -22 -200 L -62 -200 L 0 -322 L 62 -200 L 22 -200 L 22 118 Z"/>
    <circle cx="0" cy="0" r="52"/>
    <circle class="d-hub" cx="0" cy="0" r="20"/>
  </g>`);

  return `<svg class="dial" viewBox="${-R} ${-R} ${R * 2} ${R * 2}" preserveAspectRatio="xMidYMid meet" aria-label="Tide clock, ${esc(state.toLowerCase())}">${parts.join('')}</svg>`;
}

/* ---- the moon ------------------------------------------------------- */

function moonPath(r, illum, waxing, flip) {
  const b = r * Math.abs(1 - 2 * illum);
  const lit = flip ? !waxing : waxing;
  const outer = lit ? 1 : 0;
  const inner = illum > 0.5 ? outer : 1 - outer;
  return `M0 ${-r} A ${r} ${r} 0 0 ${outer} 0 ${r} A ${b} ${r} 0 0 ${inner} 0 ${-r} Z`;
}

function moonSvg(m) {
  const r = 44;
  return `<svg class="moon" viewBox="-50 -50 100 100" aria-label="${esc(m.name || '')}">
    <circle class="m-disc" cx="0" cy="0" r="${r}"/>
    <path class="m-lit" d="${moonPath(r, Number(m.illum) || 0, !!m.waxing, !!m.flip)}"/>
  </svg>`;
}

/* ---- the curve ------------------------------------------------------ */

function sunGlyph(x, y, r) {
  const rays = [];
  for (let k = 0; k < 8; k += 1) {
    const [ax, ay] = polar(r * 1.45, k * 45);
    const [bx, by] = polar(r * 2.0, k * 45);
    rays.push(`<line x1="${(x + ax).toFixed(1)}" y1="${(y + ay).toFixed(1)}" x2="${(x + bx).toFixed(1)}" y2="${(y + by).toFixed(1)}"/>`);
  }
  return `<g class="c-sun"><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}"/>${rays.join('')}</g>`;
}

/* Draws the curve into a box of w by h pixels. ``mode`` is "almanac" for the
   filled poster curve with sun marks and labelled turns, or "plain" for the
   smaller line beside the dial. */
function curveSvg(d, w, h, mode, fs) {
  const series = d.series || [];
  if (series.length < 2 || !(w > 40) || !(h > 30)) return '';
  const almanac = mode === 'almanac';
  const span = Number(d.window && d.window.minutes) || series[series.length - 1][0];
  const axisH = almanac ? fs * 1.9 : fs * 1.7;
  const padTop = almanac ? fs * 2.6 : fs * 2.0;
  const padL = almanac ? 2 : 2;
  const padR = almanac ? 2 : 2;
  const base = h - axisH;
  let lo = d.range ? d.range[0] : 0;
  let hi = d.range ? d.range[1] : 1;
  if (!(hi > lo)) { hi = lo + 1; }
  const amp = hi - lo;
  /* Leave water under the lowest low so the curve never touches the base. */
  const floor = lo - amp * (almanac ? 0.22 : 0.18);
  const X = (m) => padL + (m / span) * (w - padL - padR);
  const Y = (v) => padTop + (1 - (v - floor) / (hi - floor)) * (base - padTop);

  const pts = series.map(([m, v]) => [X(m), Y(v)]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join('');
  const fill = `${line}L${pts[pts.length - 1][0].toFixed(1)} ${base.toFixed(1)}L${pts[0][0].toFixed(1)} ${base.toFixed(1)}Z`;

  const parts = [];
  parts.push(`<path class="c-fill" d="${fill}"/>`);
  parts.push(`<path class="c-line" d="${line}"/>`);
  parts.push(`<line class="c-base" x1="${padL}" y1="${base.toFixed(1)}" x2="${(w - padR).toFixed(1)}" y2="${base.toFixed(1)}"/>`);

  /* Ticks along the base. Thin the labels when they would collide. */
  const ticks = d.ticks || [];
  const labelW = fs * 4.4;
  const every = Math.max(1, Math.ceil((ticks.length * labelW) / Math.max(1, w)));
  const phase = Math.max(0, ticks.findIndex((t) => t.midnight));
  ticks.forEach((t, i) => {
    const x = X(t.x);
    parts.push(`<line class="c-tick${t.major ? ' c-tick-major' : ''}" x1="${x.toFixed(1)}" y1="${base.toFixed(1)}" x2="${x.toFixed(1)}" y2="${(base + (t.major ? fs * 0.55 : fs * 0.35)).toFixed(1)}"/>`);
    if (((i - phase) % every + every) % every === 0) {
      if (x > labelW * 0.45 && x < w - labelW * 0.45) {
        parts.push(`<text class="c-tlabel${t.midnight ? ' c-tlabel-day' : ''}" x="${x.toFixed(1)}" y="${(base + fs * 1.45).toFixed(1)}">${esc(t.label)}</text>`);
      }
    }
  });

  /* Labels inside the water (lows, sunrise and sunset) are kept apart with a
     small bookkeeping of boxes: a later one that would sit on an earlier one
     drops a line, and off the bottom it is left out. */
  const taken = [];
  const place = (x, y, width, height) => {
    let yy = y;
    for (let tries = 0; tries < 3; tries += 1) {
      const box = [x - width / 2, yy - height, x + width / 2, yy];
      const hit = taken.some((b) => !(box[2] < b[0] || b[2] < box[0] || box[3] < b[1] || b[3] < box[1]));
      if (!hit) { taken.push(box); return yy; }
      yy += height * 1.05;
    }
    return null;
  };

  if (almanac) {
    (d.events || []).forEach((e) => {
      const x = X(e.x);
      const y = Y(e.height);
      if (x < fs * 1.5 || x > w - fs * 1.5) return;
      const text = `${e.hhmm} · ${fmtH(e.height, d.units)}`;
      const width = text.length * fs * 0.58;
      if (e.kind === 'high') {
        const yy = place(x, y - fs * 0.7, width, fs * 1.2);
        if (yy != null) parts.push(`<text class="c-ev c-ev-high" x="${x.toFixed(1)}" y="${yy.toFixed(1)}">${esc(text)}</text>`);
      } else {
        const yy = place(x, y + fs * 1.5 + fs * 1.2, width, fs * 1.2);
        if (yy != null && yy < base - fs * 0.2) parts.push(`<text class="c-ev c-ev-low" x="${x.toFixed(1)}" y="${yy.toFixed(1)}">${esc(text)}</text>`);
      }
    });
    (d.suns || []).forEach((s) => {
      const x = X(s.x);
      const y = Y(s.h);
      if (x < fs * 1.2 || x > w - fs * 1.2) return;
      const r = fs * 0.42;
      parts.push(sunGlyph(x, y, r));
      if (fs < 11) return;
      const cap = s.kind === 'sunrise' ? 'Sunrise' : 'Sunset';
      const width = Math.max(cap.length, s.hhmm.length) * fs * 0.58;
      const yy = place(x, y + fs * 2.7, width, fs * 2.2);
      if (yy != null && yy < base - fs * 0.2) {
        parts.push(`<text class="c-suncap" x="${x.toFixed(1)}" y="${(yy - fs * 1.05).toFixed(1)}">${cap}</text>`);
        parts.push(`<text class="c-suncap c-suntime" x="${x.toFixed(1)}" y="${yy.toFixed(1)}">${esc(s.hhmm)}</text>`);
      }
    });
  } else {
    (d.suns || []).forEach((s) => {
      const x = X(s.x);
      const y = Y(s.h);
      if (x < fs * 0.8 || x > w - fs * 0.8) return;
      parts.push(sunGlyph(x, y, fs * 0.3));
    });
    (d.events || []).forEach((e) => {
      const x = X(e.x);
      if (x < fs * 1.4 || x > w - fs * 1.4) return;
      const y = Y(e.height);
      const text = e.hhmm;
      const width = text.length * fs * 0.6;
      if (e.kind === 'high') {
        const yy = place(x, y - fs * 0.55, width, fs * 1.1);
        if (yy != null) parts.push(`<text class="c-ev c-ev-high" x="${x.toFixed(1)}" y="${yy.toFixed(1)}">${esc(text)}</text>`);
      } else {
        const yy = place(x, y + fs * 1.35, width, fs * 1.1);
        if (yy != null && yy < base - fs * 0.15) parts.push(`<text class="c-ev c-ev-low" x="${x.toFixed(1)}" y="${yy.toFixed(1)}">${esc(text)}</text>`);
      }
    });
  }

  /* Now: a rule the full height of the water with a triangle at the top
     and a dot where it meets the line. */
  const n = d.now || {};
  if (n.x != null) {
    const x = X(n.x);
    const y = Y(n.h);
    const top = almanac ? fs * 1.9 : fs * 0.9;
    const tri = almanac ? fs * 0.55 : fs * 0.4;
    parts.push(`<line class="c-now" x1="${x.toFixed(1)}" y1="${top.toFixed(1)}" x2="${x.toFixed(1)}" y2="${base.toFixed(1)}"/>`);
    parts.push(`<path class="c-now-tri" d="M${(x - tri).toFixed(1)} ${top.toFixed(1)}L${(x + tri).toFixed(1)} ${top.toFixed(1)}L${x.toFixed(1)} ${(top + tri * 1.3).toFixed(1)}Z"/>`);
    parts.push(`<circle class="c-now-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(almanac ? fs * 0.38 : fs * 0.36).toFixed(1)}"/>`);
    if (almanac) {
      parts.push(`<text class="c-nowlabel" x="${x.toFixed(1)}" y="${(top - fs * 0.5).toFixed(1)}">Now</text>`);
    }
  }

  return `<svg class="curve-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${parts.join('')}</svg>`;
}

/* ---- panels --------------------------------------------------------- */

function eventRows(list, d, cls) {
  if (!list || !list.length) return `<div class="${cls}-empty">No turn of the tide today.</div>`;
  return list.map((e) => `
    <div class="${cls}${e.minutes_away < 0 ? ' past' : ''}">
      <span class="ev-time">${esc(e.hhmm)}</span>
      <span class="ev-h">${esc(fmtH(e.height, d.units))}</span>
      <span class="ev-kind">${e.kind === 'high' ? 'HIGH' : 'LOW'}</span>
    </div>`).join('');
}

function nextLine(d) {
  const n = d.next;
  if (!n) return '';
  return `${n.kind === 'high' ? 'High' : 'Low'} ${esc(n.hhmm)}, ${esc(n.in)}`;
}

function stateLine(d) {
  const s = d.state === 'rising' ? 'Rising' : 'Falling';
  return `${s} · ${fmtH(d.height_now, d.units)} now · ${nextLine(d)}`;
}

function errorCard(msg, title) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <div class="err">
      <i class="ph-bold ph-warning-circle" aria-hidden="true"></i>
      <div class="err-t">${esc(title || 'Tide Clock')}</div>
      <div class="err-b">${esc(msg)}</div>
    </div>`;
}

export default async function render(shadow, ctx) {
  const d = (ctx && ctx.data) || {};
  const size = (ctx && ctx.cell && ctx.cell.size) || 'md';
  const cw = (ctx && ctx.cell && ctx.cell.w) || 640;
  const ch = (ctx && ctx.cell && ctx.cell.h) || 400;
  const portrait = ch > cw * 1.08;
  const title = d.title || d.label || 'Tide';
  let layout = d.layout || 'dial_curve';
  if (size === 'xs') layout = 'dial';
  const units = d.units || 'm';
  const sun = d.sun_today || {};
  const sunLine = sun.sunrise ? 'Sunrise ' + esc(sun.sunrise) + ' · Sunset ' + esc(sun.sunset || '') : '';

  let body;
  if (d.error) {
    body = errorCard(d.error, d.title || d.label);
  } else if (layout === 'dial') {
    const onlyText = size === 'xs' ? '' : '<div class="only-text"><div class="only-next">' + nextLine(d)
      + '</div><div class="only-sub">' + esc(title) + ' · ' + (d.state === 'rising' ? 'rising' : 'falling')
      + ', ' + fmtH(d.height_now, units) + ' now</div></div>';
    body = `
      <div class="only">
        <div class="dialbox">${dialSvg(d, size === 'xs')}</div>
        ${onlyText}
      </div>`;
  } else if (layout === 'almanac') {
    const n = d.next || {};
    const head = n.kind ? `Next ${n.kind} ${esc(n.hhmm)} · ${fmtH(n.height, units)}` : 'Tide almanac';
    body = `
      <div class="alm">
        <div class="alm-head">
          <div class="alm-moon">${moonSvg(d.moon || {})}</div>
          <div class="alm-title">
            <div class="alm-h1">${head}</div>
            <div class="alm-sub">${esc(title)} · ${esc((d.now || {}).date_line || '')} · ${d.state === 'rising' ? 'rising' : 'falling'}, ${fmtH(d.height_now, units)} now</div>
          </div>
        </div>
        <div class="curve" data-mode="almanac"></div>
        <div class="alm-foot">
          <div class="alm-list">${eventRows(d.today, d, 'alm-ev')}</div>
        </div>
        <div class="alm-src">${sunLine ? sunLine + ' · ' : ''}${(d.moon && d.moon.name) ? esc(d.moon.name) + ' · ' : ''}${esc(d.source_note || '')}</div>
      </div>`;
  } else {
    body = `
      <div class="wrap${portrait ? ' portrait' : ''}">
        <div class="dialbox">${dialSvg(d, false)}</div>
        <div class="side">
          <div class="curve" data-mode="plain"></div>
          <div class="head">
            <div class="place">${esc(title)}</div>
            <div class="date">${esc(size === 'sm' ? ((d.now || {}).date_short || '') : ((d.now || {}).date_line || ''))}</div>
            <div class="state">${stateLine(d)}</div>
          </div>
          <div class="list">${eventRows(d.today, d, 'ev')}</div>
          <div class="foot">
            <span class="foot-moon">${moonSvg(d.moon || {})}<span>${esc((d.moon || {}).name || '')}</span></span>
            ${sunLine ? '<span>' + sunLine + '</span>' : ''}
          </div>
        </div>
      </div>`;
  }

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <link rel="stylesheet" href="/plugins/tide_clock/client.css">
    <div class="tc size-${esc(size)} lay-${esc(layout)}${portrait ? ' is-portrait' : ''}">${body}</div>`;

  /* The curve wants real pixels: wait for the stylesheet (a link in a shadow
     root loads on its own time, and before it lands the box has no height),
     measure the box, then draw. Fonts are sized by CSS on the box so the SVG
     text follows the same clamp() as the rest of the widget. */
  const link = shadow.querySelector('link[href$="/client.css"]');
  await new Promise((resolve) => {
    if (!link || link.sheet) { resolve(); return; }
    link.addEventListener('load', resolve, { once: true });
    link.addEventListener('error', resolve, { once: true });
    setTimeout(resolve, 2000);
  });
  const box = shadow.querySelector('.curve');
  if (box) {
    const rect = box.getBoundingClientRect();
    const fs = parseFloat(getComputedStyle(box).fontSize) || 12;
    box.innerHTML = curveSvg(d, Math.floor(rect.width), Math.floor(rect.height), box.dataset.mode, fs);
  }
}
