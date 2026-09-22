"""Monthly snapshots — the "track monthly" half of the brief.

On the first run of each month (or when you press Take snapshot) Mittens & Pence freezes the
headline numbers so you can see the shape of things over time rather than only today.
Snapshots are never recalculated, which is the point: they record what was true then.
"""

from __future__ import annotations

import datetime as dt
import json

from .. import config, db
from . import budgets, portfolio


def _period(d: dt.date | None = None) -> str:
    return (d or dt.date.today()).strftime("%Y-%m")


def take(period: str | None = None, force: bool = False) -> dict:
    period = period or _period()
    existing = db.one("SELECT id FROM snapshots WHERE period=? AND scope='household'", (period,))
    if existing and not force:
        return {"skipped": True, "period": period}

    ov = portfolio.overall()
    nw = portfolio.net_worth()
    start, end = budgets.month_bounds(period)
    spend = budgets.spend_by_category(start, end)
    bstat = budgets.status(period)

    household = {
        "net_worth": nw["total"],
        "net_worth_groups": nw["groups"],
        "investments": ov["all"]["invested"],
        "investment_cost": ov["all"]["cost"],
        "dividends_to_date": ov["all"]["dividends"],
        "gross_returns": ov["all"]["gross"],
        "net_returns": ov["all"]["net"],
        "net_pct": ov["all"]["net_pct"],
        "cash": ov["all"]["cash"],
        "positions": ov["all"]["positions"],
        "income": spend["totals"]["income"],
        "spend": spend["totals"]["spend"],
        "saving": spend["totals"]["saving"],
        "surplus": spend["totals"]["net"],
        "budgeted": bstat["totals"]["budgeted"],
        "spent_in_budgets": bstat["totals"]["spent_in_budgets"],
        "over_budget_count": sum(1 for i in bstat["items"] if i["verdict"] == "over"),
        "sections": {k: v["spend"] for k, v in spend["sections"].items()},
        "sectors": {s["sector"]: s["value"] for s in ov["sectors"]},
        "currency": config.settings["base_currency"],
    }

    rows = [("household", household)]

    for wt, blk in portfolio.wrapper_breakdown().items():
        s = blk["summary"]
        rows.append((f"wrapper:{wt}", {
            "invested": s["invested"], "cost": s["cost"], "dividends": s["dividends"],
            "gross": s["gross"], "net": s["net"], "net_pct": s["net_pct"],
            "cash": s["cash"], "positions": s["positions"],
        }))

    for m in db.rows("SELECT id, name FROM members"):
        st = budgets.status(period, member_id=m["id"])
        rows.append((f"member:{m['id']}", {
            "name": m["name"],
            "income": st["totals"]["income"], "spend": st["totals"]["all_spend"],
            "saving": st["totals"]["saving"], "net": st["totals"]["net"],
        }))

    for a in db.rows("SELECT id, name, balance, currency FROM accounts WHERE closed=0"):
        rows.append((f"account:{a['id']}", {
            "name": a["name"], "balance": a["balance"], "currency": a["currency"]}))

    taken = dt.date.today().isoformat()
    with db.tx() as c:
        for scope, metrics in rows:
            c.execute(
                "INSERT INTO snapshots(taken_on, period, scope, metrics_json) VALUES(?,?,?,?)"
                " ON CONFLICT(period,scope) DO UPDATE SET metrics_json=excluded.metrics_json,"
                " taken_on=excluded.taken_on",
                (taken, period, scope, json.dumps(metrics, default=str)))
    db.log("snapshot", {"period": period, "scopes": len(rows)})
    return {"period": period, "taken_on": taken, "scopes": len(rows), "household": household}


def maybe_take_monthly() -> dict | None:
    """Called at start-up; takes last month's snapshot if it was never taken."""
    today = dt.date.today()
    if today.day < int(config.settings.get("monthly_snapshot_day", 1) or 1):
        return None
    prev = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    for period in (prev, _period()):
        if not db.one("SELECT id FROM snapshots WHERE period=? AND scope='household'", (period,)):
            return take(period)
    return None


def backfill(months: int = 24, force: bool = False) -> dict:
    """Reconstruct the months before Mittens & Pence was installed.

    Yesterday's share prices can't be recovered, so a back-filled month values
    investments at what was paid for them, not at what they were worth. Bank balances
    and contributions *are* recoverable — they are wound back from today's balance
    through the transactions since. Every reconstructed month is flagged `estimated`
    so a chart can say so rather than implying Mittens & Pence was watching all along.
    """
    from ..market import prices as market
    base = config.settings["base_currency"]
    today = dt.date.today()

    accounts = db.rows("SELECT * FROM accounts WHERE include_in_net_worth=1")
    assets = sum(market.convert(a["balance"] or 0, a["currency"] or base, base)
                 for a in accounts if a["account_type"] == "asset")

    # Real month-end prices where the feed can supply them, so past months show what the
    # portfolio was actually worth rather than what it cost.
    hist: dict[int, dict[str, float]] = {}
    hist_ccy: dict[int, str] = {}
    for inst in db.rows("SELECT DISTINCT instrument_id AS id FROM trades"):
        h = market.monthly_history(inst["id"], months + 12)
        if h:
            hist[inst["id"]] = h
            hist_ccy[inst["id"]] = market.history_currency(inst["id"])
    priced = bool(hist)

    made = 0
    for i in range(months, 0, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        period = f"{y:04d}-{m:02d}"
        month_end = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
        if month_end >= today:
            continue
        if not force and db.one("SELECT id FROM snapshots WHERE period=? AND scope='household'",
                                (period,)):
            continue

        bank = 0.0
        for a in accounts:
            if a["is_investment"] or a["account_type"] == "asset":
                continue
            # The parts of a split sum to the parent, so counting both would roll the
            # balance back by twice what actually moved.
            after = db.scalar("SELECT SUM(amount) FROM transactions WHERE account_id=? "
                              "AND posted_on > ? AND IFNULL(is_split, 0) = 0",
                              (a["id"], month_end.isoformat()), 0.0) or 0.0
            bank += market.convert((a["balance"] or 0) - after, a["currency"] or base, base)

        buys = db.scalar("SELECT SUM(total) FROM trades WHERE side='BUY' AND traded_on <= ?",
                         (month_end.isoformat(),), 0.0) or 0.0
        sells = db.scalar("SELECT SUM(total) FROM trades WHERE side='SELL' AND traded_on <= ?",
                          (month_end.isoformat(),), 0.0) or 0.0
        divs = db.scalar("SELECT SUM(amount) FROM dividends WHERE paid_on <= ?",
                         (month_end.isoformat(),), 0.0) or 0.0
        interest = db.scalar("SELECT SUM(amount) FROM cash_events WHERE kind='INTEREST' "
                             "AND happened_on <= ?", (month_end.isoformat(),), 0.0) or 0.0
        deposits = db.scalar("SELECT SUM(amount) FROM cash_events WHERE "
                             "kind IN ('DEPOSIT','WITHDRAWAL') AND happened_on <= ?",
                             (month_end.isoformat(),), 0.0) or 0.0
        invested_cost = max(buys - sells, 0.0)
        broker_cash = deposits + interest + divs - buys + sells

        # Shares held at that month end, replayed from the trades.
        market_value = None
        if priced:
            held: dict[int, float] = {}
            for t in db.rows("SELECT instrument_id, side, shares FROM trades "
                             "WHERE traded_on <= ?", (month_end.isoformat(),)):
                q = float(t["shares"] or 0)
                held[t["instrument_id"]] = held.get(t["instrument_id"], 0.0) + (
                    q if t["side"] == "BUY" else -q)
            total, missing = 0.0, False
            for iid, shares in held.items():
                if shares <= 1e-9:
                    continue
                px = (hist.get(iid) or {}).get(period)
                if px is None:
                    missing = True
                    continue
                total += market.convert(shares * px, hist_ccy.get(iid, base), base)
            if not missing and total > 0:
                market_value = total

        investments = market_value if market_value is not None else invested_cost
        note = ("month-end market prices"
                if market_value is not None
                else "valued at cost — the price feed had no history for these holdings")

        start, end = budgets.month_bounds(period)
        spend = budgets.spend_by_category(start, end)

        metrics = {
            "estimated": True,
            "priced": market_value is not None,
            "net_worth": investments + max(broker_cash, 0.0) + bank + assets,
            "investments": investments,
            "investments_note": note,
            "investment_cost": invested_cost,
            "dividends_to_date": divs,
            "cash": max(broker_cash, 0.0),
            "bank": bank,
            "assets": assets,
            "income": spend["totals"]["income"],
            "spend": spend["totals"]["spend"],
            "saving": spend["totals"]["saving"],
            "surplus": spend["totals"]["net"],
            "sections": {k: v["spend"] for k, v in spend["sections"].items()},
            "currency": base,
        }
        with db.tx() as c:
            c.execute("INSERT INTO snapshots(taken_on, period, scope, metrics_json)"
                      " VALUES(?,?, 'household', ?)"
                      " ON CONFLICT(period,scope) DO UPDATE SET metrics_json=excluded.metrics_json",
                      (month_end.isoformat(), period, json.dumps(metrics, default=str)))
        made += 1
    db.log("snapshot.backfill", {"months": made})
    return {"reconstructed": made}


def series(scope: str = "household", metric: str = "net_worth", months: int = 24) -> list[dict]:
    rows = db.rows("SELECT period, taken_on, metrics_json FROM snapshots WHERE scope=? "
                   "ORDER BY period DESC LIMIT ?", (scope, months))
    out = []
    for r in reversed(rows):
        m = json.loads(r["metrics_json"])
        out.append({"period": r["period"], "taken_on": r["taken_on"],
                    "value": m.get(metric), "metrics": m})
    return out


def compare(period_a: str, period_b: str, scope: str = "household") -> dict:
    a = db.one("SELECT metrics_json FROM snapshots WHERE period=? AND scope=?", (period_a, scope))
    b = db.one("SELECT metrics_json FROM snapshots WHERE period=? AND scope=?", (period_b, scope))
    if not a or not b:
        return {"error": "one of those months has no snapshot yet"}
    ma, mb = json.loads(a["metrics_json"]), json.loads(b["metrics_json"])
    diff = {}
    for k in set(ma) | set(mb):
        va, vb = ma.get(k), mb.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            diff[k] = {"from": va, "to": vb, "change": vb - va,
                       "pct": ((vb - va) / va) if va else None}
    return {"from": period_a, "to": period_b, "scope": scope, "diff": diff}


def all_periods() -> list[str]:
    return [r["period"] for r in db.rows(
        "SELECT DISTINCT period FROM snapshots ORDER BY period DESC")]
