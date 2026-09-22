"""Budgets, per section and per line, with month-by-month tracking.

A budget can be attached to a whole section ("Food & Drink") or to one line inside
it ("Groceries"). Section budgets that also have line budgets underneath show both:
how the lines are doing, and whether the section as a whole is on track.

"Spent" excludes internal transfers and money moved into savings or investments,
because moving £500 into an ISA is not spending — it shows on its own row instead.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict

from .. import config, db
from ..market import prices as market


def month_bounds(period: str) -> tuple[str, str]:
    """'2026-08' -> ('2026-08-01','2026-08-31')."""
    y, m = (int(x) for x in period.split("-")[:2])
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def current_period() -> str:
    return dt.date.today().strftime("%Y-%m")


def shift_period(period: str, months: int) -> str:
    y, m = (int(x) for x in period.split("-")[:2])
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _period_range(period: str, granularity: str = "monthly") -> tuple[str, str]:
    if granularity == "monthly":
        return month_bounds(period)
    if granularity == "weekly":
        d = dt.date.fromisoformat(period)
        start = d - dt.timedelta(days=d.weekday())
        return start.isoformat(), (start + dt.timedelta(days=6)).isoformat()
    if granularity == "yearly":
        y = int(period[:4])
        return f"{y}-01-01", f"{y}-12-31"
    return month_bounds(period)


# ---------------------------------------------------------------------------
# Spending rollup
# ---------------------------------------------------------------------------

def spend_by_category(start: str, end: str, member_id: int | None = None,
                      account_ids: list[int] | None = None) -> dict:
    base = config.settings["base_currency"]
    where = ["t.posted_on BETWEEN ? AND ?"]
    params: list = [start, end]
    if member_id:
        where.append("a.member_id = ?")
        params.append(member_id)
    if account_ids:
        where.append(f"t.account_id IN ({','.join('?' * len(account_ids))})")
        params += account_ids

    rows = db.rows(f"""
        SELECT t.amount, t.currency, t.is_transfer, t.category_id,
               COALESCE(c.parent,'Other') AS parent, COALESCE(c.name,'Uncategorised') AS name,
               c.is_income, c.is_transfer AS cat_transfer, c.is_saving
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE {' AND '.join(where)} AND {db.NOT_SPLIT_PARENT}
    """, tuple(params))

    lines: dict[tuple, dict] = {}
    totals = {"income": 0.0, "spend": 0.0, "saving": 0.0, "transfers": 0.0, "net": 0.0,
              "mortgage_capital": 0.0}
    for r in rows:
        amt = market.convert(r["amount"] or 0, r["currency"] or base, base)
        key = (r["parent"], r["name"])
        entry = lines.setdefault(key, {"parent": r["parent"], "name": r["name"],
                                       "category_id": r["category_id"],
                                       "amount": 0.0, "count": 0,
                                       "is_income": bool(r["is_income"]),
                                       "is_transfer": bool(r["cat_transfer"]),
                                       "is_saving": bool(r["is_saving"])})
        entry["amount"] += amt
        entry["count"] += 1
        if r["is_transfer"] or r["cat_transfer"]:
            totals["transfers"] += amt
        elif r["is_income"]:
            totals["income"] += amt
        elif r["is_saving"]:
            totals["saving"] += abs(amt) if amt < 0 else 0
        elif amt < 0:
            totals["spend"] += -amt
        else:
            totals["income"] += amt
    # A mortgage payment is two things wearing one number. The interest left the
    # household; the capital moved from the current account into the house. Counting the
    # whole payment as spending overstates outgoings and hides the saving every month, so
    # the capital half is moved across — visibly, and only for a mortgage somebody has
    # deliberately set up and left the split switched on for.
    adjustments = []
    try:
        from . import mortgage as mortgage_engine
        capital = mortgage_engine.capital_repaid_between(start, end)
    except Exception:
        capital = {}
    if capital:
        for v in lines.values():
            amount = capital.get(v["category_id"])
            if not amount or v["is_income"] or v["is_transfer"]:
                continue
            # Never move more than actually went out; a projection can drift past a month
            # where the payment was missed or the statement hasn't caught up.
            moved = min(abs(amount), max(-v["amount"], 0.0))
            if moved <= 0:
                continue
            v["capital_repaid"] = round(moved, 2)
            totals["spend"] -= moved
            totals["saving"] += moved
            totals["mortgage_capital"] = totals.get("mortgage_capital", 0.0) + moved
            adjustments.append({"parent": v["parent"], "name": v["name"],
                                "amount": round(moved, 2)})

    totals["net"] = totals["income"] - totals["spend"] - totals["saving"]

    sections: dict[str, dict] = {}
    for (parent, name), v in lines.items():
        sec = sections.setdefault(parent, {"parent": parent, "amount": 0.0, "spend": 0.0,
                                           "count": 0, "lines": []})
        sec["amount"] += v["amount"]
        sec["count"] += v["count"]
        if not (v["is_income"] or v["is_transfer"]):
            sec["spend"] += max(-v["amount"], 0.0)
        sec["lines"].append(v)
    for s in sections.values():
        s["lines"].sort(key=lambda x: x["amount"])
    return {"totals": totals, "sections": sections, "currency": base,
            "start": start, "end": end, "mortgage_capital": adjustments}


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

def set_budget(amount: float, category_id: int | None = None, parent: str | None = None,
               scope: str = "household", scope_id: int | None = None,
               period: str = "monthly", rollover: bool = False,
               currency: str | None = None, notes: str | None = None) -> int:
    if not category_id and not parent:
        raise ValueError("a budget needs either a category or a section")
    currency = currency or config.settings["base_currency"]
    existing = db.one(
        "SELECT id FROM budgets WHERE scope=? AND IFNULL(scope_id,-1)=IFNULL(?,-1) "
        "AND IFNULL(category_id,-1)=IFNULL(?,-1) AND IFNULL(parent_only,'')=IFNULL(?,'') "
        "AND period=?", (scope, scope_id, category_id, parent, period))
    with db.tx() as c:
        if existing:
            c.execute("UPDATE budgets SET amount=?, rollover=?, currency=?, notes=? WHERE id=?",
                      (amount, int(rollover), currency, notes, existing["id"]))
            return existing["id"]
        cur = c.execute(
            "INSERT INTO budgets(scope,scope_id,category_id,parent_only,period,amount,currency,"
            "rollover,notes) VALUES(?,?,?,?,?,?,?,?,?)",
            (scope, scope_id, category_id, parent, period, amount, currency,
             int(rollover), notes))
        return cur.lastrowid


def delete_budget(budget_id: int):
    with db.tx() as c:
        c.execute("DELETE FROM budgets WHERE id=?", (budget_id,))


def list_budgets(scope: str | None = None, scope_id: int | None = None) -> list[dict]:
    where, params = [], []
    if scope:
        where.append("b.scope=?")
        params.append(scope)
    if scope_id is not None:
        where.append("b.scope_id=?")
        params.append(scope_id)
    sql = ("SELECT b.*, c.parent AS cat_parent, c.name AS cat_name FROM budgets b "
           "LEFT JOIN categories c ON c.id=b.category_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.rows(sql + " ORDER BY COALESCE(b.parent_only, c.parent), c.name", tuple(params))


def _capital_note(budget: dict, period: str, start: str, end: str) -> float | None:
    """How much of this budget line's spending was mortgage capital, if any."""
    try:
        from . import mortgage as mortgage_engine
        capital = mortgage_engine.capital_repaid_between(start, end)
    except Exception:
        return None
    if not capital:
        return None
    if budget["category_id"] and budget["category_id"] in capital:
        return round(capital[budget["category_id"]], 2)
    if budget["parent_only"]:
        total = 0.0
        for cid, amount in capital.items():
            cat = db.one("SELECT parent FROM categories WHERE id=?", (cid,))
            if cat and cat["parent"] == budget["parent_only"]:
                total += amount
        return round(total, 2) or None
    return None


def status(period: str | None = None, member_id: int | None = None) -> dict:
    """The budget screen: every budget with spent, remaining, pace and a verdict."""
    period = period or current_period()
    start, end = month_bounds(period)
    today = dt.date.today()
    p_start = dt.date.fromisoformat(start)
    p_end = dt.date.fromisoformat(end)
    days_total = (p_end - p_start).days + 1
    days_gone = min(max((today - p_start).days + 1, 0), days_total) if today >= p_start else 0
    pace = (days_gone / days_total) if days_total else 1.0
    is_current = p_start <= today <= p_end

    spend = spend_by_category(start, end, member_id=member_id)
    by_cat = {}
    by_parent = defaultdict(float)
    for sec in spend["sections"].values():
        for line in sec["lines"]:
            if line["is_income"] or line["is_transfer"]:
                continue
            amt = max(-line["amount"], 0.0)
            if line["category_id"]:
                by_cat[line["category_id"]] = amt
            by_parent[line["parent"]] += amt

    budgets = list_budgets("household" if member_id is None else "member",
                           member_id if member_id is not None else None)
    if member_id is not None:
        budgets = budgets + list_budgets("household")

    items = []
    for b in budgets:
        if b["category_id"]:
            spent = by_cat.get(b["category_id"], 0.0)
            label = f"{b['cat_parent']} › {b['cat_name']}"
            section = b["cat_parent"]
        else:
            spent = by_parent.get(b["parent_only"], 0.0)
            label = b["parent_only"]
            section = b["parent_only"]
        amount = b["amount"] + (_rollover_amount(b, period) if b["rollover"] else 0.0)
        remaining = amount - spent
        used = (spent / amount) if amount else 0.0
        expected = amount * pace
        if used >= 1.0:
            verdict = "over"
        elif is_current and spent > expected * 1.1:
            verdict = "ahead-of-pace"
        elif used >= 0.85:
            verdict = "close"
        else:
            verdict = "on-track"
        # The budget line keeps the whole payment as spent — a Mortgage budget is
        # usually set at the full amount, and halving what counts against it would make
        # it read as permanently 40% used. But the split is worth saying out loud, so the
        # line carries the capital part as a note rather than as arithmetic.
        capital = _capital_note(b, period, start, end)
        items.append({
            "capital_part": capital,
            "budget_id": b["id"], "label": label, "section": section,
            "category_id": b["category_id"], "parent_only": b["parent_only"],
            "scope": b["scope"], "scope_id": b["scope_id"],
            "amount": amount, "base_amount": b["amount"], "spent": spent,
            "remaining": remaining, "used": used, "expected": expected,
            "verdict": verdict, "rollover": bool(b["rollover"]),
            "currency": b["currency"], "notes": b["notes"],
            "days_left": max(days_total - days_gone, 0) if is_current else None,
            "daily_allowance": (remaining / (days_total - days_gone))
                               if (is_current and days_total - days_gone > 0) else None,
        })
    items.sort(key=lambda i: (-i["used"], i["label"]))

    # A line budget inside an already-budgeted section would otherwise be counted twice
    # in the totals, which is how "unbudgeted spending" ends up negative.
    budgeted_sections = {i["section"] for i in items if not i["category_id"]}
    def counts_once(i):
        return not (i["category_id"] and i["section"] in budgeted_sections)
    budgeted = sum(i["amount"] for i in items if counts_once(i))
    spent_budgeted = sum(i["spent"] for i in items if counts_once(i))
    return {
        "period": period, "start": start, "end": end,
        "days_total": days_total, "days_gone": days_gone, "pace": pace,
        "is_current": is_current,
        "items": items,
        "totals": {
            "budgeted": budgeted,
            "spent_in_budgets": spent_budgeted,
            "remaining": budgeted - spent_budgeted,
            "all_spend": spend["totals"]["spend"],
            # Budget lines still count the whole mortgage payment, so add the capital
            # back before subtracting them, or "unbudgeted" comes out short by it.
            "unbudgeted": max(spend["totals"]["spend"]
                              + spend["totals"].get("mortgage_capital", 0.0)
                              - spent_budgeted, 0.0),
            "mortgage_capital": spend["totals"].get("mortgage_capital", 0.0),
            "nested_budgets": sum(1 for i in items if not counts_once(i)),
            "income": spend["totals"]["income"],
            "saving": spend["totals"]["saving"],
            "net": spend["totals"]["net"],
        },
        "currency": spend["currency"],
        "sections": spend["sections"],
    }


def _rollover_amount(budget: dict, period: str) -> float:
    """Unspent budget carried from the previous month (only if rollover is on)."""
    prev = shift_period(period, -1)
    start, end = month_bounds(prev)
    spend = spend_by_category(start, end)
    if budget["category_id"]:
        spent = 0.0
        for sec in spend["sections"].values():
            for line in sec["lines"]:
                if line["category_id"] == budget["category_id"]:
                    spent = max(-line["amount"], 0.0)
    else:
        sec = spend["sections"].get(budget["parent_only"])
        spent = sec["spend"] if sec else 0.0
    return max(budget["amount"] - spent, 0.0)


def suggest_budgets(months: int = 3, member_id: int | None = None) -> list[dict]:
    """Propose a starting budget per section from what has actually been spent."""
    period = current_period()
    totals: dict[str, list[float]] = defaultdict(list)
    for i in range(1, months + 1):
        p = shift_period(period, -i)
        start, end = month_bounds(p)
        s = spend_by_category(start, end, member_id=member_id)
        for parent, sec in s["sections"].items():
            if parent in config.NON_SPEND_PARENTS:
                continue
            totals[parent].append(sec["spend"])
    out = []
    for parent, vals in totals.items():
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        if avg < 1:
            continue
        out.append({"parent": parent, "average": avg, "months": len(vals),
                    "suggested": round(avg / 5) * 5 if avg > 50 else round(avg, 2),
                    "high": max(vals), "low": min(vals)})
    out.sort(key=lambda x: -x["average"])
    return out


def history(months: int = 12, member_id: int | None = None) -> list[dict]:
    """Spend per section for the last N months — feeds the trend chart."""
    period = current_period()
    out = []
    for i in range(months - 1, -1, -1):
        p = shift_period(period, -i)
        start, end = month_bounds(p)
        s = spend_by_category(start, end, member_id=member_id)
        out.append({
            "period": p,
            "income": s["totals"]["income"],
            "spend": s["totals"]["spend"],
            "saving": s["totals"]["saving"],
            "net": s["totals"]["net"],
            "sections": {k: v["spend"] for k, v in s["sections"].items()},
        })
    return out
