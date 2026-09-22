"""A mortgage is not a bank account with a minus sign in front of it.

Adding one as an ordinary account gets you a number that makes net worth correct and
tells you nothing else. The things a household actually wants to know are all
derivable, and none of them fall out of a balance:

  * **What the payment is really doing.** £1,180 leaves the current account; perhaps
    £700 of it repays capital and £480 is interest. Only the interest is spending. The
    capital moved from one pocket to another, exactly like paying into an ISA — which
    this app already declines to count as spending.
  * **When the cheap rate ends.** For anyone on a fix, that date is the most consequential
    thing in their finances, and it is nowhere on a bank statement.
  * **What an overpayment is worth.** Not "the balance goes down by £200" — that is
    obvious and uninteresting. The number worth having is the interest it saves over the
    remaining term, and the months it takes off the end.
  * **The equity.** House minus mortgage, which needs both halves.

The maths is the standard amortising annuity. Deliberately no library: the formula is
four lines, and a dependency that has to be frozen into a Windows build should earn its
place.

**Balances are projected, and said to be.** A UK mortgage statement arrives once a year.
Between statements Mittens & Pence shows a projection from the last figure somebody actually saw,
labelled as one, with the date it was last confirmed. The alternative — showing a
year-old number as though it were today's — is the same error as an account claiming
£0.00 when nobody knows the balance.
"""

from __future__ import annotations

import datetime as dt

from .. import db

#: Below this, treat a rate as zero rather than dividing by something meaningless.
EPS = 1e-9
#: How far a projection is allowed to run before it is refusing to answer instead.
MAX_MONTHS = 600


# ---------------------------------------------------------------------------
# the maths
# ---------------------------------------------------------------------------

def monthly_rate(annual_pct: float | None) -> float:
    """A nominal annual rate divided by twelve, which is what lenders actually charge.

    Not the twelfth root of (1 + r) — that would be the effective rate, and using it
    would quietly disagree with every statement the lender sends.
    """
    return (annual_pct or 0.0) / 100.0 / 12.0


def payment_for(balance: float, annual_pct: float | None, months: int) -> float | None:
    """The level monthly payment that clears `balance` over `months`."""
    if not balance or not months or months <= 0:
        return None
    r = monthly_rate(annual_pct)
    if r < EPS:
        return balance / months
    factor = (1 + r) ** months
    return balance * r * factor / (factor - 1)


def months_to_clear(balance: float, annual_pct: float | None, payment: float) -> int | None:
    """How many payments of `payment` clear `balance`. None when they never would.

    The None case is the one that matters: at 5% on £200,000 the interest alone is £833 a
    month, so a £700 payment never clears anything and the honest answer is to say so
    rather than return a very large number.
    """
    if not balance or balance <= 0:
        return 0
    if not payment or payment <= 0:
        return None
    r = monthly_rate(annual_pct)
    if r < EPS:
        return int(-(-balance // payment))
    if payment <= balance * r + 0.005:
        return None                      # the payment never touches the capital
    import math
    n = math.log(payment / (payment - balance * r)) / math.log(1 + r)
    return min(int(math.ceil(n - 1e-9)), MAX_MONTHS)


def amortise(balance: float, annual_pct: float | None, payment: float,
             months: int | None = None, start: dt.date | None = None,
             overpayment: float = 0.0, interest_only: bool = False) -> list[dict]:
    """Month by month: what is charged, what is paid, what is left."""
    rows = []
    r = monthly_rate(annual_pct)
    left = float(balance or 0)
    when = start or dt.date.today().replace(day=1)
    cap = months if months else MAX_MONTHS
    for i in range(cap):
        if left <= 0.005:
            break
        interest = left * r
        due = (interest if interest_only else payment) + overpayment
        capital = due - interest
        if capital <= 0 and not interest_only:
            # The payment does not cover the interest. Report it once and stop, rather
            # than emitting five hundred rows of a debt that grows for ever.
            rows.append({"n": i + 1, "on": _add_months(when, i).isoformat(),
                         "interest": round(interest, 2), "capital": round(capital, 2),
                         "payment": round(due, 2), "balance": round(left - capital, 2),
                         "never_clears": True})
            break
        if capital > left:
            capital = left
            due = capital + interest
        left -= capital
        rows.append({"n": i + 1, "on": _add_months(when, i).isoformat(),
                     "interest": round(interest, 2), "capital": round(capital, 2),
                     "payment": round(due, 2), "balance": round(max(left, 0), 2)})
    return rows


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.date(y, m, day)


def _months_between(a: dt.date, b: dt.date) -> int:
    return max((b.year - a.year) * 12 + (b.month - a.month), 0)


# ---------------------------------------------------------------------------
# reading one out of the database
# ---------------------------------------------------------------------------

def get(account_id: int) -> dict | None:
    return db.one("SELECT * FROM mortgages WHERE account_id=?", (int(account_id),))


def all_mortgages() -> list[dict]:
    return db.rows("""SELECT m.*, a.name AS account, a.currency, a.closed
                      FROM mortgages m JOIN accounts a ON a.id=m.account_id
                      WHERE a.closed=0 ORDER BY a.name""")


def _anchor(m: dict) -> tuple[float | None, dt.date | None, str]:
    """The most recent balance anybody actually confirmed, and where it came from."""
    if m["statement_balance"] is not None and m["statement_on"]:
        return float(m["statement_balance"]), _d(m["statement_on"]), "statement"
    if m["original_amount"] and m["started_on"]:
        return float(m["original_amount"]), _d(m["started_on"]), "the original loan"
    return None, None, "nothing yet"


def projected_balance(m: dict, on: dt.date | None = None) -> dict:
    """What is probably left today, and how much of that is a projection.

    Never presented as fact: the answer carries the date it was last confirmed and how
    many payments have been assumed since.
    """
    on = on or dt.date.today()
    balance, since, source = _anchor(m)
    if balance is None:
        return {"balance": None, "confirmed_on": None, "projected_months": 0,
                "source": source, "estimated": True}
    months = _months_between(since, on)
    if months == 0:
        return {"balance": round(balance, 2), "confirmed_on": since.isoformat(),
                "projected_months": 0, "source": source, "estimated": source != "statement"}

    payment = m["monthly_payment"] or payment_for(balance, m["rate"], m["term_months"] or 0)
    if not payment:
        return {"balance": round(balance, 2), "confirmed_on": since.isoformat(),
                "projected_months": 0, "source": source, "estimated": True}

    over = db.scalar("SELECT SUM(amount) FROM mortgage_events WHERE mortgage_id=? "
                     "AND kind='overpayment' AND happened_on > ?",
                     (m["id"], since.isoformat()), 0) or 0
    rows = amortise(balance, m["rate"], payment, months, since,
                    interest_only=(m["repayment_type"] == "interest_only"))
    left = rows[-1]["balance"] if rows else balance
    left = max(left - abs(over), 0)
    return {"balance": round(left, 2), "confirmed_on": since.isoformat(),
            "projected_months": months, "source": source, "estimated": True,
            "overpaid_since": round(abs(over), 2)}


def summarise(m: dict, on: dt.date | None = None) -> dict:
    """Everything the screen needs about one mortgage."""
    on = on or dt.date.today()
    proj = projected_balance(m, on)
    balance = proj["balance"]
    payment = m["monthly_payment"]
    interest_only = m["repayment_type"] == "interest_only"

    if payment is None and balance is not None and m["term_months"] and m["started_on"]:
        left_months = max(m["term_months"] - _months_between(_d(m["started_on"]), on), 1)
        payment = payment_for(balance, m["rate"], left_months)

    months_left = None
    payoff = None
    if balance is not None and payment:
        if interest_only:
            # An interest-only mortgage never clears itself; the term is the term.
            if m["started_on"] and m["term_months"]:
                months_left = max(m["term_months"] - _months_between(_d(m["started_on"]), on), 0)
        else:
            months_left = months_to_clear(balance, m["rate"], payment)
        if months_left is not None:
            # The date of the LAST payment, not the month after it. The schedule starts
            # with this month's payment, so n payments end at month n-1 — and the card
            # and the chart underneath it have to name the same month.
            payoff = _add_months(on.replace(day=1), max(months_left - 1, 0)).isoformat()

    # This month's split. The reason the whole module exists: a payment is two different
    # things wearing one number.
    interest = capital = None
    if balance is not None and payment:
        interest = round(balance * monthly_rate(m["rate"]), 2)
        capital = None if interest_only else round(payment - interest, 2)

    total_interest = None
    if balance is not None and payment and months_left and not interest_only:
        rows = amortise(balance, m["rate"], payment, months_left, on)
        total_interest = round(sum(r["interest"] for r in rows), 2)

    paid_off = None
    if m["original_amount"] and balance is not None and m["original_amount"] > 0:
        paid_off = max(0.0, min(1.0, 1 - balance / float(m["original_amount"])))

    equity = ltv = None
    value = _property_value(m)
    if value and balance is not None:
        equity = round(value - balance, 2)
        ltv = round(balance / value, 4) if value else None

    # The date the contract says it ends, which is not always the date the payment
    # implies. When the two disagree by more than a year, one of the numbers on file is
    # wrong, and saying so is more use than quietly showing whichever was asked for.
    term_ends = None
    if m["started_on"] and m["term_months"]:
        term_ends = _add_months(_d(m["started_on"]), int(m["term_months"])).isoformat()
    mismatch = ahead = None
    if term_ends and payoff:
        gap = _months_between(_d(min(term_ends, payoff)), _d(max(term_ends, payoff)))
        if payoff > term_ends and gap >= 12:
            # The payment is too small to clear it by the contractual end. Either a
            # figure on file is wrong or there is a real shortfall — both worth saying.
            mismatch = {"months": gap, "term_ends_on": term_ends, "payoff_on": payoff}
        elif payoff < term_ends and gap >= 12:
            ahead = gap                   # finishing early is news, not a warning

    return {
        "term_ends_on": term_ends, "term_mismatch": mismatch,
        "ahead_by_months": ahead,
        "id": m["id"], "account_id": m["account_id"],
        "lender": m["lender"], "currency": m.get("currency"),
        "balance": balance, "confirmed_on": proj["confirmed_on"],
        "projected_months": proj["projected_months"], "estimated": proj["estimated"],
        "balance_source": proj["source"],
        "rate": m["rate"], "rate_type": m["rate_type"],
        "fixed_until": m["fixed_until"], "revert_rate": m["revert_rate"],
        "days_to_rate_change": _days_until(m["fixed_until"], on),
        "monthly_payment": round(payment, 2) if payment else None,
        "interest_this_month": interest, "capital_this_month": capital,
        "repayment_type": m["repayment_type"],
        "months_left": months_left, "payoff_on": payoff,
        "interest_remaining": total_interest,
        "paid_off": paid_off, "original_amount": m["original_amount"],
        "property_value": value, "equity": equity, "ltv": ltv,
        "split_payments": bool(m["split_payments"]),
        # A payment that doesn't cover the interest is the one situation worth shouting
        # about, because the debt is growing.
        "never_clears": bool(balance and payment and not interest_only
                             and months_left is None),
    }


def _property_value(m: dict) -> float | None:
    if m["property_value"]:
        return float(m["property_value"])
    if m["property_account_id"]:
        a = db.one("SELECT balance FROM accounts WHERE id=?", (m["property_account_id"],))
        if a and a["balance"] is not None:
            return float(a["balance"])
    return None


def _days_until(iso: str | None, on: dt.date) -> int | None:
    if not iso:
        return None
    try:
        return (_d(iso) - on).days
    except ValueError:
        return None


def _d(s) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


# ---------------------------------------------------------------------------
# what an overpayment is actually worth
# ---------------------------------------------------------------------------

def overpayment_effect(m: dict, amount: float, kind: str = "monthly",
                       on: dt.date | None = None) -> dict:
    """Months saved and interest saved — not "the balance goes down by £200".

    `kind` is 'monthly' (every month from now) or 'once' (a lump sum today).
    """
    on = on or dt.date.today()
    base = summarise(m, on)
    balance, payment = base["balance"], base["monthly_payment"]
    if balance is None or not payment or base["repayment_type"] == "interest_only":
        return {"available": False,
                "why": "Mittens & Pence needs a balance and a monthly payment before it can work "
                       "out what an overpayment would save."}
    amount = abs(float(amount or 0))
    if amount <= 0:
        return {"available": False, "why": "Enter an amount to overpay."}

    before_months = months_to_clear(balance, m["rate"], payment)
    if before_months is None:
        return {"available": False,
                "why": "The current payment doesn't cover the interest, so there is no "
                       "term to shorten yet."}
    before = amortise(balance, m["rate"], payment, before_months, on)
    before_interest = sum(r["interest"] for r in before)

    if kind == "once":
        after_balance = max(balance - amount, 0)
        after_months = months_to_clear(after_balance, m["rate"], payment) or 0
        after = amortise(after_balance, m["rate"], payment, after_months, on)
    else:
        after_months = months_to_clear(balance, m["rate"], payment + amount) or 0
        after = amortise(balance, m["rate"], payment + amount, after_months, on)
    after_interest = sum(r["interest"] for r in after)

    return {
        "available": True, "kind": kind, "amount": round(amount, 2),
        "months_saved": max(before_months - len(after), 0),
        "interest_saved": round(max(before_interest - after_interest, 0), 2),
        "new_payoff_on": (_add_months(on.replace(day=1), len(after)).isoformat()
                          if after else None),
        "old_payoff_on": _add_months(on.replace(day=1), before_months).isoformat(),
        "new_payment": round(payment + amount, 2) if kind == "monthly" else round(payment, 2),
        # An overpayment charge is a real thing and Mittens & Pence has no way to know the limit,
        # so it says so rather than implying the saving is free.
        "caveat": "Most lenders cap penalty-free overpayments (often 10% of the balance a "
                  "year) while you're on a fixed rate. Check yours before setting this up.",
    }


def rate_change_effect(m: dict, new_rate: float, on: dt.date | None = None) -> dict:
    """What the payment becomes when the fix ends — the question behind the date."""
    on = on or dt.date.today()
    base = summarise(m, on)
    balance = base["balance"]
    if balance is None:
        return {"available": False}
    months_left = base["months_left"]
    if not months_left:
        if m["term_months"] and m["started_on"]:
            months_left = max(m["term_months"] - _months_between(_d(m["started_on"]), on), 1)
        else:
            return {"available": False}
    now = base["monthly_payment"]
    then = (balance * monthly_rate(new_rate) if m["repayment_type"] == "interest_only"
            else payment_for(balance, new_rate, months_left))
    return {"available": True, "new_rate": new_rate,
            "payment_now": round(now, 2) if now else None,
            "payment_then": round(then, 2) if then else None,
            "difference": round((then or 0) - (now or 0), 2),
            "over_months": months_left}


# ---------------------------------------------------------------------------
# the capital part of a payment is not spending
# ---------------------------------------------------------------------------

def capital_repaid_between(start: str, end: str) -> dict:
    """How much capital each mortgage repaid in a window, by category.

    Returns {category_id: amount}. Used to move the capital half of a mortgage payment
    out of "spending" and into "saving", where it belongs: £700 of a £1,180 payment did
    not leave the household, it moved from the current account into the house. Counting
    it as spending overstates outgoings and hides the saving, every single month.

    The split is taken from the amortisation schedule rather than from the transactions,
    because a bank statement shows one number and never says how it divided.
    """
    out: dict[int, float] = {}
    s, e = _d(start), _d(end)
    for m in all_mortgages():
        if not m["split_payments"] or not m["payment_category_id"]:
            continue
        if m["repayment_type"] == "interest_only":
            continue                      # nothing is being repaid, so nothing to move
        summary = summarise(m, s)
        balance, payment = summary["balance"], summary["monthly_payment"]
        if balance is None or not payment:
            continue
        months = _months_between(s, e) + 1
        rows = amortise(balance, m["rate"], payment, months, s,
                        interest_only=False)
        capital = sum(r["capital"] for r in rows if s <= _d(r["on"]) <= e)
        if capital > 0:
            cid = int(m["payment_category_id"])
            out[cid] = out.get(cid, 0.0) + round(capital, 2)
    return out
