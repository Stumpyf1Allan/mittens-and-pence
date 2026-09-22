"""A copy of the numbers you can read on a phone.

There is no Mittens & Pence app for iPhone or Android, and building one would mean
writing the whole thing again in a different language, paying Apple and Google for the
privilege of putting it in their shops, and then keeping three versions in step. For an
app whose job is to show a household its own figures, that is a great deal of work for
a screen you glance at.

This does the useful part instead. It writes **one HTML file** — everything inside it,
no internet needed to open it — that shows the same headline figures laid out for a
phone. Put it in iCloud, Google Drive, Dropbox or an email to yourself, tap it, and
there it is. Add it to the home screen and it looks like an app.

What it is not is live. It is a photograph of the figures at the moment it was made,
and it says so at the top, in words, because a stale balance that looks current is
worse than no balance at all.
"""

from __future__ import annotations

import datetime as dt
import html
import pathlib

from .. import config, db
from ..engine import budgets as budget_engine
from ..engine import portfolio, snapshots
from ..market import prices as market

SYMBOL = {"GBP": "£", "ZAR": "R", "USD": "$", "EUR": "€", "CHF": "CHF", "JPY": "¥"}

MONTHS = ("January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")


def long_date(d: dt.date) -> str:
    """'3 April 2026'. Built by hand because strftime's no-padding flag is %-d on Mac
    and Linux and %#d on Windows — and this app is mostly run on Windows."""
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def clock(t: dt.datetime) -> str:
    """'2:05 pm', likewise without leaning on a platform-specific strftime flag."""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def short_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{d.day} {MONTHS[d.month - 1][:3]}"


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def money(value, currency: str, dp: int | None = None) -> str:
    """Unknown is not zero — a missing balance prints as a dash, the same as in the app."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    places = dp if dp is not None else (0 if abs(v) >= 1000 else 2)
    body = f"{abs(v):,.{places}f}"
    return f"{'−' if v < 0 else ''}{SYMBOL.get(currency, '')}{body}"


def pct(value, dp: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{dp}f}%"


def _sparkline(series: list[dict], width: int = 320, height: int = 54) -> str:
    """A line drawn as SVG, so there is nothing to download and nothing to run."""
    points = [s for s in series if s.get("value") is not None]
    if len(points) < 2:
        return ""
    values = [float(p["value"]) for p in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = width / (len(values) - 1)
    coords = " ".join(
        f"{i * step:.1f},{height - 4 - ((v - lo) / span) * (height - 10):.1f}"
        for i, v in enumerate(values))
    last_x, last_y = coords.split(" ")[-1].split(",")
    rising = values[-1] >= values[0]
    stroke = "var(--up)" if rising else "var(--down)"
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
            f'role="img" aria-label="Net worth over the last {len(values)} months">'
            f'<polyline points="{coords}" fill="none" stroke="{stroke}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{last_x}" cy="{last_y}" r="3" fill="{stroke}"/></svg>')


def _rows(items: list[tuple[str, str, str]]) -> str:
    out = []
    for label, sub, value in items:
        out.append(f'<li><span class="l">{label}'
                   + (f'<span class="s">{sub}</span>' if sub else "")
                   + f'</span><span class="v num">{value}</span></li>')
    return "".join(out)


# ---------------------------------------------------------------------------
# The sections
# ---------------------------------------------------------------------------

def _net_worth_section(nw: dict, history: list[dict]) -> str:
    ccy = nw["currency"]
    order = ["Current accounts", "Savings", "Investments", "Investment cash",
             "Other assets", "Debt"]
    groups = nw.get("groups") or {}
    rows = [(_e(k), "", money(groups[k], ccy))
            for k in order if k in groups and abs(groups[k]) > 0.005]
    rows += [(_e(k), "", money(v, ccy)) for k, v in groups.items()
             if k not in order and abs(v) > 0.005]
    return f"""
    <section class="card hero">
      <h2>Everything, together</h2>
      <p class="big num">{money(nw['total'], ccy, dp=0)}</p>
      {_sparkline(history)}
      <ul class="list">{_rows(rows)}</ul>
    </section>"""


def _accounts_section(accounts: list[dict], base: str) -> str:
    if not accounts:
        return ""
    rows = []
    for a in accounts:
        sub = " · ".join(x for x in [_e(a.get("institution_name")), _e(a.get("member"))] if x)
        own = money(a.get("balance"), a.get("currency") or base)
        converted = a.get("balance_base")
        value = own
        if (a.get("currency") or base) != base and converted is not None:
            value = f'{own} <span class="s">{money(converted, base)}</span>'
        rows.append((_e(a["name"]), sub, value))
    return f"""
    <section class="card">
      <h2>Accounts</h2>
      <ul class="list">{_rows(rows)}</ul>
    </section>"""


def _budget_section(bstat: dict) -> str:
    ccy = bstat["currency"]
    t = bstat["totals"]
    if not t["budgeted"] and not t["all_spend"]:
        return ""
    _start = dt.date.fromisoformat(bstat["start"])
    label = f"{MONTHS[_start.month - 1]} {_start.year}"
    bars = []
    for i in bstat["items"][:12]:
        used = min(max(i["used"], 0.0), 1.0)
        klass = {"over": "over", "ahead-of-pace": "warn", "close": "warn"}.get(
            i["verdict"], "ok")
        left = i["remaining"]
        note = (f"{money(left, i['currency'] or ccy)} left" if left >= 0
                else f"{money(-left, i['currency'] or ccy)} over")
        bars.append(f"""
          <li class="bud">
            <div class="budtop"><span class="l">{_e(i['label'])}</span>
              <span class="v num">{money(i['spent'], i['currency'] or ccy)}
                <span class="s">of {money(i['amount'], i['currency'] or ccy)}</span></span></div>
            <div class="track"><i class="{klass}" style="width:{used * 100:.1f}%"></i></div>
            <div class="s num">{note}</div>
          </li>""")
    pace = ""
    if bstat.get("is_current") and bstat.get("days_total"):
        left_days = max(bstat["days_total"] - bstat["days_gone"], 0)
        pace = (f'<p class="s">{left_days} day{"" if left_days == 1 else "s"} left '
                f'in the month.</p>')
    return f"""
    <section class="card">
      <h2>{_e(label)}</h2>
      <ul class="list tight">{_rows([
          ('Spent', '', money(t['all_spend'], ccy)),
          ('Budgeted', '', money(t['budgeted'], ccy)),
          ('Left in budgets', '', money(t['remaining'], ccy)),
          ('Income', '', money(t['income'], ccy)),
      ])}</ul>
      {pace}
      <ul class="buds">{''.join(bars)}</ul>
    </section>"""


def _investments_section(ov: dict, top: list[dict], sectors: list[dict], ccy: str) -> str:
    if not ov or not ov.get("invested"):
        return ""
    gain = ov.get("net")
    pace = ov.get("return_on_cost_yr")
    rows = [
        ("Value", "", money(ov.get("invested"), ccy)),
        ("Cost", "", money(ov.get("cost"), ccy)),
        ("Gain", "", f'<span class="{"up" if (gain or 0) >= 0 else "down"}">'
                     f'{money(gain, ccy)}</span>'),
        ("Return", "", pct(ov.get("return_on_cost"))),
    ]
    # None, not zero: below a year of history there is no honest per-year rate, and the
    # app says so rather than dividing by a few months and calling it an annual return.
    if pace is not None:
        rows.append(("A year", "", pct(pace)))
    holdings = [(_e(h.get("name") or h.get("symbol")), _e(h.get("symbol")),
                 money(h.get("value"), ccy)) for h in top[:8]]
    sector_rows = [(_e(s["sector"]), "", pct(s["pct"])) for s in sectors[:6]]
    return f"""
    <section class="card">
      <h2>Investments</h2>
      <ul class="list tight">{_rows(rows)}</ul>
      {f'<h3>Biggest holdings</h3><ul class="list">{_rows(holdings)}</ul>' if holdings else ''}
      {f'<h3>Where it sits</h3><ul class="list tight">{_rows(sector_rows)}</ul>' if sector_rows else ''}
    </section>"""


def _recent_section(recent: list[dict], base: str) -> str:
    if not recent:
        return ""
    rows = []
    for t in recent[:15]:
        when = short_date(t.get("posted_on"))
        cat = t.get("category") or t.get("parent") or ""
        sub = " · ".join(x for x in [when, _e(t.get("account")), _e(cat)] if x)
        amount = t.get("amount")
        klass = "up" if (amount or 0) > 0 else ""
        rows.append((_e(t.get("description") or "—"), sub,
                     f'<span class="{klass}">{money(amount, t.get("currency") or base)}</span>'))
    return f"""
    <section class="card">
      <h2>Lately</h2>
      <ul class="list">{_rows(rows)}</ul>
    </section>"""


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

STYLE = """
:root{--bg:#f6f7fa;--panel:#fff;--ink:#14202e;--ink2:#47576b;--muted:#7a8798;
 --line:#e3e8ef;--accent:#2352c9;--up:#0b7a55;--down:#b3261e;--warn:#a55b00;}
@media (prefers-color-scheme:dark){:root{--bg:#0e141b;--panel:#151d27;--ink:#e7edf5;
 --ink2:#a9b7c8;--muted:#7f8ea1;--line:#233042;--accent:#6f9bff;--up:#34d399;
 --down:#f87171;--warn:#e0a458;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
 -webkit-text-size-adjust:100%;padding:0 0 84px}
.wrap{max-width:560px;margin:0 auto;padding:0 14px}
header{padding:22px 0 6px}
h1{font-size:1.35rem;margin:0;letter-spacing:-.02em}
h2{font-size:.82rem;margin:0 0 10px;text-transform:uppercase;letter-spacing:.07em;
 color:var(--muted);font-weight:700}
h3{font-size:.8rem;margin:16px 0 8px;color:var(--muted);font-weight:700;
 text-transform:uppercase;letter-spacing:.05em}
.stamp{color:var(--ink2);font-size:.82rem;margin:4px 0 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
 padding:16px;margin-top:12px}
.hero .big{font-size:2.1rem;font-weight:700;margin:2px 0 6px;letter-spacing:-.03em}
.num{font-variant-numeric:tabular-nums}
.spark{display:block;width:100%;height:54px;margin:2px 0 10px}
ul{list-style:none;margin:0;padding:0}
.list li{display:flex;justify-content:space-between;gap:12px;align-items:baseline;
 padding:9px 0;border-top:1px solid var(--line)}
.list li:first-child{border-top:0}
.list.tight li{padding:6px 0}
.l{min-width:0;overflow-wrap:anywhere}
.v{text-align:right;white-space:nowrap;font-weight:600}
.s{display:block;color:var(--muted);font-size:.78rem;font-weight:400}
.v .s{display:inline;margin-left:4px}
.up{color:var(--up)}.down{color:var(--down)}
.buds{margin-top:12px}
.bud{padding:10px 0;border-top:1px solid var(--line)}
.budtop{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.track{height:6px;border-radius:99px;background:var(--line);overflow:hidden;margin:7px 0 4px}
.track i{display:block;height:100%;background:var(--accent)}
.track i.ok{background:var(--up)}.track i.warn{background:var(--warn)}
.track i.over{background:var(--down)}
footer{color:var(--muted);font-size:.76rem;text-align:center;padding:26px 14px 0;
 line-height:1.6}
.hidden .num,.hidden .big{filter:blur(7px);-webkit-filter:blur(7px)}
.eye{position:fixed;right:14px;bottom:14px;border:1px solid var(--line);
 background:var(--panel);color:var(--ink2);border-radius:99px;padding:10px 16px;
 font:inherit;font-size:.82rem;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.14)}
@media print{.eye{display:none}}
"""

SCRIPT = """
// The only script in the file: a blur toggle, so the figures can be opened on a train.
// Nothing here talks to anything; this page has no network access of any kind.
(function(){
  var b=document.body,k='mp-phone-hidden';
  try{ if(localStorage.getItem(k)==='1') b.classList.add('hidden'); }catch(e){}
  document.getElementById('eye').addEventListener('click',function(){
    var on=b.classList.toggle('hidden');
    this.textContent=on?'Show amounts':'Hide amounts';
    try{ localStorage.setItem(k,on?'1':'0'); }catch(e){}
  });
  var on=b.classList.contains('hidden');
  document.getElementById('eye').textContent=on?'Show amounts':'Hide amounts';
})();
"""


def render() -> str:
    """The whole page as a string. No files touched — handy for tests."""
    nw = portfolio.net_worth()
    base = nw["currency"]
    ov = portfolio.overall()
    rows = portfolio.holdings_rows()
    bstat = budget_engine.status()
    accounts = db.rows("""SELECT a.*, m.name AS member, c.institution_name
                          FROM accounts a LEFT JOIN members m ON m.id=a.member_id
                          LEFT JOIN connections c ON c.id=a.connection_id
                          WHERE a.closed=0 ORDER BY a.is_investment, a.name""")
    for a in accounts:
        a["balance_base"] = (None if a["balance"] is None else
                             market.convert(a["balance"], a["currency"] or base, base))
    # A split parent must never be listed: its children carry the money, and showing
    # both would count the same payment twice on the one screen somebody glances at.
    recent = db.rows(f"""SELECT t.posted_on, t.description, t.amount, t.currency,
                                a.name AS account, c.parent, c.name AS category
                         FROM transactions t JOIN accounts a ON a.id=t.account_id
                         LEFT JOIN categories c ON c.id=t.category_id
                         WHERE {db.NOT_SPLIT_PARENT}
                         ORDER BY t.posted_on DESC, t.id DESC LIMIT 15""")
    history = snapshots.series("household", "net_worth", months=18)
    top = sorted(rows, key=lambda r: -(r["value"] or 0))

    made = dt.datetime.now()
    stamp = f"{long_date(made.date())} at {clock(made)}"
    body = "".join([
        _net_worth_section(nw, history),
        _budget_section(bstat),
        _accounts_section(accounts, base),
        _investments_section(ov.get("all", {}), top, ov.get("sectors", []), base),
        _recent_section(recent, base),
    ])
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{_e(config.APP_NAME)}">
<meta name="theme-color" content="#f6f7fa" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e141b" media="(prefers-color-scheme: dark)">
<title>{_e(config.APP_NAME)} — {_e(stamp)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{_e(config.APP_NAME)}</h1>
    <p class="stamp">A snapshot taken on {_e(stamp)}. It does not update on its own —
      make a new one whenever you want fresher figures.</p>
  </header>
  {body}
  <footer>
    Made by {_e(config.APP_NAME)} {_e(config.APP_VERSION)}. Everything is inside this one
    file — it needs no internet, and it sends nothing anywhere.<br>© Allan Clark
  </footer>
</div>
<button class="eye" id="eye" type="button">Hide amounts</button>
<script>{SCRIPT}</script>
</body>
</html>
"""


def build(path: pathlib.Path | None = None) -> pathlib.Path:
    """Write the snapshot and return where it went."""
    out = pathlib.Path(path) if path else (
        config.exports_dir() /
        f"{config.APP_FILE_NAME} on your phone — {dt.date.today().isoformat()}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(), encoding="utf-8")
    return out
