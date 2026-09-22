"""Portfolio maths — the same numbers the Investments 2 workbook computes.

Column-for-column equivalents (ISA tab):
    # shares        -> shares
    Cost            -> cost            (money actually put in, average-cost basis)
    Current Price   -> price           (converted to the base currency)
    Inv now         -> value           (= shares x price)
    Price change    -> price_change    (= value - cost)
    % change        -> pct_change      (= price_change / cost)
    Divs            -> dividends
    Overall change  -> overall_change  (= (price_change + dividends) / cost)
    Total value     -> total_value     (= value + dividends)
    TOTAL Money made-> money_made      (= total_value - cost)
    TOTAL % change  -> money_made_pct  (= money_made / cost)

Header block:
    Invested value  -> invested        (sum of value)
    Cost value      -> cost            (sum of cost)
    Gross returns   -> gross           (= invested + dividends)
    Net Returns     -> net             (= gross - cost)
    Balanced G/L    -> balanced        (= gross - cost, as % of gross)
    Investment G/L  -> investment      (= sum of price_change, as % of invested)
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .. import config, db
from ..market import prices as market


# ---------------------------------------------------------------------------
# Holdings from trades
# ---------------------------------------------------------------------------

def rebuild_holdings(account_id: int) -> dict:
    """Replay the trade history into current positions, average-cost basis.

    Anything that goes to zero shares becomes a row on the Sold tab, keeping the
    realised profit and the dividends it collected while it was held.
    """
    trades = db.rows(
        "SELECT * FROM trades WHERE account_id=? ORDER BY traded_on, id", (account_id,))
    if not trades:
        return {"open": 0, "closed": 0}

    # `total` on a trade is always in the account's own currency, whatever currency the
    # share price was quoted in. Getting this wrong divides GBX-quoted holdings by 100.
    acct = db.one("SELECT currency FROM accounts WHERE id=?", (account_id,))
    acct_ccy = (acct or {}).get("currency") or config.settings["base_currency"]

    state: dict[int, dict] = {}
    closed: list[dict] = []
    for t in trades:
        iid = t["instrument_id"]
        s = state.setdefault(iid, {"shares": 0.0, "cost": 0.0, "buys": [], "ccy": acct_ccy,
                                   "first_buy": None, "realised": 0.0, "sell_dates": [],
                                   "sell_shares": 0.0, "proceeds": 0.0})
        qty = float(t["shares"] or 0)
        total = float(t["total"] or 0)
        if t["side"] == "BUY":
            s["shares"] += qty
            s["cost"] += total
            s["buys"].append(t["traded_on"])
            s["first_buy"] = s["first_buy"] or t["traded_on"]
        else:
            if s["shares"] <= 0:
                continue
            portion = min(qty, s["shares"]) / s["shares"] if s["shares"] else 0
            cost_out = s["cost"] * portion
            s["realised"] += total - cost_out
            s["cost"] -= cost_out
            s["shares"] -= min(qty, s["shares"])
            s["sell_dates"].append(t["traded_on"])
            s["sell_shares"] += qty
            s["proceeds"] += total
            if s["shares"] <= 1e-9:
                closed.append({
                    "instrument_id": iid,
                    "bought_on": s["first_buy"],
                    "buy_shares": s["sell_shares"],
                    "cost": round(s["cost"] + cost_out, 2) if s["cost"] else round(cost_out, 2),
                    "sold_on": t["traded_on"],
                    "sell_shares": s["sell_shares"],
                    "proceeds": round(s["proceeds"], 2),
                    "currency": s["ccy"],
                })
                s["shares"] = 0.0
                s["cost"] = 0.0
                s["buys"] = []
                s["first_buy"] = None
                s["sell_shares"] = 0.0
                s["proceeds"] = 0.0

    with db.tx() as c:
        c.execute("DELETE FROM holdings WHERE account_id=? AND source IN ('trades','import')",
                  (account_id,))
        n_open = 0
        for iid, s in state.items():
            if s["shares"] > 1e-9:
                c.execute(
                    # cost_known=1: this cost was replayed from actual trades, so the
                    # return figure is real. This is the path that repairs a crypto
                    # holding once its trade history has been uploaded.
                    "INSERT INTO holdings(account_id,instrument_id,shares,cost,cost_known,"
                    "cost_currency,buy_dates,source,updated_at)"
                    " VALUES(?,?,?,?,1,?,?, 'trades', datetime('now'))",
                    (account_id, iid, s["shares"], s["cost"], s["ccy"],
                     "; ".join(sorted(set(s["buys"]))[:8])))
                n_open += 1
        for cl in closed:
            inst = db.one("SELECT symbol, exchange FROM instruments WHERE id=?",
                          (cl["instrument_id"],))
            divs = db.scalar(
                "SELECT SUM(amount) FROM dividends WHERE account_id=? AND instrument_id=?"
                " AND paid_on <= ?", (account_id, cl["instrument_id"], cl["sold_on"]), 0.0) or 0.0
            c.execute(
                "INSERT INTO sold_positions(account_id,instrument_id,symbol,exchange,bought_on,"
                "buy_shares,cost,sold_on,sell_shares,proceeds,dividends,currency,auto)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (account_id, cl["instrument_id"], (inst or {}).get("symbol", "?"),
                 (inst or {}).get("exchange"), cl["bought_on"], cl["buy_shares"], cl["cost"],
                 cl["sold_on"], cl["sell_shares"], cl["proceeds"], divs, cl["currency"]))
    # de-duplicate the auto-generated Sold rows on re-import
    with db.tx() as c:
        c.execute("""DELETE FROM sold_positions WHERE auto=1 AND id NOT IN (
                       SELECT MIN(id) FROM sold_positions WHERE auto=1
                       GROUP BY account_id, instrument_id, sold_on, sell_shares)""")
    return {"open": n_open, "closed": len(closed)}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def recompute_cash(account_id: int) -> float:
    """Work out uninvested cash on a broker account from its own history.

    An API connection reports the free-cash figure directly and we leave that alone.
    A CSV-fed account has no balance line anywhere, so it is derived:
        deposits + interest + dividends + sale proceeds - purchases - withdrawals - fees
    """
    acct = db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not acct or not acct["is_investment"]:
        return acct["balance"] if acct else 0.0
    if db.scalar("SELECT COUNT(*) FROM holdings WHERE account_id=? AND source='api'",
                 (account_id,), 0):
        return acct["balance"] or 0.0

    deposits = db.scalar("SELECT SUM(amount) FROM cash_events WHERE account_id=? "
                         "AND kind IN ('DEPOSIT','WITHDRAWAL')", (account_id,), 0.0) or 0.0
    interest = db.scalar("SELECT SUM(amount) FROM cash_events WHERE account_id=? "
                         "AND kind='INTEREST'", (account_id,), 0.0) or 0.0
    fees = db.scalar("SELECT SUM(amount) FROM cash_events WHERE account_id=? AND kind='FEE'",
                     (account_id,), 0.0) or 0.0
    divs = db.scalar("SELECT SUM(amount) FROM dividends WHERE account_id=?",
                     (account_id,), 0.0) or 0.0
    buys = db.scalar("SELECT SUM(total + IFNULL(fees,0)) FROM trades WHERE account_id=? "
                     "AND side='BUY'", (account_id,), 0.0) or 0.0
    sells = db.scalar("SELECT SUM(total - IFNULL(fees,0)) FROM trades WHERE account_id=? "
                      "AND side='SELL'", (account_id,), 0.0) or 0.0
    cash = deposits + interest + fees + divs - buys + sells
    note = None
    if cash < -1:
        # Almost always means the statement doesn't reach back to the early deposits.
        note = ("Cash works out negative, which usually means this export doesn't go back "
                "far enough to include the earliest deposits. Download a longer history, or "
                "type the platform's cash balance in on the Accounts screen.")
    with db.tx() as c:
        c.execute("UPDATE accounts SET balance=?, last_updated=datetime('now'), notes=? "
                  "WHERE id=?", (round(cash, 2), note, account_id))
    return round(cash, 2)


def _base() -> str:
    return config.settings["base_currency"]


#: below this, dividing a return by the elapsed time inflates it into nonsense — three
#: months of +4% is not "16% a year", it is three months of +4%.
MIN_ANNUALISE_YEARS = 1.0


def _first_activity() -> dt.date | None:
    """The earliest date Mittens & Pence actually has evidence for.

    The old code read `portfolio_start_date` from settings, which ships with the anchor
    date from the original Investments 2 sheet (Aug 2021). On a fresh install with three
    months of imported history that produced "8.7% a year over 5.0 yrs" — a rate computed
    by dividing a real return by five years of data that do not exist.
    """
    candidates = []
    for sql in ("SELECT MIN(traded_on) d FROM trades",
                "SELECT MIN(paid_on) d FROM dividends",
                "SELECT MIN(posted_on) d FROM transactions"):
        try:
            row = db.one(sql)
        except Exception:
            continue
        if row and row["d"]:
            try:
                candidates.append(dt.date.fromisoformat(str(row["d"])[:10]))
            except ValueError:
                pass
    return min(candidates) if candidates else None


def _years_of_history() -> float:
    """How long the data actually spans, in years. 0 when there is nothing."""
    start = _first_activity()
    if start is None:
        # Nothing imported yet. Only then does the configured anchor mean anything, and
        # even then it is a stated intention rather than evidence.
        try:
            start = dt.date.fromisoformat(config.settings["portfolio_start_date"])
        except Exception:
            return 0.0
    days = (dt.date.today() - start).days
    return max(days, 0) / 365.25


def annualised(pct: float | None, years: float | None = None) -> float | None:
    """A per-year rate, or None when the history is too short to support one.

    Returning None rather than a big confident number is the whole point: the UI shows a
    dash and says how much history there is, which is the truthful answer.
    """
    if pct is None:
        return None
    years = _years_of_history() if years is None else years
    if not years or years < MIN_ANNUALISE_YEARS:
        return None
    return pct / years


def _years_since_start() -> float:
    """Kept for callers that want a divisor; never below one, so nothing inflates."""
    return max(_years_of_history(), MIN_ANNUALISE_YEARS)


def holdings_rows(account_ids: list[int] | None = None, member_id: int | None = None,
                  wrapper: str | None = None) -> list[dict]:
    """One enriched row per position, ready for the table and the Excel export."""
    base = _base()
    where, params = ["a.is_investment=1", "a.closed=0"], []
    if account_ids:
        where.append(f"h.account_id IN ({','.join('?' * len(account_ids))})")
        params += account_ids
    if member_id:
        where.append("a.member_id=?")
        params.append(member_id)
    if wrapper:
        where.append("a.account_type=?")
        params.append(wrapper)

    rows = db.rows(f"""
        SELECT h.*, i.symbol, i.exchange, i.name AS instrument_name, i.sector, i.currency AS inst_ccy,
               i.isin, i.asset_class, a.name AS account_name, a.account_type, a.currency AS acct_ccy,
               a.id AS acct_id, m.name AS member_name, c.institution_name
        FROM holdings h
        JOIN instruments i ON i.id = h.instrument_id
        JOIN accounts a ON a.id = h.account_id
        LEFT JOIN members m ON m.id = a.member_id
        LEFT JOIN connections c ON c.id = a.connection_id
        WHERE {' AND '.join(where)}
        ORDER BY i.symbol
    """, tuple(params))

    px = market.latest_prices()
    div_by = defaultdict(float)
    for d in db.rows("SELECT account_id, instrument_id, amount, currency FROM dividends"):
        div_by[(d["account_id"], d["instrument_id"])] += market.convert(
            d["amount"] or 0, d["currency"] or base, base)

    out = []
    for r in rows:
        p = px.get(r["instrument_id"])
        price_ccy = (p or {}).get("currency") or r["inst_ccy"] or base
        price_raw = (p or {}).get("price")
        price = market.convert(price_raw, price_ccy, base) if price_raw is not None else None
        shares = float(r["shares"] or 0)
        cost = market.convert(float(r["cost"] or 0), r["cost_currency"] or r["acct_ccy"] or base, base)
        # A crypto exchange tells you what you hold, never what you paid. Treating an
        # unknown cost as zero would report the whole position as profit, so return is
        # left as None and the UI shows a dash until a trade history fills the gap.
        cost_known = bool(r["cost_known"]) if "cost_known" in r.keys() else True
        value = (shares * price) if price is not None else None
        divs = div_by.get((r["account_id"], r["instrument_id"]), 0.0)
        price_change = (value - cost) if value is not None and cost_known else None
        total_value = ((value or 0) + divs) if value is not None else None
        money_made = ((total_value - cost) if total_value is not None and cost_known
                      else None)
        out.append({
            "holding_id": r["id"],
            "account_id": r["account_id"],
            "account_name": r["account_name"],
            "account_type": r["account_type"],
            "institution": r["institution_name"],
            "member": r["member_name"],
            "instrument_id": r["instrument_id"],
            "symbol": r["symbol"],
            "exchange": r["exchange"],
            "name": r["instrument_name"] or r["symbol"],
            "isin": r["isin"],
            "sector": r["sector"] or "Other",
            "asset_class": r["asset_class"],
            "buy_dates": r["buy_dates"],
            "shares": shares,
            "cost": cost,
            "price": price,
            "price_currency": base,
            "price_native": price_raw,
            "price_native_currency": price_ccy,
            "price_as_of": (p or {}).get("as_of"),
            "day_change_pct": (p or {}).get("change_pct"),
            "value": value,
            "price_change": price_change,
            "pct_change": (price_change / cost) if (price_change is not None and cost) else None,
            "dividends": divs,
            "overall_change": ((price_change + divs) / cost)
                              if (price_change is not None and cost) else None,
            "total_value": total_value,
            "money_made": money_made,
            "money_made_pct": (money_made / cost) if (money_made is not None and cost) else None,
            "stale": p is None,
        })
    return out


def cash_rows(account_ids: list[int] | None = None) -> list[dict]:
    base = _base()
    where, params = ["a.is_investment=1", "a.closed=0"], []
    if account_ids:
        where.append(f"a.id IN ({','.join('?' * len(account_ids))})")
        params += account_ids
    accts = db.rows(f"SELECT a.*, c.institution_name FROM accounts a "
                    f"LEFT JOIN connections c ON c.id=a.connection_id "
                    f"WHERE {' AND '.join(where)}", tuple(params))
    out = []
    for a in accts:
        interest = db.scalar(
            "SELECT SUM(amount) FROM cash_events WHERE account_id=? AND kind='INTEREST'",
            (a["id"],), 0.0) or 0.0
        deposits = db.scalar(
            "SELECT SUM(amount) FROM cash_events WHERE account_id=? AND kind IN ('DEPOSIT','WITHDRAWAL')",
            (a["id"],), 0.0) or 0.0
        out.append({
            "account_id": a["id"],
            "account": a["name"],
            "institution": a["institution_name"],
            "account_type": a["account_type"],
            "cash": market.convert(a["balance"] or 0.0, a["currency"] or base, base),
            "interest": market.convert(interest, a["currency"] or base, base),
            "net_deposits": market.convert(deposits, a["currency"] or base, base),
            "currency": base,
        })
    return out


def summarise(rows: list[dict], cash: list[dict] | None = None) -> dict:
    invested = sum(r["value"] or 0 for r in rows)
    cost = sum(r["cost"] or 0 for r in rows)
    dividends = sum(r["dividends"] or 0 for r in rows)
    price_change = sum(r["price_change"] or 0 for r in rows)
    gross = invested + dividends
    net = gross - cost
    years = _years_of_history()
    interest = sum(c["interest"] for c in (cash or []))
    cash_total = sum(c["cash"] for c in (cash or []))
    return {
        "positions": len(rows),
        "invested": invested,
        "cost": cost,
        "dividends": dividends,
        "gross": gross,
        "net": net,
        "net_pct": (net / gross) if gross else 0.0,
        # The dashboard shows net/cost as the headline; this is that same figure per
        # year, or None when the history is too short to divide by. Same number, same
        # denominator — a sub-line that annualises a *different* ratio invites the
        # reader to do arithmetic that doesn't work.
        "return_on_cost": (net / cost) if cost else 0.0,
        "return_on_cost_yr": annualised((net / cost) if cost else 0.0, years),
        "dividend_yield_on_cost": (dividends / cost) if cost else 0.0,
        "balanced_gain": net,
        "balanced_pct": (net / gross) if gross else 0.0,
        "balanced_pct_yr": annualised((net / gross) if gross else 0.0, years),
        "investment_gain": price_change,
        "investment_pct": (price_change / invested) if invested else 0.0,
        "investment_pct_yr": annualised((price_change / invested) if invested else 0.0, years),
        "cash": cash_total,
        "cash_interest": interest,
        "total_with_cash": gross + cash_total,
        "years": years,
        "currency": _base(),
    }


def wrapper_breakdown() -> dict:
    """ISA / GIA / SIPP / TFSA / other — the tabs of the original workbook."""
    out = {}
    types = db.rows("SELECT DISTINCT account_type FROM accounts WHERE is_investment=1 AND closed=0")
    for t in types:
        wt = t["account_type"]
        rows = holdings_rows(wrapper=wt)
        ids = [a["id"] for a in db.rows(
            "SELECT id FROM accounts WHERE is_investment=1 AND closed=0 AND account_type=?", (wt,))]
        out[wt] = {"summary": summarise(rows, cash_rows(ids)), "rows": rows}
    return out


def platform_breakdown() -> dict:
    """One block per broker account — the Freetrade and Trading 212 tabs."""
    out = {}
    for a in db.rows("SELECT a.*, c.institution_name FROM accounts a "
                     "LEFT JOIN connections c ON c.id=a.connection_id "
                     "WHERE a.is_investment=1 AND a.closed=0 ORDER BY a.name"):
        rows = holdings_rows(account_ids=[a["id"]])
        out[a["name"]] = {
            "account_id": a["id"],
            "institution": a["institution_name"],
            "account_type": a["account_type"],
            "summary": summarise(rows, cash_rows([a["id"]])),
            "rows": rows,
        }
    return out


def sold_rows() -> list[dict]:
    base = _base()
    rows = db.rows("""SELECT s.*, i.name AS instrument_name, i.sector
                      FROM sold_positions s LEFT JOIN instruments i ON i.id=s.instrument_id
                      ORDER BY s.sold_on DESC""")
    px = market.latest_prices()
    out = []
    for r in rows:
        cost = market.convert(r["cost"] or 0, r["currency"] or base, base)
        proceeds = market.convert(r["proceeds"] or 0, r["currency"] or base, base)
        divs = market.convert(r["dividends"] or 0, r["currency"] or base, base)
        made = proceeds + divs - cost
        # "If left in" — what the position would be worth today, the sheet's O column
        if_left = None
        p = px.get(r["instrument_id"]) if r["instrument_id"] else None
        if p and r["sell_shares"]:
            if_left = market.convert(p["price"], p["currency"], base) * float(r["sell_shares"])
        out.append({
            **{k: r[k] for k in ("id", "symbol", "exchange", "bought_on", "sold_on",
                                 "buy_shares", "sell_shares", "note", "auto")},
            "name": r["instrument_name"] or r["symbol"],
            "sector": r["sector"],
            "cost": cost,
            "proceeds": proceeds,
            "dividends": divs,
            "gain": proceeds - cost,
            "gain_pct": ((proceeds - cost) / cost) if cost else None,
            "total_made": made,
            "total_pct": (made / cost) if cost else None,
            "if_left_in": if_left,
            "missed_out": (proceeds - if_left) if if_left is not None else None,
            "currency": base,
        })
    return out


def sector_allocation(rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else holdings_rows()
    total = sum(r["value"] or 0 for r in rows) or 1.0
    agg = defaultdict(float)
    for r in rows:
        agg[r["sector"] or "Other"] += (r["value"] or 0)
    out = [{"sector": k, "value": v, "pct": v / total} for k, v in agg.items()]
    out.sort(key=lambda x: -x["value"])
    return out


def overall() -> dict:
    """The Overall tab: everything, plus the ISA-only line the original tracked."""
    rows = holdings_rows()
    cash = cash_rows()
    everything = summarise(rows, cash)
    isa_rows = holdings_rows(wrapper="isa")
    isa_ids = [a["id"] for a in db.rows(
        "SELECT id FROM accounts WHERE is_investment=1 AND account_type='isa'")]
    isa = summarise(isa_rows, cash_rows(isa_ids))
    sold = sold_rows()
    realised = sum(s["total_made"] or 0 for s in sold)
    avg_sold_pct = (sum(s["total_pct"] or 0 for s in sold) / len(sold)) if sold else 0.0
    return {
        "all": everything,
        "isa": isa,
        "sold": {"count": len(sold), "realised": realised, "avg_pct": avg_sold_pct},
        "sectors": sector_allocation(rows),
        "as_of": dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
        # the sheet's headline: net % including the average realised percentage
        "headline_net_pct": (everything["net"] / everything["cost"] if everything["cost"] else 0.0)
                            + avg_sold_pct,
        "headline_net_pct_yr": annualised(
            (everything["net"] / everything["cost"] if everything["cost"] else 0.0)
            + avg_sold_pct),
        "years": _years_of_history(),
        "history_from": (_first_activity().isoformat() if _first_activity() else None),
    }


def _mortgage_owed(account_id: int) -> float | None:
    """What a mortgage account probably owes today, projected from its last statement."""
    try:
        from . import mortgage as mortgage_engine
        rec = mortgage_engine.get(account_id)
        if not rec:
            return None
        return mortgage_engine.projected_balance(rec)["balance"]
    except Exception:
        return None


def net_worth() -> dict:
    """Everything Mittens & Pence knows about, investments and banking together."""
    base = _base()
    accounts = db.rows("SELECT * FROM accounts WHERE closed=0 AND include_in_net_worth=1")
    invest_rows = holdings_rows()
    investments = sum(r["value"] or 0 for r in invest_rows)
    groups = defaultdict(float)
    for a in accounts:
        bal = market.convert(a["balance"] or 0, a["currency"] or base, base)
        if a["is_investment"]:
            groups["Investment cash"] += bal
        elif a["account_type"] in ("credit", "loan", "mortgage"):
            # A mortgage statement arrives once a year, so the stored balance goes stale
            # for eleven months of it. The mortgage screen already projects forward from
            # it; net worth using the older figure would put two different debts on two
            # screens of the same app.
            owed = _mortgage_owed(a["id"])
            if owed is not None:
                bal = market.convert(owed, a["currency"] or base, base)
            groups["Debt"] += -abs(bal)
        elif a["account_type"] == "asset":
            groups["Other assets"] += bal
        elif a["account_type"] in ("savings", "isa_cash"):
            groups["Savings"] += bal
        else:
            groups["Current accounts"] += bal
    # Only when there is something to put in it. Setting the key unconditionally meant a
    # brand-new copy with no accounts and no holdings still reported one group, so the
    # Dashboard opened on "£0 · 1 group" — which reads as a figure that failed to load
    # rather than as an app nobody has told anything yet.
    if invest_rows:
        groups["Investments"] = investments
    total = sum(groups.values())
    return {"total": total, "groups": dict(groups), "currency": base,
            "as_of": dt.date.today().isoformat()}
