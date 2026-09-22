"""Read a budget out of somebody's existing spreadsheet.

A personal budget workbook is nothing like a bank statement. There is no agreed shape:
months tile sideways, categories run down, subtotals sit in the middle of the data, and
every household invents its own layout. Trying to absorb one wholesale is how you end up
silently wrong.

So this reads one specific, valuable thing: **the plan**. A list of categories with a
monthly amount against each. That is the part the app cannot derive from anything else —
spending it will recompute from statements, and importing someone's own arithmetic
alongside their statements double-counts every pound.

Finding the plan is tractable where finding "the whole budget" is not. A budget list is a
column of text labels beside a column of numbers, usually under a header containing the
word budget. The traps, all of which this handles:

  * **Subtotal and Total rows.** Import one as a category and the household's spending
    doubles with nothing obviously wrong on screen. Rejected by label *and* by arithmetic
    — a row whose value equals the sum of the rows above it is a total whatever it is
    called.
  * **Sign.** Budgets are written negative in some sheets and positive in others. The
    sign is normalised on the way in and reported, rather than assumed.
  * **The display table versus the source table.** A sheet often shows budget figures in
    several places, all pointing at one master list. Preferring the block with a header
    that says budget, and the one with the most distinct labels, lands on the master.
"""

from __future__ import annotations

import re

#: a header that means "this column holds the monthly figure"
BUDGET_HEADER = re.compile(
    r"budget|per\s*month|p/?m|monthly\s*(amount|allowance|target)|planned", re.I)
#: labels that are aggregates rather than categories
TOTAL_LABEL = re.compile(
    r"^\s*(sub\s*)?(total|totals|sum|grand\s*total|net|balance)\b", re.I)
#: a marker some sheets put above the master list
MARKER = re.compile(r"don'?t\s*touch|do\s*not\s*touch|master|source", re.I)

MIN_ROWS = 4


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("£", "").replace("R", "").replace("$", "")
        s = s.replace("(", "-").replace(")", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _text(v) -> str:
    return str(v).strip() if v is not None and str(v).strip() else ""


def find_budget_lists(path) -> list[dict]:
    """Every plausible category+amount table in the workbook, best first."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    out = []
    for order, ws in enumerate(wb.worksheets):
        if ws.sheet_state != "visible":
            continue
        for b in _scan_sheet(ws):
            b["sheet_order"] = order
            out.append(b)
    # A workbook usually shows the same figures more than once — a display table and the
    # master list behind it. Prefer a block whose header says budget; then, among equals,
    # the LATER sheet, because a workbook with a tab per year keeps the current plan last.
    out.sort(key=lambda b: (-b["score"], -b["sheet_order"], -len(b["entries"])))
    return out[:12]


def has_budget_list(path) -> bool:
    """Is there a budget in here at all? Stops at the first one it finds.

    The full scan reads every cell of every sheet, which is right when somebody has
    chosen to import a budget and wrong when they have merely dropped an .xlsx statement
    on the page and are waiting for the columns to appear.
    """
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=False)
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        for b in _scan_sheet(ws):
            if b["score"] >= 6:          # a header that actually says budget
                return True
    return False


def _scan_sheet(ws) -> list[dict]:
    max_r = min(ws.max_row, 500)
    max_c = min(ws.max_column, 120)
    grid = [[ws.cell(r, c).value for c in range(1, max_c + 1)] for r in range(1, max_r + 1)]
    found = []

    for label_c in range(max_c):
        for value_c in range(label_c + 1, min(label_c + 6, max_c)):
            block = _read_block(grid, label_c, value_c, max_r)
            if block:
                block["sheet"] = ws.title
                block["currency"] = _col_currency(ws, block)
                found.append(block)
    return _dedupe(found)


#: how a spreadsheet writes each currency in a cell's number format. Checked in this
#: order, because "[$£-en-GB]" and "[$R-en-ZA]" both contain a dollar sign.
_CCY = (
    ("GBP", re.compile(r"£")),
    ("EUR", re.compile(r"€")),
    ("ZAR", re.compile(r"\[\$R\b|\bZAR\b|\"R\"")),
    ("USD", re.compile(r"\$")),
)


def _col_currency(ws, block) -> str | None:
    """What currency the sheet formats this column in — a hint, never a decision.

    A household with commitments in two countries writes both in one column, and only
    they know which is which. Reporting what the formatting says lets the screen ask a
    pointed question instead of a blank one.
    """
    counts: dict[str, int] = {}
    first = block["first_row"]
    span = len(block["entries"]) + len(block["excluded_totals"])
    for r in range(first, first + span):
        try:
            fmt = ws.cell(r, block["value_col"]).number_format or ""
        except Exception:
            break
        for code, pattern in _CCY:
            if pattern.search(fmt):
                counts[code] = counts.get(code, 0) + 1
                break
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _read_block(grid, label_c, value_c, max_r) -> dict | None:
    """A run of rows where the label column holds text and the value column a number."""
    best = None
    r = 0
    while r < max_r:
        # find the start of a run
        if not (_text(grid[r][label_c]) and _num(grid[r][value_c]) is not None):
            r += 1
            continue
        start = r
        rows = []
        while r < max_r:
            label = _text(grid[r][label_c])
            val = _num(grid[r][value_c])
            if not label or val is None:
                break
            rows.append((r, label, val))
            r += 1
        if len(rows) >= MIN_ROWS:
            blk = _finish(grid, label_c, value_c, start, rows)
            if blk and (best is None or blk["score"] > best["score"]):
                best = blk
    return best


def _finish(grid, label_c, value_c, start, rows) -> dict | None:
    # header: look up to 3 rows above the run
    header, marker = "", False
    for back in range(1, 4):
        rr = start - back
        if rr < 0:
            break
        h = _text(grid[rr][value_c]) or _text(grid[rr][label_c])
        if h and not header:
            header = h
        if MARKER.search(" ".join(_text(x) for x in grid[rr] if x is not None)):
            marker = True

    entries, totals = [], []
    running = 0.0
    for _, label, val in rows:
        # A total by name, or a value that equals what came before it. Either way it is
        # an aggregate, and importing it would double the household's spending.
        looks_total = bool(TOTAL_LABEL.match(label))
        sums_above = len(entries) >= 3 and abs(val - running) < max(0.02, abs(running) * 0.001)
        if looks_total or sums_above:
            totals.append(label)
            continue
        entries.append({"label": label, "amount": val})
        running += val

    if len(entries) < MIN_ROWS:
        return None

    score = 0
    if BUDGET_HEADER.search(header):
        score += 6
    if marker:
        score += 4
    if len({e["label"].lower() for e in entries}) == len(entries):
        score += 2                      # a real category list has no repeats
    if totals:
        score += 1                      # a table with a total under it is a real table
    nonzero = [e for e in entries if e["amount"]]
    if len(nonzero) < 3:
        score -= 4
    return {
        "label_col": label_c + 1,
        "value_col": value_c + 1,
        "first_row": start + 1,
        "header": header,
        "marker": marker,
        "entries": entries,
        "excluded_totals": totals,
        "score": score,
        # Budgets are written negative in some sheets, positive in others.
        "sign": "negative" if sum(e["amount"] for e in entries) < 0 else "positive",
    }


def _dedupe(blocks: list[dict]) -> list[dict]:
    seen, out = set(), []
    for b in sorted(blocks, key=lambda x: -x["score"]):
        key = tuple(sorted((e["label"].lower(), round(e["amount"], 2)) for e in b["entries"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# mapping someone's own category names onto Mittens & Pence's sections
# ---------------------------------------------------------------------------

#: keyword -> section. Deliberately a suggestion the person confirms, never a silent
#: decision: two households mean different things by "Other".
HINTS = [
    ("Home", r"rent|mortgage|council\s*tax|rates|home\s*(maint|repair|insur)|homeowner|"
             r"household|garden|cleaner|cleaning|levy|levies|security|furniture"),
    ("Utilities", r"electric|gas\b|water|sanitation|broadband|fibre|wifi|internet|mweb|"
                  r"vodacom|mobile|phone|airtime|waste|refuse|tv\s*licen|energy"),
    ("Subscriptions", r"stream|netflix|spotify|subscription|multichoice|dstv|showmax"),
    ("Food & Drink", r"grocer|food|restaurant|takeaway|coffee|snack|liquor|alcohol|wine|pub"),
    ("Transport", r"car\b|petrol|fuel|diesel|public\s*transport|taxi|uber|bus|train|"
                  r"parking|toll|licence\s*disc|servicing|mot\b"),
    ("Health", r"med(ical)?\s*aid|medical|health|pharmac|chemist|dentist|optician|gym|"
               r"fitness|therapy|gap\s*cover|doctor|consult"),
    ("Family", r"child|school|creche|pet|cat\b|dog\b|vet\b|nanny|au\s*pair"),
    ("Shopping", r"clothing|clothes|shoes|electronic|book|gift|toiletr|"
                 r"groom|hair|beauty|cosmetic"),
    ("Travel", r"flight|holiday|accommodation|hotel|travel"),
    ("Insurance & Protection", r"insurance|life\s*cover|income\s*protection|funeral"),
    ("Financial", r"bank\s*fee|charges|interest\s*paid|overdraft|loan|credit\s*card|fee|repayment"),
    ("Income", r"salary|income|pension|dividend|interest\s*received|refund|rental\s*income"),
    ("Saving & Investing", r"saving|invest|isa\b|tfsa|retirement|annuit"),
    ("Giving", r"donat|charit|tith|sponsor"),
    ("Entertainment", r"entertain|cinema|leisure|hobby|hobbies"),
]


def suggest_section(label: str, existing: dict[str, list[str]]) -> tuple[str, str, bool]:
    """(section, line, section_is_new) for one of their category names.

    Prefers a category the household already has, so importing a budget twice doesn't
    create a second 'Groceries' beside the first. A suggestion is allowed to name a
    section that does not exist yet — burying "Entertainment" in "Other" because the
    default list happens not to include it would lose the very structure being imported.
    Sections are editable, and the person confirms every row before anything is written.
    """
    low = label.strip().lower()
    for section, lines in existing.items():
        for line in lines:
            if line.strip().lower() == low:
                return section, line, False
    for section, pattern in HINTS:
        if re.search(pattern, low):
            for line in existing.get(section, []):
                if line.strip().lower() == low:
                    return section, line, False
            return section, label.strip(), section not in existing
    return "Other", label.strip(), "Other" not in existing


# ---------------------------------------------------------------------------
# writing the plan into the household
# ---------------------------------------------------------------------------

def apply_budget(rows: list[dict], currency: str | None = None) -> dict:
    """Create any missing sections and lines, then set a budget on each.

    `rows` is what the person confirmed on screen: [{label, amount, section, line}].
    Nothing here guesses — the suggestions were made earlier and approved. Amounts are
    stored positive, because a budget is a ceiling rather than a signed movement, and a
    sheet that writes them negative would otherwise produce budgets of minus ninety
    pounds that never match anything.
    """
    from .. import db
    from ..engine import budgets as budget_engine
    from ..engine import sections as sections_engine

    made_sections, made_lines, set_budgets, skipped = [], [], 0, []
    existing = {s["name"] for s in sections_engine.list_sections()}

    for row in rows:
        section = (row.get("section") or "Other").strip()
        line = (row.get("line") or row.get("label") or "").strip()
        amount = abs(float(row.get("amount") or 0))
        if not line:
            continue
        if not amount:
            # A category budgeted at zero is one they stopped using. Creating it adds
            # clutter and a permanent 0/0 row; leave it out and say so.
            skipped.append(line)
            continue

        if section not in existing:
            sections_engine.add_section(section, [line])
            existing.add(section)
            made_sections.append(section)
            made_lines.append(f"{section} › {line}")
        cat = db.one("SELECT id FROM categories WHERE parent=? AND lower(name)=lower(?)",
                     (section, line))
        if not cat:
            sections_engine.add_line(section, line)
            made_lines.append(f"{section} › {line}")
            cat = db.one("SELECT id FROM categories WHERE parent=? AND lower(name)=lower(?)",
                         (section, line))
        if cat:
            budget_engine.set_budget(amount, category_id=cat["id"], currency=currency)
            set_budgets += 1

    return {"sections_created": made_sections, "lines_created": made_lines,
            "budgets_set": set_budgets, "skipped_zero": skipped}
