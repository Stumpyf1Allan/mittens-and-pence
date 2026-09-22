"""Transactions typed in by hand.

Most of what Mittens & Pence holds arrives from a file or an API, identified by a fingerprint
that makes re-uploading safe. A typed entry has no such provenance, and that difference
drives every decision in here:

* **It can be corrected.** A mistyped amount cannot be fixed by re-importing, so a
  hand-typed row may be edited and deleted. An imported one may not — its fingerprint
  says "this is what the statement said", and letting it be edited would quietly break
  the guarantee that re-uploading a file changes nothing.

* **It gets out of the way when the real thing arrives.** Somebody notes a £40 cash
  withdrawal on the day it happens; three weeks later the statement carrying that same
  £40 is imported. Two rows, one payment, every budget wrong, and nothing on screen
  looks broken. So an import *supersedes* the note it matches instead of landing beside
  it — see `supersede_with_import`.

* **The sign is never typed.** "Spent" and "Received" are the two things a person
  actually knows; a minus sign in a text box is the single most common way to enter a
  number backwards. Callers pass `direction`, not a signed number.
"""

from __future__ import annotations

import datetime as dt

from .. import config, db
from ..engine import categorise

#: How far apart a typed note and the statement row it refers to may be. A card payment
#: can take a few days to post, and people write the date they spent the money.
MATCH_WINDOW_DAYS = 6


class ManualError(ValueError):
    """Something about the entry is wrong, phrased for the person who typed it."""


# ---------------------------------------------------------------------------
# writing one
# ---------------------------------------------------------------------------

def _clean_date(value) -> str:
    raw = str(value or "").strip()[:10]
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        raise ManualError("That date isn't one Mittens & Pence can read — use the date picker.")
    if d > dt.date.today() + dt.timedelta(days=1):
        # Tomorrow is allowed for a payment scheduled overnight; next year is a typo.
        raise ManualError("That date is in the future.")
    if d.year < 1990:
        raise ManualError("That date is a very long time ago — check the year.")
    return d.isoformat()


def _clean_amount(value, direction: str) -> float:
    try:
        n = abs(float(str(value).replace(",", "").replace("£", "").replace("R", "").strip()))
    except (TypeError, ValueError):
        raise ManualError("That amount isn't a number.")
    if n <= 0:
        raise ManualError("An amount of nothing isn't a transaction.")
    if direction not in ("out", "in"):
        raise ManualError("Say whether the money went out or came in.")
    return -n if direction == "out" else n


def guess_category(description: str, amount: float, account_id: int | None = None):
    """What the categoriser would have made of this description, before it is saved.

    Typing "Tesco" and having Groceries appear is the difference between filling in a
    form and answering a question. It is a default, not a decision — the dropdown is
    right there.
    """
    text = (description or "").strip()
    if not text:
        return None
    for r in categorise._rules():
        if r["account_id"] and account_id and r["account_id"] != account_id:
            continue
        if categorise._match(r, text, amount or 0):
            return r["category_id"]
    return None


def add(account_id: int, posted_on, description: str, amount, direction: str,
        category_id: int | None = None, currency: str | None = None,
        reference: str | None = None, notes: str | None = None,
        transfer_to_account_id: int | None = None,
        transfer_amount=None) -> dict:
    """Write one hand-typed transaction, and its other half if it was a transfer."""
    acct = db.one("SELECT * FROM accounts WHERE id=?", (int(account_id),))
    if not acct:
        raise ManualError("Pick an account for this transaction.")
    desc = (description or "").strip()
    if not desc:
        raise ManualError("Give it a description — it's what you'll search for later.")

    date = _clean_date(posted_on)
    signed = _clean_amount(amount, direction)
    ccy = (currency or acct["currency"] or config.settings["base_currency"]).upper()

    other = None
    if transfer_to_account_id:
        other = db.one("SELECT * FROM accounts WHERE id=?", (int(transfer_to_account_id),))
        if not other:
            raise ManualError("That account to transfer to no longer exists.")
        if int(transfer_to_account_id) == int(account_id):
            raise ManualError("A transfer needs two different accounts.")

    # Work out the far leg BEFORE writing anything. Validating halfway through left the
    # money leaving one account with nothing arriving in the other — an orphan half of a
    # transfer, which is worse than the error that caused it.
    arriving = other_ccy = None
    if other is not None:
        other_ccy = (other["currency"] or ccy).upper()
        if other_ccy == ccy:
            # The far leg always carries the opposite sign: money out of one account is
            # money into the other.
            arriving = -signed
        else:
            # £500 out of a UK account is not R500 into a South African one, and only the
            # person who made the transfer knows what rate their bank used. Mirroring the
            # number would invent an exchange rate and put a wrong balance on screen.
            if transfer_amount in (None, ""):
                raise ManualError(
                    f"Those two accounts are in different currencies ({ccy} and "
                    f"{other_ccy}), so Mittens & Pence needs the amount that actually arrived.")
            arriving = _clean_amount(transfer_amount, "in" if signed < 0 else "out")

    cat = category_id
    if cat is None and not transfer_to_account_id:
        cat = guess_category(desc, signed, int(account_id))

    first = _insert(int(account_id), date, desc, signed, ccy, cat, reference, notes)

    made = [first]
    if other is not None:
        made.append(_insert(int(transfer_to_account_id), date, desc, arriving,
                            other_ccy, None, reference, notes))
        # Pair them here rather than leaving it to the detector. The detector guesses at
        # imported rows by matching amounts within a few days, and it can pair the wrong
        # £500 when several exist — but this transfer was *declared*, so there is nothing
        # to guess at.
        _pair(made[0], made[1])

    rows = [db.one("SELECT * FROM transactions WHERE id=?", (i,)) for i in made]
    return {"ids": made, "rows": [r for r in rows if r],
            "transfer": other is not None}


def _pair(a: int, b: int):
    cat = db.category_id("Transfers", "Between own accounts")
    with db.tx() as c:
        c.execute("UPDATE transactions SET transfer_pair=?, is_transfer=1, "
                  "category_id=COALESCE(category_id, ?) WHERE id=?", (b, cat, a))
        c.execute("UPDATE transactions SET transfer_pair=?, is_transfer=1, "
                  "category_id=COALESCE(category_id, ?) WHERE id=?", (a, cat, b))


def _insert(account_id: int, date: str, desc: str, signed: float, ccy: str,
            category_id, reference, notes) -> int:
    # The fingerprint carries a timestamp so two genuinely identical entries — two £3
    # coffees on the same day — both survive. An imported row is de-duplicated against
    # the statement; a typed one is only ever de-duplicated against itself, and a person
    # who types the same thing twice usually means it.
    fp = db.fingerprint("manual", date, desc[:80], round(signed, 2),
                        dt.datetime.now().isoformat(timespec="microseconds"))
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO transactions(account_id,posted_on,description,amount,currency,"
            " category_id,category_source,reference,raw_json,fingerprint,entry_source)"
            " VALUES(?,?,?,?,?,?,?,?,?,?, 'manual')",
            (account_id, date, desc, signed, ccy, category_id,
             "manual" if category_id else "auto", (reference or "").strip() or None,
             _notes_json(notes), fp))
        return cur.lastrowid


def _notes_json(notes) -> str | None:
    import json
    text = (notes or "").strip()
    return json.dumps({"note": text}) if text else None


# ---------------------------------------------------------------------------
# correcting and removing one
# ---------------------------------------------------------------------------

def _must_be_manual(tid: int) -> dict:
    tx = db.one("SELECT * FROM transactions WHERE id=?", (int(tid),))
    if not tx:
        raise ManualError("That transaction is no longer there.")
    if (tx["entry_source"] or "import") != "manual":
        raise ManualError(
            "That one came from a statement, so Mittens & Pence keeps it exactly as the bank "
            "sent it — editing it would break the promise that re-uploading a file "
            "changes nothing. You can change its category, or add a correcting entry.")
    return tx


def update(tid: int, **fields) -> dict:
    tx = _must_be_manual(tid)
    sets, params = [], []

    if "posted_on" in fields:
        sets.append("posted_on=?"); params.append(_clean_date(fields["posted_on"]))
    if "description" in fields:
        desc = (fields["description"] or "").strip()
        if not desc:
            raise ManualError("Give it a description — it's what you'll search for later.")
        sets.append("description=?"); params.append(desc)
    if "amount" in fields or "direction" in fields:
        direction = fields.get("direction") or ("out" if (tx["amount"] or 0) < 0 else "in")
        amount = fields.get("amount", abs(tx["amount"] or 0))
        sets.append("amount=?"); params.append(_clean_amount(amount, direction))
    if "currency" in fields and fields["currency"]:
        sets.append("currency=?"); params.append(str(fields["currency"]).upper())
    if "category_id" in fields:
        cid = fields["category_id"]
        sets.append("category_id=?"); params.append(int(cid) if cid else None)
        sets.append("category_source=?"); params.append("manual" if cid else "auto")
    if "reference" in fields:
        sets.append("reference=?"); params.append((fields["reference"] or "").strip() or None)
    if "notes" in fields:
        sets.append("raw_json=?"); params.append(_notes_json(fields["notes"]))

    if not sets:
        return tx
    with db.tx() as c:
        c.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id=?",
                  tuple(params) + (int(tid),))
    return db.one("SELECT * FROM transactions WHERE id=?", (int(tid),))


def delete(tid: int) -> dict:
    tx = _must_be_manual(tid)
    pair = tx["transfer_pair"]
    removed = [int(tid)]
    with db.tx() as c:
        c.execute("DELETE FROM transactions WHERE id=?", (int(tid),))
        # The other half of a transfer typed as a pair goes too — half a transfer is
        # money appearing from nowhere.
        if pair:
            other = c.execute("SELECT entry_source FROM transactions WHERE id=?",
                              (pair,)).fetchone()
            if other and (other["entry_source"] or "import") == "manual":
                c.execute("DELETE FROM transactions WHERE id=?", (pair,))
                removed.append(int(pair))
        c.execute("UPDATE transactions SET transfer_pair=NULL WHERE transfer_pair=?",
                  (int(tid),))
    return {"deleted": removed}


# ---------------------------------------------------------------------------
# what happens when the statement finally arrives
# ---------------------------------------------------------------------------

def find_superseded(account_id: int, imported_ids: list[int]) -> list[dict]:
    """Typed notes that the rows just imported appear to be the real version of.

    Matched on account, amount to the penny, and a date within a few days. Deliberately
    strict: a wrong match here deletes something somebody typed, which is worse than
    leaving a duplicate they can see and remove themselves.
    """
    if not imported_ids:
        return []
    marks = ",".join("?" * len(imported_ids))
    fresh = db.rows(
        f"SELECT id, posted_on, amount, description FROM transactions WHERE id IN ({marks})",
        tuple(imported_ids))
    typed = db.rows(
        "SELECT id, posted_on, amount, description FROM transactions "
        "WHERE account_id=? AND entry_source='manual'", (int(account_id),))
    if not typed:
        return []

    out, claimed = [], set()
    for note in typed:
        for row in fresh:
            if row["id"] in claimed:
                continue
            if round(row["amount"] or 0, 2) != round(note["amount"] or 0, 2):
                continue
            if abs((_date(row["posted_on"]) - _date(note["posted_on"])).days) > MATCH_WINDOW_DAYS:
                continue
            claimed.add(row["id"])
            out.append({"manual_id": note["id"], "imported_id": row["id"],
                        "amount": note["amount"], "posted_on": note["posted_on"],
                        "description": note["description"],
                        "imported_description": row["description"]})
            break
    return out


def supersede(matches: list[dict]) -> int:
    """Remove the typed notes in `matches`, keeping the statement's version."""
    ids = [int(m["manual_id"]) for m in matches or []]
    if not ids:
        return 0
    with db.tx() as c:
        c.executemany("DELETE FROM transactions WHERE id=? AND entry_source='manual'",
                      [(i,) for i in ids])
    return len(ids)


def _date(s):
    return dt.date.fromisoformat(str(s)[:10])
