/* Mittens & Pence front end. Vanilla — no build step, no CDN, works offline. */
'use strict';

// ---------------------------------------------------------------- utilities
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {                       // app state
  boot: null, view: 'dashboard', data: {}, currency: 'GBP',
  hide: false, period: null, filters: {},
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const SYM = { GBP: '£', ZAR: 'R', USD: '$', EUR: '€' };

// Rounding above £1,000 keeps the headline figures readable, but inside a table it
// makes a row contradict itself: £1,450 − £1,534 was printed as −£84.40. Anywhere the
// numbers sit in a column and are meant to add up, pass { dp: 2 }.
function money(v, ccy, opts = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const c = ccy || S.currency;
  const abs = Math.abs(v);
  const dp = opts.dp !== undefined ? opts.dp : (abs >= 1000 || opts.round ? 0 : 2);
  const s = abs.toLocaleString('en-GB', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const sign = v < 0 ? '−' : (opts.plus && v > 0 ? '+' : '');
  return `${sign}${SYM[c] || ''}${s}`;
}

// How long the figures actually cover, and whether that supports a per-year rate.
// The old line divided the return by a start date read from settings, which ships with
// the anchor from the original spreadsheet — so three months of data reported a rate
// "over 5.0 yrs". A period is now only annualised when there is a year of it.
function spanWords(years) {
  if (!years) return 'no history yet';
  const months = Math.round(years * 12);
  if (months < 24) return `${months} month${months === 1 ? '' : 's'}`;
  return `${years.toFixed(1)} years`;
}

function returnPace(ov) {
  const years = ov.years || 0;
  const yr = ov.return_on_cost_yr;
  // Nothing invested at all is the case this missed: the API returns a rate of 0.0
  // rather than null, so the guard below waved it through and a brand-new install —
  // the very first screen anybody sees — announced "0.0% a year over 5.1 years",
  // those years coming from the shipped default start date. Same bug as before, one
  // step earlier: test for having any data, not for the rate being null.
  if (!ov.positions && !ov.cost) return 'nothing invested yet';
  if (yr === null || yr === undefined) {
    // Say what there is instead of a number there is no basis for. A dash with an
    // explanation is a better answer than a confident rate over years nobody has.
    return years ? `${spanWords(years)} of history — too short for a yearly rate`
                 : 'no history yet';
  }
  return `${pct(yr)} a year over ${spanWords(years)}`;
}

function pct(v, dp = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(dp)}%`;
}

function signed(v, ccy, opts = {}) { return money(v, ccy, { plus: true, ...opts }); }

// A tooltip on a chart. `before` and `after` are the words, `amount` is the figure —
// kept apart because an SVG <title> is drawn by the browser itself and no amount of CSS
// will blur it, so with Hide amounts on the figure has to actually be gone. The version
// to show while hiding rides along in data-h and `applyPrivacy` swaps the two.
function tip(before, amount, after = '') {
  const plain = `${before}${amount}${after}`;
  const hidden = `${before}•••${after}`;
  return `<title data-h="${esc(hidden)}">${esc(plain)}</title>`;
}
function cls(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; }
function date(d) {
  if (!d) return '—';
  const x = new Date(d);
  if (Number.isNaN(+x)) return d;
  return x.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' });
}
function monthName(p) {
  if (!p) return '';
  const [y, m] = p.split('-');
  return new Date(+y, +m - 1, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.raw ? {} : { 'Content-Type': 'application/json' },
    body: opts.raw ? opts.body : (opts.body ? JSON.stringify(opts.body) : undefined),
  });
  let payload;
  try { payload = await res.json(); } catch { payload = { error: 'Unreadable reply' }; }
  if (!res.ok || payload.error) throw new Error(payload.error || `HTTP ${res.status}`);
  return payload;
}

function toast(msg, kind = '') {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  // A multi-line diagnosis needs reading time and a way to dismiss it, so errors stay
  // put until clicked rather than vanishing after four seconds.
  const text = String(msg || '');
  t.innerHTML = esc(text) + (kind === 'err'
    ? '<div class="tiny muted" style="margin-top:8px">Click to dismiss</div>' : '');
  t.onclick = () => t.remove();
  t.title = 'Click to dismiss';
  $('#toasts').append(t);
  const sticky = kind === 'err' && (text.length > 90 || text.includes('\n'));
  if (!sticky) {
    const life = kind === 'err' ? 9000 : 4200;
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; }, life);
    setTimeout(() => t.remove(), life + 400);
  }
}

function busy(btn, on, label) {
  if (!btn) return;
  if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || 'Working…'; btn.disabled = true; }
  else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; }
}

// ---------------------------------------------------------------- modal
function modal(title, bodyHtml, footHtml = '') {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHtml;
  $('#modal-foot').innerHTML = footHtml;
  $('#modal').hidden = false;
  // Focusing the first field used to scroll the dialog down to it — which on the
  // connection dialogs meant opening halfway down, past step 1 and past the "don't
  // enter your real bank details" warning. Focus without scrolling, and start at the top.
  $('#modal-body').scrollTop = 0;
  $('#modal').scrollTop = 0;
  const first = $('#modal-body input, #modal-body select');
  if (first) setTimeout(() => {
    first.focus({ preventScroll: true });
    $('#modal-body').scrollTop = 0;
    $('#modal').scrollTop = 0;
  }, 30);
  return $('#modal-body');
}
function closeModal() { $('#modal').hidden = true; $('#modal-body').innerHTML = ''; }
$('#modal-close').onclick = closeModal;
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });


// ---- pasting -------------------------------------------------------------
// Two different problems wear the same complaint, "I couldn't paste it in".
//
// 1. Ctrl+V doing nothing in a text box. In a plain browser the OS handles it; inside
//    a native app window it depends on the host wiring the accelerator through, and
//    pywebview does not always. So: if a Ctrl/Cmd+V keydown is NOT followed by a real
//    `paste` event, the host swallowed it — read the clipboard ourselves and insert.
//    Detecting it this way means we never interfere where paste already works.
//
// 2. Wanting to paste rows of transactions rather than upload a file. Handled on the
//    Import screen: paste a block of tab- or comma-separated text and it goes through
//    exactly the same reader a dropped file would.

let lastPasteAt = 0;
document.addEventListener('paste', () => { lastPasteAt = Date.now(); }, true);

function isEditable(el) {
  if (!el) return false;
  const tag = (el.tagName || '').toLowerCase();
  if (tag === 'textarea') return true;
  if (el.isContentEditable) return true;
  if (tag !== 'input') return false;
  return !['checkbox', 'radio', 'button', 'submit', 'file', 'range', 'color']
    .includes((el.type || 'text').toLowerCase());
}

function insertAtCursor(el, text) {
  if (!text) return;
  const start = el.selectionStart, end = el.selectionEnd;
  if (start === null || start === undefined) { el.value += text; }
  else {
    el.value = el.value.slice(0, start) + text + el.value.slice(end);
    const at = start + text.length;
    el.setSelectionRange(at, at);
  }
  // The app listens for input and change, so both have to look like a real edit.
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

document.addEventListener('keydown', (e) => {
  const meta = e.ctrlKey || e.metaKey;
  if (!meta || e.altKey) return;
  const key = (e.key || '').toLowerCase();
  const el = document.activeElement;

  if (key === 'v' && isEditable(el)) {
    const before = lastPasteAt;
    setTimeout(async () => {
      if (lastPasteAt !== before) return;         // the host handled it; leave well alone
      try {
        const text = await navigator.clipboard.readText();
        insertAtCursor(el, text);
      } catch {
        toast('This window is blocking clipboard access — try right-click → Paste', 'warn');
      }
    }, 60);
  }

  // Cut and copy go the same way, since a window that swallows one usually swallows all.
  if ((key === 'c' || key === 'x') && isEditable(el) && el.selectionStart !== el.selectionEnd) {
    setTimeout(async () => {
      try {
        const sel = el.value.slice(el.selectionStart, el.selectionEnd);
        if (!sel || !document.hasFocus()) return;
        const already = await navigator.clipboard.readText().catch(() => null);
        if (already === sel) return;              // the host already did it
        await navigator.clipboard.writeText(sel);
        if (key === 'x') insertAtCursor(el, '');
      } catch { /* nothing more we can do */ }
    }, 60);
  }
}, true);

// ---------------------------------------------------------------- charts
function lineChart(points, { height = 130, ccy, area = true } = {}) {
  const vals = points.map(p => p.value).filter(v => typeof v === 'number');
  if (vals.length < 2) return `<p class="muted small">Not enough history yet — Mittens & Pence adds a point each month.</p>`;
  const w = 640, h = height, pad = 6;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const x = i => pad + (i * (w - pad * 2)) / (points.length - 1);
  const y = v => h - pad - ((v - min) / span) * (h - pad * 2 - 12);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const fill = area
    ? `<path d="${d} L${x(points.length - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z"
         fill="var(--accent)" opacity=".10"/>` : '';
  // A point Mittens & Pence reconstructed from imported history is drawn hollow, so a chart
  // never implies the app was watching before it was installed.
  const est = points.filter(p => p.metrics && p.metrics.estimated).length;
  const firstLive = points.findIndex(p => !(p.metrics && p.metrics.estimated));
  const seg = (from, to) => points.slice(from, to).map((p, i) =>
    `${i ? 'L' : 'M'}${x(from + i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const split = est && firstLive > 0;
  const dashed = split
    ? `<path d="${seg(0, firstLive + 1)}" fill="none" stroke="var(--accent)" stroke-width="2"
        stroke-dasharray="4 3" opacity=".6"/>` : '';
  const solid = split ? seg(firstLive, points.length) : d;
  const dots = points.map((p, i) => {
    const e = p.metrics && p.metrics.estimated;
    return `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.4"
      fill="${e ? 'var(--panel)' : 'var(--accent)'}" stroke="var(--accent)" stroke-width="${e ? 1.4 : 0}">
      ${tip(monthName(p.period) + ': ', money(p.value, ccy, { round: true }),
        e ? ' (reconstructed — ' + (p.metrics.investments_note || 'estimated') + ')' : '')}</circle>`;
  }).join('');
  const last = points[points.length - 1], first = points[0];
  const change = last.value - first.value;
  const estNote = est
    ? `<div class="tiny muted" style="margin-top:2px">The hollow points are reconstructed from
        your imported history — ${esc(points.find(p => p.metrics && p.metrics.estimated)
          .metrics.investments_note || 'estimated')}. Solid points are months Mittens & Pence recorded live.</div>`
    : '';
  return `<svg class="chart" viewBox="0 0 ${w} ${h + 18}" role="img"
    style="max-height:${h + 40}px">
    ${fill}<path d="${solid}" fill="none" stroke="var(--accent)" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>${dashed}${dots}
    <text x="0" y="${h + 12}">${esc(monthName(first.period))}</text>
    <text x="${w}" y="${h + 12}" text-anchor="end">${esc(monthName(last.period))}</text>
  </svg>
  <div class="row small muted" style="margin-top:6px">
    <span class="pv ${cls(change)}">${signed(change, ccy)}</span>
    <span>over ${points.length} months</span></div>${estNote}`;
}

const PALETTE = ['#2352c9', '#0b7a55', '#a55b00', '#7b3fc4', '#0e7490', '#b3261e',
  '#4d7c0f', '#9d174d', '#1f6feb', '#78716c'];

function donut(items, { size = 168, ccy, centre = 'total' } = {}) {
  const total = items.reduce((a, b) => a + (b.value || 0), 0);
  if (!total) return '<p class="muted small">Nothing to show yet.</p>';
  const r = size / 2 - 14, cx = size / 2, cy = size / 2;
  let a0 = -Math.PI / 2;
  const arcs = items.map((it, i) => {
    const frac = (it.value || 0) / total;
    const a1 = a0 + frac * Math.PI * 2;
    const large = frac > 0.5 ? 1 : 0;
    const p = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    // A single slice covering the whole ring starts and ends at the same point, and an
    // SVG arc between two identical points draws NOTHING — an empty box where the chart
    // should be. That is the ordinary first-real-use case: one account, or a first month
    // where all the spending is in one section. Two half arcs make a full circle.
    const d = frac >= 0.999
      ? `M${cx},${(cy - r).toFixed(2)} A${r},${r} 0 1 1 ${cx},${(cy + r).toFixed(2)}`
        + ` A${r},${r} 0 1 1 ${cx},${(cy - r).toFixed(2)}`
      : `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
    a0 = a1;
    return `<path d="${d}" fill="none" stroke="${PALETTE[i % PALETTE.length]}"
      stroke-width="20" stroke-linecap="butt">${tip(it.label + ' — ',
      money(it.value, ccy, { round: true }), ' (' + pct(frac) + ')')}</path>`;
  }).join('');
  const legend = items.slice(0, 8).map((it, i) =>
    `<span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(it.label)}
      <span class="muted">${pct((it.value || 0) / total, 0)}</span></span>`).join('');
  return `<div class="row" style="gap:20px;align-items:center;flex-wrap:wrap">
    <svg viewBox="0 0 ${size} ${size}" style="width:${size}px;height:${size}px;flex:none">${arcs}
      <text x="${cx}" y="${cy - 2}" text-anchor="middle" style="font-size:11px;fill:var(--muted)">${esc(centre)}</text>
      <text class="pv" x="${cx}" y="${cy + 14}" text-anchor="middle"
        style="font-size:14px;fill:var(--ink);font-weight:600">${esc(money(total, ccy, { round: true }))}</text>
    </svg>
    <div class="legend grow">${legend}</div></div>`;
}

function barPair(rows, { ccy, height = 150 } = {}) {
  if (!rows.length) return '<p class="muted small">No months yet.</p>';
  const w = 640;
  const max = Math.max(...rows.flatMap(r => [r.income, r.spend])) || 1;
  const bw = w / rows.length;
  const bars = rows.map((r, i) => {
    const x = i * bw;
    const hi = (r.income / max) * height, hs = (r.spend / max) * height;
    return `<g>
      <rect x="${(x + bw * 0.14).toFixed(1)}" y="${(height - hi).toFixed(1)}"
        width="${(bw * 0.32).toFixed(1)}" height="${hi.toFixed(1)}" fill="var(--up)" opacity=".85" rx="2">
        ${tip(monthName(r.period) + ' in: ', money(r.income, ccy, { round: true }))}</rect>
      <rect x="${(x + bw * 0.5).toFixed(1)}" y="${(height - hs).toFixed(1)}"
        width="${(bw * 0.32).toFixed(1)}" height="${hs.toFixed(1)}" fill="var(--down)" opacity=".8" rx="2">
        ${tip(monthName(r.period) + ' out: ', money(r.spend, ccy, { round: true }))}</rect>
    </g>`;
  }).join('');
  const every = Math.max(1, Math.ceil(rows.length / 6));
  const labels = rows.map((r, i) => (i % every === 0)
    ? `<text x="${(i * bw + bw / 2).toFixed(1)}" y="${height + 13}" text-anchor="middle">${esc(r.period.slice(2))}</text>`
    : '').join('');
  return `<svg class="chart" viewBox="0 0 ${w} ${height + 18}" style="max-height:${height + 40}px">
    ${bars}${labels}</svg>
    <div class="legend small" style="margin-top:4px">
      <span><i style="background:var(--up)"></i>Money in</span>
      <span><i style="background:var(--down)"></i>Money out</span></div>`;
}

// ---------------------------------------------------------------- privacy / theme
function applyPrivacy() {
  // This used to toggle two classes nothing in the app has ever carried, so the only
  // thing that actually blurred was the four headline tiles — every table amount, every
  // balance and every recent transaction stayed in plain sight. The classes that really
  // mark money are `.v` (stat tiles), `.num` (anything in a column) and `.pv` on a
  // figure written into a sentence. A test checks all three are classes that exist.
  $$('.v, .num, .pv').forEach(e => e.classList.toggle('blur', S.hide));
  // Chart tooltips are the browser's own drawing and cannot be blurred, so the figure is
  // swapped out rather than covered up. `tip()` supplies both versions.
  $$('svg title[data-h]').forEach(t => {
    if (t.dataset.v === undefined) t.dataset.v = t.textContent;
    const want = S.hide ? t.dataset.h : t.dataset.v;
    // Only when it differs: assigning the same text still replaces the text node, which
    // the observer below would see as a change, and call this again, for ever.
    if (t.textContent !== want) t.textContent = want;
  });
  $('#btn-privacy').textContent = S.hide ? 'Show amounts' : 'Hide amounts';
}
$('#btn-privacy').onclick = () => { S.hide = !S.hide; applyPrivacy(); };

// Hide amounts has to hold for everything that appears *after* it was switched on — a
// dialog, a drill-down, a table that reloaded on its own. Calling applyPrivacy() at the
// end of every one of those was the old plan and it leaked every time somebody added a
// screen. Watching the document instead means new markup is covered whoever wrote it.
new MutationObserver(() => {
  if (!S.hide || applyPrivacy.queued) return;
  applyPrivacy.queued = true;
  requestAnimationFrame(() => { applyPrivacy.queued = false; applyPrivacy(); });
}).observe(document.body, { childList: true, subtree: true });

// Auto follows the clock on this computer: light through the day, dark in the evening.
// It re-checks every few minutes, so it turns over at dusk without a restart.
const DARK_FROM = 19;   // 7pm
const DARK_UNTIL = 7;   // 7am

function darkByClock(now = new Date()) {
  const h = now.getHours();
  return h >= DARK_FROM || h < DARK_UNTIL;
}

function themeMode() {
  const m = localStorage.getItem('mithapp-theme');
  return (m === 'light' || m === 'dark') ? m : 'auto';
}

function applyTheme(t) {
  const mode = t || themeMode();
  const dark = mode === 'dark' || (mode === 'auto' && darkByClock());
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  localStorage.setItem('mithapp-theme', mode);

  const btn = $('#btn-theme');
  if (btn) {
    btn.textContent = mode === 'auto' ? '◐' : (mode === 'dark' ? '☾' : '☀');
    const until = dark
      ? `light again at ${String(DARK_UNTIL).padStart(2, '0')}:00`
      : `dark from ${String(DARK_FROM).padStart(2, '0')}:00`;
    btn.title = mode === 'auto'
      ? `Following the clock — ${dark ? 'dark' : 'light'} right now, ${until}.\nClick for always light.`
      : mode === 'light' ? 'Always light. Click for always dark.'
      : 'Always dark. Click to follow the clock again.';
    btn.setAttribute('aria-label', btn.title.split('\n')[0]);
  }
  return dark;
}

// Auto → Light → Dark → Auto
$('#btn-theme').onclick = () => {
  const next = { auto: 'light', light: 'dark', dark: 'auto' }[themeMode()];
  applyTheme(next);
  toast(next === 'auto'
    ? `Following this computer's clock — dark between ${DARK_FROM}:00 and ${DARK_UNTIL}:00.`
    : `Always ${next}.`);
  render();
};

// Re-evaluate on a timer and whenever the window is brought back into focus, so a
// machine left open overnight isn't still in daylight at midnight.
setInterval(() => { if (themeMode() === 'auto') applyTheme(); }, 5 * 60 * 1000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && themeMode() === 'auto') applyTheme();
});

// ---------------------------------------------------------------- router
const VIEWS = {};
const TITLES = {
  dashboard: ['Dashboard', 'Everything, at a glance'],
  investments: ['Investments', 'All investments overview'],
  budget: ['Budget', 'Your money month by month'],
  import: ['Import a file', 'Statements or a budget spreadsheet — drop in as many as you like'],
  export: ['Export a Spreadsheet', 'Workbooks, and a copy for your phone'],
  settings: ['Settings', ''],
  setup: ['Welcome', 'Let’s connect the first account'],
};

// Four screens that used to sit in the rail now live as tabs inside Budget. They are
// still separate views internally — nothing about how they render changed — so `go()`
// keeps accepting their old names and simply lands on the right tab. That matters
// because a couple of dozen buttons around the app say things like `go('transactions')`,
// and a rename that quietly broke every one of them would be hard to spot.
const BUDGET_TABS = [
  ['budgets', 'Budgets', 'A budget per section, tracked month by month'],
  ['transactions', 'Transactions', 'Where the money actually went'],
  ['accounts', 'Accounts', 'All overall balances'],
  ['connections', 'Connections', 'Banks and brokers, and how each one updates'],
];
const BUDGET_TAB_IDS = BUDGET_TABS.map(t => t[0]);

function go(view) {
  if (BUDGET_TAB_IDS.includes(view)) {
    S.budgetTab = view;
    view = 'budget';
  }
  S.view = view;
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
  const [t, s] = TITLES[view] || [view, ''];
  $('#view-title').textContent = t;
  $('#view-sub').textContent = s;
  render();
}
$('#nav').addEventListener('click', e => {
  const b = e.target.closest('button[data-view]');
  if (b) go(b.dataset.view);
});

// The Budget screen: a tab strip, then whichever sub-view is selected rendered into a
// host of its own. Each sub-view is untouched — it still receives an element and fills
// it — so nothing had to be rewritten to be nested.
VIEWS.budget = async (host) => {
  const tab = BUDGET_TAB_IDS.includes(S.budgetTab) ? S.budgetTab : 'budgets';
  const [, , sub] = BUDGET_TABS.find(t => t[0] === tab);
  $('#view-sub').textContent = sub;

  host.innerHTML = `
    <div class="subtabs" role="tablist">
      ${BUDGET_TABS.map(([id, label]) => `
        <button role="tab" class="subtab ${id === tab ? 'on' : ''}" data-tab="${id}"
          aria-selected="${id === tab}">${esc(label)}</button>`).join('')}
    </div>
    <div id="subview" style="margin-top:14px"></div>`;

  $$('[data-tab]', host).forEach(b => b.onclick = () => {
    S.budgetTab = b.dataset.tab;
    render();
  });

  const inner = $('#subview');
  inner.innerHTML = `<div class="card card-flat"><div class="spinner"></div></div>`;
  await VIEWS[tab](inner);
};

async function render() {
  const host = $('#view');
  // A view that attached a document-level listener takes it off again here, or every
  // re-render leaves another one behind and a single paste fires the handler five times.
  if (S.cleanup) { try { S.cleanup(); } catch { /* nothing to undo */ } S.cleanup = null; }
  host.innerHTML = `<div class="card card-flat"><div class="spinner"></div></div>`;
  try {
    await (VIEWS[S.view] || VIEWS.dashboard)(host);
  } catch (e) {
    host.innerHTML = `<div class="card"><h2>That didn't load</h2>
      <p class="muted small" style="margin-top:6px">${esc(e.message)}</p>
      <button class="btn" style="margin-top:12px" onclick="location.reload()">Reload</button></div>`;
  }
  applyPrivacy();
}

// ================================================================ DASHBOARD
VIEWS.dashboard = async (host) => {
  const d = await api('/api/dashboard');
  S.data.dashboard = d;
  const c = d.currency;
  const nw = d.net_worth, ov = d.overall, b = d.budget;

  // A mortgage is not a place your money "sits". Taking the absolute value put
  // £188k of debt in the ring as though it were an asset, and the figure in the
  // middle — labelled "total" — came to twice the net worth printed a few
  // centimetres above it. The ring is what you own; what you owe is a line under it.
  const owned = Object.entries(nw.groups).filter(([, v]) => v > 0.5)
    .map(([label, value]) => ({ label, value }));
  const owedTotal = Object.values(nw.groups).filter(v => v < -0.5)
    .reduce((a, v) => a + v, 0);
  const ownedTotal = owned.reduce((a, g) => a + g.value, 0);
  const groupCount = Object.keys(nw.groups).length;
  const invested = !!(ov.positions || ov.cost || ov.invested);

  // Every item carries where to go and what to open, so the card is a list of things
  // to DO. It used to render the text and throw the ids away, which left the front
  // screen telling you something was wrong and then making you go and find it.
  const alerts = (d.health || []).slice(0, 5).map((i, n) =>
    `<button class="alert" data-alert="${n}" type="button">
       <span class="pill ${i.level === 'warn' ? 'warn' : 'info'}">${i.level === 'warn' ? '!' : 'i'}</span>
       <span class="small grow">${esc(i.text)}</span>
       ${i.action ? `<span class="tiny go">${esc(i.do || 'Open')} →</span>` : ''}
     </button>`).join('') ||
    // "Every account is up to date" is a reassuring thing to read and a false one when
    // there are no accounts. An empty copy needs a first step, not an all-clear.
    ((d.accounts || []).length
      ? `<p class="small muted">Nothing needs your attention — every account is up to date
          and everything is categorised.</p>`
      : `<p class="small muted">Nothing here yet. <a href="#" onclick="go('accounts');return false">Add
          an account</a>, or drop a statement into your
          <a href="#" onclick="go('import');return false">statements folder</a> and press
          Sync everything — Mittens &amp; Pence will work out the rest.</p>`);

  const netlines = d.network.down
    ? `<div class="card" style="border-color:var(--warn)">
         <div class="row"><span class="pill warn">Offline</span>
         <span class="small">No price feed reachable, so values are the last ones Mittens & Pence saw.
         Press <b>Refresh prices</b> once you're back online.</span></div></div>` : '';

  host.innerHTML = `
    ${netlines}
    <div class="grid g4">
      <div class="card"><div class="stat">
        <span class="k">Net worth</span>
        ${/* With nothing on file this said "£0 · 1 group", which is a claim about
              somebody's money rather than an admission that nobody has been told
              anything yet. Unknown is not zero — here as everywhere else. */''}
        <span class="v">${esc(groupCount ? money(nw.total, c, { round: true }) : '—')}</span>
        <span class="s muted">${groupCount
          ? `${groupCount} group${groupCount === 1 ? '' : 's'} · ${c}`
          : 'nothing on file yet'}</span></div></div>
      ${/* Nothing invested yet is not a 0.0% return on nothing. A fresh copy used to
            open on "RETURN 0.0%" and "0.0% of what you put in", which looks like a
            portfolio that has gone nowhere rather than one that doesn't exist. */''}
      <div class="card"><div class="stat">
        <span class="k">Investments</span>
        <span class="v">${esc(invested ? money(ov.invested, c, { round: true }) : '—')}</span>
        <span class="s ${invested ? 'pv ' + cls(ov.net) : 'muted'}">${invested
          ? `${esc(signed(ov.net, c))} on ${esc(money(ov.cost, c, { round: true }))} in`
          : 'nothing invested yet'}</span></div></div>
      <div class="card"><div class="stat">
        <span class="k">Return</span>
        <span class="v ${invested ? cls(ov.net) : ''}">${esc(ov.cost ? pct(ov.net / ov.cost) : '—')}</span>
        <span class="s muted">${esc(returnPace(ov))}</span></div></div>
      <div class="card"><div class="stat">
        <span class="k">Dividends received</span>
        <span class="v">${esc(invested ? money(ov.dividends, c, { round: true }) : '—')}</span>
        <span class="s muted">${ov.cost
          ? `${esc(pct(ov.dividend_yield_on_cost))} of what you put in`
          : 'none yet'}</span></div></div>
    </div>

    <div class="grid g2" style="margin-top:14px">
      <div class="card">
        <div class="spread"><h2>Net worth over time</h2>
          <button class="btn btn-sm" id="btn-snap">Take snapshot</button></div>
        <div style="margin-top:10px">${lineChart(d.history, { ccy: c })}</div>
      </div>
      <div class="card">
        <h2>Where it sits</h2>
        <div style="margin-top:12px">${donut(owned, { ccy: c, centre: 'what you own' })}</div>
        ${owedTotal ? `<div class="spread small" style="margin-top:12px;
          border-top:1px solid var(--line);padding-top:10px">
          <span class="muted">Less what you owe</span>
          <span class="down pv">${esc(money(owedTotal, c, { round: true }))}</span></div>
          <div class="spread small" style="margin-top:6px">
          <span class="muted">Net worth</span>
          <b class="pv">${esc(money(ownedTotal + owedTotal, c, { round: true }))}</b></div>` : ''}
      </div>
    </div>

    <div class="grid g2" style="margin-top:14px">
      <div class="card">
        <div class="spread"><h2>${esc(monthName(b.period))}</h2>
          <span class="pill">${esc(pct(b.pace, 0))} through the month</span></div>
        ${/* Four figures, not three. "Left over" is income less spending less what was
              put by, and with saving left off the card the number at the end didn't
              follow from the two before it — £0 in, £2,290 out, and somehow −£3,985
              left over. The arithmetic has to be visible or it looks like a mistake. */''}
        <div class="grid g4" style="margin-top:12px;gap:8px">
          <div class="stat"><span class="k">In</span><span class="v" style="font-size:1.15rem">${esc(money(b.totals.income, c, { round: true }))}</span></div>
          <div class="stat"><span class="k">Out</span><span class="v" style="font-size:1.15rem">${esc(money(b.totals.all_spend, c, { round: true }))}</span></div>
          <div class="stat"><span class="k">Put by</span><span class="v" style="font-size:1.15rem">${esc(money(b.totals.saving, c, { round: true }))}</span>
            <span class="s muted">savings, investments${b.totals.mortgage_capital ? ', mortgage capital' : ''}</span></div>
          <div class="stat"><span class="k">Left over</span><span class="v ${cls(b.totals.net)}" style="font-size:1.15rem">${esc(money(b.totals.net, c, { round: true }))}</span>
            <span class="s muted">in less out less put by</span></div>
        </div>
        ${b.totals.mortgage_capital ? `<p class="tiny muted" style="margin-top:8px">
          <span class="pv">${esc(money(b.totals.mortgage_capital, c, { dp: 0 }))}</span> of the
          mortgage payment is counted under Put by rather than Out, because that part came
          off what you owe rather than leaving the household — which is why the budgets
          below add up to more than Out.</p>` : ''}
        <div class="stack" style="margin-top:14px">
          ${b.items.length ? b.items.map(i => budgetLine(i, c)).join('')
            : `<p class="small muted">No budgets set yet.
                <a href="#" onclick="go('budgets');return false">Set some up</a> — Mittens & Pence can suggest
                figures from what you've actually been spending.</p>`}
        </div>
      </div>
      <div class="card">
        <h2>Income and spending</h2>
        <div style="margin-top:14px">${barPair(d.spend_history, { ccy: c })}</div>
      </div>
    </div>

    <div class="grid g3" style="margin-top:14px">
      <div class="card">
        <h2>Biggest holdings</h2>
        <div class="stack" style="margin-top:10px;gap:7px">
          ${d.top.map(r => `<div class="spread">
            <span><span class="sym">${esc(r.symbol)}</span>
              <span class="muted small">${esc((r.name || '').slice(0, 22))}</span></span>
            <span class="num">${esc(money(r.value, c, { round: true }))}
              <span class="${cls(r.money_made)} small">${esc(pct(r.money_made_pct))}</span></span>
          </div>`).join('') || '<p class="small muted">No holdings yet.</p>'}
        </div>
      </div>
      <div class="card">
        <h2>Needs a look</h2>
        <div class="stack" style="margin-top:10px;gap:8px">${alerts}</div>
      </div>
      <div class="card">
        <h2>Latest transactions</h2>
        <div class="stack" style="margin-top:10px;gap:6px">
          ${(d.recent || []).slice(0, 8).map(t => `<div class="spread small">
            <span class="grow" style="min-width:0"><span style="display:block;overflow:hidden;
              text-overflow:ellipsis;white-space:nowrap">${esc(t.description)}</span>
              <span class="muted tiny">${esc(date(t.posted_on))} · ${esc(t.category || '—')}</span></span>
            <span class="num ${cls(t.amount)}">${esc(signed(t.amount, t.currency))}</span>
          </div>`).join('') || '<p class="small muted">Nothing imported yet.</p>'}
        </div>
      </div>
    </div>`;

  $$('[data-alert]').forEach(b => b.onclick = () => openAlert((d.health || [])[+b.dataset.alert]));
  $('#btn-snap').onclick = async (e) => {
    busy(e.target, true, 'Saving…');
    try { await api('/api/snapshots', { method: 'POST', body: {} }); toast('Snapshot saved', 'ok'); render(); }
    catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
};

function budgetLine(i, c) {
  const state = i.verdict === 'over' ? 'over' : i.verdict === 'close' ? 'close' : 'ok';
  return `<div>
    <div class="spread small"><span>${esc(i.label)}</span>
      <span class="num">${esc(money(i.spent, c, { round: true }))}
        <span class="muted">/ ${esc(money(i.amount, c, { round: true }))}</span></span></div>
    <div class="bar ${state}" style="margin-top:4px"><i style="width:${Math.min(i.used * 100, 100).toFixed(1)}%"></i></div>
  </div>`;
}

// ================================================================ INVESTMENTS
VIEWS.investments = async (host) => {
  const wrapper = S.filters.wrapper || '';
  const d = await api('/api/investments' + (wrapper ? `?wrapper=${wrapper}` : ''));
  S.data.investments = d;
  const c = d.currency, s = d.summary;
  const wrappers = Object.entries(d.wrappers);
  const WNAME = { isa: 'ISA', gia: 'GIA', sipp: 'SIPP', tfsa: 'TFSA', ra: 'RA',
    trading: 'Trading', crypto: 'Crypto', pension: 'Pension', unit_trust: 'Unit trusts' };

  const rows = [...d.rows].sort((a, b) => (b.value || 0) - (a.value || 0));

  host.innerHTML = `
    <div class="grid g4">
      ${stat('Invested now', money(s.invested, c, { round: true }), `${s.positions} position${s.positions === 1 ? '' : 's'}`)}
      ${stat('Cost', money(s.cost, c, { round: true }), 'what you put in')}
      ${stat('Net return', signed(s.net, c), pct(s.cost ? s.net / s.cost : 0) + ' overall · ' + returnPace(s), cls(s.net))}
      ${stat('Dividends', money(s.dividends, c, { round: true }), pct(s.dividend_yield_on_cost) + ' of cost')}
    </div>

    <div class="card" style="margin-top:14px">
      <div class="spread">
        <div class="chip-row">
          <button class="chip ${!wrapper ? 'on' : ''}" data-w="">Everything</button>
          ${wrappers.map(([k]) => `<button class="chip ${wrapper === k ? 'on' : ''}" data-w="${k}">${esc(WNAME[k] || k)}</button>`).join('')}
        </div>
        <div class="row">
          <button class="btn btn-sm" id="btn-prices">Refresh prices</button>
          <button class="btn btn-sm" id="btn-add-holding">Add a holding</button>
        </div>
      </div>

      <div class="table-wrap" style="margin-top:12px">
        <table>
          <thead><tr>
            <th>Symbol</th><th>Name</th><th class="right">Shares</th><th class="right">Cost</th>
            <th class="right">Price</th><th class="right">Value</th><th class="right">Divs</th>
            <th class="right">Made</th><th class="right">%</th><th>Sector</th><th>Where</th><th></th>
          </tr></thead>
          <tbody>${rows.map(r => `<tr data-h="${r.holding_id}" data-i="${r.instrument_id}">
            <td class="sym">${esc(r.symbol)}${r.stale ? ' <span class="pill warn tiny">no price</span>' : ''}</td>
            <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.name || '')}</td>
            <td class="right num nowrap">${r.shares.toLocaleString('en-GB', { maximumFractionDigits: 4 })}</td>
            <td class="right num nowrap">${esc(money(r.cost, c, { dp: 2 }))}</td>
            <td class="right num nowrap">${esc(money(r.price, c))}</td>
            <td class="right num nowrap">${esc(money(r.value, c, { dp: 2 }))}</td>
            <td class="right num nowrap">${esc(money(r.dividends, c, { dp: 2 }))}</td>
            <td class="right num nowrap ${cls(r.money_made)}">${esc(signed(r.money_made, c, { dp: 2 }))}</td>
            <td class="right num nowrap ${cls(r.money_made)}">${esc(pct(r.money_made_pct))}</td>
            <td class="small muted">${esc(r.sector || '')}</td>
            <td class="small muted">${esc(r.account_name || '')}</td>
            <td class="right"><button class="btn btn-sm btn-ghost" data-edit="${r.instrument_id}">edit</button></td>
          </tr>`).join('') || `<tr><td colspan="12" class="empty">
            <h3>No holdings yet</h3><p>Connect a broker or import an activity file and they'll appear here.</p></td></tr>`}
          </tbody>
        </table>
      </div>
    </div>

    <div class="grid g2" style="margin-top:14px">
      <div class="card"><h2>By sector</h2>
        <div style="margin-top:12px">${donut(d.sectors.map(x => ({ label: x.sector, value: x.value })), { ccy: c })}</div></div>
      <div class="card"><h2>By wrapper and platform</h2>
        ${wrappers.length || Object.keys(d.platforms).length ? `
        <table style="margin-top:8px"><thead><tr><th>Where</th><th class="right">Cost</th>
          <th class="right">Now</th><th class="right">Return</th></tr></thead><tbody>
          ${wrappers.map(([k, v]) => tinyRow(WNAME[k] || k, v, c, true)).join('')}
          ${Object.entries(d.platforms).map(([k, v]) => tinyRow(k, v.summary || v, c, false)).join('')}
        </tbody></table>`
        : `<p class="muted small" style="margin-top:8px">Nothing here yet. Once there are
            holdings, this splits them by tax wrapper — ISA, SIPP, TFSA — and by the
            platform they sit on, so you can see how much of the total is sheltered.</p>`}</div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="spread"><h2>Sold</h2>
        <button class="btn btn-sm" id="btn-add-sold">Record a sale</button></div>
      <p class="muted small" style="margin-top:4px">“If held” values those shares at today's price,
        so the last column is what selling cost you — a negative number means selling was right.</p>
      <div class="table-wrap" style="margin-top:10px;max-height:40vh">
        <table><thead><tr><th>Symbol</th><th>Bought</th><th>Sold</th><th class="right">Cost</th>
          <th class="right">Proceeds</th><th class="right">Divs</th><th class="right">Made</th>
          <th class="right">%</th><th class="right">If held</th><th class="right">Missed</th>
          <th></th></tr></thead>
        <tbody>${d.sold.map(x => `<tr>
          <td class="sym">${esc(x.symbol)}</td><td>${esc(date(x.bought_on))}</td><td>${esc(date(x.sold_on))}</td>
          <td class="right num nowrap">${esc(money(x.cost, c, { dp: 2 }))}</td><td class="right num nowrap">${esc(money(x.proceeds, c, { dp: 2 }))}</td>
          <td class="right num nowrap">${esc(money(x.dividends, c, { dp: 2 }))}</td>
          <td class="right num nowrap ${cls(x.total_made)}">${esc(signed(x.total_made, c, { dp: 2 }))}</td>
          <td class="right num nowrap ${cls(x.total_made)}">${esc(pct(x.total_pct))}</td>
          <td class="right num nowrap">${esc(money(x.if_left_in, c, { dp: 2 }))}</td>
          <td class="right num nowrap ${cls(x.missed_out)}">${esc(signed(x.missed_out, c, { dp: 2 }))}</td>
          <td class="right nowrap"><button class="btn btn-sm btn-ghost" data-sold-del="${x.id}"
            title="Remove this sale">remove</button></td></tr>`).join('')
          || '<tr><td colspan="11" class="empty">Nothing sold yet.</td></tr>'}
        </tbody></table>
      </div>
    </div>`;

  $$('.chip[data-w]').forEach(b => b.onclick = () => { S.filters.wrapper = b.dataset.w; render(); });
  $('#btn-prices').onclick = async (e) => {
    busy(e.target, true, 'Fetching…');
    try { const r = await api('/api/prices/refresh', { method: 'POST', body: {} });
      toast(`${r.updated} price${r.updated === 1 ? '' : 's'} updated${r.failed ? `, ${r.failed} not found` : ''}`, r.failed ? '' : 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  $('#btn-add-holding').onclick = addHoldingDialog;
  $('#btn-add-sold').onclick = addSoldDialog;
  $$('[data-sold-del]').forEach(b => b.onclick = async () => {
    if (!confirmed(b, 'really remove?')) return;
    try {
      const r = await api(`/api/investments/sold/${b.dataset.soldDel}`, { method: 'DELETE' });
      toast(r.auto ? 'Removed — it will come back if you import that activity file again'
                   : 'Removed', 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); }
  });
  $$('[data-edit]').forEach(b => b.onclick = () => editInstrument(+b.dataset.edit));
};

// Take them to the thing, not to the screen the thing is on.
function openAlert(issue) {
  const a = issue && issue.action;
  if (!a) return;
  if (a.uncategorised) S.filters.uncat = true;
  if (a.connection) S.filters.importConnection = a.connection;
  go(a.go);
  // The sub-view has to exist before anything in it can be opened, and render() is
  // async, so the dialog waits its turn rather than racing an empty screen.
  if (a.open) setTimeout(() => setupConnection(a.open), 400);
}

function stat(k, v, s, klass = '') {
  return `<div class="card"><div class="stat"><span class="k">${esc(k)}</span>
    <span class="v ${klass}">${esc(v)}</span><span class="s muted">${esc(s)}</span></div></div>`;
}

function tinyRow(name, v, c, bold) {
  return `<tr><td${bold ? ' style="font-weight:600"' : ' class="muted"'}>${esc(name)}</td>
    <td class="right num nowrap">${esc(money(v.cost, c, { round: true }))}</td>
    <td class="right num nowrap">${esc(money(v.invested, c, { round: true }))}</td>
    <td class="right num nowrap ${cls(v.net)}">${esc(pct(v.cost ? v.net / v.cost : 0))}</td></tr>`;
}

async function addHoldingDialog() {
  const accts = (await api('/api/accounts')).items.filter(a => a.is_investment && !a.closed);
  const inst = await api('/api/instruments');
  if (!accts.length) { toast('Add an investment account first', 'err'); return; }
  modal('Add a holding', `
    <div class="stack">
      <label class="field">Account
        <select id="f-acct">${accts.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>
      <div class="grid g2">
        <label class="field">Symbol <span class="hint">e.g. DGE, JNJ, SPXP</span>
          <input type="text" id="f-sym" autocomplete="off"></label>
        <label class="field">Exchange <span class="hint">LON, NYSE, NASDAQ, AMS, JSE…</span>
          <input type="text" id="f-exch" placeholder="LON"></label>
      </div>
      <div class="grid g2">
        <label class="field">Shares<input type="number" step="any" id="f-shares"></label>
        <label class="field">Total cost<input type="number" step="any" id="f-cost"></label>
      </div>
      <label class="field">Sector
        <select id="f-sector">${inst.sectors.map(s => `<option>${esc(s)}</option>`).join('')}</select></label>
      <label class="field">Buy dates <span class="hint">optional, free text — “27/8/21; 15/11/21”</span>
        <input type="text" id="f-dates"></label>
    </div>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="f-save">Add it</button>`);
  $('#f-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/investments/holding', { method: 'POST', body: {
        account_id: +$('#f-acct').value, symbol: $('#f-sym').value.trim(),
        exchange: $('#f-exch').value.trim() || null, shares: +$('#f-shares').value,
        cost: +$('#f-cost').value, sector: $('#f-sector').value,
        buy_dates: $('#f-dates').value || null } });
      closeModal(); toast('Holding added', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

async function editInstrument(iid) {
  const all = await api('/api/instruments');
  const it = all.items.find(x => x.id === iid);
  if (!it) return;
  modal(`${it.symbol}`, `
    <div class="stack">
      <label class="field">Name<input type="text" id="e-name" value="${esc(it.name || '')}"></label>
      <div class="grid g2">
        <label class="field">Exchange<input type="text" id="e-exch" value="${esc(it.exchange || '')}"></label>
        <label class="field">Quote symbol <span class="hint">what the price feed is asked for</span>
          <input type="text" id="e-qs" value="${esc(it.quote_symbol || '')}"></label>
      </div>
      <label class="field">Sector<select id="e-sector">
        ${all.sectors.map(s => `<option ${s === it.sector ? 'selected' : ''}>${esc(s)}</option>`).join('')}
      </select></label>
      <label class="field">Price by hand <span class="hint">leave blank to use the live feed</span>
        <input type="number" step="any" id="e-price" value="${it.manual_price ?? ''}"></label>
    </div>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="e-save">Save</button>`);
  $('#e-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api(`/api/instruments/${iid}`, { method: 'PATCH', body: {
        name: $('#e-name').value, exchange: $('#e-exch').value.toUpperCase() || null,
        quote_symbol: $('#e-qs').value || null, sector: $('#e-sector').value,
        manual_price: $('#e-price').value === '' ? null : +$('#e-price').value } });
      closeModal(); toast('Saved', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

function addSoldDialog() {
  modal('Record a sale', `
    <p class="small muted">Sales found in an imported activity file appear here automatically.
      Use this for anything older than your export goes back.</p>
    <div class="grid g2" style="margin-top:12px">
      <label class="field">Symbol<input type="text" id="s-sym"></label>
      <label class="field">Exchange<input type="text" id="s-exch" placeholder="LON"></label>
      <label class="field">Bought<input type="date" id="s-bd"></label>
      <label class="field">Sold<input type="date" id="s-sd"></label>
      <label class="field">Shares sold<input type="number" step="any" id="s-sh"></label>
      <label class="field">Total cost<input type="number" step="any" id="s-cost"></label>
      <label class="field">Proceeds<input type="number" step="any" id="s-proc"></label>
      <label class="field">Dividends while held<input type="number" step="any" id="s-div" value="0"></label>
    </div>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="sold-save">Save</button>`);
  $('#sold-save').onclick = async (e) => {
    // The server checks all of this too — this is here so the answer comes back before
    // the round trip, pointing at the box that needs filling in.
    const need = [['#s-sym', 'a symbol'], ['#s-sd', 'the date you sold it'],
                  ['#s-cost', 'what it cost you'], ['#s-proc', 'what you got for it']];
    for (const [sel, what] of need) {
      if (!$(sel).value.trim()) { toast(`A sale needs ${what}.`, 'warn'); $(sel).focus(); return; }
    }
    if ($('#s-bd').value && $('#s-bd').value > $('#s-sd').value) {
      toast('That says it was sold before it was bought.', 'warn'); $('#s-bd').focus(); return;
    }
    busy(e.target, true);
    try {
      await api('/api/investments/sold', { method: 'POST', body: {
        symbol: $('#s-sym').value, exchange: $('#s-exch').value || null,
        bought_on: $('#s-bd').value || null, sold_on: $('#s-sd').value || null,
        sell_shares: +$('#s-sh').value || null, buy_shares: +$('#s-sh').value || null,
        cost: +$('#s-cost').value || null, proceeds: +$('#s-proc').value || null,
        dividends: +$('#s-div').value || 0 } });
      closeModal(); toast('Saved', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

// ================================================================ ACCOUNTS
const TYPE_LABEL = {
  current: 'Current account', savings: 'Savings', credit: 'Credit card', isa: 'ISA',
  gia: 'General investment', sipp: 'SIPP', tfsa: 'Tax-free savings (SA)',
  ra: 'Retirement annuity (SA)', pension: 'Pension', crypto: 'Crypto', asset: 'Other asset',
  loan: 'Loan', mortgage: 'Mortgage', unit_trust: 'Unit trust', trading: 'Trading',
  isa_cash: 'Cash ISA', wallet: 'Wallet', joint: 'Joint account', other: 'Other', pot: 'Pot',
};

VIEWS.accounts = async (host) => {
  const [d, morts] = await Promise.all([api('/api/accounts'), api('/api/mortgages')]);
  const c = d.currency;
  const withMortgage = new Set(morts.items.map(x => x.account_id));
  const open = d.items.filter(a => !a.closed);
  // `|| 0` used to stand in for a balance nobody has given yet, so an account list where
  // nothing had been imported added up to a confident "£0" — and one where half the
  // balances were missing added up to a figure that was simply wrong, with nothing on
  // the screen to say so. Unknown is not zero; it is counted and admitted to.
  const counted = open.filter(a => a.include_in_net_worth);
  const known = counted.filter(a => a.balance_base !== null && a.balance_base !== undefined);
  const total = known.reduce((s, a) => s + a.balance_base, 0);
  const missing = counted.length - known.length;

  host.innerHTML = `
    <div class="spread">
      <div class="stat"><span class="k">Across every account</span>
        <span class="v">${esc(known.length ? money(total, c, { round: true }) : '—')}</span>
        <span class="s muted">${!counted.length ? 'no accounts yet'
          : !known.length ? 'no balances on file yet'
          : missing ? `${missing} account${missing === 1 ? ' has' : 's have'} no balance yet,
              so ${missing === 1 ? "it isn't" : "they aren't"} in this`
          : `${known.length} account${known.length === 1 ? '' : 's'} · ${c}`}</span></div>
      <button class="btn btn-primary" id="btn-new-account">Add an account</button>
    </div>
    <div class="card" style="margin-top:14px">
      <div class="table-wrap">
        <table><thead><tr><th>Account</th><th>Institution</th><th>Type</th><th>Who</th>
          <th class="right">Balance</th><th class="right">In ${esc(c)}</th><th>Updates</th>
          <th>Last activity</th><th></th></tr></thead>
        <tbody>${open.map(a => `<tr>
          <td><b>${esc(a.name)}</b>${a.notes ? `<div class="tiny" style="color:var(--warn)">${esc(a.notes)}</div>` : ''}</td>
          <td class="muted small">${esc(a.institution_name || '—')}</td>
          <td class="small">${esc(TYPE_LABEL[a.account_type] || a.account_type)}</td>
          <td class="small muted">${esc(a.member || '')}</td>
          <td class="right num nowrap">${esc(money(a.balance, a.currency, { dp: 2 }))}</td>
          <td class="right num nowrap">${esc(money(a.balance_base, c, { dp: 2 }))}</td>
          <td class="small muted">${esc({ openbanking: 'Open Banking', direct_api: 'API',
            aggregator: 'Aggregator', csv: 'File upload', manual: 'By hand' }[a.method] || '—')}</td>
          <td class="small muted">${a.last_transaction ? esc(date(a.last_transaction)) : '—'}
            ${a.n_transactions ? `<span class="tiny">(${a.n_transactions})</span>` : ''}</td>
          <td class="right nowrap">${(a.account_type === 'mortgage' || a.account_type === 'loan')
              && !withMortgage.has(a.id)
            ? `<button class="btn btn-sm" data-mort-add="${a.id}">set it up</button> ` : ''}
            <button class="btn btn-sm btn-ghost" data-acct="${a.id}">edit</button></td>
        </tr>`).join('') || `<tr><td colspan="9" class="empty"><h3>No accounts yet</h3>
          <p>Head to Connections and pick your bank.</p></td></tr>`}
        </tbody></table>
      </div>
    </div>

    ${morts.items.length ? `<h2 style="margin-top:20px">Mortgage${morts.items.length > 1 ? 's' : ''}</h2>
      <div class="stack" style="margin-top:10px">
        ${morts.items.map(s => mortgageCard(s, c)).join('')}
      </div>`
    : `<div class="card card-flat" style="margin-top:14px">
        <div class="spread">
          <div><b>Got a mortgage?</b>
            <p class="small muted" style="margin-top:4px">Add it and Mittens & Pence works out how much
              of each payment is interest and how much comes off the balance, when your fixed
              rate ends, what an overpayment would save you, and your equity in the house.</p></div>
          <button class="btn btn-primary" id="btn-new-mortgage">Add a mortgage</button>
        </div>
      </div>`}`;

  $('#btn-new-account').onclick = () => go('connections');
  $$('[data-acct]').forEach(b => b.onclick = () => editAccount(d.items.find(a => a.id === +b.dataset.acct)));
  $$('[data-mort-add]').forEach(b => b.onclick = () =>
    mortgageForm(d.items.find(a => a.id === +b.dataset.mortAdd), null));
  $$('[data-mort-open]').forEach(b => b.onclick = () => mortgageDetail(+b.dataset.mortOpen));
  $$('[data-mort-stmt]').forEach(b => b.onclick = () => mortgageEvent(+b.dataset.mortStmt, 'statement'));
  const nm = $('#btn-new-mortgage');
  if (nm) nm.onclick = () => newMortgageAccount(d.items);
};

// Somebody with no mortgage account yet shouldn't have to know that a mortgage is an
// "account" of a particular "type" before they can add one.
function newMortgageAccount(accounts) {
  const existing = accounts.filter(a => !a.closed
    && (a.account_type === 'mortgage' || a.account_type === 'loan'));
  modal('Add a mortgage', `
    ${existing.length ? `<label class="field"><span>Use an account you already have</span>
      <select id="nm-existing"><option value="">— no, make a new one —</option>
        ${existing.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}
      </select></label><div style="height:12px"></div>` : ''}
    <div class="grid g2">
      <label class="field"><span>Call it</span>
        <input type="text" id="nm-name" placeholder="e.g. Santander mortgage"></label>
      <label class="field"><span>Currency</span>
        <select id="nm-ccy">${['GBP', 'ZAR', 'USD', 'EUR'].map(x =>
          `<option ${x === S.currency ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    </div>
    <p class="tiny muted" style="margin-top:12px">Mittens & Pence will ask for the balance, rate and
      payment next. It counts as a debt, so it comes off your net worth.</p>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="nm-go">Next</button>`);
  $('#nm-go').onclick = async (e) => {
    const pick = $('#nm-existing');
    if (pick && pick.value) {
      const a = accounts.find(x => x.id === +pick.value);
      closeModal(); mortgageForm(a, null); return;
    }
    const name = $('#nm-name').value.trim();
    if (!name) { toast('Give it a name', 'warn'); return; }
    busy(e.target, true, 'Creating…');
    try {
      const a = await api('/api/accounts', { method: 'POST', body: {
        name, account_type: 'mortgage', currency: $('#nm-ccy').value } });
      closeModal(); mortgageForm(a, null);
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

async function editAccount(a) {
  const members = (await api('/api/members')).items;
  modal(a.name, `
    <div class="stack">
      <label class="field">Name<input type="text" id="a-name" value="${esc(a.name)}"></label>
      <div class="grid g2">
        <label class="field">Type<select id="a-type">
          ${Object.entries(TYPE_LABEL).map(([k, v]) =>
            `<option value="${k}" ${a.account_type === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}
        </select></label>
        <label class="field">Currency<select id="a-ccy">
          ${['GBP', 'ZAR', 'USD', 'EUR'].map(x => `<option ${a.currency === x ? 'selected' : ''}>${x}</option>`).join('')}
        </select></label>
      </div>
      <div class="grid g2">
        <label class="field">Balance <span class="hint">type it in for anything not synced</span>
          <input type="number" step="any" id="a-bal" value="${a.balance ?? 0}"></label>
        <label class="field">Who<select id="a-member">
          <option value="">—</option>
          ${members.map(m => `<option value="${m.id}" ${a.member_id === m.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}
        </select></label>
      </div>
      <label class="row" style="gap:8px"><input type="checkbox" id="a-nw" ${a.include_in_net_worth ? 'checked' : ''}
        style="width:auto"> <span class="small">Count towards net worth</span></label>
      <label class="row" style="gap:8px"><input type="checkbox" id="a-closed" ${a.closed ? 'checked' : ''}
        style="width:auto"> <span class="small">Closed — hide it</span></label>
    </div>`,
    `<button class="btn btn-danger" id="a-del">Delete</button>
     <span class="grow"></span>
     <button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="a-save">Save</button>`);
  $('#a-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api(`/api/accounts/${a.id}`, { method: 'PATCH', body: {
        name: $('#a-name').value, account_type: $('#a-type').value, currency: $('#a-ccy').value,
        balance: +$('#a-bal').value, member_id: $('#a-member').value ? +$('#a-member').value : null,
        include_in_net_worth: $('#a-nw').checked ? 1 : 0, closed: $('#a-closed').checked ? 1 : 0 } });
      closeModal(); toast('Saved', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  $('#a-del').onclick = async () => {
    if (!confirm(`Delete ${a.name} and everything imported into it?`)) return;
    await api(`/api/accounts/${a.id}`, { method: 'DELETE' });
    closeModal(); toast('Deleted'); render();
  };
}



// A member is a label on an account — whose ISA is whose — not a user account. There is
// no sign-in anywhere in Mittens & Pence. Until now they could only be added, which meant the
// name the app shipped with was the name you were stuck with.
const MEMBER_COLOURS = ['#4f7cff', '#0f9d8e', '#c2410c', '#7b3fc4', '#b3261e',
                        '#4d7c0f', '#9d174d', '#0e7490'];

function memberDialog(m, all) {
  if (!m) return;
  const others = all.filter(x => x.id !== m.id);
  modal(m.name, `
    <label class="field"><span>Name</span>
      <input type="text" id="mb-name" value="${esc(m.name)}" maxlength="60"></label>

    <div class="field field-block" style="margin-top:14px"><span>Colour</span>
      <div class="row wrap" id="mb-colours" style="gap:8px;margin-top:2px">
        ${MEMBER_COLOURS.map(col => `<button type="button" class="swatch ${col === m.colour ? 'on' : ''}"
          data-colour="${col}" style="background:${col}" title="${col}"></button>`).join('')}
      </div></div>

    ${m.is_default ? `<p class="small muted" style="margin-top:14px">This is the main
      person — new accounts and the dashboard default to them.</p>`
    : `<label class="row" style="gap:8px;margin-top:14px">
        <input type="checkbox" id="mb-main" style="width:auto">
        <span class="small">Make this the main person</span></label>`}

    ${(m.accounts || m.budgets) ? `<p class="tiny muted" style="margin-top:14px">
      ${m.accounts ? m.accounts + ' account' + (m.accounts === 1 ? '' : 's') : ''}${
        m.accounts && m.budgets ? ' and ' : ''}${
        m.budgets ? m.budgets + ' budget' + (m.budgets === 1 ? '' : 's') : ''} tagged to them.</p>` : ''}

    ${others.length ? `<label class="field" id="mb-move-wrap" style="margin-top:14px;display:none">
      <span>If you remove them, their accounts go to</span>
      <select id="mb-move">
        ${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}
        <option value="">— nobody —</option>
      </select></label>` : ''}`,

    `${all.length > 1 ? '<button class="btn btn-ghost" id="mb-del" style="color:var(--warn)">Remove</button>' : ''}
     <span class="grow"></span>
     <button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="mb-save">Save</button>`);

  let colour = m.colour;
  $$('#mb-colours .swatch').forEach(b => b.onclick = () => {
    colour = b.dataset.colour;
    $$('#mb-colours .swatch').forEach(x => x.classList.toggle('on', x === b));
  });

  $('#mb-save').onclick = async (e) => {
    busy(e.target, true, 'Saving…');
    try {
      const body = { name: $('#mb-name').value, colour };
      const main = $('#mb-main');
      if (main && main.checked) body.is_default = true;
      await api(`/api/members/${m.id}`, { method: 'PATCH', body });
      closeModal(); S.boot = await api('/api/bootstrap'); toast('Saved', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };

  const del = $('#mb-del');
  if (del) del.onclick = async (e) => {
    const wrap = $('#mb-move-wrap');
    // Show where their things would land before asking again, rather than after.
    if (wrap && wrap.style.display === 'none' && (m.accounts || m.budgets)) {
      wrap.style.display = '';
      e.target.textContent = 'Remove them';
      return;
    }
    if (!confirmed(e.target, `Remove ${m.name}?`)) return;
    busy(e.target, true, 'Removing…');
    try {
      const move = $('#mb-move');
      const qs = move && move.value ? `?move_to=${move.value}` : '';
      await api(`/api/members/${m.id}${qs}`, { method: 'DELETE' });
      closeModal(); S.boot = await api('/api/bootstrap'); toast('Removed', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

// ================================================================ MORTGAGES
// A mortgage added as an ordinary account is a number with a minus sign: it makes net
// worth correct and answers nothing else. The four things worth knowing are the split
// between interest and capital, the date the cheap rate ends, what an overpayment is
// really worth, and the equity — and none of them fall out of a balance.

// The balance falling to zero, forwards. `lineChart` is built for history — its footer
// reads "−£229,955 over 19 months", which for a mortgage means "you paid off £230,000",
// printed in red as though it were a loss. A projection needs its own picture.
function monthYear(iso) {
  const d = new Date(String(iso).slice(0, 10) + 'T00:00:00');
  return Number.isNaN(+d) ? String(iso)
    : d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
}

function runDownChart(curve, { ccy, fixedUntil } = {}) {
  if (!curve || curve.length < 2) {
    return '<p class="small muted">Add a rate and a monthly payment and Mittens & Pence can draw '
         + 'the run-down.</p>';
  }
  const w = 640, h = 130, pad = 6;
  const vals = curve.map(p => p.balance);
  const max = Math.max(...vals, 1);
  const x = i => pad + (i * (w - pad * 2)) / (curve.length - 1);
  const y = v => h - pad - (v / max) * (h - pad * 2 - 10);
  const d = curve.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.balance).toFixed(1)}`).join(' ');
  const dots = curve.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.balance).toFixed(1)}"
    r="2.4" fill="var(--accent)">${tip(date(p.on) + ': ',
      money(p.balance, ccy, { round: true }))}</circle>`).join('');

  // Where the cheap rate runs out, marked on the curve — everything to the right of that
  // line is drawn at a rate you are not yet paying.
  let marker = '';
  if (fixedUntil) {
    const idx = curve.findIndex(p => p.on >= fixedUntil);
    if (idx > 0) {
      const mx = x(idx).toFixed(1);
      marker = `<line x1="${mx}" y1="${pad}" x2="${mx}" y2="${h - pad}" stroke="var(--warn)"
        stroke-width="1.5" stroke-dasharray="3 3" opacity=".8"/>
        <text x="${mx}" y="${h - pad - 4}" text-anchor="${idx < curve.length / 2 ? 'start' : 'end'}"
          dx="${idx < curve.length / 2 ? 4 : -4}"
          style="fill:var(--warn);font-size:10px">rate ends</text>`;
    }
  }
  const last = curve[curve.length - 1];
  return `<svg class="chart" viewBox="0 0 ${w} ${h + 18}" role="img" style="max-height:${h + 40}px">
      <path d="${d} L${x(curve.length - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z"
        fill="var(--accent)" opacity=".10"/>
      <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>${marker}${dots}
      <text x="0" y="${h + 12}">${esc(monthYear(curve[0].on))}</text>
      <text x="${w}" y="${h + 12}" text-anchor="end">${esc(monthYear(last.on))}</text>
    </svg>
    <p class="small muted" style="margin-top:6px">
      ${esc(money(curve[0].balance, ccy, { round: true }))} now, on course to
      ${last.balance <= 1 ? 'clear' : `be down to ${esc(money(last.balance, ccy, { round: true }))}`}
      by ${esc(monthYear(last.on))} — at today's rate and payment.</p>`;
}

function mortgageCard(s, c) {
  const pace = s.paid_off === null || s.paid_off === undefined ? null : s.paid_off;
  const fixDays = s.days_to_rate_change;
  const fixNote = s.fixed_until === null || fixDays === null ? null
    : fixDays < 0 ? { cls: 'warn', text: `Your ${esc(s.rate_type || 'fixed')} rate ended ${Math.abs(fixDays)} days ago` }
    : fixDays < 180 ? { cls: 'warn', text: `${fixDays} days until your rate changes` }
    : { cls: '', text: `Fixed until ${esc(date(s.fixed_until))}` };

  return `<div class="card mort" data-mort="${s.account_id}">
    <div class="spread">
      <div><h2>${esc(s.lender || 'Mortgage')}</h2>
        <p class="small muted">${s.rate ? esc(pct(s.rate / 100, 2)) + ' · ' : ''}${
          esc({ repayment: 'Repayment', interest_only: 'Interest only',
                part_and_part: 'Part and part' }[s.repayment_type] || '')}</p></div>
      ${fixNote ? `<span class="pill ${fixNote.cls}">${fixNote.text}</span>` : ''}
    </div>

    <div class="grid g4" style="margin-top:14px">
      <div class="stat"><span class="k">Still owed</span>
        <span class="v">${esc(money(s.balance === null ? null : -s.balance, s.currency || c, { round: true }))}</span>
        <span class="tiny muted">${s.estimated && s.projected_months
          ? `projected — last confirmed ${esc(date(s.confirmed_on))}`
          : (s.confirmed_on ? `as at ${esc(date(s.confirmed_on))}` : 'no balance yet')}</span></div>
      <div class="stat"><span class="k">Monthly payment</span>
        <span class="v">${esc(money(s.monthly_payment, s.currency || c, { dp: 2 }))}</span>
        <span class="tiny muted">${s.interest_this_month !== null && s.capital_this_month !== null
          ? `${esc(money(s.interest_this_month, s.currency || c, { dp: 0 }))} interest ·
             ${esc(money(s.capital_this_month, s.currency || c, { dp: 0 }))} off the balance`
          : 'interest only'}</span></div>
      <div class="stat"><span class="k">Paid off</span>
        <span class="v">${pace === null ? '—' : esc(pct(pace, 0))}</span>
        <span class="tiny muted">${s.payoff_on ? `clear by ${esc(monthYear(s.payoff_on))}` : 'no end date yet'}</span></div>
      <div class="stat"><span class="k">${s.equity === null ? 'Interest still to pay' : 'Equity'}</span>
        <span class="v">${esc(money(s.equity === null ? s.interest_remaining : s.equity,
                                    s.currency || c, { round: true }))}</span>
        <span class="tiny muted">${s.ltv !== null && s.ltv !== undefined
          ? esc(pct(s.ltv, 0)) + ' loan to value' : 'over the remaining term'}</span></div>
    </div>

    ${pace !== null ? `<div class="mort-bar" style="margin-top:14px">
      <div class="mort-bar-fill" style="width:${Math.max(2, Math.round(pace * 100))}%"></div>
    </div>` : ''}

    ${s.never_clears ? `<p class="small" style="margin-top:12px;color:var(--warn)">
      That payment doesn't cover the interest, so the balance grows rather than shrinks.
      Check the rate and the payment — one of them isn't what Mittens & Pence has on file.</p>` : ''}
    ${s.term_mismatch ? `<p class="small" style="margin-top:12px;color:var(--warn)">
      At this payment it clears in ${esc(monthYear(s.term_mismatch.payoff_on))}, but the term
      ends ${esc(monthYear(s.term_mismatch.term_ends_on))} — ${s.term_mismatch.months} months
      apart. Either the payment or the rate on file isn't right.</p>` : ''}
    ${s.ahead_by_months ? `<p class="small" style="margin-top:12px">
      You're about ${Math.round(s.ahead_by_months / 12 * 10) / 10} years ahead of the
      original term.</p>` : ''}

    <div class="row" style="margin-top:14px">
      <button class="btn btn-sm" data-mort-open="${s.account_id}">Details and what-ifs</button>
      <button class="btn btn-sm btn-ghost" data-mort-stmt="${s.account_id}">Update the balance</button>
      ${s.split_payments && s.capital_this_month ? `<span class="grow"></span>
        <span class="tiny muted">the ${esc(money(s.capital_this_month, s.currency || c, { dp: 0 }))}
          capital counts as saving, not spending</span>` : ''}
    </div>
  </div>`;
}

async function mortgageDetail(accountId) {
  const d = await api(`/api/mortgages/${accountId}`);
  const s = d.summary, c = s.currency || S.currency;
  const curve = d.curve || [];

  const chart = runDownChart(curve, { ccy: c, fixedUntil: s.fixed_until });

  modal(`${d.account.name}`, `
    <div class="grid g2">
      <div class="stat"><span class="k">Still owed</span>
        <span class="v">${esc(money(s.balance, c, { round: true }))}</span>
        <span class="tiny muted">${s.projected_months
          ? `projected ${s.projected_months} month${s.projected_months === 1 ? '' : 's'} on from ${esc(date(s.confirmed_on))}`
          : 'as confirmed'}</span></div>
      <div class="stat"><span class="k">Interest still to pay</span>
        <span class="v">${esc(money(s.interest_remaining, c, { round: true }))}</span>
        <span class="tiny muted">${s.months_left ? `over ${Math.round(s.months_left / 12 * 10) / 10} years` : ''}</span></div>
    </div>

    <h3 style="margin-top:18px">How it runs down</h3>
    <div style="margin-top:8px">${chart}</div>

    <h3 style="margin-top:18px">What an overpayment would do</h3>
    <div class="row" style="margin-top:8px;gap:8px">
      <input type="text" id="mo-over" inputmode="decimal" placeholder="200" style="max-width:120px">
      <select id="mo-kind" style="max-width:190px">
        <option value="monthly">every month</option>
        <option value="once">as a one-off</option>
      </select>
      <button class="btn btn-sm" id="mo-calc">Work it out</button>
    </div>
    <div id="mo-result" style="margin-top:10px"></div>

    ${s.revert_rate ? `<h3 style="margin-top:18px">When the rate changes</h3>
      <div id="mo-rate" class="small muted" style="margin-top:8px">Working it out…</div>` : ''}

    <h3 style="margin-top:18px">History</h3>
    <table style="margin-top:8px"><tbody>
      ${(d.events || []).map(e => `<tr>
        <td class="small nowrap">${esc(date(e.happened_on))}</td>
        <td class="small">${esc({ statement: 'Statement balance', overpayment: 'Overpayment',
          rate_change: 'Rate changed', valuation: 'Property valued' }[e.kind] || e.kind)}
          ${e.note ? `<div class="tiny muted">${esc(e.note)}</div>` : ''}</td>
        <td class="right num small">${e.amount !== null ? esc(money(e.amount, c, { dp: 0 })) : ''}${
          e.rate !== null && e.rate !== undefined ? esc(pct(e.rate / 100, 2)) : ''}</td>
      </tr>`).join('') || '<tr><td class="small muted">Nothing recorded yet.</td></tr>'}
    </tbody></table>`,
    `<button class="btn btn-ghost" id="mo-edit">Edit the details</button>
     <span class="grow"></span>
     <button class="btn" onclick="closeModal()">Close</button>
     <button class="btn btn-primary" id="mo-over-log">Record an overpayment</button>`);

  $('#mo-calc').onclick = async (e) => {
    const amount = parseFloat($('#mo-over').value || '0');
    if (!amount) { toast('Enter an amount first', 'warn'); return; }
    busy(e.target, true, 'Working…');
    try {
      const r = await api(`/api/mortgages/${accountId}/overpayment?amount=${amount}&kind=${$('#mo-kind').value}`);
      busy(e.target, false);
      $('#mo-result').innerHTML = r.available ? `
        <div class="card card-flat" style="padding:12px">
          <p class="small"><b class="pv">${esc(money(r.interest_saved, c, { round: true }))}</b> less interest, and
            <b>${r.months_saved} month${r.months_saved === 1 ? '' : 's'}</b> off the end —
            clear by ${esc(monthYear(r.new_payoff_on))} instead of ${esc(monthYear(r.old_payoff_on))}.</p>
          <p class="tiny muted" style="margin-top:8px">${esc(r.caveat)}</p>
        </div>` : `<p class="small muted">${esc(r.why)}</p>`;
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };

  if (s.revert_rate) {
    api(`/api/mortgages/${accountId}/rate-change?rate=${s.revert_rate}`).then(r => {
      const el = $('#mo-rate');
      if (!el) return;
      el.innerHTML = r.available ? `At ${esc(pct(s.revert_rate / 100, 2))} the payment becomes
        <b class="pv">${esc(money(r.payment_then, c, { dp: 0 }))}</b> a month —
        ${r.difference >= 0 ? 'up' : 'down'} <span class="pv">${esc(money(Math.abs(r.difference), c, { dp: 0 }))}</span>
        from <span class="pv">${esc(money(r.payment_now, c, { dp: 0 }))}</span>.`
      : 'Not enough on file to work that out.';
    }).catch(() => {});
  }

  $('#mo-edit').onclick = () => mortgageForm(d.account, d.record);
  $('#mo-over-log').onclick = () => mortgageEvent(accountId, 'overpayment');
}

function mortgageEvent(accountId, kind) {
  const today = new Date().toISOString().slice(0, 10);
  const titles = { statement: 'Update the balance', overpayment: 'Record an overpayment',
                   rate_change: 'Record a rate change', valuation: 'Record a valuation' };
  const isRate = kind === 'rate_change';
  modal(titles[kind] || 'Add', `
    <div class="grid g2">
      <label class="field"><span>Date</span>
        <input type="date" id="me-date" value="${today}" max="${today}"></label>
      <label class="field"><span>${isRate ? 'New rate (%)' : 'Amount'}</span>
        <input type="text" id="me-amt" inputmode="decimal" placeholder="${isRate ? '5.29' : '0.00'}"></label>
    </div>
    <label class="field" style="margin-top:12px"><span>Note <i class="hint">optional</i></span>
      <input type="text" id="me-note" placeholder="Anything you'll want to remember"></label>
    ${kind === 'statement' ? `<p class="tiny muted" style="margin-top:12px">
      Mortgage statements usually arrive once a year. Between them Mittens & Pence projects the
      balance from this figure and says so, rather than showing a year-old number as
      though it were today's.</p>` : ''}`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="me-save">Save</button>`);
  $('#me-save').onclick = async (e) => {
    const v = $('#me-amt').value.trim();
    if (!v) { toast(isRate ? 'Enter the new rate' : 'Enter an amount', 'warn'); return; }
    busy(e.target, true, 'Saving…');
    try {
      await api(`/api/mortgages/${accountId}/events`, { method: 'POST', body: {
        kind, happened_on: $('#me-date').value,
        amount: isRate ? null : v, rate: isRate ? v : null, note: $('#me-note').value } });
      closeModal(); toast('Saved', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

async function mortgageForm(account, record) {
  const r = record || {};
  const cats = await api('/api/categories');
  const assetAccounts = (await api('/api/accounts')).items.filter(
    a => !a.closed && (a.account_type === 'asset' || a.account_type === 'other'));
  const v = (k, d = '') => (r[k] === null || r[k] === undefined ? d : r[k]);
  modal(`${account.name} — the details`, `
    <p class="small muted">Only the lender and a balance are needed to start. Every extra
      figure unlocks something: a rate and a payment give the interest/capital split and
      the payoff date, a fixed-until date gives the warning before it ends, a property
      value gives your equity.</p>

    <div class="grid g2" style="margin-top:14px">
      <label class="field"><span>Lender</span>
        <input type="text" id="mf-lender" value="${esc(v('lender'))}" placeholder="Santander"></label>
      <label class="field"><span>Kind</span>
        <select id="mf-type">
          ${[['repayment', 'Repayment'], ['interest_only', 'Interest only'],
             ['part_and_part', 'Part and part']].map(([k, l]) =>
            `<option value="${k}" ${v('repayment_type', 'repayment') === k ? 'selected' : ''}>${l}</option>`).join('')}
        </select></label>
    </div>

    <div class="grid g2" style="margin-top:12px">
      <label class="field"><span>Balance owed now <i class="hint">from your latest statement</i></span>
        <input type="text" id="mf-bal" inputmode="decimal" value="${esc(v('statement_balance'))}"></label>
      <label class="field"><span>as at</span>
        <input type="date" id="mf-bal-on" value="${esc(String(v('statement_on')).slice(0, 10))}"></label>
    </div>

    <div class="grid g2" style="margin-top:12px">
      <label class="field"><span>Monthly payment</span>
        <input type="text" id="mf-pay" inputmode="decimal" value="${esc(v('monthly_payment'))}"></label>
      <label class="field"><span>Interest rate (%)</span>
        <input type="text" id="mf-rate" inputmode="decimal" value="${esc(v('rate'))}" placeholder="4.29"></label>
    </div>

    <div class="grid g2" style="margin-top:12px">
      <label class="field"><span>Rate type</span>
        <select id="mf-ratetype">
          ${[['fixed', 'Fixed'], ['variable', 'Variable / SVR'], ['tracker', 'Tracker'],
             ['discount', 'Discount']].map(([k, l]) =>
            `<option value="${k}" ${v('rate_type', 'fixed') === k ? 'selected' : ''}>${l}</option>`).join('')}
        </select></label>
      <label class="field"><span>Fixed until <i class="hint">the date worth knowing</i></span>
        <input type="date" id="mf-fixed" value="${esc(String(v('fixed_until')).slice(0, 10))}"></label>
    </div>

    <div class="grid g2" style="margin-top:12px">
      <label class="field"><span>Rate it reverts to (%) <i class="hint">optional</i></span>
        <input type="text" id="mf-revert" inputmode="decimal" value="${esc(v('revert_rate'))}" placeholder="7.74"></label>
      <label class="field"><span>Property worth <i class="hint">optional — gives you equity</i></span>
        <input type="text" id="mf-value" inputmode="decimal" value="${esc(v('property_value'))}"
          ${r.property_account_id ? 'disabled' : ''}></label>
    </div>

    ${assetAccounts.length ? `<label class="field" style="margin-top:12px">
      <span>…or take it from an account you already have</span>
      <select id="mf-prop"><option value="">— use the figure above —</option>
        ${assetAccounts.map(a => `<option value="${a.id}" ${+v('property_account_id') === a.id ? 'selected' : ''}
          >${esc(a.name)} — ${esc(money(a.balance, a.currency, { round: true }))}</option>`).join('')}
      </select>
      <span class="hint">Then there's one figure for the house, not two that can disagree.</span>
    </label>` : ''}

    <details style="margin-top:14px"><summary class="small">The original loan, if you have it</summary>
      <div class="grid g2" style="margin-top:10px">
        <label class="field"><span>Amount borrowed</span>
          <input type="text" id="mf-orig" inputmode="decimal" value="${esc(v('original_amount'))}"></label>
        <label class="field"><span>Started</span>
          <input type="date" id="mf-start" value="${esc(String(v('started_on')).slice(0, 10))}"></label>
      </div>
      <label class="field" style="margin-top:10px"><span>Term in years</span>
        <input type="text" id="mf-term" inputmode="numeric"
          value="${r.term_months ? Math.round(r.term_months / 12) : ''}" placeholder="25"></label>
      <p class="tiny muted" style="margin-top:8px">These give the "paid off" figure and let
        Mittens & Pence check the payment really does clear it by the end of the term.</p>
    </details>

    <label class="row" style="gap:8px;margin-top:16px"><input type="checkbox" id="mf-split"
      ${v('split_payments', 1) ? 'checked' : ''} style="width:auto">
      <span class="small">Count the capital part as saving, not spending</span></label>
    <p class="tiny muted" style="margin-top:4px">Only the interest really leaves the
      household — the rest moves from your current account into the house, exactly like
      paying into an ISA. With this on, your monthly spending drops by the capital and
      your saving rises by the same amount.</p>
    <label class="field" style="margin-top:10px"><span>Payments show up under</span>
      <select id="mf-cat"><option value="">— pick the category your payment lands in —</option>
        ${Object.entries(cats.tree).map(([parent, list]) =>
          `<optgroup label="${esc(parent)}">${list.map(x =>
            `<option value="${x.id}" ${+v('payment_category_id') === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</optgroup>`).join('')}
      </select></label>`,
    `${record ? '<button class="btn btn-ghost" id="mf-del" style="color:var(--warn)">Remove the details</button>' : ''}
     <span class="grow"></span>
     <button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="mf-save">Save</button>`);

  const prop = $('#mf-prop');
  if (prop) prop.onchange = () => {
    // Two figures for one house is how they end up disagreeing. Pick one.
    $('#mf-value').disabled = !!prop.value;
    if (prop.value) $('#mf-value').value = '';
  };

  $('#mf-save').onclick = async (e) => {
    const years = parseFloat($('#mf-term').value || '0');
    busy(e.target, true, 'Saving…');
    try {
      await api(`/api/mortgages/${account.id}`, { method: 'POST', body: {
        lender: $('#mf-lender').value, repayment_type: $('#mf-type').value,
        statement_balance: $('#mf-bal').value, statement_on: $('#mf-bal-on').value,
        monthly_payment: $('#mf-pay').value, rate: $('#mf-rate').value,
        rate_type: $('#mf-ratetype').value, fixed_until: $('#mf-fixed').value,
        revert_rate: $('#mf-revert').value,
        property_value: ($('#mf-prop') && $('#mf-prop').value) ? '' : $('#mf-value').value,
        property_account_id: $('#mf-prop') ? $('#mf-prop').value : '',
        original_amount: $('#mf-orig').value, started_on: $('#mf-start').value,
        term_months: years ? Math.round(years * 12) : '',
        split_payments: $('#mf-split').checked ? 1 : 0,
        payment_category_id: $('#mf-cat').value } });
      closeModal(); toast('Saved', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  const del = $('#mf-del');
  if (del) del.onclick = async (e) => {
    if (!confirmed(e.target, 'Remove them?')) return;
    await api(`/api/mortgages/${account.id}`, { method: 'DELETE' });
    closeModal(); toast('Removed — the account itself is still there'); render();
  };
}

// ================================================================ TRANSACTIONS
VIEWS.transactions = async (host) => {
  const f = S.filters;
  const qs = new URLSearchParams();
  if (f.period) qs.set('period', f.period);
  if (f.account_id) qs.set('account_id', f.account_id);
  if (f.section) qs.set('section', f.section);
  if (f.q) qs.set('q', f.q);
  if (f.uncat) qs.set('uncategorised', '1');
  qs.set('limit', '400');

  const [d, cats, accts] = await Promise.all([
    api('/api/transactions?' + qs), api('/api/categories'), api('/api/accounts')]);
  const c = d.currency;
  const sections = Object.keys(cats.tree);

  host.innerHTML = `
    <div class="card card-flat" style="padding:12px">
      <div class="row wrap">
        <input type="search" id="t-q" placeholder="Search description or merchant"
          value="${esc(f.q || '')}" style="max-width:280px">
        <select id="t-acct" style="max-width:220px"><option value="">Every account</option>
          ${accts.items.filter(a => !a.closed).map(a =>
            `<option value="${a.id}" ${f.account_id == a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
        </select>
        <select id="t-section" style="max-width:200px"><option value="">Every section</option>
          ${sections.map(s => `<option ${f.section === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
        </select>
        <input type="month" id="t-period" value="${esc(f.period || '')}" style="max-width:160px">
        <label class="row small" style="gap:6px"><input type="checkbox" id="t-uncat"
          ${f.uncat ? 'checked' : ''} style="width:auto"> needs a category</label>
        <span class="grow"></span>
        <span class="muted small">${d.total.toLocaleString('en-GB')} found</span>
        <button class="btn btn-sm" id="t-recat">Re-sort into categories</button>
        <button class="btn btn-sm btn-primary" id="t-add">＋ Add a transaction</button>
      </div>
    </div>

    <div class="card" style="margin-top:12px">
      <div class="table-wrap" style="max-height:70vh">
        <table><thead><tr><th>Date</th><th>Description</th><th>Account</th>
          <th>Category</th><th class="right">Amount</th><th></th></tr></thead>
        <tbody>${d.items.map(t => `<tr>
          <td class="nowrap small">${esc(date(t.posted_on))}</td>
          <td style="max-width:330px">
            <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${esc(t.description)}${t.is_transfer ? ' <span class="pill tiny">transfer</span>' : ''}
              ${t.entry_source === 'manual' ? ' <span class="pill tiny typed">typed in</span>' : ''}
              ${t.split_of ? ' <span class="pill tiny split">part</span>' : ''}</div>
            ${t.split_of ? `<div class="tiny muted">part of ${esc(money(t.split_total, t.currency, { dp: 2 }))}
              — ${esc(t.split_description)}</div>` : ''}</td>
          <td class="small muted">${esc(t.account)}</td>
          <td><button class="btn btn-sm btn-ghost" data-cat="${t.id}">
            ${esc(t.category === 'Uncategorised' ? '＋ choose' : (t.category || '—'))}</button></td>
          <td class="right num nowrap ${cls(t.amount)}">${esc(signed(t.amount, t.currency, { dp: 2 }))}</td>
          <td class="right nowrap">
            <button class="btn btn-sm btn-ghost" data-split="${t.id}"
              title="${t.split_of ? 'Change how this payment is split' : 'Split it across categories'}"
              >${t.split_of ? 'Split…' : 'Split'}</button>
            ${t.entry_source === 'manual'
              ? `<button class="btn btn-sm btn-ghost" data-edit="${t.id}" title="Edit or delete">Edit</button>`
              : ''}</td>
        </tr>`).join('') || `<tr><td colspan="6" class="empty"><h3>Nothing here</h3>
          <p>Import a statement, add one by hand, or widen the filters.</p></td></tr>`}
        </tbody></table>
      </div>
    </div>`;

  const setF = (k, v) => { S.filters[k] = v || undefined; render(); };
  $('#t-acct').onchange = e => setF('account_id', e.target.value);
  $('#t-section').onchange = e => setF('section', e.target.value);
  $('#t-period').onchange = e => setF('period', e.target.value);
  $('#t-uncat').onchange = e => setF('uncat', e.target.checked);
  let timer; $('#t-q').oninput = e => { clearTimeout(timer); timer = setTimeout(() => setF('q', e.target.value), 350); };
  $('#t-recat').onclick = async (e) => {
    busy(e.target, true, 'Sorting…');
    try { const r = await api('/api/recategorise', { method: 'POST', body: {} });
      toast(`${r.categorised} sorted, ${r.transfers_paired} transfers paired`, 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  $$('[data-cat]').forEach(b => b.onclick = () =>
    categoryDialog(+b.dataset.cat, cats, d.items.find(t => t.id === +b.dataset.cat)));
  $('#t-add').onclick = () => transactionDialog(accts.items, cats, null);
  $$('[data-split]').forEach(b => b.onclick = () => splitDialog(+b.dataset.split));
  $$('[data-edit]').forEach(b => b.onclick = () => {
    const t = d.items.find(x => x.id === +b.dataset.edit);
    let note = '';
    try { note = (JSON.parse(t.raw_json || '{}') || {}).note || ''; } catch { /* not ours */ }
    transactionDialog(accts.items, cats, { ...t, note });
  });
};

function categoryDialog(tid, cats, tx) {
  const opts = Object.entries(cats.tree).map(([parent, list]) =>
    `<optgroup label="${esc(parent)}">${list.map(x =>
      `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</optgroup>`).join('');
  modal('Categorise', `
    <p class="small muted">${esc(tx.description)} · ${esc(signed(tx.amount, tx.currency))}</p>
    <label class="field" style="margin-top:12px">Category<select id="c-sel">${opts}</select></label>
    <details class="small muted" style="margin-top:10px">
      <summary style="cursor:pointer">Shopping or Food &amp; Drink?</summary>
      <p style="margin-top:8px">Things you <b>consume</b> against things you <b>buy and
        keep</b> — decided by the shop, not by what was in the basket. One supermarket
        payment is <b>Groceries</b> in full, even if a third of it was a frying pan: your
        bank sends one amount and has no idea what was in the trolley. When that matters,
        close this and use <b>Split</b> instead.</p>
      <p style="margin-top:8px"><b>Household &amp; general</b> is the shop that sells
        everything — Amazon, eBay, John Lewis, Argos, Wilko.</p>
    </details>
    <label class="row" style="gap:8px;margin-top:12px"><input type="checkbox" id="c-rule" checked
      style="width:auto"><span class="small">Remember this — and apply it to everything else that matches</span></label>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="c-save">Save</button>`);
  $('#c-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      const r = await api(`/api/transactions/${tid}/category`, { method: 'POST', body: {
        category_id: +$('#c-sel').value, make_rule: $('#c-rule').checked } });
      closeModal();
      toast(r.backfilled ? `Done — ${r.backfilled} other transactions matched` : 'Saved', 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}


// ---- typing one in by hand ------------------------------------------------
// Cash, a payment the statement hasn't caught up with, an account no bank will export.
// Two things this form does deliberately: it never asks anyone to type a minus sign
// (out/in is what a person actually knows, and a stray minus is how a number gets
// entered backwards), and it guesses the category from the description as you type, so
// "Tesco" lands in Groceries without a second decision.

function transactionDialog(accts, cats, existing) {
  const open = accts.filter(a => !a.closed);
  if (!open.length) {
    modal('Add a transaction',
      `<p class="small">There are no accounts yet. Add one first — a transaction has to
        live somewhere.</p>`,
      `<button class="btn" onclick="closeModal()">Close</button>
       <button class="btn btn-primary" onclick="closeModal();go('accounts')">Go to Accounts</button>`);
    return;
  }
  const ed = existing || null;
  const today = new Date().toISOString().slice(0, 10);
  // Default to the account they're already filtered to, then whichever they use most.
  // Guessing wrong here files a transaction against the wrong account, which is exactly
  // the mistake the multi-file import was taught not to make.
  const preferred = ed ? ed.account_id
    : (+S.filters.account_id || (open.slice().sort((a, b) =>
        (b.n_transactions || 0) - (a.n_transactions || 0))[0] || {}).id);
  const opts = (sel) => Object.entries(cats.tree).map(([parent, list]) =>
    `<optgroup label="${esc(parent)}">${list.map(x =>
      `<option value="${x.id}" ${x.id === sel ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</optgroup>`).join('');
  const dir = ed ? (ed.amount < 0 ? 'out' : 'in') : 'out';

  modal(ed ? 'Edit this transaction' : 'Add a transaction', `
    <div class="grid g2">
      <label class="field">Account
        <select id="m-acct" ${ed ? 'disabled' : ''}>${open.map(a =>
          `<option value="${a.id}" ${a.id === preferred ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
        </select>${ed ? '<span class="hint">a transaction can’t change account — delete it and add it again</span>' : ''}
      </label>
      <label class="field">Date
        <input type="date" id="m-date" value="${esc(ed ? ed.posted_on.slice(0, 10) : today)}" max="${today}"></label>
    </div>

    <label class="field" style="margin-top:12px"><span>What was it</span>
      <input type="text" id="m-desc" placeholder="e.g. Tesco Metro, or Window cleaner"
        value="${esc(ed ? ed.description : '')}" autocomplete="off">
      <span class="hint" id="m-guess"></span></label>

    <div class="grid g2" style="margin-top:12px">
      <label class="field"><span>Amount <i class="hint" id="m-ccy"></i></span>
        <input type="text" id="m-amt" inputmode="decimal" placeholder="0.00"
          value="${ed ? Math.abs(ed.amount).toFixed(2) : ''}"></label>
      <div class="field field-block"><span>Which way</span>
        <div class="seg" id="m-dir">
          <button type="button" class="seg-btn ${dir === 'out' ? 'on' : ''}" data-dir="out">Money out</button>
          <button type="button" class="seg-btn ${dir === 'in' ? 'on' : ''}" data-dir="in">Money in</button>
        </div></div>
    </div>

    <label class="field" style="margin-top:12px">Category
      <select id="m-cat"><option value="">— let Mittens & Pence sort it —</option>${opts(ed ? ed.category_id : null)}</select></label>

    ${ed ? '' : `<label class="row small" style="gap:8px;margin-top:14px">
      <input type="checkbox" id="m-xfer" style="width:auto">
      <span>This was a transfer between my own accounts</span></label>
    <div id="m-xfer-wrap" style="margin-top:8px;display:none">
      <label class="field"><span>Into</span>
        <select id="m-xfer-to">${open.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select>
        <span class="hint">Both halves get written, so the money leaves one account and arrives in the other.</span>
      </label>
      <label class="field" id="m-xfer-amt-wrap" style="margin-top:10px;display:none">
        <span>And this much arrived <i class="hint" id="m-xfer-ccy"></i></span>
        <input type="text" id="m-xfer-amt" inputmode="decimal" placeholder="0.00">
        <span class="hint">The two accounts are in different currencies, and only your bank
          knows the rate it used — so Mittens & Pence asks rather than inventing one.</span>
      </label>
    </div>`}

    <label class="field" style="margin-top:12px"><span>Note <i class="hint">optional</i></span>
      <input type="text" id="m-note" placeholder="Anything you'll want to remember"
        value="${esc(ed && ed.note ? ed.note : '')}"></label>

    ${ed ? '' : `<p class="tiny muted" style="margin-top:14px">If this payment later turns up
      in a statement you import, Mittens & Pence keeps the bank's version and removes this one —
      so it never counts twice.</p>`}`,

    `${ed ? '<button class="btn btn-ghost" id="m-del" style="color:var(--warn)">Delete</button>' : ''}
     <span class="grow"></span>
     <button class="btn" onclick="closeModal()">Cancel</button>
     ${ed ? '' : '<button class="btn" id="m-save-more">Save and add another</button>'}
     <button class="btn btn-primary" id="m-save">${ed ? 'Save changes' : 'Add it'}</button>`);

  const showCcy = () => {
    const a = open.find(x => x.id === +$('#m-acct').value);
    $('#m-ccy').textContent = a ? `in ${a.currency}` : '';
  };
  showCcy();
  $('#m-acct').onchange = () => { showCcy(); showCrossRate(); };

  let direction = dir;
  $$('#m-dir .seg-btn').forEach(b => b.onclick = () => {
    direction = b.dataset.dir;
    $$('#m-dir .seg-btn').forEach(x => x.classList.toggle('on', x === b));
  });

  const XFER_NOTE = 'Transfers between your own accounts never count as spending.';
  const xfer = $('#m-xfer');
  // Only ask for the arriving amount when the two accounts really are in different
  // currencies — an extra box on every transfer would be noise.
  const showCrossRate = () => {
    if (!xfer || !xfer.checked) return;
    const from = open.find(x => x.id === +$('#m-acct').value);
    const to = open.find(x => x.id === +$('#m-xfer-to').value);
    const differ = from && to && from.currency !== to.currency;
    $('#m-xfer-amt-wrap').style.display = differ ? '' : 'none';
    $('#m-xfer-ccy').textContent = differ ? `in ${to.currency}` : '';
  };
  if (xfer) xfer.onchange = () => {
    $('#m-xfer-wrap').style.display = xfer.checked ? '' : 'none';
    $('#m-cat').disabled = xfer.checked;
    $('#m-guess').textContent = xfer.checked ? XFER_NOTE : '';
    showCrossRate();
  };
  if ($('#m-xfer-to')) $('#m-xfer-to').onchange = showCrossRate;

  // Guess the category from what's being typed, but never overrule a choice already made.
  let gt, touched = !!(ed && ed.category_id);
  $('#m-cat').onchange = () => { touched = true; };
  $('#m-desc').oninput = () => {
    clearTimeout(gt);
    gt = setTimeout(async () => {
      // The dialog may have been saved and closed while this was pending.
      const desc = $('#m-desc'), guess = $('#m-guess'), acct = $('#m-acct');
      if (!desc || !guess || !acct) return;
      const text = desc.value.trim();
      // Don't wipe the transfer explanation just because a guess isn't wanted here.
      if (xfer && xfer.checked) { guess.textContent = XFER_NOTE; return; }
      if (!text) { guess.textContent = ''; return; }
      try {
        const r = await api('/api/transactions/guess-category?description='
          + encodeURIComponent(text) + '&account_id=' + acct.value);
        if (!$('#m-guess')) return;
        if (!r.category_id) { $('#m-guess').textContent = ''; return; }
        $('#m-guess').textContent = `looks like ${r.category.parent} › ${r.category.name}`;
        if (!touched && $('#m-cat')) $('#m-cat').value = r.category_id;
      } catch { /* a guess that fails is just no guess */ }
    }, 300);
  };

  const collect = () => ({
    account_id: +$('#m-acct').value,
    posted_on: $('#m-date').value,
    description: $('#m-desc').value,
    amount: $('#m-amt').value,
    direction,
    category_id: (xfer && xfer.checked) ? null : (+$('#m-cat').value || null),
    notes: $('#m-note').value,
    transfer_to_account_id: (xfer && xfer.checked) ? +$('#m-xfer-to').value : null,
    transfer_amount: (xfer && xfer.checked && $('#m-xfer-amt')) ? $('#m-xfer-amt').value : null,
  });

  const save = async (btn, again) => {
    busy(btn, true, 'Saving…');
    try {
      if (ed) {
        await api(`/api/transactions/${ed.id}`, { method: 'PATCH', body: collect() });
        closeModal(); toast('Saved', 'ok'); render();
        return;
      }
      const r = await api('/api/transactions', { method: 'POST', body: collect() });
      toast(r.transfer ? 'Transfer added — both sides' : 'Added', 'ok');
      if (!again) { closeModal(); render(); return; }
      // Keep the account and the date: several entries in a row usually share both.
      $('#m-desc').value = ''; $('#m-amt').value = ''; $('#m-note').value = '';
      $('#m-cat').value = ''; $('#m-guess').textContent = ''; touched = false;
      busy(btn, false);
      $('#m-desc').focus({ preventScroll: true });
    } catch (err) { toast(err.message, 'err'); busy(btn, false); }
  };

  $('#m-save').onclick = (e) => save(e.target, false);
  const more = $('#m-save-more');
  if (more) more.onclick = (e) => save(e.target, true);

  const del = $('#m-del');
  if (del) del.onclick = async (e) => {
    if (!confirmed(e.target, 'Delete it?')) return;
    busy(e.target, true, 'Deleting…');
    try {
      const r = await api(`/api/transactions/${ed.id}`, { method: 'DELETE' });
      closeModal();
      toast(r.deleted.length > 1 ? 'Deleted — both sides of the transfer' : 'Deleted', 'ok');
      render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

// A destructive button that asks once, in place, rather than through a browser confirm()
// — a native dialog blocks the whole window and cannot be styled or undone.
function confirmed(btn, question) {
  if (btn.dataset.armed === '1') return true;
  btn.dataset.armed = '1';
  const was = btn.textContent;
  btn.textContent = question;
  setTimeout(() => { btn.dataset.armed = ''; btn.textContent = was; }, 4000);
  return false;
}

// ================================================================ BUDGETS
VIEWS.budgets = async (host) => {
  const period = S.period || '';
  const d = await api('/api/budgets' + (period ? `?period=${period}` : ''));
  S.period = d.period;
  const c = d.currency, t = d.totals;
  const cats = await api('/api/categories');

  host.innerHTML = `
    <div class="spread">
      <div class="row">
        <select id="b-period" style="max-width:200px">
          ${d.periods.map(p => `<option value="${p}" ${p === d.period ? 'selected' : ''}>${esc(monthName(p))}</option>`).join('')}
        </select>
        ${d.is_current ? `<span class="pill nowrap">${esc(pct(d.pace, 0))} through the month</span>` : ''}
      </div>
      <div class="row">
        ${d.suggestions.length ? `<button class="btn btn-sm" id="b-suggest">Suggest from my spending</button>` : ''}
        <button class="btn btn-primary btn-sm" id="b-new">Set a budget</button>
      </div>
    </div>

    <div class="grid g4" style="margin-top:14px">
      ${/* Not rounded. These four tiles sit directly above the table they summarise, and
            a budget of £42.50 in the table under a tile reading £43 looks like one of the
            two is wrong. money() already drops the pennies above £1,000 on its own, so
            small figures agree with the table and large ones still read cleanly. */''}
      ${stat('Budgeted', money(t.budgeted, c), `${d.items.length} budget${d.items.length === 1 ? '' : 's'}`)}
      ${stat('Spent against them', money(t.spent_in_budgets, c),
        t.budgeted ? pct(t.spent_in_budgets / t.budgeted) + ' used' : '')}
      ${stat('Left', money(t.remaining, c), '', t.remaining < 0 ? 'down' : 'up')}
      ${stat('Outside any budget', money(t.unbudgeted, c),
        t.nested_budgets ? 'section budgets counted once' : 'spending you have not budgeted for')}
    </div>

    ${t.mortgage_capital ? `<p class="small muted" style="margin-top:10px">
      <span class="pv">${esc(money(t.mortgage_capital, c, { dp: 0 }))}</span> of your mortgage payment this month
      came off what you owe rather than leaving the household, so it counts as saving
      rather than spending. Turn that off in the mortgage's details if you'd rather see
      the whole payment as spending.</p>` : ''}

    <div class="card" style="margin-top:14px">
      <div class="table-wrap" style="max-height:56vh">
        <table><thead><tr><th>Budget</th><th class="right">Set</th><th class="right">Spent</th>
          <th class="right">Left</th><th style="width:150px">Progress</th><th class="right">Per day left</th>
          <th></th></tr></thead>
        <tbody>${d.items.map(i => {
          const state = i.verdict === 'over' ? 'over' : i.verdict === 'close' ? 'close' : 'ok';
          return `<tr>
            <td><b>${esc(i.label)}</b>${i.rollover ? ' <span class="pill tiny">rolls over</span>' : ''}
              ${i.capital_part ? `<div class="tiny muted"><span class="pv">${esc(money(i.capital_part, c, { dp: 0 }))}</span>
                of this came off what you owe, rather than being spent</div>` : ''}</td>
            <td class="right num nowrap">${esc(money(i.amount, c, { dp: 2 }))}</td>
            <td class="right num nowrap">${esc(money(i.spent, c, { dp: 2 }))}</td>
            <td class="right num nowrap ${i.remaining < 0 ? 'down' : ''}">${esc(money(i.remaining, c, { dp: 2 }))}</td>
            <td><div class="bar ${state}"><i style="width:${Math.min(i.used * 100, 100).toFixed(1)}%"></i></div>
              <span class="tiny muted">${esc(pct(i.used, 0))}${i.verdict === 'ahead-of-pace' ? ' · running hot' : ''}</span></td>
            <td class="right num nowrap small">${i.daily_allowance != null
              ? esc(money(i.daily_allowance, c)) + ` <span class="tiny muted">${i.days_left}d</span>`
              : '<span class="muted">—</span>'}</td>
            <td class="right"><button class="btn btn-sm btn-ghost" data-bdel="${i.budget_id}">✕</button></td>
          </tr>`; }).join('') || `<tr><td colspan="7" class="empty">
            <h3>No budgets yet</h3><p>Set one per section — Mittens & Pence can propose figures
            from the last three months.</p></td></tr>`}
        </tbody></table>
      </div>
    </div>

    <div class="grid g2" style="margin-top:14px">
      <div class="card"><h2>Income and spending</h2>
        <div style="margin-top:12px">${barPair(d.history, { ccy: c })}</div></div>
      <div class="card"><h2>${esc(monthName(d.period))} by section</h2>
        <div style="margin-top:12px">${donut(Object.entries(d.sections)
          .filter(([k, v]) => v.spend > 0 && !specialSections().includes(k))
          .sort((a, b) => b[1].spend - a[1].spend)
          .map(([k, v]) => ({ label: k, value: v.spend })), { ccy: c })}</div></div>
    </div>`;

  $('#b-period').onchange = e => { S.period = e.target.value; render(); };
  $('#b-new').onclick = () => budgetDialog(cats, c);
  const sg = $('#b-suggest');
  if (sg) sg.onclick = () => suggestDialog(d.suggestions, c);
  $$('[data-bdel]').forEach(b => b.onclick = async () => {
    if (!confirm('Remove this budget?')) return;
    await api(`/api/budgets/${b.dataset.bdel}`, { method: 'DELETE' });
    toast('Removed'); render();
  });
};

function budgetDialog(cats, c) {
  const sections = Object.keys(cats.tree).filter(s =>
    !specialSections().includes(s));
  modal('Set a budget', `
    <div class="stack">
      <label class="field">Section<select id="bd-parent">
        ${sections.map(s => `<option>${esc(s)}</option>`).join('')}</select></label>
      <label class="field">Just one line inside it? <span class="hint">optional — leave on
        “the whole section” to budget the section as a whole</span>
        <select id="bd-cat"><option value="">The whole section</option></select></label>
      <label class="field">Amount each month (${esc(c)})<input type="number" step="any" id="bd-amt"></label>
      <label class="row" style="gap:8px"><input type="checkbox" id="bd-roll" style="width:auto">
        <span class="small">Carry anything unspent into next month</span></label>
      <label class="field">Note <span class="hint">optional</span><input type="text" id="bd-note"></label>
    </div>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="bd-save">Save</button>`);
  const fill = () => {
    const list = cats.tree[$('#bd-parent').value] || [];
    $('#bd-cat').innerHTML = '<option value="">The whole section</option>' +
      list.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
  };
  $('#bd-parent').onchange = fill; fill();
  $('#bd-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/budgets', { method: 'POST', body: {
        amount: +$('#bd-amt').value, parent: $('#bd-cat').value ? null : $('#bd-parent').value,
        category_id: $('#bd-cat').value || null, rollover: $('#bd-roll').checked,
        notes: $('#bd-note').value || null } });
      closeModal(); toast('Budget set', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

function suggestDialog(suggestions, c) {
  modal('Suggested budgets', `
    <p class="small muted">Averages of the last few months, rounded. Nothing is saved until you press Apply.</p>
    <table style="margin-top:12px"><thead><tr><th>Section</th><th class="right">Average</th>
      <th class="right">Range</th><th class="right">Suggested</th></tr></thead><tbody>
      ${suggestions.map(s => `<tr><td>${esc(s.parent)}</td>
        <td class="right num nowrap">${esc(money(s.average, c))}</td>
        <td class="right num nowrap small muted">${Math.round(s.low) === Math.round(s.high)
          ? esc(money(s.low, c, { round: true }))
          : esc(money(s.low, c, { round: true })) + '–' + esc(money(s.high, c, { round: true }))}</td>
        <td class="right num nowrap"><b>${esc(money(s.suggested, c))}</b></td></tr>`).join('')}
    </tbody></table>`,
    `<button class="btn" onclick="closeModal()">Cancel</button>
     <button class="btn btn-primary" id="sg-apply">Apply all</button>`);
  $('#sg-apply').onclick = async (e) => {
    busy(e.target, true);
    try { const r = await api('/api/budgets/apply-suggestions', { method: 'POST', body: {} });
      closeModal(); toast(`${r.created} budgets set`, 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

// ================================================================ CONNECTIONS
const METHOD_LABEL = {
  openbanking: 'Open Banking', direct_api: 'The provider’s own API',
  aggregator: 'Data aggregator', csv: 'Statement upload', manual: 'Typed in by hand',
};
const STATUS_PILL = {
  ready: ['info', 'Ready'], linked: ['up', 'Connected'], new: ['', 'New'],
  needs_credentials: ['warn', 'Needs details'], error: ['down', 'Problem'],
  expired: ['down', 'Consent expired'],
};

VIEWS.connections = async (host) => {
  const d = await api('/api/connections');
  const items = d.items;

  host.innerHTML = `
    <div class="spread">
      <p class="muted small" style="max-width:52ch">Pick an institution and Mittens & Pence tells you what
        it can do with it — link it automatically, or take a statement file. Both work; one is just
        less typing.</p>
      <button class="btn btn-primary" id="c-add">Add an institution</button>
    </div>

    <div class="grid g2" style="margin-top:14px">
      ${items.map(x => {
        const [pk, pl] = STATUS_PILL[x.status] || ['', x.status];
        return `<div class="card">
          <div class="spread">
            <div>
              <h3>${esc(x.label || x.institution_name)}</h3>
              <p class="tiny muted">${esc(x.institution_name)} · ${esc(x.country)} ·
                ${esc(METHOD_LABEL[x.method] || x.method)}${x.member_name ? ' · ' + esc(x.member_name) : ''}</p>
            </div>
            <span class="pill ${pk}">${esc(pl)}</span>
          </div>
          ${x.status_detail ? `<p class="tiny muted" style="margin-top:8px">${esc(x.status_detail)}</p>` : ''}
          ${x.consent_days_left != null && x.consent_days_left >= 0
            ? `<p class="tiny" style="margin-top:6px;color:var(--warn)">Re-approval needed in ${x.consent_days_left} days</p>` : ''}
          <div class="row" style="margin-top:12px;flex-wrap:wrap">
            <span class="tiny muted grow">${x.n_accounts} account${x.n_accounts === 1 ? '' : 's'}
              ${x.last_sync ? '· last ' + esc(date(x.last_sync)) : ''}</span>
            ${x.method === 'csv'
              ? `<button class="btn btn-sm" data-imp="${x.id}">Upload a statement</button>`
              : `<button class="btn btn-sm" data-setup="${x.id}">Set up</button>
                 <button class="btn btn-sm btn-primary" data-sync="${x.id}">Sync</button>`}
            <button class="btn btn-sm btn-ghost" data-del="${x.id}">✕</button>
          </div>
        </div>`; }).join('') || `<div class="card empty" style="grid-column:1/-1">
          <h3>Nothing connected yet</h3>
          <p>Start with your main current account, then add the brokers.</p>
          <button class="btn btn-primary" style="margin-top:14px" onclick="addInstitution()">Add an institution</button>
        </div>`}
    </div>`;

  $('#c-add').onclick = addInstitution;
  $$('[data-setup]').forEach(b => b.onclick = () => setupConnection(+b.dataset.setup));
  $$('[data-imp]').forEach(b => b.onclick = () => { S.filters.importConnection = +b.dataset.imp; go('import'); });
  $$('[data-sync]').forEach(b => b.onclick = async () => {
    busy(b, true, 'Syncing…');
    try { const r = await api(`/api/connections/${b.dataset.sync}/sync`, { method: 'POST', body: {} });
      toast(r.message, r.ok ? 'ok' : 'err'); render();
    } catch (e) { toast(e.message, 'err'); busy(b, false); }
  });
  $$('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Remove this connection? Accounts and history stay unless you delete them too.')) return;
    await api(`/api/connections/${b.dataset.del}?keep=1`, { method: 'DELETE' });
    toast('Removed'); render();
  });
};

// ---- the institution dropdown -------------------------------------------
async function addInstitution() {
  const boot = S.boot;
  modal('Add an institution', `
    <div class="stack">
      <div class="chip-row" id="ai-country">
        ${boot.countries.map((c, i) => `<button class="chip ${i === 0 ? 'on' : ''}"
          data-c="${c.code}">${esc(c.name)} <span class="muted">${c.count}</span></button>`).join('')}
        <button class="chip" data-c="">Both</button>
      </div>
      <div class="picker">
        <input type="search" id="ai-q" autocomplete="off" placeholder="Start typing — Monzo, Capitec, Freetrade, Nationwide…">
        <div class="picker-list" id="ai-list" hidden></div>
      </div>
      <div id="ai-step"></div>
    </div>`, '');

  let country = boot.countries[0].code;
  let chosen = null;

  const search = async () => {
    const q = $('#ai-q').value.trim();
    const fetchFor = async (c) => (await api(`/api/institutions?q=${encodeURIComponent(q)}` +
      (c ? `&country=${c}` : '') + '&limit=40')).items;
    let items = await fetchFor(country);
    let note = '';
    // Typing "Capitec" with the UK filter on shouldn't come back empty — look wider.
    if (!items.length && country && q) {
      items = await fetchFor('');
      if (items.length) {
        const other = S.boot.countries.find(c => c.code === items[0].country);
        note = `Nothing in ${esc(S.boot.countries.find(c => c.code === country).name)} — `
             + `showing ${esc(other ? other.name : 'the other country')} instead.`;
      }
    }
    const list = $('#ai-list');
    if (!items.length) {
      list.innerHTML = `<div class="picker-item"><span class="muted">Nothing matches.
        Pick “Other / not listed” and upload a file — that always works for any institution.</span></div>`;
    } else {
      let lastKind = null;
      list.innerHTML = (note ? `<div class="picker-note">${note}</div>` : '') + items.map(i => {
        const head = i.kind_label !== lastKind
          ? `<div class="picker-group">${esc(i.kind_label)}</div>` : '';
        lastKind = i.kind_label;
        const tag = { direct_api: 'API', openbanking: 'Auto', aggregator: 'Aggregator',
          csv: 'File', manual: 'By hand' }[i.recommended] || '';
        return head + `<div class="picker-item" data-id="${esc(i.id)}">
          <span><span class="nm">${esc(i.name)}</span>
            ${i.aka && i.aka.length ? `<span class="meta"> · ${esc(i.aka.join(', '))}</span>` : ''}
            <div class="meta">${esc(i.group)} · ${esc(i.country)}</div></span>
          <span class="pill ${i.recommended === 'openbanking' || i.recommended === 'direct_api' ? 'up' : ''}">${esc(tag)}</span>
        </div>`;
      }).join('');
    }
    list.hidden = false;
    $$('.picker-item[data-id]', list).forEach(el =>
      el.onclick = () => choose(el.dataset.id, el.querySelector('.nm').textContent));
  };

  const choose = async (id, name) => {
    chosen = id;
    $('#ai-q').value = name;
    $('#ai-list').hidden = true;
    const step = await api(`/api/institutions/${id}/next-step`);
    renderNextStep(step);
  };

  const openSite = (url) => api('/api/open-url', { method: 'POST', body: { url } })
    .catch(() => toast('Could not open that link', 'err'));

  const renderNextStep = (step) => {
    const inst = step.institution;
    const best = step.recommended;
    $('#ai-step').innerHTML = `
      <div class="card card-flat" style="background:var(--panel-2);margin-top:6px">
        <div class="spread">
          <h3>${step.website
            ? `<button class="linkish" data-site="${esc(step.website)}"
                 title="Open ${esc(inst.name)} in your browser">${esc(inst.name)} ↗</button>`
            : esc(inst.name)}</h3>
          <span class="pill">${esc(inst.kind_label)}</span></div>
        <p class="small" style="margin-top:8px">${esc(step.summary)}</p>
        ${step.website ? `<p class="tiny muted" style="margin-top:6px">Click the name to open
          their website and sign in to download your statements.</p>` : ''}
      </div>
      <h3 style="margin-top:14px">What you can do next</h3>
      <div class="steps" style="margin-top:8px">
        ${step.methods.map(mth => `
          <label class="step ${mth.method === best ? 'best' : ''}" style="cursor:pointer">
            <h4><input type="radio" name="ai-method" value="${esc(mth.method)}"
              ${mth.method === best ? 'checked' : ''} style="width:auto">
              ${esc(mth.label)}
              ${mth.method === best ? '<span class="pill info">recommended</span>' : ''}
              ${!mth.available_now ? '<span class="pill warn">not self-serve</span>' : ''}</h4>
            <p class="small muted">${esc(mth.auth || '')}</p>
            ${mth.where ? `<p class="small"><b>Where to get the file:</b> ${esc(mth.where)}</p>` : ''}
            ${mth.note ? `<p class="tiny muted">${esc(mth.note)}</p>` : ''}
            ${mth.refresh ? `<p class="tiny muted">${esc(mth.refresh)}</p>` : ''}
            ${mth.ready_providers && mth.ready_providers.length
              ? `<p class="tiny">Through: <b>${mth.providers_detail.filter(p => p.usable)
                  .map(p => esc(p.name)).join(', ')}</b></p>` : ''}
            ${mth.documented_only && mth.documented_only.length
              ? `<p class="tiny muted">Also possible in principle, but not built into Mittens & Pence yet:
                  ${mth.providers_detail.filter(p => !p.built && p.self_serve && !p.deprecated)
                    .map(p => esc(p.name)).join(', ')}.</p>` : ''}
          </label>`).join('')}
      </div>
      ${(step.withheld || []).length ? `<p class="tiny muted" style="margin-top:10px">
        ${esc(inst.name)} also supports ${esc(step.withheld.map(w => w.label).join(' and '))},
        but reaching it needs a paid provider account, so Mittens &amp; Pence doesn't offer it.</p>` : ''}
      <div class="grid g2" style="margin-top:14px">
        <label class="field">Call it<input type="text" id="ai-label" value="${esc(inst.name)}"></label>
        ${/* The institution's usual products first, then everything else. This used to
              be the institution's list and nothing more, so somebody with a Nationwide
              current account — the most common current account in the country — could
              choose Savings, Cash ISA or Mortgage and nothing else. A catalogue that is
              a little out of date should be a hint about what to pick, never a wall. */''}
        <label class="field">Account type<select id="ai-type">
          ${(() => {
            const usual = inst.account_types && inst.account_types.length
              ? inst.account_types : ['current'];
            const rest = Object.keys(TYPE_LABEL).filter(t => !usual.includes(t));
            const opt = t => `<option value="${t}">${esc(TYPE_LABEL[t] || t)}</option>`;
            return usual.map(opt).join('')
              + `<optgroup label="Anything else">${rest.map(opt).join('')}</optgroup>`;
          })()}
        </select></label>
      </div>
      <div class="grid g2" style="margin-top:10px">
        <label class="field">Whose is it<select id="ai-member">
          ${S.boot.members.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join('')}
        </select></label>
        <label class="field">Currency<select id="ai-ccy">
          ${['GBP', 'ZAR', 'USD', 'EUR'].map(x =>
            `<option ${x === inst.currency ? 'selected' : ''}>${x}</option>`).join('')}
        </select></label>
      </div>`;
    $('#modal-foot').innerHTML = `<button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="ai-go">Add it</button>`;
    $('#ai-go').onclick = () => createIt(step);
    $$('[data-site]').forEach(b => b.onclick = () => openSite(b.dataset.site));
  };

  const createIt = async (step) => {
    const method = ($$('input[name=ai-method]').find(r => r.checked) || {}).value || 'csv';
    const mth = step.methods.find(m => m.method === method) || {};
    // Only ever pick a provider Mittens & Pence has a client for. Choosing one that is merely
    // documented leaves the connection unusable and the setup screen contradicting itself.
    const provider = (mth.ready_providers || [])[0] || null;
    const btn = $('#ai-go'); busy(btn, true, 'Adding…');
    try {
      const conn = await api('/api/connections', { method: 'POST', body: {
        institution_id: chosen, method, provider,
        member_id: +$('#ai-member').value, label: $('#ai-label').value,
        account_type: $('#ai-type').value, currency: $('#ai-ccy').value } });
      closeModal();
      toast(`${conn.institution_name} added`, 'ok');
      // Stay put. Adding three accounts in a row is the normal case, and being thrown
      // to the Import screen after each one means navigating back every time.
      S.filters.importConnection = conn.id;
      go('connections');
      if (method !== 'csv') setTimeout(() => setupConnection(conn.id), 250);
    } catch (e) { toast(e.message, 'err'); busy(btn, false); }
  };

  let t; $('#ai-q').oninput = () => { clearTimeout(t); t = setTimeout(search, 180); };
  $('#ai-q').onfocus = search;
  $$('#ai-country .chip').forEach(b => b.onclick = () => {
    $$('#ai-country .chip').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); country = b.dataset.c; search();
  });
  search();
}
window.addInstitution = addInstitution;

// Provider setup steps are written against the usual port; show the one Mittens & Pence
// actually bound to, because a redirect URI that's off by one silently fails.
function livePort(text) {
  const port = (S.boot && S.boot.port) || 8765;
  return String(text).replace(/127\.0\.0\.1:8765/g, `127.0.0.1:${port}`);
}

// Turn URLs and bare domains in instruction text into real links. They go through the
// server so the system browser opens even when Mittens & Pence is in a native window, where
// target="_blank" does nothing.
const DOMAIN_RE = /\b((?:https?:\/\/)?(?:[a-z0-9-]+\.)+(?:com|org|net|io|co\.uk|money|bank|dev)(?:\/[^\s,)]*)?)/gi;

function linkify(text) {
  const parts = [];
  let last = 0, m;
  const src = String(text);
  DOMAIN_RE.lastIndex = 0;
  while ((m = DOMAIN_RE.exec(src)) !== null) {
    // A redirect URI is shown as a copy box, not a link — clicking it is never useful.
    if (/127\.0\.0\.1|localhost/.test(m[0])) continue;
    parts.push(esc(src.slice(last, m.index)));
    const href = m[0].startsWith('http') ? m[0] : 'https://' + m[0];
    parts.push(`<a href="${esc(href)}" data-ext="${esc(href)}">${esc(m[0])}</a>`);
    last = m.index + m[0].length;
  }
  parts.push(esc(src.slice(last)));
  return parts.join('');
}

function redirectUri() {
  const port = (S.boot && S.boot.port) || 8765;
  return `http://127.0.0.1:${port}/oauth/callback`;
}

function openExternal(url) {
  api('/api/open-url', { method: 'POST', body: { url } })
    .catch(() => window.open(url, '_blank', 'noopener'));
}

document.addEventListener('click', e => {
  const a = e.target.closest('a[data-ext]');
  if (!a) return;
  e.preventDefault();
  openExternal(a.dataset.ext);
});

async function copyText(value, btn) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(value);
    ok = true;
  } catch {
    // Clipboard API needs a secure context, which http://127.0.0.1 sometimes isn't
    // treated as. Fall back to the old select-and-copy trick.
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
  }
  if (btn) {
    const was = btn.textContent;
    btn.textContent = ok ? 'Copied' : 'Select it and press Ctrl+C';
    btn.classList.toggle('btn-primary', ok);
    setTimeout(() => { btn.textContent = was; btn.classList.remove('btn-primary'); }, 1900);
  }
  return ok;
}

// A one-line value with a Copy button — for anything that must be pasted elsewhere
// character-for-character, like a redirect URI.
function copyBox(value, label) {
  const id = 'cp' + Math.random().toString(36).slice(2, 8);
  return `<div class="copybox">
    ${label ? `<div class="copybox-label">${esc(label)}</div>` : ''}
    <div class="copybox-row">
      <code id="${id}">${esc(value)}</code>
      <button class="btn btn-sm" data-copy="${id}">Copy</button>
    </div></div>`;
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-copy]');
  if (!b) return;
  const el = document.getElementById(b.dataset.copy);
  if (el) copyText(el.textContent, b);
});

// ---- provider credentials / linking -------------------------------------
async function setupConnection(cid) {
  const [conn, provs] = await Promise.all([api(`/api/connections/${cid}`), api('/api/providers')]);
  const step = await api(`/api/institutions/${conn.institution_id}/next-step`);
  const mth = step.methods.find(m => m.method === conn.method) || {};
  const choices = mth.ready_providers || [];
  // A provider saved on the connection only counts if there's still code behind it.
  const current = (choices.includes(conn.provider) ? conn.provider : null) || choices[0];
  const p = current ? provs.providers.find(x => x.id === current) : null;
  const pInfo = current ? (provs.catalogue || {})[current] : null;

  if (!p) {
    const csv = step.methods.find(m => m.method === 'csv');
    // Barclays genuinely does support Open Banking — saying "can't be linked" flat out
    // would be a lie. What's true is that every route to it costs money, so name the
    // reason and let him decide, rather than pretending the door isn't there.
    const ob = step.methods.find(m => m.method === 'openbanking');
    const paid = ob ? (ob.paid_providers || []).map(
      id => (ob.providers_detail.find(x => x.id === id) || {}).name).filter(Boolean) : [];
    // "Mittens & Pence can't link IG automatically" would be false — IG's API is free and
    // self-serve, it simply cannot see a share-dealing ISA. Say which it is, rather
    // than implying the institution has nothing. Looks across every method, because
    // the unbuilt API is rarely on the route the connection was saved against.
    const knownApis = [];
    for (const m of step.methods) {
      for (const d of (m.providers_detail || [])) {
        if (d.built || !d.self_serve || d.deprecated) continue;
        if (!(m.documented_only || []).includes(d.id)) continue;
        if (knownApis.some(x => x.id === d.id)) continue;
        knownApis.push(d);
      }
    }
    modal(conn.institution_name, `
      <p class="small">${paid.length
        ? `${esc(conn.institution_name)} supports Open Banking, but every aggregator that can
           reach it charges a business subscription — there is no free tier for a household.
           So a statement file is the practical way in.`
        : knownApis.length
        ? `A statement file is the way in for ${esc(conn.institution_name)} — see below for
           why its own API doesn't help here.`
        : `Mittens & Pence can't link ${esc(conn.institution_name)} automatically, so a statement file
           is the way in.`}
        It reads the columns the first time and remembers them, and re-uploading a file that
        overlaps an earlier one is safe.</p>
      ${csv && csv.where ? `<p class="small" style="margin-top:12px">
        <b>Where to get the file:</b> ${esc(csv.where)}</p>` : ''}
      ${paid.length ? `<p class="tiny muted" style="margin-top:12px">Already pay for
        ${esc(paid.join(' or '))}? <a href="#" id="sc-paid">Switch this connection to it</a>.
        Otherwise ignore this line.</p>` : ''}
      ${knownApis.length ? `<div class="callout warn" style="margin-top:14px">
        <b>${esc(conn.institution_name)} does publish an API</b>
        ${knownApis.map(a => `<p style="margin-top:6px">${esc(a.name)}${a.warning
          ? ` — ${esc(a.warning)}`
          : ` exists and you can sign up for it yourself, but Mittens & Pence doesn't have a client
              for it yet, so it can't be used from here.`}</p>`).join('')}
      </div>` : ''}`,
      `<button class="btn" onclick="closeModal()">Close</button>
       <button class="btn btn-primary" id="sc-imp">Upload a statement</button>`);
    $('#sc-imp').onclick = () => { closeModal(); S.filters.importConnection = cid; go('import'); };
    const paidLink = $('#sc-paid');
    if (paidLink) paidLink.onclick = async e => {
      e.preventDefault();
      try {
        await api(`/api/connections/${cid}`, {
          method: 'PATCH',
          body: { method: 'openbanking', provider: ob.paid_providers[0] },
        });
        closeModal();
        await setupConnection(cid);
      } catch (err) { toast(err.message || String(err), 'bad'); }
    };
    return;
  }

  const heading = p.name.toLowerCase().startsWith(conn.institution_name.toLowerCase())
    ? p.name : `${conn.institution_name} — ${p.name}`;
  modal(heading, `
    ${choices.length > 1 ? `<div class="chooser">
      <div class="chooser-head">How to connect</div>
      <p class="tiny muted" style="margin:2px 0 8px">Both end at the same place. Pick whichever suits you.</p>
      <select id="sc-prov" class="select-loud">${choices.map(x => {
        const pp = provs.providers.find(y => y.id === x); if (!pp) return '';
        const blurb = {
          truelayer: ' — needs a paid plan for real bank data',
          enablebanking: ' — free, but EEA banks only (not the UK)',
          monzo: ' — free, direct from Monzo (recommended)',
          starling: ' — free, direct from Starling (recommended)',
          gocardless: ' — only if you already hold a key',
        }[x] || '';
        return `<option value="${x}" ${x === current ? 'selected' : ''}>${esc(pp.name + blurb)}</option>`;
      }).join('')}</select></div>` : ''}
    ${pInfo && pInfo.warning ? `<div class="callout warn" style="margin-bottom:14px">
      <b>Before you start</b><p style="margin-top:4px">${esc(pInfo.warning)}</p></div>` : ''}
    <div class="card card-flat" style="background:var(--panel-2)">
      <h3>How to get set up</h3>
      <ol class="steps-list">
        ${(p.setup_steps || []).map(s => {
          const t = livePort(s);
          // Only offer the address to a provider that actually redirects. Starling's
          // step 5 says "no redirect URL, no client secret" — matching on the words
          // alone put a copy box under a sentence saying you don't need one.
          const showsUri = p.redirect_flow && !/\bno redirect\b/i.test(t) && (
            /127\.0\.0\.1:\d+\/oauth\/callback/.test(t) || /redirect (url|uri)/i.test(t));
          return `<li>${linkify(t)}${showsUri ? copyBox(redirectUri()) : ''}</li>`;
        }).join('')}
      </ol>
      ${p.redirect_flow && S.boot.port !== 8765 ? `<p class="tiny" style="margin-top:10px;
        color:var(--warn)">Note the port: Mittens & Pence is on <b>${S.boot.port}</b> today because
        8765 was busy. The redirect URI must match exactly, so use
        <b>http://127.0.0.1:${S.boot.port}/oauth/callback</b> — or free up 8765 and restart
        Mittens & Pence so the address stays the same every time.</p>` : ''}
      ${p.docs ? `<p class="tiny" style="margin-top:10px">Docs: <a href="${esc(p.docs)}" target="_blank" rel="noopener">${esc(p.docs)}</a></p>` : ''}
    </div>
    <div class="stack" style="margin-top:14px">
      ${p.fields.map(f => {
        const multiline = /private_key|pem/.test(f.key);
        const val = (conn.credentials || {})[f.key] && !f.secret ? conn.credentials[f.key] : '';
        return `<label class="field">${esc(f.label)}
        ${f.help ? `<span class="hint">${esc(f.help)}</span>` : ''}
        ${multiline
          ? `<textarea id="sc-${esc(f.key)}" rows="6" spellcheck="false"
              style="font-family:var(--mono);font-size:.76rem"
              placeholder="-----BEGIN PRIVATE KEY-----&#10;…&#10;-----END PRIVATE KEY-----"></textarea>`
          : `<input type="${f.secret ? 'password' : 'text'}" id="sc-${esc(f.key)}"
              placeholder="${esc(f.placeholder || '')}" value="${esc(val)}">`}</label>`;
      }).join('')}
    </div>
    <p class="tiny muted" style="margin-top:12px">Stored encrypted on this computer
      (${esc(S.boot.credential_store === 'os-keychain' ? 'key held in your OS keychain'
        : 'key in a protected file — install the “keyring” package to use the OS keychain')}).
      ${p.redirect_flow
        ? `Mittens & Pence never sees your banking password — you log in on ${esc(conn.institution_name)}'s
           own site and it hands Mittens & Pence a read-only token.`
        : `This is a read-only key you created yourself, not your banking password. You can
           revoke it from your ${esc(conn.institution_name)} account settings at any time,
           and Mittens & Pence will simply stop syncing.`}</p>`,
    `<button class="btn" onclick="closeModal()">Close</button>
     <button class="btn" id="sc-save">Save details</button>
     ${p.redirect_flow ? `<button class="btn btn-primary" id="sc-link">Save &amp; link</button>`
       : `<button class="btn btn-primary" id="sc-test">Save &amp; test</button>`}`);

  const collect = () => Object.fromEntries(p.fields.map(f => [f.key, $(`#sc-${f.key}`).value.trim()]));
  // Switching provider genuinely re-points the connection and reopens the right form.
  const sel = $('#sc-prov');
  if (sel) sel.onchange = async () => {
    if (sel.value === conn.provider) return;
    try {
      await api(`/api/connections/${cid}`, { method: 'PATCH', body: { provider: sel.value } });
      closeModal();
      setupConnection(cid);
    } catch (err) { toast(err.message, 'err'); }
  };
  const save = async (btn, label) => {
    busy(btn, true, label);
    const r = await api(`/api/connections/${cid}/credentials`, { method: 'POST', body: collect() });
    busy(btn, false);
    return r;
  };

  if ($('#sc-save')) $('#sc-save').onclick = async (e) => {
    try { const r = await save(e.target, 'Saving…'); toast(r.message, r.ok ? 'ok' : ''); render(); }
    catch (err) { toast(err.message, 'err'); }
  };
  if ($('#sc-test')) $('#sc-test').onclick = async (e) => {
    try { const r = await save(e.target, 'Testing…'); toast(r.message, r.ok ? 'ok' : 'err');
      if (r.ok) { closeModal(); render(); } }
    catch (err) { toast(err.message, 'err'); }
  };
  if ($('#sc-link')) $('#sc-link').onclick = async (e) => {
    try {
      await save(e.target, 'Saving…');
      busy(e.target, true, 'Opening…');
      const r = await api(`/api/connections/${cid}/link`, { method: 'POST', body: {} });
      window.open(r.url, '_blank', 'noopener');
      $('#modal-body').innerHTML = `<div class="empty">
        <h3>Approve it in the window that just opened</h3>
        <p>Log in at ${esc(conn.institution_name)} and allow read-only access.
          Come back here when it says you're connected, then press Sync.</p>
        <p class="tiny muted" style="margin-top:12px">Nothing opened? Paste this into your browser:<br>
          <span style="word-break:break-all">${esc(r.url)}</span></p></div>`;
      busy(e.target, false);
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}

// ================================================================ IMPORT
function boxCounts(box) {
  return `<div class="row small" style="margin-top:12px;gap:16px;color:var(--ink-2)">
      <span><b>${box.waiting_count}</b> waiting</span>
      <span><b>${box.filed}</b> filed</span>
      <span><b>${box.receipts}</b> receipt${box.receipts === 1 ? '' : 's'} kept</span>
    </div>
    ${box.waiting_count ? `<div class="small" style="margin-top:10px;color:var(--ink-2)">
      ${box.waiting.map(esc).join(' · ')}</div>` : ''}`;
}

VIEWS.import = async (host) => {
  const [accts, conns, hist, box] = await Promise.all([
    api('/api/accounts'), api('/api/connections'), api('/api/import/history'),
    api('/api/inbox').catch(() => null)]);
  const open = accts.items.filter(a => !a.closed);

  const folderCard = !box ? '' : `
    <div class="card" style="margin-bottom:14px">
      <div class="spread">
        <div><h2>Your statements folder</h2>
          <p class="small muted" style="margin-top:6px">Drop statements in here — any format,
            and receipts too — and press <b>Sync everything</b>. They go in on their own and
            are filed away as they land. This is the whole monthly job.</p>
          <p class="tiny mono" style="margin-top:8px;word-break:break-all;color:var(--ink-2)">${esc(box.folder)}</p></div>
        <div class="row" style="align-items:flex-start">
          <button class="btn" id="box-open">Open it</button>
          <button class="btn btn-primary" id="box-run">Bring them in now</button>
        </div>
      </div>
      ${/* Refreshed after an import. These used to be drawn once, so the card could say
            "3 waiting" directly above a report saying all three had just gone in. */''}
      <div id="box-counts">${boxCounts(box)}</div>
      <div id="box-out" style="margin-top:12px"></div>
    </div>`;

  host.innerHTML = folderCard + `
    <div class="grid g2">
      <div>
        <div class="dropzone" id="dz">
          <h3>Drop statements here</h3>
          <p class="muted small" style="margin-top:6px">CSV, Excel, OFX/QFX, QIF or PDF —
            as many at once as you like. Mittens & Pence reads the header row, works out which
            column is which, and shows you before anything is saved.</p>
          <p class="muted small" style="margin-top:6px">Already keep a budget in a
            spreadsheet? Drop that in too — Mittens & Pence reads the categories and monthly
            amounts straight out of it.</p>
          <p class="tiny muted" style="margin-top:10px">Or copy rows from your bank's
            website or a spreadsheet and press <b>Ctrl+V</b> right here.</p>
          <p class="tiny muted" style="margin-top:6px">Re-uploading a file that overlaps
            an earlier one is safe — duplicates are ignored.</p>
          <input type="file" id="file" hidden multiple
            accept=".csv,.tsv,.txt,.xlsx,.xlsm,.ofx,.qfx,.qif,.pdf">
        </div>
        <div id="imp-result" style="margin-top:14px"></div>
      </div>
      <div class="card">
        <h2>Where to get each file</h2>
        <div class="stack" style="margin-top:10px;gap:10px">
          ${conns.items.filter(x => x.method === 'csv').map(x => `<div>
            <div class="spread"><b class="small">${esc(x.label || x.institution_name)}</b>
              <button class="btn btn-sm btn-ghost" data-where="${esc(x.institution_id)}">show me</button></div>
            <div class="tiny muted" id="w-${esc(x.institution_id)}"></div>
          </div>`).join('') || '<p class="small muted">No file-based connections yet.</p>'}
        </div>
        <h2 style="margin-top:20px">Recent imports</h2>
        <table style="margin-top:8px"><tbody>
          ${hist.items.slice(0, 8).map(b => `<tr>
            <td class="small">${esc(b.filename || '—')}<div class="tiny muted">${esc(b.account || '')}</div></td>
            <td class="right small num">+${b.rows_added}<div class="tiny muted">${b.rows_skipped} skipped</div></td>
          </tr>`).join('') || '<tr><td class="small muted">Nothing yet.</td></tr>'}
        </tbody></table>
      </div>
    </div>`;

  const boxOpen = $('#box-open');
  if (boxOpen) boxOpen.onclick = () => api('/api/inbox/open', { method: 'POST', body: {} });
  const boxRun = $('#box-run');
  if (boxRun) boxRun.onclick = async (e) => {
    busy(e.target, true, 'Reading…');
    try {
      const r = await api('/api/inbox/import', { method: 'POST', body: {} });
      $('#box-out').innerHTML = inboxReport(r);
      if (r.added) toast(`${r.added} transaction${r.added === 1 ? '' : 's'} added`, 'ok');
      else if (!r.looked_at) toast('The folder is empty');
      refreshReminder();
      try {
        const fresh = await api('/api/inbox');
        if ($('#box-counts')) $('#box-counts').innerHTML = boxCounts(fresh);
      } catch { /* the report above is the real answer; the counts catch up on reload */ }
    } catch (err) { toast(err.message, 'err'); }
    busy(e.target, false);
  };

  const dz = $('#dz'), input = $('#file');
  dz.onclick = () => input.click();

  // Paste rows straight in. Copying a table out of a bank's web page or a spreadsheet
  // is quicker than finding the export button, and the pasted text goes through the
  // same reader a dropped file does — so mapping, de-duplication and the preview all
  // behave identically.
  const onPaste = (e) => {
    if (S.view !== 'import') return;
    if (isEditable(document.activeElement)) return;      // they meant the text box
    const text = (e.clipboardData || window.clipboardData || {}).getData?.('text/plain');
    if (!text || !text.trim()) return;
    const lines = text.trim().split(/\r?\n/);
    if (lines.length < 2) return;                        // one line is not a table
    e.preventDefault();
    const name = `pasted-${new Date().toISOString().slice(0, 10)}.csv`;
    handleFiles([new File([text], name, { type: 'text/csv' })], open);
  };
  document.addEventListener('paste', onPaste);
  S.cleanup = () => document.removeEventListener('paste', onPaste);
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('hot'); };
  dz.ondragleave = () => dz.classList.remove('hot');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('hot');
    const files = [...(e.dataTransfer.files || [])];
    if (files.length) handleFiles(files, open); };
  input.onchange = () => {
    const files = [...(input.files || [])];
    if (files.length) handleFiles(files, open);
    input.value = '';        // so re-picking the same file still fires onchange
  };

  $$('[data-where]').forEach(b => b.onclick = async () => {
    const s = await api(`/api/institutions/${b.dataset.where}/next-step`);
    const csv = s.methods.find(m => m.method === 'csv');
    $(`#w-${b.dataset.where}`).textContent = csv?.where || 'Check your online account’s statements section.';
  });
};


// ---- several files at once ----------------------------------------------
// Statements arrive in handfuls — a month of four accounts — and confirming each one
// separately is the tedious part. Files whose layout Mittens & Pence already recognises go
// straight in; only the ones it hasn't seen before stop and ask.

async function handleFiles(files, accounts) {
  if (files.length === 1) { S.queue = null; return handleFile(files[0], accounts); }

  const q = files.map(f => ({ file: f, state: 'waiting', note: '' }));
  S.queue = q;
  const box = $('#imp-result');

  const draw = (detailHtml = '') => {
    box.innerHTML = `
      <div class="card">
        <div class="spread"><h2>${q.length} files</h2>
          <span class="tiny muted">${q.filter(x => x.state === 'done').length} of ${q.length} done</span></div>
        <div class="qlist" style="margin-top:10px">
          ${q.map(x => `<div class="qrow ${x.state}">
            <span class="qdot"></span>
            <span class="qname">${esc(x.file.name)}</span>
            <span class="grow"></span>
            <span class="tiny muted">${esc(x.note || {
              waiting: 'waiting', reading: 'reading…', asking: 'needs a look',
              done: 'imported', failed: 'couldn\'t read', skipped: 'skipped'
            }[x.state] || '')}</span></div>`).join('')}
        </div>
      </div>
      <div id="q-detail" style="margin-top:12px">${detailHtml}</div>`;
  };
  draw();

  for (const item of q) {
    item.state = 'reading'; draw();
    let an;
    try {
      an = await api(`/api/import/analyse?filename=${encodeURIComponent(item.file.name)}`,
        { method: 'POST', raw: true, body: item.file });
    } catch (e) {
      item.state = 'failed'; item.note = e.message.slice(0, 60); draw();
      continue;
    }

    // Both must hold before a file goes in unattended: a layout Mittens & Pence has been taught,
    // AND a file that names its own account. Skipping the second check once put a
    // Barclays PDF into a Capitec account without asking — tedious to unpick, easy to
    // miss, and the sort of thing that makes someone stop trusting the whole import.
    const g = guessAccount(item.file, accounts, an);
    const known = (an.source === 'remembered' || an.source === 'profile-handler')
                  && !(an.missing || []).length && g.sure;
    if (known) {
      try {
        const acct = g.account;
        const r = await api('/api/import/commit', { method: 'POST', body: {
          token: an.token, account_id: acct.id, kind: an.kind, profile: an.profile,
          mapping: an.mapping, filename: item.file.name, remember: true } });
        const bits = [];
        if (r.added !== undefined) bits.push(`${r.added} added`);
        if (r.skipped) bits.push(`${r.skipped} skipped`);
        item.state = 'done';
        item.note = `${bits.join(', ')} → ${acct.name}`;
      } catch (e) {
        item.state = 'failed'; item.note = e.message.slice(0, 60);
      }
      draw();
      continue;
    }

    // Otherwise stop and ask about this one, then carry on with the rest.
    item.state = 'asking'; draw();
    await new Promise(resolve => {
      showFileDetail(item.file, an, accounts, (res) => {
        item.state = res.ok ? 'done' : 'failed';
        item.note = res.ok ? res.summary : (res.error || '').slice(0, 60);
        draw();
        resolve();
      }, $('#q-detail'));
    });
  }

  const done = q.filter(x => x.state === 'done').length;
  const failed = q.filter(x => x.state === 'failed');
  S.queue = null;
  draw(`<div class="card">
      <h2>${done} of ${q.length} imported</h2>
      ${failed.length ? `<p class="small" style="margin-top:6px;color:var(--warn)">
        ${failed.map(f => esc(f.file.name + ' — ' + f.note)).join('<br>')}</p>` : ''}
      <div class="row" style="margin-top:14px">
        <button class="btn" onclick="go('transactions')">See the transactions</button>
        <button class="btn btn-primary" onclick="go('export')">Build the spreadsheets</button>
      </div></div>`);
  toast(`${done} of ${q.length} files imported`, failed.length ? 'warn' : 'ok');
}

async function handleFile(file, accounts, onDone, target, forceAs) {
  const box = target || $('#imp-result');
  if (!S.queue) box.innerHTML = '<div class="card"><div class="spinner"></div></div>';
  let an;
  try {
    an = await api(`/api/import/analyse?filename=${encodeURIComponent(file.name)}`
        + (forceAs ? `&as=${forceAs}` : ''),
      { method: 'POST', raw: true, body: file });
  } catch (e) {
    box.innerHTML = `<div class="card"><b>Couldn't read that file</b>
      <p class="small muted" style="margin-top:6px">${esc(e.message)}</p></div>`;
    if (onDone) onDone({ ok: false, error: e.message });
    return;
  }
  showFileDetail(file, an, accounts, onDone, box);
}

// Which account does this file most likely belong to? With four statements dropped at
// once, defaulting all of them to the first account guarantees at least three wrong
// answers, and an import into the wrong account is tedious to unpick.
// Words that appear in half the account names and half the filenames, and so identify
// nothing. "bank" alone once matched "Capitec Bank" strongly enough to send a Barclays
// statement there unattended.
const NOISE_WORDS = new Set([
  'bank', 'account', 'accounts', 'statement', 'statements', 'export', 'exports',
  'download', 'downloads', 'transactions', 'transaction', 'history', 'data', 'file',
  'copy', 'final', 'new', 'the', 'and', 'for', 'csv', 'pdf', 'xls', 'xlsx', 'ofx',
  'qif', 'txt', 'current', 'savings', 'card', 'credit', 'debit', 'report',
  'january', 'february', 'march', 'april', 'june', 'july', 'august', 'september',
  'october', 'november', 'december',
]);

function guessAccount(file, accounts, an) {
  // The filename only. The detected profile is a guess about the *format*, and here it
  // was wrong — a Barclays PDF was profiled as "Standard Bank", whose words then voted
  // for the wrong account.
  const words = file.name
    .toLowerCase().replace(/[^a-z0-9]+/g, ' ').split(' ')
    .filter(w => w.length > 2 && !NOISE_WORDS.has(w) && !/^\d+$/.test(w));
  let best = null, bestScore = 0, runnerUp = 0;
  for (const a of accounts) {
    const hay = `${a.name} ${a.institution_name || ''}`.toLowerCase();
    const score = words.reduce((n, w) => n + (hay.includes(w) ? w.length : 0), 0);
    if (score > bestScore) { runnerUp = bestScore; best = a; bestScore = score; }
    else if (score > runnerUp) { runnerUp = score; }
  }
  const fallback = accounts.find(a => a.conn_id === S.filters.importConnection) || accounts[0];
  return {
    account: best || fallback,
    // "Confident" means the filename carried a distinctive word that named this account
    // and no other. A remembered *layout* says nothing about which account a file belongs
    // to — four banks can share one column layout — so this is what decides whether a
    // file may be imported without stopping to ask.
    sure: !!best && bestScore >= 4 && bestScore > runnerUp,
  };
}

function showFileDetail(file, an, accounts, onDone, target) {
  const box = target || $('#imp-result');
  // The server reads a budget workbook rather than refusing it — a budget has no
  // account and no columns to map, so it gets its own screen.
  if (an.kind === 'budget') return showBudgetDetail(file, an, accounts, onDone, box);
  box.closest('.grid')?.classList.remove('one-col');
  const guessed = guessAccount(file, accounts, an).account;
  const fields = an.kind === 'transactions'
    ? ['date', 'description', 'amount', 'debit', 'credit', 'balance', 'currency', 'type', 'reference']
    : an.kind === 'holdings'
      ? ['symbol', 'name', 'isin', 'shares', 'cost', 'price', 'value', 'currency', 'exchange', 'sector']
      : ['date', 'action', 'symbol', 'name', 'isin', 'shares', 'price', 'total', 'currency', 'fees'];

  box.innerHTML = `
    <div class="card">
      <div class="spread">
        <div><h2>${esc(file.name)}</h2>
          <p class="small muted">${an.rows.toLocaleString('en-GB')} rows ·
            ${esc({ profile: 'recognised format', 'profile-handler': 'recognised format',
              remembered: 'you mapped this layout before', auto: 'columns worked out automatically' }[an.source])}
            ${an.profile_label ? ' · ' + esc(an.profile_label) : ''}</p></div>
        <span class="pill ${an.source === 'auto' ? 'warn' : 'up'}">${esc(an.kind)}</span>
      </div>
      ${an.profile_notes ? `<p class="tiny muted" style="margin-top:8px">${esc(an.profile_notes)}</p>` : ''}
      ${an.missing && an.missing.length ? `<p class="small" style="margin-top:10px;color:var(--warn)">
        Mittens & Pence couldn't find: <b>${esc(an.missing.join(', '))}</b> — set them below.</p>` : ''}

      <div class="grid g2" style="margin-top:14px">
        <label class="field">Import into
          <select id="im-acct">${accounts.map(a =>
            `<option value="${a.id}" ${a.id === (guessed || {}).id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
          </select></label>
        <label class="field">Treat this file as
          <select id="im-kind">
            <option value="transactions" ${an.kind === 'transactions' ? 'selected' : ''}>Bank transactions</option>
            <option value="activity" ${an.kind === 'activity' ? 'selected' : ''}>Broker activity (buys, sells, dividends)</option>
            <option value="holdings" ${an.kind === 'holdings' ? 'selected' : ''}>A list of holdings</option>
            ${an.has_budget ? '<option value="budget">A budget (categories and monthly amounts)</option>' : ''}
          </select></label>
      </div>

      ${an.handler ? `<p class="small muted" style="margin-top:12px">
        This is a format Mittens & Pence knows inside out — no column mapping needed.</p>`
      : `<h3 style="margin-top:16px">Which column is which</h3>
        <div class="grid g2" style="margin-top:8px;gap:8px">
          ${fields.map(f => `<label class="field">${esc(f)}
            ${an.confidence && an.confidence[f] ? `<span class="hint">${Math.round(an.confidence[f] * 100)}% sure</span>` : ''}
            <select data-map="${f}"><option value="">— not in this file —</option>
              ${an.columns.map((col, i) => `<option value="${i}"
                ${an.mapping[f] === i ? 'selected' : ''}>${esc(col || '(column ' + (i + 1) + ')')}</option>`).join('')}
            </select></label>`).join('')}
        </div>`}

      <h3 style="margin-top:16px">First few rows</h3>
      <div class="table-wrap" style="margin-top:8px;max-height:220px">
        <table><thead><tr>${an.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${an.sample.map(r => `<tr>${an.columns.map(c =>
          `<td class="small">${esc(String(r[c] ?? '').slice(0, 30))}</td>`).join('')}</tr>`).join('')}
        </tbody></table>
      </div>

      <div class="row" style="margin-top:16px">
        <label class="row small" style="gap:6px"><input type="checkbox" id="im-remember" checked
          style="width:auto"> Remember this layout for next time</label>
        <span class="grow"></span>
        <button class="btn btn-primary" id="im-go">Import ${an.rows.toLocaleString('en-GB')} rows</button>
      </div>
    </div>`;

  box.querySelector('#im-kind').onchange = (e) => {
    if (e.target.value === 'budget') return analyseBudget(file, accounts, box, onDone);
    handleFile(file, accounts, onDone, box);
  };
  box.querySelector('#im-go').onclick = async (e) => {
    const mapping = {};
    box.querySelectorAll('[data-map]').forEach(s => { if (s.value !== '') mapping[s.dataset.map] = +s.value; });
    busy(e.target, true, 'Importing…');
    try {
      const r = await api('/api/import/commit', { method: 'POST', body: {
        token: an.token, account_id: +box.querySelector('#im-acct').value, kind: box.querySelector('#im-kind').value,
        profile: an.profile, mapping, filename: file.name,
        remember: box.querySelector('#im-remember').checked } });
      const bits = [];
      if (r.added !== undefined) bits.push(`${r.added} transactions added`);
      if (r.trades) bits.push(`${r.trades} trades`);
      if (r.dividends) bits.push(`${r.dividends} dividends`);
      if (r.cash) bits.push(`${r.cash} cash entries`);
      if (r.skipped) bits.push(`${r.skipped} skipped`);
      toast(bits.join(', ') || 'Nothing new to add', 'ok');
      if (onDone) { onDone({ ok: true, summary: bits.join(', ') || 'nothing new' }); return; }
      box.innerHTML = `<div class="card"><h2>Done</h2>
        <p class="small" style="margin-top:6px">${esc(bits.join(' · '))}</p>
        ${(r.warnings || []).map(w => `<p class="small" style="color:var(--warn);margin-top:8px">${esc(w)}</p>`).join('')}
        <div class="row" style="margin-top:14px">
          <button class="btn" onclick="go('transactions')">See the transactions</button>
          <button class="btn" onclick="go('investments')">See the investments</button>
          <button class="btn btn-primary" onclick="go('export')">Build the spreadsheets</button>
        </div></div>`;
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
}



// ---- splitting one payment across categories ------------------------------
// £100 at Tesco is often £50 of groceries, £20 of electronics and £30 of liquor. The
// bank sends one line and has no idea. Two rules make this safe:
//   * the original row is never altered — it keeps the bank's figure and its
//     fingerprint, so re-uploading the statement still changes nothing;
//   * the parts must add up to the penny, or spending quietly disappears.
// Nobody types a minus sign here either: the parts inherit the payment's direction.

async function splitDialog(tid) {
  let d;
  try { d = await api(`/api/transactions/${tid}/split`); }
  catch (e) { toast(e.message, 'err'); return; }
  const cats = await api('/api/categories');
  const p = d.parent;
  const c = p.currency || S.currency;
  const sign = (p.amount || 0) < 0 ? -1 : 1;
  const total = Math.abs(p.amount || 0);

  // Work in positive numbers on screen; the sign comes back on the way out.
  // A part Mittens & Pence named itself ("TESCO STORES 3411 (1 of 3)") must come back as an
  // empty field, not as text that looks like something the person typed and now has to
  // maintain. The placeholder says what it will be called.
  const autoNamed = (text, i, n) => text === `${p.description} (${i + 1} of ${n})`;
  let rows = (d.is_split ? d.parts.map((x, i) => ({
                                 amount: Math.abs(x.amount), category_id: x.category_id,
                                 description: autoNamed(x.description, i, d.parts.length)
                                              ? '' : x.description }))
                         : (d.suggested || []).map(x => ({ amount: Math.abs(x.amount),
                                                           category_id: x.category_id, description: '' })));
  if (rows.length < 2) rows.push({ amount: 0, category_id: null, description: '' });

  // A part landing in Income or Transfers is nearly always a mis-click, and it quietly
  // takes that money out of spending altogether. Say so on the row; never block it —
  // a genuinely mixed line does exist.
  const secs = await api('/api/sections');
  const special = new Set(secs.special || []);
  const sectionOf = (cid) => {
    if (!cid) return null;
    for (const [parent, list] of Object.entries(cats.tree)) {
      if (list.some(x => x.id === cid)) return parent;
    }
    return null;
  };
  const opts = (sel) => Object.entries(cats.tree).map(([parent, list]) =>
    `<optgroup label="${esc(parent)}">${list.map(x =>
      `<option value="${x.id}" ${x.id === sel ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</optgroup>`).join('');

  const draw = () => {
    const allocated = rows.reduce((n, r) => n + (+r.amount || 0), 0);
    const left = Math.round((total - allocated) * 100) / 100;
    const balanced = Math.abs(left) < 0.005;

    modal('Split this payment', `
      <div class="spread">
        <div><b>${esc(p.description)}</b>
          <p class="small muted">${esc(date(p.posted_on))} · ${esc(p.account)}</p></div>
        <div class="right"><div class="tiny muted">The payment</div>
          <b class="num">${esc(money(total, c, { dp: 2 }))}</b></div>
      </div>

      <p class="small muted" style="margin-top:10px">The original stays exactly as your
        bank sent it — these parts sit underneath it and carry the categories. Nothing is
        counted twice, and re-importing the statement still changes nothing.</p>

      <div class="table-wrap" style="margin-top:14px;max-height:44vh">
        <table><thead><tr><th class="right" style="width:130px">Amount</th>
          <th>Category</th><th>Call it</th>
          <th style="width:34px"></th></tr></thead>
        <tbody>
          ${rows.map((r, i) => `<tr>
            <td><input class="cell-num" data-samt="${i}" inputmode="decimal"
                  value="${(+r.amount || 0) ? (+r.amount).toFixed(2) : ''}" placeholder="0.00"></td>
            <td><select data-scat="${i}">
                  <option value="">— pick a category —</option>${opts(r.category_id)}</select>
                ${special.has(sectionOf(r.category_id)) ? `<div class="tiny" style="color:var(--warn)">
                  ${esc(sectionOf(r.category_id))} doesn't count as spending</div>` : ''}</td>
            <td><input class="cell-txt" data-sdesc="${i}" value="${esc(r.description || '')}"
                  placeholder="${esc(p.description)}"></td>
            <td>${rows.length > 2 ? `<button class="btn btn-sm btn-ghost" data-srm="${i}"
                  title="Remove this part">✕</button>` : ''}</td>
          </tr>`).join('')}
        </tbody></table>
      </div>

      <div class="row" style="margin-top:10px">
        <button class="btn btn-sm" id="sp-add">＋ Add a part</button>
        <span class="grow"></span>
        <div class="right">
          <div class="tiny muted">${left < -0.005 ? 'Over by' : 'Left to allocate'}</div>
          <b class="num ${balanced ? 'up' : 'down'}">${esc(money(Math.abs(left), c, { dp: 2 }))}</b>
        </div>
      </div>
      ${!balanced && left > 0 ? `<p class="small" style="margin-top:8px">
        <button class="btn btn-sm btn-ghost" id="sp-rest">Put the remaining
          ${esc(money(left, c, { dp: 2 }))} in the last part</button></p>` : ''}`,

      `${d.is_split ? '<button class="btn btn-ghost" id="sp-undo" style="color:var(--warn)">Undo the split</button>' : ''}
       <span class="grow"></span>
       <button class="btn" onclick="closeModal()">Cancel</button>
       <button class="btn btn-primary" id="sp-save" ${balanced ? '' : 'disabled'}>
         ${d.is_split ? 'Save the split' : `Split into ${rows.length}`}</button>`);

    box().querySelectorAll('[data-samt]').forEach(el => el.onchange = () => {
      rows[+el.dataset.samt].amount = Math.abs(parseFloat(el.value) || 0); draw(); });
    box().querySelectorAll('[data-scat]').forEach(el => el.onchange = () => {
      // Redraw: the row's warning about income and transfer categories is rendered by
      // draw(), so without this it could never appear. A select is safe to redraw on
      // change — unlike a text field, the person has finished with it.
      rows[+el.dataset.scat].category_id = +el.value || null; draw(); });
    box().querySelectorAll('[data-sdesc]').forEach(el => el.onchange = () => {
      rows[+el.dataset.sdesc].description = el.value.trim(); });
    box().querySelectorAll('[data-srm]').forEach(el => el.onclick = () => {
      rows.splice(+el.dataset.srm, 1); draw(); });

    $('#sp-add').onclick = () => { rows.push({ amount: 0, category_id: null, description: '' }); draw(); };
    const rest = $('#sp-rest');
    if (rest) rest.onclick = () => {
      rows[rows.length - 1].amount = Math.round(((+rows[rows.length - 1].amount || 0) + left) * 100) / 100;
      draw();
    };

    $('#sp-save').onclick = async (e) => {
      const missing = rows.filter(r => +r.amount > 0 && !r.category_id).length;
      if (missing) { toast('Every part needs a category', 'warn'); return; }
      busy(e.target, true, 'Splitting…');
      try {
        await api(`/api/transactions/${p.id}/split`, { method: 'POST', body: {
          parts: rows.filter(r => +r.amount > 0).map(r => ({
            amount: (+r.amount) * sign, category_id: r.category_id,
            description: r.description })) } });
        closeModal(); toast('Split', 'ok'); render();
      } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
    };

    const undo = $('#sp-undo');
    if (undo) undo.onclick = async (e) => {
      if (!confirmed(e.target, 'Undo it?')) return;
      busy(e.target, true, 'Undoing…');
      try {
        await api(`/api/transactions/${p.id}/split`, { method: 'DELETE' });
        closeModal(); toast('Back to one transaction', 'ok'); render();
      } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
    };
  };

  const box = () => $('#modal-body');
  draw();
}

// ---- somebody's own budget spreadsheet -----------------------------------
// Mum has kept a budget in Excel for years. The valuable part of it is the plan — a
// list of categories with a monthly figure — and that is the one thing Mittens & Pence cannot
// work out from statements. Spending it recomputes; importing her arithmetic alongside
// her statements would double every pound. So this screen reads the plan, shows every
// row it found, and writes nothing until she has looked at it.

async function analyseBudget(file, accounts, box, onDone) {
  box.innerHTML = '<div class="card"><div class="spinner"></div></div>';
  try {
    const an = await api(`/api/import/budget/analyse?filename=${encodeURIComponent(file.name)}`,
      { method: 'POST', raw: true, body: file });
    showBudgetDetail(file, an, accounts, onDone, box);
  } catch (e) {
    box.innerHTML = `<div class="card"><b>Couldn't find a budget in that file</b>
      <p class="small muted" style="margin-top:6px">${esc(e.message)}</p></div>`;
    if (onDone) onDone({ ok: false, error: e.message });
  }
}

function showBudgetDetail(file, an, accounts, onDone, target) {
  const box = target || $('#imp-result');
  box.closest('.grid')?.classList.add('one-col');
  const CCY = ['GBP', 'ZAR', 'EUR', 'USD'];
  let bi = 0;                                   // which block is on screen
  let rows = an.blocks[0].rows.map(r => ({ ...r }));

  const sectionNames = () => {
    const names = Object.keys(an.sections);
    rows.forEach(r => { if (r.section && !names.includes(r.section)) names.push(r.section); });
    return names.sort((a, b) => a.localeCompare(b));
  };

  const draw = () => {
    const b = an.blocks[bi];
    const on = rows.filter(r => r.include);
    const total = on.reduce((n, r) => n + (+r.amount || 0), 0);
    const newSections = [...new Set(on.filter(r => !(r.section in an.sections)).map(r => r.section))];
    const ccy = box.querySelector('#bg-ccy')?.value || b.currency || an.base_currency;

    box.innerHTML = `
      <div class="card">
        <div class="spread">
          <div><h2>${esc(file.name)}</h2>
            <p class="small muted">A budget — ${b.rows.length} categories found on
              <b>${esc(b.sheet)}</b>${b.header ? ' under “' + esc(b.header) + '”' : ''}.</p></div>
          <span class="pill up">budget</span>
        </div>

        <p class="small muted" style="margin-top:10px">Mittens & Pence is taking the
          <b>plan</b> only — what each category is meant to cost per month. Spending comes
          from your statements, so nothing here is double-counted.
          ${b.sign === 'negative' ? 'Your sheet writes these as negatives; they come in as amounts to stay under.' : ''}</p>

        ${b.excluded_totals.length ? `<p class="tiny muted" style="margin-top:8px">
          Left out as totals rather than categories: ${esc(b.excluded_totals.join(', '))}.</p>` : ''}

        <div class="grid g2" style="margin-top:14px">
          ${an.blocks.length > 1 ? `<label class="field">Which list
            <select id="bg-block">${an.blocks.map((x, i) => `<option value="${i}" ${i === bi ? 'selected' : ''}
              >${esc(x.sheet)} — ${esc(x.header || 'row ' + x.first_row)} (${x.rows.length} lines,
              ${money(x.total, x.currency || an.base_currency, { dp: 2 })})</option>`).join('')}
            </select></label>` : ''}
          <label class="field">These figures are in
            <select id="bg-ccy">${CCY.map(c => `<option value="${c}" ${c === ccy ? 'selected' : ''}>${c}</option>`).join('')}
            </select>
            ${b.currency ? `<span class="hint">the sheet formats this column as ${esc(b.currency)}</span>`
                         : '<span class="hint">the sheet doesn’t say — check before importing</span>'}
          </label>
        </div>

        <div class="spread" style="margin-top:18px">
          <h3>What will be set</h3>
          <div class="row">
            <button class="btn btn-sm btn-ghost" id="bg-all">tick all</button>
            <button class="btn btn-sm btn-ghost" id="bg-none">untick all</button>
          </div>
        </div>

        <div class="table-wrap" style="margin-top:8px;max-height:min(60vh,560px)">
          <table><thead><tr>
            <th style="width:34px"></th><th>From the spreadsheet</th><th class="right">Per month</th>
            <th>Section</th><th>Called</th></tr></thead>
          <tbody>
            ${rows.map((r, i) => `<tr class="${r.include ? '' : 'off'}">
              <td><input type="checkbox" data-inc="${i}" ${r.include ? 'checked' : ''} style="width:auto"></td>
              <td class="small">${esc(r.label)}
                ${r.note ? `<div class="tiny muted">${esc(r.note)}</div>` : ''}</td>
              <td class="right num"><input class="cell-num" data-amt="${i}" value="${(+r.amount).toFixed(2)}"></td>
              <td><select data-sec="${i}">
                ${sectionNames().map(s => `<option value="${esc(s)}" ${s === r.section ? 'selected' : ''}>${esc(s)}</option>`).join('')}
                <option value="__new__">+ new section…</option>
              </select>${(r.section in an.sections) ? '' : '<span class="hint">new</span>'}</td>
              <td><input class="cell-txt" data-line="${i}" value="${esc(r.line)}"></td>
            </tr>`).join('')}
          </tbody></table>
        </div>

        ${newSections.length ? `<p class="small" style="margin-top:10px">
          New sections will be created: <b>${esc(newSections.join(', '))}</b>.
          You can rename or merge them later in Settings.</p>` : ''}

        <div class="row" style="margin-top:16px">
          <div><div class="tiny muted">Monthly budget total</div>
            <b class="num">${money(total, ccy, { dp: 2 })}</b></div>
          <span class="grow"></span>
          ${an.also_statement ? `<button class="btn btn-ghost" id="bg-stmt">No — these are transactions</button>` : ''}
          <button class="btn btn-primary" id="bg-go">Set ${on.length} budgets</button>
        </div>
      </div>`;

    const blockSel = box.querySelector('#bg-block');
    if (blockSel) blockSel.onchange = () => {
      bi = +blockSel.value;
      rows = an.blocks[bi].rows.map(r => ({ ...r }));
      draw();
    };
    box.querySelector('#bg-ccy').onchange = draw;
    const stmt = box.querySelector('#bg-stmt');
    if (stmt) stmt.onclick = () => handleFile(file, accounts || [], onDone, box, 'transactions');
    box.querySelector('#bg-all').onclick = () => { rows.forEach(r => r.include = +r.amount > 0 && !r.note); draw(); };
    box.querySelector('#bg-none').onclick = () => { rows.forEach(r => r.include = false); draw(); };

    box.querySelectorAll('[data-inc]').forEach(el => el.onchange = () => {
      rows[+el.dataset.inc].include = el.checked; draw(); });
    box.querySelectorAll('[data-amt]').forEach(el => el.onchange = () => {
      rows[+el.dataset.amt].amount = Math.abs(parseFloat(el.value) || 0); draw(); });
    box.querySelectorAll('[data-line]').forEach(el => el.onchange = () => {
      rows[+el.dataset.line].line = el.value.trim(); });
    box.querySelectorAll('[data-sec]').forEach(el => el.onchange = () => {
      const i = +el.dataset.sec;
      if (el.value === '__new__') {
        const name = (prompt('Name the new section', rows[i].section) || '').trim();
        if (!name) { draw(); return; }
        rows[i].section = name;
      } else { rows[i].section = el.value; }
      draw();
    });

    box.querySelector('#bg-go').onclick = async (e) => {
      const chosen = rows.filter(r => r.include && +r.amount > 0 && r.line);
      if (!chosen.length) { toast('Nothing ticked to import', 'warn'); return; }
      const chosenCcy = box.querySelector('#bg-ccy').value;
      busy(e.target, true, 'Setting budgets…');
      try {
        const r = await api('/api/import/budget/commit', { method: 'POST', body: {
          token: an.token, currency: chosenCcy, rows: chosen } });
        const bits = [`${r.budgets_set} budgets set`];
        if (r.sections_created.length) bits.push(`${r.sections_created.length} new sections`);
        if (r.lines_created.length) bits.push(`${r.lines_created.length} new categories`);
        toast(bits.join(', '), 'ok');
        if (onDone) { onDone({ ok: true, summary: bits.join(', ') }); return; }
        box.innerHTML = `<div class="card"><h2>Budget imported</h2>
          <p class="small" style="margin-top:6px">${esc(bits.join(' · '))} —
            ${money(chosen.reduce((n, x) => n + +x.amount, 0), chosenCcy, { dp: 2 })} a month in total.</p>
          ${r.skipped_zero.length ? `<p class="tiny muted" style="margin-top:8px">
            Left out because they were budgeted at zero: ${esc(r.skipped_zero.join(', '))}.</p>` : ''}
          <p class="small muted" style="margin-top:10px">Next, drop in her bank statements —
            spending will fill in against these categories on its own.</p>
          <div class="row" style="margin-top:14px">
            <button class="btn" onclick="go('budgets')">See the budgets</button>
            <button class="btn btn-primary" onclick="go('import')">Import statements</button>
          </div></div>`;
      } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
    };
  };
  draw();
}

// ================================================================ EXPORT
function builtRows(items) {
  return (items || []).map(f => `<tr><td class="small">${esc(f.name)}</td>
    <td class="right small muted">${(f.size / 1024).toFixed(0)} KB</td>
    <td class="right small muted">${esc(new Date(f.modified * 1000).toLocaleString('en-GB'))}</td>
    <td class="right">${f.kind === 'phone'
      ? `<a class="btn btn-sm btn-ghost" href="/exports/${encodeURIComponent(f.name)}"
           target="_blank" rel="noopener">open</a>` : ''}</td></tr>`)
    .join('') || '<tr><td class="small muted">Nothing built yet.</td></tr>';
}

VIEWS.export = async (host) => {
  const [ex, st, gs] = await Promise.all([
    api('/api/exports'), api('/api/settings'), api('/api/gsheets/status')]);
  host.innerHTML = `
    <div class="grid g2">
      <div class="card">
        <h2>Investments workbook</h2>
        <p class="small muted" style="margin-top:6px">An Overall tab, one per wrapper,
          one per platform, Sold, Sectors and a Monthly history.
          Real formulas, so it still recalculates if you edit a cost by hand.</p>
        <button class="btn btn-primary" style="margin-top:14px" data-ex="investments">Build it</button>
      </div>
      <div class="card">
        <h2>Banking &amp; budgets workbook</h2>
        <p class="small muted" style="margin-top:6px">Accounts, every transaction with its category,
          spending by month, and a budget tracker with a month selector that actually works.</p>
        <button class="btn btn-primary" style="margin-top:14px" data-ex="banking">Build it</button>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="spread">
        <div><h2>A copy for your phone</h2>
          <p class="small muted" style="margin-top:6px">One file with everything inside it —
            the headline figures, this month's budgets, the accounts and what you've spent
            lately, laid out for a small screen. Put it in iCloud, Drive or Dropbox, or email
            it to yourself, and open it on your phone. It needs no internet and sends nothing
            anywhere. It's a snapshot, not a live feed: make a new one whenever you want
            fresher numbers.</p></div>
        <div class="row"><button class="btn btn-primary" data-ex="phone">Make one</button></div>
      </div>
      <div id="ex-phone" style="margin-top:12px"></div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="spread">
        <div><h2>Both, in one go</h2>
          <p class="small muted">Saved to <span class="mono">${esc(ex.folder)}</span></p></div>
        <div class="row">
          <button class="btn" id="ex-open">Open the folder</button>
          <button class="btn btn-primary" data-ex="both">Build both</button>
        </div>
      </div>
      <div id="ex-out" style="margin-top:12px"></div>
      <h3 style="margin-top:18px">Already built</h3>
      ${/* Filled in below and again after every build. Rendering it once from the page
            load meant pressing Build it left this saying "Nothing built yet" under a
            green box announcing the file had been written. */''}
      <table style="margin-top:8px"><tbody id="ex-built">${builtRows(ex.items)}</tbody></table>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="spread"><h2>Google Sheets</h2>
        <span class="pill ${gs.linked ? 'up' : ''}">${gs.linked ? 'Drive connected'
          : (gs.configured ? 'Not linked yet' : 'Not set up')}</span></div>
      <p class="small muted" style="margin-top:6px">The simple way: both workbooks are .xlsx, which
        Google Sheets opens directly — drag the file into Drive and open it with Sheets. Formulas,
        formatting and charts all carry across.</p>
      <p class="small muted" style="margin-top:8px">The automatic way: link your own Google account
        once and Mittens & Pence publishes into Drive itself, replacing the same sheet each time — so
        whoever you've shared it with always sees the current figures, and their link never changes.</p>

      ${gs.linked ? `
        <div class="row" style="margin-top:14px">
          <button class="btn btn-primary" id="gs-pub">Publish both to Google Sheets</button>
          <button class="btn btn-ghost" id="gs-unlink">Disconnect Drive</button>
        </div>
        ${Object.keys(gs.files || {}).length ? `<div class="stack tiny" style="margin-top:12px;gap:4px">
          ${Object.entries(gs.files).map(([k, id]) =>
            `<div><b>${esc(k)}</b> · <a href="https://docs.google.com/spreadsheets/d/${esc(id)}/edit"
              target="_blank" rel="noopener">open in Google Sheets</a></div>`).join('')}
        </div>` : ''}
        <div id="gs-out" style="margin-top:12px"></div>`
      : `
        <details style="margin-top:14px">
          <summary class="small" style="cursor:pointer">Set up automatic publishing (about five minutes, once)</summary>
          <ol class="small" style="margin:10px 0 0;padding-left:20px;color:var(--ink-2)">
            <li style="margin:4px 0">Go to <b>console.cloud.google.com</b> and create a project — any name.</li>
            <li style="margin:4px 0">APIs &amp; Services → Library → enable <b>Google Drive API</b>.</li>
            <li style="margin:4px 0">OAuth consent screen → External → add your own email as a test user.</li>
            <li style="margin:4px 0">Credentials → Create credentials → <b>OAuth client ID</b> → Desktop app.</li>
            <li style="margin:4px 0">Paste the two values here.</li>
          </ol>
          <div class="grid g2" style="margin-top:12px">
            <label class="field">Client ID<input type="text" id="gs-id"
              value="${esc(gs.client_id || '')}"></label>
            <label class="field">Client secret<input type="password" id="gs-secret"></label>
          </div>
          <div class="row" style="margin-top:12px">
            <button class="btn" id="gs-save">Save</button>
            <button class="btn btn-primary" id="gs-link" ${gs.configured ? '' : 'disabled'}>Connect Drive</button>
          </div>
          <p class="tiny muted" style="margin-top:10px">Mittens & Pence asks for the narrowest Drive scope
            there is — it can only see and change the files it created itself, never the rest of
            your Drive.</p>
        </details>`}
    </div>`;

  $$('[data-ex]').forEach(b => b.onclick = async () => {
    busy(b, true, 'Building…');
    try {
      const r = await api('/api/export', { method: 'POST', body: { what: b.dataset.ex } });
      const phone = b.dataset.ex === 'phone';
      $(phone ? '#ex-phone' : '#ex-out').innerHTML =
        `<div class="card card-flat" style="background:var(--up-soft)">
        <b>Built.</b> <span class="small">${r.names.map(esc).join(' · ')}</span>
        <div class="tiny muted" style="margin-top:4px">${esc(r.folder)}</div>
        ${phone ? `<div class="row" style="margin-top:10px">
          <button class="btn btn-sm" id="ph-open">Open the folder</button>
          <a class="btn btn-sm" href="/exports/${encodeURIComponent(r.names[0])}"
             target="_blank" rel="noopener">Have a look</a></div>` : ''}</div>`;
      if (phone) $('#ph-open').onclick = () => api('/api/open-folder',
        { method: 'POST', body: {} });
      toast(phone ? 'Phone copy ready' : 'Workbook ready', 'ok');
      busy(b, false);
      try {
        const fresh = await api('/api/exports');
        if ($('#ex-built')) $('#ex-built').innerHTML = builtRows(fresh.items);
      } catch { /* the file is written either way; the list catches up on reload */ }
    } catch (e) { toast(e.message, 'err'); busy(b, false); }
  });
  $('#ex-open').onclick = async () => {
    const r = await api('/api/open-folder', { method: 'POST', body: {} });
    if (!r.ok) toast('Open this folder yourself: ' + r.path, '');
  };

  const save = $('#gs-save');
  if (save) save.onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/gsheets/client', { method: 'POST', body: {
        client_id: $('#gs-id').value, client_secret: $('#gs-secret').value } });
      toast('Saved — now press Connect Drive', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  const link = $('#gs-link');
  if (link) link.onclick = async (e) => {
    busy(e.target, true, 'Opening…');
    try { const r = await api('/api/gsheets/link', { method: 'POST', body: {} });
      window.open(r.url, '_blank', 'noopener');
      toast('Approve it in the window that opened, then come back');
      busy(e.target, false);
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  const pub = $('#gs-pub');
  if (pub) pub.onclick = async (e) => {
    busy(e.target, true, 'Publishing…');
    try {
      const r = await api('/api/gsheets/publish', { method: 'POST', body: { what: 'both' } });
      $('#gs-out').innerHTML = r.sheets.map(x => `<div class="card card-flat"
        style="background:var(--up-soft);margin-bottom:6px"><b>${esc(x.name)}</b>
        ${x.replaced ? '<span class="tiny muted"> — same sheet, refreshed</span>' : ''}
        <div class="tiny" style="margin-top:4px"><a href="${esc(x.url)}" target="_blank"
          rel="noopener">${esc(x.url)}</a></div></div>`).join('');
      toast('Published to Google Sheets', 'ok'); busy(e.target, false);
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  const un = $('#gs-unlink');
  if (un) un.onclick = async () => {
    if (!confirm('Disconnect Drive? The sheets already in Drive stay where they are.')) return;
    await api('/api/gsheets/unlink', { method: 'POST', body: {} });
    toast('Disconnected'); render();
  };
};

// ================================================================ SETTINGS
VIEWS.settings = async (host) => {
  const [st, members, conns] = await Promise.all([
    api('/api/settings'), api('/api/members'), api('/api/connections')]);
  const s = st.settings;
  host.innerHTML = `
    <div class="grid g2">
      <div class="card">
        <h2>The basics</h2>
        <div class="stack" style="margin-top:12px">
          <label class="field">Base currency <span class="hint">everything is converted to this</span>
            <select id="s-ccy">${st.currencies.map(c =>
              `<option ${c === s.base_currency ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
          <label class="field">Investing since <span class="hint">used for the “per year” figures</span>
            <input type="date" id="s-start" value="${esc(s.portfolio_start_date)}"></label>
          <label class="field">ISA allowance<input type="number" id="s-isa" value="${s.isa_allowance}"></label>
          <label class="field">Take a monthly snapshot on day
            <input type="number" min="1" max="28" id="s-snap" value="${s.monthly_snapshot_day}"></label>
          <button class="btn btn-primary" id="s-save">Save</button>
        </div>
      </div>

      <div class="card">
        <h2>Who's in the household</h2>
        <p class="small muted" style="margin-top:4px">Names for tagging accounts — whose ISA
          is whose, whose salary that is — so you can filter by person and set budgets per
          person. It isn't a login; Mittens & Pence has no accounts and no sign-in.</p>
        ${members.unnamed ? `<p class="small" style="margin-top:10px;color:var(--warn)">
          The main person is still called “Me”. Give yourself a name and the accounts and
          filters read properly.</p>` : ''}
        <div class="stack" style="margin-top:12px;gap:8px">
          ${members.items.map(m => `<div class="spread">
            <span><span class="pill" style="background:${esc(m.colour)};color:#fff;border:0">&nbsp;</span>
              ${esc(m.name)}
              ${m.is_default ? '<span class="tiny muted">main</span>' : ''}</span>
            <span class="row" style="gap:6px">
              <span class="tiny muted">${m.accounts ? m.accounts + ' account' + (m.accounts === 1 ? '' : 's') : ''}</span>
              <button class="btn btn-sm btn-ghost" data-member="${m.id}">edit</button>
            </span></div>`).join('')}
          <div class="row" style="margin-top:6px">
            <input type="text" id="s-newmember" placeholder="Add someone">
            <button class="btn" id="s-addmember">Add</button>
          </div>
        </div>

        <h2 style="margin-top:20px">Where things live</h2>
        <dl class="kv" style="margin-top:8px">
          <dt>Data</dt><dd class="tiny" style="word-break:break-all">${esc(st.data_dir)}</dd>
          <dt>Spreadsheets</dt><dd class="tiny" style="word-break:break-all">${esc(st.exports_dir)}</dd>
          <dt>Credentials</dt><dd class="tiny">${esc(st.credential_store === 'os-keychain'
            ? 'Encrypted, key in your OS keychain' : 'Encrypted, key in a protected file')}</dd>
        </dl>
        <div class="row" style="margin-top:12px">
          <button class="btn btn-sm" id="s-backup">Back up now</button>
          <button class="btn btn-sm" id="s-openfolder">Open the folder</button>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>Budget sections</h2>
      <p class="small muted" style="margin-top:6px">The buckets your spending is sorted
        into. Renaming one keeps every transaction and budget attached to it. Removing one
        asks where its transactions should go first.</p>
      <div id="sec-list" style="margin-top:12px"><div class="spinner"></div></div>
      <div class="row" style="margin-top:12px">
        <input id="sec-new" placeholder="New section, e.g. Charity" style="max-width:260px">
        <button class="btn" id="sec-add">Add section</button>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>Something wrong, or an idea?</h2>
      <p class="small muted" style="margin-top:6px">The Help button in the bottom corner
        opens your email with a few harmless details already filled in — the version, which
        screen you were on, nothing about your money. Or write to
        <b>${esc(HELP_TO)}</b> directly.</p>
      <div class="row" style="margin-top:12px">
        <button class="btn" id="s-help">Send an email</button>
        <button class="btn btn-ghost" id="s-copyhelp">Copy the address</button>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>Show me round again</h2>
      <p class="small muted" style="margin-top:6px">The short tour of what each tab is for.
        Handy when someone else in the house opens Mittens & Pence for the first time.</p>
      <div class="row" style="margin-top:12px">
        <button class="btn" id="s-tour">Replay the tour</button>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>Statements folder and the monthly reminder</h2>
      <div id="rem-box" style="margin-top:8px"><div class="spinner"></div></div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>Updates</h2>
      <div id="upd-box" style="margin-top:8px"><div class="spinner"></div></div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>Sample data</h2>
      <p class="small muted" style="margin-top:6px">A made-up household with two ISAs, a current account
        in each country, a year of spending and some budgets — handy for a look around, or for showing
        someone how it works. It writes into the same database, so clear it before you add real accounts.</p>
      <div class="row" style="margin-top:12px">
        <button class="btn" id="s-demo">Load sample data</button>
      </div>
      <p class="tiny muted" style="margin-top:8px">Pressing it again won't build a second
        household — it tops up the one that's there.</p>
    </div>

    ${/* Its own card, with its own heading. "Clear everything" used to sit next to
          "Load sample data" under the Sample data heading, which reads as "clear the
          sample data" — and it does not: it deletes every real account and every real
          transaction too. The most destructive button in the app was the one whose
          label was most likely to be misread. */''}
    <div class="card" style="margin-top:14px;border-color:var(--down)">
      <h2>Start again</h2>
      <p class="small muted" style="margin-top:6px">Deletes <b>everything</b> — every account,
        transaction, holding and budget, real and sample alike — and puts you back at the
        first screen. Your saved bank logins go too. A backup of the database is taken
        first, into the same folder as your exports, so nothing is unrecoverable.</p>
      <p class="small muted" style="margin-top:6px">Workbooks you have already built are
        files of their own and are left alone — they'll still hold the old figures until
        you build them again.</p>
      <div class="row" style="margin-top:12px">
        <button class="btn btn-danger" id="s-clear">Clear everything</button>
      </div>
    </div>`;

  $('#s-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/settings', { method: 'POST', body: {
        base_currency: $('#s-ccy').value, portfolio_start_date: $('#s-start').value,
        isa_allowance: +$('#s-isa').value, monthly_snapshot_day: +$('#s-snap').value } });
      toast('Saved', 'ok'); S.boot = await api('/api/bootstrap'); S.currency = S.boot.settings.base_currency;
      busy(e.target, false);
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  $('#s-addmember').onclick = async (e) => {
    const name = $('#s-newmember').value.trim(); if (!name) return;
    try {
      await api('/api/members', { method: 'POST', body: { name } });
      S.boot = await api('/api/bootstrap'); toast('Added', 'ok'); render();
    } catch (err) { toast(err.message, 'err'); }
  };
  $$('[data-member]').forEach(b => b.onclick = () =>
    memberDialog(members.items.find(m => m.id === +b.dataset.member), members.items));
  $('#s-backup').onclick = async (e) => {
    busy(e.target, true); const r = await api('/api/backup', { method: 'POST', body: {} });
    toast('Backed up'); busy(e.target, false);
  };
  $('#s-openfolder').onclick = () => api('/api/open-folder',
    { method: 'POST', body: { path: st.data_dir } });
  renderSections();
  $('#sec-add').onclick = async (e) => {
    const name = $('#sec-new').value.trim();
    if (!name) return;
    try {
      await api('/api/sections', { method: 'POST', body: { name } });
      $('#sec-new').value = ''; toast(`Added ${name}`, 'ok'); renderSections();
    } catch (err) { toast(err.message, 'err'); }
  };
  renderReminderSettings();
  renderUpdateSettings(s);
  $('#s-tour').onclick = () => startTour();
  $('#s-help').onclick = () => openHelpEmail();
  $('#s-copyhelp').onclick = (e) => copyText(HELP_TO, e.target);
  $('#s-demo').onclick = async (e) => {
    busy(e.target, true, 'Building…');
    try { await api('/api/demo/load', { method: 'POST', body: {} });
      toast('Sample data loaded', 'ok'); S.boot = await api('/api/bootstrap'); go('dashboard');
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  $('#s-clear').onclick = async (e) => {
    if (!confirm('Delete EVERYTHING — every account, transaction, holding and budget, '
      + 'real as well as sample, and your saved bank logins?\n\nA backup of the database '
      + 'is taken first. Workbooks you have already exported are left as they are.')) return;
    busy(e.target, true, 'Clearing…');
    try {
      const r = await api('/api/demo/clear', { method: 'POST', body: {} });
      S.boot = await api('/api/bootstrap');
      toast(r.backup ? 'Cleared — backup saved' : 'Cleared');
      go('setup');
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
};

// ================================================================ SETUP
VIEWS.setup = async (host) => {
  host.innerHTML = `
    <div class="card" style="max-width:760px">
      <h2>Welcome to Mittens & Pence</h2>
      <p style="margin-top:8px">One place for the investments, the current accounts and the
        budgets — and it rebuilds both spreadsheets whenever you ask.</p>

      <h3 style="margin-top:22px">How it goes</h3>
      <ol class="small" style="margin:8px 0 0;padding-left:20px;color:var(--ink-2)">
        <li style="margin:6px 0"><b>Pick an institution.</b> Every bank, building society,
          broker and platform in the list — ${S.boot.countries.map(c => c.count).reduce((a, b) => a + b, 0)} of them.</li>
        <li style="margin:6px 0"><b>Mittens & Pence tells you what's possible.</b> Some can be linked and synced
          on their own; the rest take a statement file, which is a minute a month.</li>
        <li style="margin:6px 0"><b>It sorts the spending</b> into sections, so budgets mean something.</li>
        <li style="margin:6px 0"><b>Press Build</b> and out come the two workbooks.</li>
      </ol>

      <div class="row" style="margin-top:24px">
        <button class="btn btn-primary" onclick="addInstitution()">Add my first institution</button>
        <button class="btn" id="su-demo">Have a look with sample data first</button>
      </div>
      <p class="tiny muted" style="margin-top:16px">Everything stays on this computer. Nothing is sent
        anywhere except to the banks and price feeds you connect yourself.</p>
    </div>`;
  $('#su-demo').onclick = async (e) => {
    busy(e.target, true, 'Building…');
    try { await api('/api/demo/load', { method: 'POST', body: {} });
      S.boot = await api('/api/bootstrap'); toast('Sample data loaded', 'ok'); go('dashboard');
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
};

// ================================================================ FIRST-RUN TOUR
// A first-time user opens Mittens & Pence to a page of empty cards and no obvious first move.
// This walks them round the rail once: what each tab is for, and — first and loudest —
// where to add an account. Four dark panels around the target rather than a clip-path
// cut-out, because that renders identically everywhere and needs no compositing tricks.

const TOUR = [
  {
    // Asked once, on the very first run, because it is the only moment we know for
    // certain this is a new household. Both boxes on one card: they are the same "who
    // are you" moment, and as two steps the spotlight sat on the same corner twice in a
    // row, which reads as a tour that has got stuck rather than one being thorough.
    // Skippable — between them they set one label and one reminder.
    el: '.brand',
    field: 'who',
    title: 'First — who are you?',
    body: 'Mittens & Pence tags each account with whoever it belongs to, so you can filter by '
        + 'person and set budgets per person. There is no sign-in and nothing leaves this '
        + 'computer. The email address is for the monthly reminder to go and fetch your '
        + 'statements: it goes on a repeating entry in your own calendar, so a calendar '
        + 'that emails you about events will email you too. Both are optional, and both '
        + 'live in Settings afterwards.',
  },
  {
    // One step for this screen, not two. There used to be a separate "for anything that
    // can't link" step pointing at the same button, from before the folder existed —
    // and a tour that spotlights one thing twice reads as stuck rather than thorough.
    nav: 'import',
    field: 'folder',
    title: 'The folder that does the work',
    body: 'Most banks can’t be linked for free, so once a month you download a statement '
        + 'for those accounts. Mittens & Pence has made you a folder: drop the files '
        + 'straight in — any format, receipts too — and press Sync everything. They go in '
        + 'on their own and are filed away as they land. You can also drag files onto this '
        + 'screen or paste rows straight in. It works out which column is which the first '
        + 'time and remembers it, and a file that overlaps an earlier one never doubles up.',
  },
  {
    // One step, not four: Accounts, Transactions, Budgets and Connections are tabs of
    // this screen now, and spotlighting the same button four times in a row reads as a
    // stuck tour rather than a thorough one.
    nav: 'budget',
    title: 'Start here — Budget',
    body: 'Four tabs behind this one button. Connections is where an account comes from: '
        + 'pick your bank or broker from the list of 275 and it tells you honestly whether '
        + 'it can link for free or needs a statement. Accounts shows every balance. '
        + 'Transactions is where the money actually went, sorted into categories on its own. '
        + 'Budgets sets a monthly figure per section and tracks the pace through the month.',
  },
  {
    nav: 'investments',
    title: 'Everything you hold',
    body: 'Holdings, cost, value, dividends and return per position, with live prices. '
        + 'A house, a pension or a mortgage that has no statement at all can be typed in '
        + 'and kept up to date by hand.',
  },
  {
    nav: 'export',
    title: 'Out come the workbooks',
    body: 'Rebuilds both spreadsheets whenever you ask — Investments, and Banking & Budgets '
        + '— with live formulas, ready to open in Excel or push to Google Sheets.',
  },
  {
    el: '#btn-sync',
    title: 'Once a month, press this',
    body: 'One button does the lot: every linked account refreshes itself, and everything '
        + 'sitting in your statements folder is read and filed away. Anything it can’t place '
        + 'is left alone and shown to you rather than guessed at. That is the monthly job — '
        + 'download, drop in the folder, press this.',
  },
  {
    el: '#btn-theme',
    title: 'One last thing',
    body: 'Mittens & Pence goes dark in the evening on its own. This button forces light or dark if '
        + 'you’d rather choose. You can replay this tour any time from Settings.',
  },
];

let tourAt = -1;

function tourEls() {
  let host = $('#tour');
  if (!host) {
    host = document.createElement('div');
    host.id = 'tour';
    host.innerHTML = `
      <div class="tour-pane" data-p="top"></div>
      <div class="tour-pane" data-p="bottom"></div>
      <div class="tour-pane" data-p="left"></div>
      <div class="tour-pane" data-p="right"></div>
      <div class="tour-ring" aria-hidden="true"></div>
      <div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title">
        <div class="tour-step"></div>
        <h2 id="tour-title"></h2>
        <p class="tour-body"></p>
        <div class="tour-foot">
          <button class="btn btn-ghost btn-sm" id="tour-skip">Skip</button>
          <span class="grow"></span>
          <button class="btn btn-sm" id="tour-back">Back</button>
          <button class="btn btn-primary btn-sm" id="tour-next">Next</button>
        </div>
      </div>`;
    document.body.appendChild(host);
    $('#tour-skip').onclick = async () => { await saveTourAnswers(); endTour(); };
    $('#tour-back').onclick = () => showTourStep(tourAt - 1);
    // Both of these MUST await. saveTourAnswers() awaits a network call, and without
    // the await the step is torn down and rebuilt before it resumes — so it read the
    // next step's empty box and posted that. The one thing the tour asks you to type
    // twice was silently discarded both times.
    $('#tour-next').onclick = async (e) => {
      e.target.disabled = true;
      const ok = await saveTourAnswers();
      e.target.disabled = false;
      // A typed-wrong address writes a note under the box. Moving on anyway tore the
      // note down in the same frame it appeared, so the address was quietly dropped and
      // nothing ever said so.
      if (ok) showTourStep(tourAt + 1);
    };
    // Escape leaves, and the arrow keys walk it — the same keys the rest of the app uses.
    host.addEventListener('keydown', e => {
      if (e.key === 'Escape') endTour();
      if (e.key === 'ArrowRight') showTourStep(tourAt + 1);
      if (e.key === 'ArrowLeft') showTourStep(tourAt - 1);
    });
  }
  return host;
}

function placeTour(target) {
  const host = tourEls();
  const pad = 6;
  const r = target.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const box = { t: r.top - pad, l: r.left - pad, w: r.width + pad * 2, h: r.height + pad * 2 };

  const pane = p => host.querySelector(`.tour-pane[data-p="${p}"]`);
  Object.assign(pane('top').style, { top: '0px', left: '0px', width: vw + 'px', height: Math.max(box.t, 0) + 'px' });
  Object.assign(pane('bottom').style, { top: (box.t + box.h) + 'px', left: '0px', width: vw + 'px', height: Math.max(vh - box.t - box.h, 0) + 'px' });
  Object.assign(pane('left').style, { top: box.t + 'px', left: '0px', width: Math.max(box.l, 0) + 'px', height: box.h + 'px' });
  Object.assign(pane('right').style, { top: box.t + 'px', left: (box.l + box.w) + 'px', width: Math.max(vw - box.l - box.w, 0) + 'px', height: box.h + 'px' });

  const ring = host.querySelector('.tour-ring');
  Object.assign(ring.style, { top: box.t + 'px', left: box.l + 'px', width: box.w + 'px', height: box.h + 'px' });

  // Prefer the right of the target (the rail is on the left); fall back to below,
  // then above, and always keep the card fully on screen.
  const card = host.querySelector('.tour-card');
  card.style.top = '0px'; card.style.left = '0px';       // measure unconstrained
  const cw = card.offsetWidth, ch = card.offsetHeight, gap = 14;
  let left = box.l + box.w + gap;
  let top = box.t;
  if (left + cw > vw - 12) {
    left = Math.min(Math.max(box.l, 12), vw - cw - 12);
    top = box.t + box.h + gap;
    if (top + ch > vh - 12) top = box.t - ch - gap;
  }
  card.style.left = Math.round(Math.min(Math.max(left, 12), Math.max(vw - cw - 12, 12))) + 'px';
  card.style.top = Math.round(Math.min(Math.max(top, 12), Math.max(vh - ch - 12, 12))) + 'px';

  // Put it back where it belongs whenever it changes size or the window does. The
  // statements-folder step fills its path in from the server after the card has already
  // been placed, so the card grew a line taller a moment later and, on a 900x600 window,
  // its bottom edge ended up off the screen with the Next button on it.
  placeTour.target = target;
  if (!placeTour.watching) {
    placeTour.watching = true;
    new ResizeObserver(() => {
      const t = placeTour.target;
      if (t && t.isConnected && !tourEls().hidden) placeTour(t);
    }).observe(card);
    addEventListener('resize', () => {
      const t = placeTour.target;
      if (t && t.isConnected && !tourEls().hidden) placeTour(t);
    });
  }
}

function showTourStep(i) {
  if (i < 0) return;
  if (i >= TOUR.length) return endTour(true);
  // Which way the person was going, read BEFORE tourAt moves. It used to be assigned
  // first, so the "was this forwards?" test below compared i with itself, always came
  // out false, and a step whose element was missing sent the tour backwards however you
  // had arrived at it — walking down to step 0 and, if step 0 was the missing one,
  // calling itself with 0 for ever.
  const forwards = i >= tourAt;
  tourAt = i;
  const step = TOUR[i];
  const host = tourEls();
  host.hidden = false;

  // Show the page the step is about, so the spotlight sits over something real.
  if (step.nav && S.view !== step.nav) go(step.nav);

  const target = step.el ? $(step.el) : $(`#nav button[data-view="${step.nav}"]`);
  // Skip in whichever direction the person was already going. Always stepping forward
  // meant Back bounced them straight to the step they came from.
  // Never strand someone on a missing element: carry on the way they were going, and
  // if that runs off the front of the tour, close it rather than bouncing at zero.
  if (!target) return forwards ? showTourStep(i + 1) : (i ? showTourStep(i - 1) : endTour());

  host.querySelector('.tour-step').textContent = `${i + 1} of ${TOUR.length}`;
  host.querySelector('#tour-title').textContent = step.title;
  host.querySelector('.tour-body').textContent = step.body;
  const extra = host.querySelector('.tour-extra');
  if (extra) extra.remove();
  if (step.field === 'who') {
    const wrap = document.createElement('div');
    wrap.className = 'tour-extra';
    wrap.innerHTML = `<input type="text" id="tour-name" maxlength="60"
        placeholder="Your name" style="margin-top:10px">
      <input type="email" id="tour-email" maxlength="200"
        placeholder="you@example.com — for the monthly reminder" style="margin-top:8px">
      <div id="tour-email-note" class="tiny muted" style="margin-top:6px"></div>`;
    host.querySelector('.tour-body').after(wrap);
    const me = (S.boot.members || []).find(x => x.is_default);
    const name = wrap.querySelector('#tour-name');
    // "Me" is the placeholder the app ships with, not an answer — don't hand it back.
    if (me && me.name && me.name.toLowerCase() !== 'me') name.value = me.name;
    wrap.querySelector('#tour-email').value = (S.reminder && S.reminder.email) || '';
    setTimeout(() => name.focus({ preventScroll: true }), 40);
    wrap.querySelectorAll('input').forEach(el => {
      el.onkeydown = (ev) => { if (ev.key === 'Enter') $('#tour-next').click(); };
    });
  }
  if (step.field === 'folder') {
    const wrap = document.createElement('div');
    wrap.className = 'tour-extra';
    wrap.innerHTML = `<div class="tiny mono" id="tour-folder"
        style="margin-top:10px;word-break:break-all;color:var(--ink-2)">…</div>
      <button class="btn btn-sm" id="tour-openfolder" style="margin-top:8px">Show me the folder</button>`;
    host.querySelector('.tour-body').after(wrap);
    api('/api/inbox').then(r => {
      const el = $('#tour-folder'); if (el) el.textContent = r.folder;
    }).catch(() => {});
    wrap.querySelector('#tour-openfolder').onclick = () =>
      api('/api/inbox/open', { method: 'POST', body: {} });
  }
  host.querySelector('#tour-back').disabled = i === 0;
  host.querySelector('#tour-next').textContent = i === TOUR.length - 1 ? 'Finish' : 'Next';

  // The rail is sticky and the card is measured after paint, so wait a frame.
  requestAnimationFrame(() => {
    placeTour(target);
    host.querySelector('#tour-next').focus();
  });
}

// Whatever the step on screen was asking for. Nothing here is allowed to stop the
// tour: a name or an address that won't save is not worth trapping somebody in a
// modal over.
// Returns false only when the person typed something that isn't an address, so the
// caller can hold the step open long enough for them to read why.
async function saveTourAnswers() {
  // Read every field FIRST, synchronously. Anything read after an await is read from
  // a step that may already have been replaced.
  const nameEl = $('#tour-name');
  const emailEl = $('#tour-email');
  const name = nameEl ? nameEl.value.trim() : null;
  const email = emailEl ? emailEl.value.trim() : null;

  if (name !== null) await saveTourName(name);
  if (email) {
    if (!/^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$/.test(email)) {
      const note = $('#tour-email-note');
      if (note) {
        note.textContent = 'That address looks incomplete. Fix it, or clear the box to skip it.';
        note.style.color = 'var(--warn)';
      }
      if (emailEl) emailEl.focus();
      return false;
    }
    try { S.reminder = await api('/api/reminder', { method: 'POST', body: { reminder_email: email } }); }
    catch { /* not worth stopping the tour over */ }
  }
  return true;
}

async function saveTourName(given) {
  const input = $('#tour-name');
  const name = given !== undefined ? given : (input ? input.value.trim() : '');
  const me = (S.boot.members || []).find(x => x.is_default);
  if (!name || !me || name === me.name) return;
  try {
    await api(`/api/members/${me.id}`, { method: 'PATCH', body: { name } });
    S.boot = await api('/api/bootstrap');
  } catch { /* a name that won't save is not worth stopping the tour for */ }
}

async function endTour(finished = false) {
  const host = $('#tour');
  if (host) host.hidden = true;
  tourAt = -1;
  try {
    await api('/api/settings', { method: 'POST', body: { tour_done: true } });
    if (S.boot && S.boot.settings) S.boot.settings.tour_done = true;
  } catch { /* a tour that can't save its flag is still a tour that ran */ }
  if (finished) {
    // End where the first real action is, rather than on whatever page the last step
    // happened to be sitting over.
    go('connections');
    toast('That’s the tour. Add your first account here.', 'ok');
  }
}

function startTour() {
  showTourStep(0);
}
window.startTour = startTour;

// The rail is sticky but the topbar is not, so scrolling moves two of the targets out
// from under their own spotlight. Re-measure on both.
let tourNudge;
function reflowTour() {
  if (tourAt < 0) return;
  clearTimeout(tourNudge);
  tourNudge = setTimeout(() => {
    const step = TOUR[tourAt];
    const target = step.el ? $(step.el) : $(`#nav button[data-view="${step.nav}"]`);
    if (target) placeTour(target);
  }, 80);
}
window.addEventListener('resize', reflowTour);
window.addEventListener('scroll', reflowTour, { passive: true });

// Which sections are income/transfers rather than spending. Read from the data, not
// from a list of names typed in here — otherwise renaming "Income" in Settings would
// quietly turn someone's salary into spending on every chart.
let _special = ['Income', 'Transfers'];
function specialSections() { return _special; }
async function loadSpecialSections() {
  try { const d = await api('/api/sections'); if (d.special) _special = d.special; }
  catch { /* keep the sensible default */ }
}

// ---- budget sections ----------------------------------------------------
async function renderSections() {
  const host = $('#sec-list');
  if (!host) return;
  let data;
  try { data = await api('/api/sections'); }
  catch (e) { host.innerHTML = `<p class="small muted">${esc(e.message)}</p>`; return; }
  const names = data.items.map(s => s.name);

  host.innerHTML = data.items.map(sec => `
    <div class="sec-row" data-sec="${esc(sec.name)}">
      <div class="sec-head">
        <b>${esc(sec.name)}</b>
        ${sec.protected ? `<span class="pill tiny">needed by ${esc(sec.role)}</span>` : ''}
        <span class="grow"></span>
        <span class="tiny muted">${sec.line_count} line${sec.line_count === 1 ? '' : 's'}
          · ${sec.transactions} transaction${sec.transactions === 1 ? '' : 's'}</span>
        <button class="btn btn-sm btn-ghost" data-ren="${esc(sec.name)}">Rename</button>
        ${sec.protected ? ''
          : `<button class="btn btn-sm btn-ghost btn-danger" data-del="${esc(sec.name)}">Remove</button>`}
        <button class="btn btn-sm btn-ghost" data-lines="${esc(sec.name)}">Lines</button>
      </div>
      <div class="sec-lines" hidden>
        ${sec.lines.map(l => `<div class="sec-line">
          <span>${esc(l.name)}</span><span class="grow"></span>
          <button class="btn btn-sm btn-ghost" data-lren="${l.id}"
            data-lname="${esc(l.name)}">Rename</button>
          <button class="btn btn-sm btn-ghost btn-danger" data-ldel="${l.id}"
            data-lname="${esc(l.name)}">Remove</button></div>`).join('')}
        <div class="row" style="margin-top:8px">
          <input class="sec-newline" placeholder="New line" style="max-width:220px">
          <button class="btn btn-sm" data-addline="${esc(sec.name)}">Add</button>
        </div>
      </div>
    </div>`).join('');

  host.querySelectorAll('[data-lines]').forEach(b => b.onclick = () => {
    const box = b.closest('.sec-row').querySelector('.sec-lines');
    box.hidden = !box.hidden;
  });
  host.querySelectorAll('[data-ren]').forEach(b => b.onclick = async () => {
    const now = b.dataset.ren;
    const next = prompt(`Rename "${now}" to:`, now);
    if (!next || next === now) return;
    try {
      const r = await api(`/api/sections/${encodeURIComponent(now)}`,
                          { method: 'PATCH', body: { name: next } });
      toast(r.budgets ? `Renamed — ${r.budgets} budget moved with it` : 'Renamed', 'ok');
      renderSections();
    } catch (err) { toast(err.message, 'err'); }
  });
  host.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    const name = b.dataset.del;
    const sec = data.items.find(s => s.name === name);
    // Never silently un-file someone's history: say how much is at stake and offer
    // somewhere for it to go.
    let dest = '';
    if (sec.transactions) {
      dest = prompt(
        `${name} has ${sec.transactions} transaction(s) filed against it.\n\n` +
        `Type the name of a section to move them to, or leave blank to leave them ` +
        `uncategorised:\n\n${names.filter(n => n !== name).join(', ')}`, '');
      if (dest === null) return;
    } else if (!confirm(`Remove the ${name} section?`)) {
      return;
    }
    const qs = dest.trim() ? `?move_to=${encodeURIComponent(dest.trim())}` : '';
    try {
      const r = await api(`/api/sections/${encodeURIComponent(name)}${qs}`,
                          { method: 'DELETE' });
      toast(r.moved_transactions
        ? `Removed — ${r.moved_transactions} transaction(s) moved`
        : 'Section removed', 'ok');
      renderSections();
    } catch (err) { toast(err.message, 'err'); }
  });
  host.querySelectorAll('[data-addline]').forEach(b => b.onclick = async () => {
    const input = b.closest('.row').querySelector('.sec-newline');
    const name = input.value.trim();
    if (!name) return;
    try {
      await api(`/api/sections/${encodeURIComponent(b.dataset.addline)}/lines`,
                { method: 'POST', body: { name } });
      renderSections();
    } catch (err) { toast(err.message, 'err'); }
  });
  host.querySelectorAll('[data-lren]').forEach(b => b.onclick = async () => {
    const next = prompt('Rename this line to:', b.dataset.lname);
    if (!next || next === b.dataset.lname) return;
    try {
      await api(`/api/categories/${b.dataset.lren}`, { method: 'PATCH', body: { name: next } });
      renderSections();
    } catch (err) { toast(err.message, 'err'); }
  });
  host.querySelectorAll('[data-ldel]').forEach(b => b.onclick = async () => {
    if (!confirm(`Remove the "${b.dataset.lname}" line? Anything filed under it becomes uncategorised.`)) return;
    try {
      await api(`/api/categories/${b.dataset.ldel}`, { method: 'DELETE' });
      renderSections();
    } catch (err) { toast(err.message, 'err'); }
  });
}

// ================================================================ HELP
// One button, one job: open the user's own mail client with enough context already in
// the body that a bug report is useful without them having to describe their setup.
// Nothing financial goes in it — version, platform and which screen they were on.

const HELP_TO = 'allan.michaelclark@gmail.com';

function helpBody() {
  const b = S.boot || {};
  const app = b.app || {};
  const lines = [
    '',
    '',
    '—————————————————————————',
    'Sent from the Help button. The details below just save you describing the setup —',
    'delete them if you would rather not send them. No account or balance data is included.',
    `App: ${app.name || 'Mittens & Pence'} ${app.version || ''}`.trim(),
    `Screen: ${S.view || 'unknown'}`,
    `System: ${navigator.platform || 'unknown'}`,
    `Credential store: ${b.credential_store || 'unknown'}`,
    `Date: ${new Date().toISOString().slice(0, 16).replace('T', ' ')}`,
  ];
  return lines.join('\n');
}

async function openHelpEmail() {
  const url = 'mailto:' + HELP_TO
    + '?subject=' + encodeURIComponent('Mittens & Pence — ')
    + '&body=' + encodeURIComponent(helpBody());
  try {
    await api('/api/open-url', { method: 'POST', body: { url } });
  } catch {
    // No mail client, or the request was refused — give them the address rather than
    // a dead button.
    window.location.href = url;
    setTimeout(() => toast(
      `If no email window opened, write to ${HELP_TO} — the address is in Settings too.`,
      'info'), 1200);
  }
}

(function wireHelp() {
  const el = $('#help');
  if (el) el.onclick = openHelpEmail;
})();

// ================================================================ THE LOGO
// Mittens does her ten seconds, then sits still for two to three minutes. Restarting a
// CSS animation needs the class off, a reflow, and the class back on — assigning it
// twice in the same frame is a no-op, which is the classic way this silently fails.

const LOGO_REST_MIN = 120;   // seconds
const LOGO_REST_MAX = 180;
let logoTimer;

function playLogo() {
  const el = $('#logo');
  if (!el) return;
  el.classList.remove('playing');
  void el.offsetWidth;                       // force reflow, or the restart never happens
  el.classList.add('playing');
}

function scheduleLogo(first = false) {
  clearTimeout(logoTimer);
  const wait = first ? 1200
    : (LOGO_REST_MIN + Math.random() * (LOGO_REST_MAX - LOGO_REST_MIN)) * 1000;
  logoTimer = setTimeout(() => { playLogo(); scheduleLogo(); }, wait);
}

(function wireLogo() {
  const el = $('#logo');
  if (!el) return;
  // Rest on frame 0 rather than holding the last frame, so the idle state is the same
  // picture every time.
  el.addEventListener('animationend', e => {
    if (e.animationName === 'sprite-y') el.classList.remove('playing');
  });
  el.addEventListener('click', () => { playLogo(); scheduleLogo(); });
  // A background tab still fires timers, just slowly, and animating off-screen is waste.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(logoTimer); else scheduleLogo();
  });
  scheduleLogo(true);
})();

// ================================================================ UPDATES
//
// What this does and, more importantly, what it stops short of. The app is not
// code-signed, so it will never overwrite itself: it finds the new version, downloads
// it, checks the download against the checksum in the manifest, and then hands over a
// verified file and a sentence explaining what to do with it. A finance app that
// silently swapped its own executable would be doing the exact thing everybody is
// told to be suspicious of.

let updTimer = null;

// What the person actually has to do with the file that just landed. On Windows it is
// the program itself and they run it; on a Mac it is a zipped .app, and "run the new
// file" sends them to double-click a zip and wonder why nothing happened. The filename
// is the only thing that knows which, so it is the thing that decides.
function runIt(u) {
  return /\.zip$/i.test(u.file || '')
    ? 'unzip it and drag the app into Applications, replacing the old one'
    : 'run the new file';
}

function updateBar(u) {
  const bar = $('#update-bar');
  if (!bar) return;
  const v = u.latest ? esc(u.latest) : '';
  const notes = (u.notes || '').trim();
  const whatsNew = u.page
    ? `<button class="btn btn-sm btn-ghost" id="ub-notes">What's new</button>` : '';
  const dismiss = `<button class="ub-x" id="ub-x" title="Not now" aria-label="Dismiss">✕</button>`;
  let html = '', quiet = false, show = true;

  if (u.status === 'downloading') {
    const pct = Math.round((u.progress || 0) * 100);
    html = `<div class="ub-text"><b>Getting version ${v}…</b>
        <span class="ub-note">You can carry on using the app — nothing changes until you
        run the new file yourself.</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <span class="tiny muted" style="min-width:38px;text-align:right">${pct}%</span>`;
  } else if (u.status === 'ready' && u.file) {
    html = `<div class="ub-text"><b>Version ${v} is downloaded and checked.</b>
        <span class="ub-note">Close Mittens &amp; Pence, then ${runIt(u)}. Your
        accounts, budgets and history all stay exactly where they are.</span></div>
      <div class="ub-actions">
        <button class="btn btn-sm btn-primary" id="ub-open">Show me the file</button>
        ${whatsNew}${dismiss}</div>`;
  } else if (u.show_banner && u.status !== 'error') {
    const sub = u.frozen && u.download
      ? 'Downloading it now — it will tell you when it is ready.'
      : (u.frozen ? 'Your copy can be updated from the release page.'
                  : 'You are running from the source folder, so update with git pull.');
    html = `<div class="ub-text"><b>Version ${v} is out.</b>
        <span class="ub-note">${esc(notes ? notes.split('\n')[0].slice(0, 140) : sub)}</span></div>
      <div class="ub-actions">
        ${u.frozen && u.download && u.status !== 'ready'
          ? `<button class="btn btn-sm btn-primary" id="ub-get">Get it</button>` : ''}
        ${whatsNew}${dismiss}</div>`;
  } else if (u.show_banner && u.status === 'error') {
    quiet = true;
    html = `<div class="ub-text"><b>Version ${v} is out, but it wouldn't download.</b>
        <span class="ub-note">${esc(u.last_error || '')}</span></div>
      <div class="ub-actions">
        <button class="btn btn-sm" id="ub-get">Try again</button>${whatsNew}${dismiss}</div>`;
  } else {
    show = false;
  }

  bar.hidden = !show;
  if (!show) { bar.innerHTML = ''; return; }
  bar.classList.toggle('is-quiet', quiet);
  bar.innerHTML = html;
  const x = $('#ub-x', bar);
  if (x) x.onclick = async () => {
    bar.hidden = true;
    try { S.update = await api('/api/update/dismiss', { method: 'POST', body: { version: u.latest } }); }
    catch { /* dismissing is cosmetic */ }
  };
  const get = $('#ub-get', bar);
  if (get) get.onclick = async (e) => {
    busy(e.target, true, 'Starting…');
    try { S.update = await api('/api/update/download', { method: 'POST', body: {} }); updateBar(S.update); }
    catch (err) { toast(err.message, 'err'); busy(e.target, false); }
    pollUpdates(1200);
  };
  const open = $('#ub-open', bar);
  if (open) open.onclick = () => api('/api/open-folder',
    { method: 'POST', body: { path: u.file.replace(/[\\/][^\\/]*$/, '') } });
  const nt = $('#ub-notes', bar);
  if (nt) nt.onclick = () => (u.page ? openExternal(u.page) : showNotes(u));
}

async function renderReminderSettings() {
  const box = $('#rem-box');
  if (!box) return;
  let r, b;
  try { [r, b] = await Promise.all([api('/api/reminder'), api('/api/inbox')]); }
  catch { box.innerHTML = `<p class="small muted">Couldn't read the settings.</p>`; return; }
  S.reminder = r;

  const days = Array.from({ length: 31 }, (_, i) => i + 1).map(d => {
    const label = d >= 29 ? `${d} — the last day of the month` : `${d}${ordinal(d)}`;
    return `<option value="${d}" ${r.day === d ? 'selected' : ''}>${label}</option>`;
  }).join('');

  box.innerHTML = `
    <p class="small muted">Your folder. Drop statements in, press Sync everything, done.</p>
    <p class="tiny mono" style="margin-top:6px;word-break:break-all;color:var(--ink-2)">${esc(b.folder)}</p>
    <div class="row" style="margin-top:10px">
      <button class="btn btn-sm" id="rm-open">Open it</button>
      <button class="btn btn-sm btn-ghost" id="rm-move">Use a different folder</button>
    </div>

    <h3 style="margin-top:20px">The monthly reminder</h3>
    <p class="small muted" style="margin-top:6px">Mittens &amp; Pence runs on this computer
      and has no mail server of its own, so it can't post you an email out of the blue —
      there would be nothing running to send it. What it does instead actually works
      better: it writes a repeating entry for <b>your own calendar</b>, which then reminds
      you every month wherever you are, phone included. If your calendar emails you about
      events, you get an email — sent by something whose job that is.</p>
    <div class="grid g2" style="margin-top:12px">
      <label class="field">Your email address
        <span class="hint">goes on the calendar entry, so a calendar that emails about
          events emails you</span>
        <input type="email" id="rm-email" value="${esc(r.email || '')}"
          placeholder="you@example.com"></label>
      <label class="field">Remind me on
        <span class="hint">29, 30 and 31 all mean the last day — 28 in February, 29 in a leap year</span>
        <select id="rm-day">${days}</select></label>
    </div>
    <label class="row" style="gap:8px;margin-top:10px"><input type="checkbox" id="rm-on"
      style="width:auto" ${r.on ? 'checked' : ''}> Remind me each month</label>
    <p class="small" style="margin-top:10px;color:var(--ink-2)">Next one:
      <b>${esc(niceDate(r.next_due))}</b></p>
    <div class="row" style="margin-top:12px">
      <button class="btn btn-primary" id="rm-save">Save</button>
      <a class="btn" href="/api/reminder/calendar" download>Add it to my calendar</a>
    </div>
    <p class="tiny muted" style="margin-top:8px">The calendar file is written when you
      download it. Change the address or the day afterwards and you'll want to download it
      again, replacing the old entry.</p>

    <details style="margin-top:16px">
      <summary class="small muted" style="cursor:pointer">Send a real email instead (advanced)</summary>
      <p class="small muted" style="margin-top:8px">Only if you have mail-server details to
        hand. It means storing a password — kept in the same encrypted vault as your bank
        keys, never in a plain file — and Gmail and Outlook both need an <b>app password</b>
        rather than your normal one. The calendar reminder above needs none of this, which
        is why it is the default.</p>
      <div class="grid g2" style="margin-top:10px">
        <label class="field">Server<input id="rm-host" placeholder="smtp.gmail.com"></label>
        <label class="field">Port<input id="rm-port" type="number" value="587"></label>
        <label class="field">Username<input id="rm-user" placeholder="you@gmail.com"></label>
        <label class="field">App password<input id="rm-pass" type="password"></label>
      </div>
      <div class="row" style="margin-top:10px">
        <button class="btn btn-sm" id="rm-smtp">Save these</button>
        <button class="btn btn-sm" id="rm-test" ${r.smtp_configured ? '' : 'disabled'}>Send me a test</button>
        ${r.smtp_configured ? `<button class="btn btn-sm btn-ghost" id="rm-forget">Forget them</button>` : ''}
      </div>
      ${r.smtp_configured ? `<p class="tiny" style="margin-top:8px;color:var(--up)">A mail
        server is set up, so the reminder is emailed as well.</p>` : ''}
    </details>`;

  $('#rm-open').onclick = () => api('/api/inbox/open', { method: 'POST', body: {} });
  $('#rm-move').onclick = () => {
    modal('Use a different folder', `
      <p class="small">Type the full path to the folder you would rather use. It is made
        if it isn't there yet, and nothing in the old one is moved or deleted.</p>
      <input id="rm-path" style="margin-top:12px" value="${esc(b.folder)}">`,
      `<button class="btn" onclick="closeModal()">Cancel</button>
       <button class="btn btn-primary" id="rm-path-save">Use this folder</button>`);
    $('#rm-path-save').onclick = async (e) => {
      busy(e.target, true);
      try {
        await api('/api/inbox/folder', { method: 'POST', body: { folder: $('#rm-path').value } });
        closeModal(); toast('Folder changed', 'ok'); renderReminderSettings();
      } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
    };
  };
  $('#rm-save').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/reminder', { method: 'POST', body: {
        reminder_email: $('#rm-email').value.trim(),
        reminder_day: +$('#rm-day').value,
        reminder_on: $('#rm-on').checked } });
      toast('Saved', 'ok'); renderReminderSettings(); refreshReminder();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  $('#rm-smtp').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/reminder/smtp', { method: 'POST', body: {
        host: $('#rm-host').value.trim(), port: +$('#rm-port').value,
        username: $('#rm-user').value.trim(), password: $('#rm-pass').value } });
      toast('Saved — try the test', 'ok'); renderReminderSettings();
    } catch (err) { toast(err.message, 'err'); busy(e.target, false); }
  };
  const test = $('#rm-test');
  if (test) test.onclick = async (e) => {
    busy(e.target, true, 'Sending…');
    try {
      const out = await api('/api/reminder/test', { method: 'POST', body: {} });
      toast(out.sent ? `Sent to ${out.to}` : out.why, out.sent ? 'ok' : 'err');
    } catch (err) { toast(err.message, 'err'); }
    busy(e.target, false);
  };
  const forget = $('#rm-forget');
  if (forget) forget.onclick = async () => {
    await api('/api/reminder/smtp', { method: 'POST', body: { forget: true } });
    toast('Forgotten'); renderReminderSettings();
  };
}

function ordinal(d) {
  if (d % 100 >= 11 && d % 100 <= 13) return 'th';
  return ['th', 'st', 'nd', 'rd'][d % 10] || 'th';
}

function niceDate(iso) {
  try {
    return new Date(iso + 'T12:00:00').toLocaleDateString('en-GB',
      { weekday: 'long', day: 'numeric', month: 'long' });
  } catch { return iso; }
}

async function renderUpdateSettings(saved) {
  const box = $('#upd-box');
  if (!box) return;
  let u;
  try { u = S.update = await api('/api/update'); }
  catch { box.innerHTML = `<p class="small muted">Couldn't read the update status.</p>`; return; }

  const line = {
    off: 'This copy has no update address set, so it never looks for one.',
    idle: 'Not checked yet.',
    checking: 'Checking…',
    current: `You have the newest version.`,
    available: `Version ${esc(u.latest || '')} is available.`,
    downloading: `Downloading version ${esc(u.latest || '')}…`,
    ready: `Version ${esc(u.latest || '')} is downloaded and checked — close the app and ${runIt(u)}.`,
    error: `Last check didn't work: ${esc(u.last_error || '')}`,
  }[u.status] || '';
  // Two different things, and conflating them is how "Last looked: never" ended up
  // under an error message about a check that had just run. `checked_at` is the last
  // time an answer came back; `attempted_at` is the last time we tried.
  const stamp = t => new Date(t * 1000).toLocaleString('en-GB',
    { dateStyle: 'medium', timeStyle: 'short' });
  const when = u.checked_at ? stamp(u.checked_at) : 'never';
  const tried = u.attempted_at && u.attempted_at !== u.checked_at
    ? stamp(u.attempted_at) : '';

  box.innerHTML = `
    <dl class="kv"><dt>This copy</dt><dd>${esc(S.boot.app.version)}</dd>
      <dt>Status</dt><dd>${line}</dd>
      <dt>Last answer</dt><dd class="tiny muted">${esc(when)}</dd>
      ${tried ? `<dt>Last tried</dt><dd class="tiny muted">${esc(tried)}</dd>` : ''}</dl>
    <p class="small muted" style="margin-top:10px">The check sends nothing but a request
      for a small text file — no accounts, no balances, nothing about the household. A new
      version is downloaded and checked against its published fingerprint, and then it
      waits for you: Mittens &amp; Pence never replaces itself while it is running.</p>
    <div class="stack" style="margin-top:12px;gap:8px">
      <label class="row" style="gap:8px"><input type="checkbox" id="u-on" style="width:auto"
        ${saved.check_for_updates !== false ? 'checked' : ''}> Look for new versions</label>
      <label class="row" style="gap:8px"><input type="checkbox" id="u-auto" style="width:auto"
        ${saved.auto_download_updates !== false ? 'checked' : ''}> Download one as soon as it appears</label>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="btn" id="u-check">Check now</button>
      ${u.download && u.status !== 'ready'
        ? `<button class="btn" id="u-get">Download it</button>` : ''}
      ${u.file ? `<button class="btn" id="u-open">Show me the file</button>` : ''}
    </div>
    <details style="margin-top:14px">
      <summary class="small muted" style="cursor:pointer">Where it looks</summary>
      <p class="small muted" style="margin-top:8px">Built into this copy, and normally left
        alone. Change it only if you are hosting your own builds.</p>
      <div class="row" style="margin-top:8px">
        <input id="u-url" placeholder="https://…/latest.json"
          value="${esc(saved.update_url || '')}" style="flex:1;min-width:220px">
        <button class="btn btn-sm" id="u-saveurl">Save</button>
      </div>
      <p class="tiny muted" style="margin-top:6px">Leave it empty to use whatever this copy
        was built with${u.configured ? '' : ' — which is nothing, so updates are off'}.</p>
    </details>`;

  const flag = async (key, el) => {
    try {
      await api('/api/settings', { method: 'POST', body: { [key]: el.checked } });
      S.boot = await api('/api/bootstrap');
      toast('Saved', 'ok');
    } catch (err) { toast(err.message, 'err'); el.checked = !el.checked; }
  };
  $('#u-on').onchange = (e) => flag('check_for_updates', e.target);
  $('#u-auto').onchange = (e) => flag('auto_download_updates', e.target);
  $('#u-check').onclick = async (e) => {
    busy(e.target, true, 'Looking…');
    try {
      S.update = await api('/api/update/check', { method: 'POST', body: {} });
      updateBar(S.update);
      toast(S.update.status === 'current' ? 'You are on the newest version'
        : S.update.status === 'off' ? 'No update address is set for this copy'
        : S.update.status === 'error' ? S.update.last_error
        : `Version ${S.update.latest} is available`,
        S.update.status === 'error' ? 'err' : 'ok');
    } catch (err) { toast(err.message, 'err'); }
    busy(e.target, false);
    renderUpdateSettings(saved);
  };
  const get = $('#u-get');
  if (get) get.onclick = async (e) => {
    busy(e.target, true, 'Starting…');
    try { await api('/api/update/download', { method: 'POST', body: {} }); pollUpdates(800); }
    catch (err) { toast(err.message, 'err'); }
    busy(e.target, false);
    setTimeout(() => renderUpdateSettings(saved), 1200);
  };
  const open = $('#u-open');
  if (open) open.onclick = () => api('/api/open-folder',
    { method: 'POST', body: { path: u.file.replace(/[\\/][^\\/]*$/, '') } });
  $('#u-saveurl').onclick = async (e) => {
    busy(e.target, true);
    try {
      await api('/api/settings', { method: 'POST', body: { update_url: $('#u-url').value.trim() } });
      toast('Saved — looking now', 'ok');
      setTimeout(() => { pollUpdates(0); renderUpdateSettings(saved); }, 900);
    } catch (err) { toast(err.message, 'err'); }
    busy(e.target, false);
  };
}

function showNotes(u) {
  modal(`What's new in ${u.latest || 'the new version'}`,
    `<div class="small" style="white-space:pre-wrap">${esc(u.notes || 'No notes were published.')}</div>`,
    `<button class="btn" onclick="closeModal()">Close</button>`);
}

// The monthly nudge. It shares the strip at the top with the update banner, and the
// update takes precedence when both are true — a new version is the rarer event.
function reminderBar(r) {
  const bar = $('#reminder-bar');
  if (!bar) return;
  if (!r || !r.show_banner) { bar.hidden = true; bar.innerHTML = ''; return; }
  const month = monthName(r.period);
  bar.hidden = false;
  bar.innerHTML = `
    <div class="ub-text"><b>Time to fetch ${esc(month)}'s statements.</b>
      <span class="ub-note">Download them from every account that can't update itself,
        drop the files in your statements folder, then press Sync everything.</span></div>
    <div class="ub-actions">
      <button class="btn btn-sm btn-primary" id="rb-open">Open the folder</button>
      <button class="ub-x" id="rb-x" title="Not now" aria-label="Dismiss">✕</button>
    </div>`;
  $('#rb-open', bar).onclick = () => api('/api/inbox/open', { method: 'POST', body: {} });
  $('#rb-x', bar).onclick = async () => {
    bar.hidden = true;
    try { S.reminder = await api('/api/reminder/dismiss', { method: 'POST', body: {} }); }
    catch { /* dismissing is cosmetic */ }
  };
}

function inboxReport(r) {
  const bits = [];
  if (r.imported && r.imported.length) {
    bits.push(`<div class="card card-flat" style="background:var(--up-soft)">
      <b>In they go.</b>
      <div class="small" style="margin-top:6px">${r.imported.map(i =>
        `${esc(i.file)} → ${esc(i.account)} (${i.added} new)`).join('<br>')}</div></div>`);
  }
  if (r.receipts && r.receipts.length) {
    bits.push(`<p class="small muted" style="margin-top:8px">${r.receipts.length}
      receipt${r.receipts.length === 1 ? '' : 's'} filed by month and kept — not read,
      because figures taken off a photograph would be invented ones.</p>`);
  }
  if (r.skipped && r.skipped.length) {
    bits.push(`<div class="card card-flat" style="margin-top:8px;background:var(--warn-soft)">
      <b>Left for you.</b>
      <div class="small" style="margin-top:6px">${r.skipped.map(x =>
        `<b>${esc(x.file)}</b> — ${esc(x.why)}`).join('<br>')}</div>
      <div class="tiny muted" style="margin-top:8px">Import one by hand below and the next
        one like it will go in on its own.</div></div>`);
  }
  if (!bits.length) bits.push('<p class="small muted">Nothing new in the folder.</p>');
  return bits.join('');
}

async function refreshReminder() {
  try { S.reminder = await api('/api/reminder'); reminderBar(S.reminder); }
  catch { /* the banner is not worth an error */ }
}

// One timer, re-armed each time with a gap that suits what is happening: fast while a
// download is running, slow otherwise. Polling a local endpoint every second all day
// would be silly, and a download with no visible progress would be worse.
async function pollUpdates(delay = 0) {
  clearTimeout(updTimer);
  const tick = async () => {
    try {
      S.update = await api('/api/update');
      updateBar(S.update);
      const busyNow = ['checking', 'downloading'].includes(S.update.status);
      updTimer = setTimeout(tick, busyNow ? 900 : 1000 * 60 * 15);
    } catch {
      updTimer = setTimeout(tick, 1000 * 60 * 30);
    }
  };
  updTimer = setTimeout(tick, delay);
}

// ================================================================ BOOT
$('#btn-sync').onclick = async (e) => {
  busy(e.target, true, 'Syncing…');
  $('#sync-note').textContent = 'Contacting your connections…';
  try {
    const r = await api('/api/sync-all', { method: 'POST', body: {} });
    await api('/api/prices/refresh', { method: 'POST', body: {} }).catch(() => {});
    const inbox = r.inbox || {};
    const ok = r.results.filter(x => x.ok).length;
    const bits = [];
    if (r.synced) bits.push(`${ok}/${r.synced} connections updated`);
    if (inbox.imported && inbox.imported.length) {
      bits.push(`${inbox.imported.length} file${inbox.imported.length === 1 ? '' : 's'} imported`);
    }
    if (inbox.skipped && inbox.skipped.length) {
      bits.push(`${inbox.skipped.length} need${inbox.skipped.length === 1 ? 's' : ''} a look`);
    }
    $('#sync-note').textContent = bits.length ? bits.join(' · ')
      : 'Nothing new — the statements folder is empty.';
    if (inbox.added) toast(`${inbox.added} transaction${inbox.added === 1 ? '' : 's'} added from your folder`, 'ok');
    else if (r.synced) toast(`${ok} of ${r.synced} connections updated`);
    else toast('Nothing new to bring in');
    if (inbox.skipped && inbox.skipped.length) {
      modal('Some files need a look',
        `<p class="small">Everything else went in. These stayed in the folder because
          Mittens &amp; Pence will not guess where a file belongs:</p>
         <ul class="small" style="margin:12px 0 0;padding-left:18px;color:var(--ink-2)">
           ${inbox.skipped.map(x => `<li style="margin:6px 0"><b>${esc(x.file)}</b><br>
             <span class="muted">${esc(x.why)}</span></li>`).join('')}
         </ul>
         <p class="small muted" style="margin-top:12px">Import one by hand from this screen
           and it will recognise that layout next time.</p>`,
        `<button class="btn" onclick="closeModal()">Close</button>
         <button class="btn btn-primary" onclick="closeModal();go('import')">Go to Import</button>`);
    }
    refreshReminder();
    render();
  } catch (err) { toast(err.message, 'err'); $('#sync-note').textContent = ''; }
  busy(e.target, false);
};

window.go = go;
window.closeModal = closeModal;

(async function start() {
  applyTheme();
  try {
    S.boot = await api('/api/bootstrap');
    S.currency = S.boot.settings.base_currency;
    $('#boot').hidden = true;
    $('#app').hidden = false;
    await loadSpecialSections();
    go(S.boot.has_data ? 'dashboard' : 'setup');
    if (!S.boot.has_data) {
      $('#view-title').textContent = 'Welcome';
      $('#view-sub').textContent = 'Let’s connect the first account';
    }
    // First run: walk them round once. Only ever offered when the app is genuinely
    // empty, so loading the sample data or adding an account doesn't trigger it later.
    if (!S.boot.settings.tour_done && !S.boot.has_data) {
      setTimeout(startTour, 550);
    }
    // Late, so it is never what the first screen is waiting for.
    pollUpdates(6000);
    refreshReminder();
  } catch (e) {
    $('#boot').innerHTML = `<div class="boot-inner"><h2>Mittens & Pence couldn't start</h2>
      <p>${esc(e.message)}</p></div>`;
  }
})();
