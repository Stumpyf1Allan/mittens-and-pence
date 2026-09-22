"""Splitting one payment across several categories.

£100 at Tesco is often £50 of groceries, £20 of electronics and £30 of liquor. The bank
sends one line and has no idea; putting the whole £100 in Groceries makes the grocery
budget look blown and hides the other two entirely.

**The parent row is never touched.** It keeps the bank's amount and its fingerprint, so
re-uploading the statement still changes nothing, and the record of what the bank
actually said survives. It is marked `is_split` and from then on is excluded from every
total. The parts become child rows carrying the categories and the money.

That gives one rule, and everything depends on it holding everywhere:

    a split parent is never counted; its children are.

Count both and every figure in the app silently doubles, with nothing on screen looking
wrong — the same failure mode as importing a spreadsheet's own totals. `db.NOT_SPLIT_PARENT`
is the clause; a test asserts household totals are *identical* before and after a split,
which is the only check that actually proves it.

**The parts must add up.** Splitting £100 into £50 + £20 + £25 loses £5 — the spending
disappears, the account no longer reconciles, and nobody notices. So the total is
enforced to the penny, and the screen shows what is left to allocate rather than making
anyone do the arithmetic.
"""

from __future__ import annotations

from .. import db

#: A penny of tolerance for float arithmetic, and not one more.
TOLERANCE = 0.005
MAX_PARTS = 30


class SplitError(ValueError):
    """Something about the split is wrong, phrased for the person doing it."""


def get(tid: int) -> dict | None:
    """The parent, its parts, and what is left to allocate."""
    parent = db.one("""SELECT t.*, a.name AS account, a.currency AS account_currency,
                              c.parent AS section, c.name AS category
                       FROM transactions t JOIN accounts a ON a.id=t.account_id
                       LEFT JOIN categories c ON c.id=t.category_id
                       WHERE t.id=?""", (int(tid),))
    if not parent:
        return None
    if parent["split_of"]:
        # Asked about a part — answer about the whole, which is what the screen wants.
        return get(parent["split_of"])
    parts = db.rows("""SELECT t.*, c.parent AS section, c.name AS category
                       FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
                       WHERE t.split_of=? ORDER BY t.id""", (parent["id"],))
    allocated = round(sum(p["amount"] or 0 for p in parts), 2)
    return {"parent": parent, "parts": parts, "is_split": bool(parent["is_split"]),
            "allocated": allocated,
            "remaining": round((parent["amount"] or 0) - allocated, 2)}


def _check(parent: dict, parts: list[dict]) -> list[tuple[float, int | None, str]]:
    total = parent["amount"] or 0
    if parent["split_of"]:
        raise SplitError("That one is already part of a split — edit the whole payment "
                         "instead.")
    if len(parts) < 2:
        raise SplitError("A split needs at least two parts.")
    if len(parts) > MAX_PARTS:
        raise SplitError(f"That's more than {MAX_PARTS} parts — is something wrong?")

    cleaned = []
    running = 0.0
    for p in parts:
        try:
            amount = float(str(p.get("amount")).replace(",", "").replace("£", "").strip())
        except (TypeError, ValueError):
            raise SplitError(f"“{p.get('amount')}” isn't an amount.")
        if abs(amount) < 0.005:
            raise SplitError("A part of nothing isn't a part — remove it instead.")
        # Every part pulls the same way as the payment. A positive part inside a payment
        # out would quietly *reduce* the household's spending.
        if (amount > 0) != (total > 0):
            raise SplitError("Every part has to go the same way as the payment itself.")
        cid = p.get("category_id")
        cleaned.append((round(amount, 2), int(cid) if cid else None,
                        (p.get("description") or "").strip()))
        running += amount

    if abs(running - total) > TOLERANCE:
        # Compare magnitudes, not signed values. A payment out is negative, so
        # `total - running` is negative when the parts fall SHORT — and the first
        # version of this line read that as "too much", telling somebody the exact
        # opposite of what was wrong with their split.
        gap = round(abs(total) - abs(running), 2)
        raise SplitError(
            f"The parts come to {abs(running):,.2f} but the payment was "
            f"{abs(total):,.2f} — {abs(gap):,.2f} "
            f"{'still to allocate' if gap > 0 else 'too much'}.")
    return cleaned


def split(tid: int, parts: list[dict]) -> dict:
    """Replace a payment's single category with several, without altering the payment."""
    parent = db.one("SELECT * FROM transactions WHERE id=?", (int(tid),))
    if not parent:
        raise SplitError("That transaction is no longer there.")
    cleaned = _check(parent, parts)

    with db.tx() as c:
        # Re-splitting replaces the parts rather than adding to them, so editing a split
        # is the same operation as making one and there is no way to end up with both.
        c.execute("DELETE FROM transactions WHERE split_of=?", (parent["id"],))
        for i, (amount, category_id, note) in enumerate(cleaned, start=1):
            desc = note or f"{parent['description']} ({i} of {len(cleaned)})"
            fp = db.fingerprint("split", parent["id"], i, round(amount, 2))
            c.execute(
                "INSERT INTO transactions(account_id, posted_on, description, merchant,"
                " amount, currency, category_id, category_source, reference, fingerprint,"
                " entry_source, split_of, is_transfer)"
                " VALUES(?,?,?,?,?,?,?,'manual',?,?,'split',?,0)",
                (parent["account_id"], parent["posted_on"], desc, parent["merchant"],
                 amount, parent["currency"], category_id, parent["reference"], fp,
                 parent["id"]))
        c.execute("UPDATE transactions SET is_split=1 WHERE id=?", (parent["id"],))
    return get(parent["id"])


def unsplit(tid: int) -> dict:
    """Put it back to one row. The parent was never altered, so there is nothing to undo
    beyond removing the parts."""
    parent = db.one("SELECT * FROM transactions WHERE id=?", (int(tid),))
    if not parent:
        raise SplitError("That transaction is no longer there.")
    if parent["split_of"]:
        parent = db.one("SELECT * FROM transactions WHERE id=?", (parent["split_of"],))
    with db.tx() as c:
        n = c.execute("DELETE FROM transactions WHERE split_of=?", (parent["id"],)).rowcount
        c.execute("UPDATE transactions SET is_split=0 WHERE id=?", (parent["id"],))
    return {"ok": True, "removed": n, "id": parent["id"]}


def suggest(tid: int) -> list[dict]:
    """A starting point: the whole amount in the category it already has, plus a blank.

    Better than two empty rows — most splits are "most of it was what you thought, and
    a bit of it wasn't".
    """
    row = db.one("SELECT * FROM transactions WHERE id=?", (int(tid),))
    if not row:
        return []
    return [{"amount": round(row["amount"] or 0, 2), "category_id": row["category_id"]},
            {"amount": 0, "category_id": None}]
