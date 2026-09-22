"""Adding, renaming and removing budget sections and the lines inside them.

Three things make this less trivial than "UPDATE categories":

  * **Budgets on a section are stored by name.** `budgets.parent_only` holds the section's
    text, not a foreign key, so renaming a section without updating those orphans every
    budget set against it — the budget survives, points at a section that no longer
    exists, and silently stops matching any spending.
  * **Deleting loses history unless you say where it goes.** `transactions.category_id`
    is ON DELETE SET NULL, so dropping a section quietly un-files every transaction ever
    put in it. That is sometimes what you want and must never be what you get by accident,
    so `delete_*` takes an explicit destination and reports how many rows moved.
  * **Three sections carry meaning the engine relies on.** Income, transfers and saving
    are identified by the `is_income` / `is_transfer` / `is_saving` flags, and budgets,
    the spend total and the export all behave differently for them. They can be renamed —
    the flags travel with the rows — but removing one would leave the engine with no way
    to recognise, say, a transfer between your own accounts.
"""

from __future__ import annotations

from .. import db

#: a section holding any of these flags is structural, not just a spending bucket
FLAGS = ("is_income", "is_transfer", "is_saving")


def _flagged(parent: str) -> str | None:
    row = db.one(
        "SELECT MAX(is_income) i, MAX(is_transfer) t, MAX(is_saving) s "
        "FROM categories WHERE parent=?", (parent,))
    if not row:
        return None
    if row["i"]:
        return "income"
    if row["t"]:
        return "transfers"
    if row["s"]:
        return "saving"
    return None


def list_sections() -> list[dict]:
    """Every section with enough context to decide what may be done to it."""
    out = []
    for r in db.rows("SELECT parent, COUNT(*) n, MIN(sort_order) so FROM categories "
                     "GROUP BY parent ORDER BY MIN(sort_order), parent"):
        parent = r["parent"]
        used = db.scalar(
            "SELECT COUNT(*) FROM transactions t JOIN categories c ON c.id=t.category_id "
            "WHERE c.parent=?", (parent,), 0)
        budgets = db.scalar(
            "SELECT COUNT(*) FROM budgets b LEFT JOIN categories c ON c.id=b.category_id "
            "WHERE b.parent_only=? OR c.parent=?", (parent, parent), 0)
        role = _flagged(parent)
        out.append({
            "name": parent,
            "lines": db.rows("SELECT id, name, is_income, is_transfer, is_saving "
                             "FROM categories WHERE parent=? ORDER BY sort_order, name",
                             (parent,)),
            "line_count": r["n"],
            "transactions": used,
            "budgets": budgets,
            "role": role,
            # A structural section can be renamed but never removed: the engine finds
            # income and internal transfers through it.
            "protected": role is not None,
        })
    return out


def special_sections() -> list[str]:
    """Sections the spending views exclude. Derived from the flags, not from a list of
    names — otherwise renaming 'Income' would quietly turn your salary into spending."""
    return [s["name"] for s in list_sections() if s["role"] in ("income", "transfers")]


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def add_section(name: str, lines: list[str] | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("A section needs a name.")
    if db.one("SELECT 1 FROM categories WHERE parent=?", (name,)):
        raise ValueError(f"There is already a section called {name}.")
    lines = [ln.strip() for ln in (lines or ["General"]) if ln.strip()] or ["General"]
    nxt = (db.scalar("SELECT MAX(sort_order) FROM categories", (), 0) or 0) + 10
    with db.tx() as c:
        for i, ln in enumerate(lines):
            c.execute("INSERT OR IGNORE INTO categories(parent,name,sort_order) "
                      "VALUES(?,?,?)", (name, ln, nxt + i))
    return {"name": name, "lines": lines}


def rename_section(old: str, new: str) -> dict:
    old, new = (old or "").strip(), (new or "").strip()
    if not new:
        raise ValueError("A section needs a name.")
    if old == new:
        return {"renamed": 0, "budgets": 0}
    if not db.one("SELECT 1 FROM categories WHERE parent=?", (old,)):
        raise ValueError(f"There is no section called {old}.")
    if db.one("SELECT 1 FROM categories WHERE parent=?", (new,)):
        raise ValueError(f"There is already a section called {new}. Merging two sections "
                         f"isn't supported — move the lines across instead.")
    with db.tx() as c:
        cur = c.execute("UPDATE categories SET parent=? WHERE parent=?", (new, old))
        n = cur.rowcount
        # The reason this function exists. A section budget names its section in text.
        b = c.execute("UPDATE budgets SET parent_only=? WHERE parent_only=?",
                      (new, old)).rowcount
    return {"renamed": n, "budgets": b}


def delete_section(name: str, move_to: str | None = None) -> dict:
    """Remove a section. Transactions go to `move_to` if given, else become uncategorised."""
    name = (name or "").strip()
    sec = next((s for s in list_sections() if s["name"] == name), None)
    if not sec:
        raise ValueError(f"There is no section called {name}.")
    if sec["protected"]:
        raise ValueError(
            f"{name} is how Mittens & Pence recognises "
            f"{'your income' if sec['role'] == 'income' else 'transfers between your own accounts' if sec['role'] == 'transfers' else 'money you put away'}"
            f", so it can't be removed. You can rename it, and change the lines inside it.")

    dest_id = None
    if move_to:
        dest = db.one("SELECT id FROM categories WHERE parent=? ORDER BY sort_order LIMIT 1",
                      (move_to,))
        if not dest:
            raise ValueError(f"There is no section called {move_to} to move them to.")
        dest_id = dest["id"]

    ids = [r["id"] for r in db.rows("SELECT id FROM categories WHERE parent=?", (name,))]
    moved = 0
    with db.tx() as c:
        if ids:
            qs = ",".join("?" * len(ids))
            if dest_id:
                moved = c.execute(
                    f"UPDATE transactions SET category_id=?, category_source='manual' "
                    f"WHERE category_id IN ({qs})", (dest_id, *ids)).rowcount
            c.execute(f"DELETE FROM rules WHERE category_id IN ({qs})", ids)
            c.execute(f"DELETE FROM budgets WHERE category_id IN ({qs})", ids)
        c.execute("DELETE FROM budgets WHERE parent_only=?", (name,))
        c.execute("DELETE FROM categories WHERE parent=?", (name,))
    return {"removed_lines": len(ids), "moved_transactions": moved,
            "uncategorised": 0 if dest_id else db.scalar(
                "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL", (), 0)}


# ---------------------------------------------------------------------------
# lines within a section
# ---------------------------------------------------------------------------

def add_line(parent: str, name: str) -> dict:
    parent, name = (parent or "").strip(), (name or "").strip()
    if not parent or not name:
        raise ValueError("A line needs a section and a name.")
    if not db.one("SELECT 1 FROM categories WHERE parent=?", (parent,)):
        raise ValueError(f"There is no section called {parent}.")
    if db.one("SELECT 1 FROM categories WHERE parent=? AND name=?", (parent, name)):
        raise ValueError(f"{parent} already has a line called {name}.")
    # Inherit the section's role, so a new line under Income counts as income.
    role = db.one("SELECT MAX(is_income) i, MAX(is_transfer) t, MAX(is_saving) s "
                  "FROM categories WHERE parent=?", (parent,)) or {}
    nxt = (db.scalar("SELECT MAX(sort_order) FROM categories WHERE parent=?",
                     (parent,), 0) or 0) + 1
    with db.tx() as c:
        c.execute("INSERT INTO categories(parent,name,is_income,is_transfer,is_saving,"
                  "sort_order) VALUES(?,?,?,?,?,?)",
                  (parent, name, role.get("i") or 0, role.get("t") or 0,
                   role.get("s") or 0, nxt))
    return db.one("SELECT * FROM categories WHERE parent=? AND name=?", (parent, name))


def rename_line(category_id: int, name: str) -> dict:
    name = (name or "").strip()
    row = db.one("SELECT * FROM categories WHERE id=?", (category_id,))
    if not row:
        raise ValueError("That line no longer exists.")
    if not name:
        raise ValueError("A line needs a name.")
    if db.one("SELECT 1 FROM categories WHERE parent=? AND name=? AND id<>?",
              (row["parent"], name, category_id)):
        raise ValueError(f"{row['parent']} already has a line called {name}.")
    with db.tx() as c:
        c.execute("UPDATE categories SET name=? WHERE id=?", (name, category_id))
    return db.one("SELECT * FROM categories WHERE id=?", (category_id,))


def delete_line(category_id: int, move_to: int | None = None) -> dict:
    row = db.one("SELECT * FROM categories WHERE id=?", (category_id,))
    if not row:
        raise ValueError("That line no longer exists.")
    n_left = db.scalar("SELECT COUNT(*) FROM categories WHERE parent=?", (row["parent"],), 0)
    if n_left <= 1:
        raise ValueError(
            f"{row['name']} is the only line in {row['parent']}. Remove the whole section "
            f"instead, or add another line first.")
    moved = 0
    with db.tx() as c:
        if move_to:
            moved = c.execute("UPDATE transactions SET category_id=? WHERE category_id=?",
                              (int(move_to), category_id)).rowcount
        c.execute("DELETE FROM rules WHERE category_id=?", (category_id,))
        c.execute("DELETE FROM budgets WHERE category_id=?", (category_id,))
        c.execute("DELETE FROM categories WHERE id=?", (category_id,))
    return {"moved_transactions": moved}
