"""Turn a PDF bank statement into the same Table every other importer produces.

Why this is more than "extract the text": a PDF has no rows and no columns. It has
glyphs at coordinates. `page.extract_text()` gives you a wall of words in reading order,
which throws away exactly the thing that makes a statement a statement — that the number
at x=470 is a debit and the one at x=530 is the balance. So this works from word
positions instead, and rebuilds the grid.

The strategy, in order:

  1. If the PDF has ruled table lines, believe them. pdfplumber's table finder is better
     at that than anything hand-rolled.
  2. Otherwise infer the columns: group words into lines by their vertical position, find
     the header row, take the column spans from it, then *re-fit* those spans to where
     the data actually sits. That second pass matters — headers are usually left-aligned
     and amounts right-aligned, so the header's own x-range is a bad guess for its column.
  3. Stitch continuation lines. A long description wraps onto a second line with no date
     and no amount; that belongs to the row above, not to a row of its own.

Once a Table comes out, everything downstream — column mapping, the remembered layout,
the SHA-256 fingerprint that makes re-uploading safe — works exactly as it does for CSV.

Scanned statements (a photograph of paper) contain no text at all. Rather than return an
empty table and let the user wonder, that case is detected and named, because the answer
is completely different: ask the bank for a CSV, or run it through OCR first.
"""

from __future__ import annotations

import re

from .readers import Table, parse_amount, parse_date

#: two words closer than this horizontally belong to the same header, e.g. "Money In"
HEADER_GAP = 12.0
#: words whose vertical centres are within this belong to the same line
LINE_TOL = 3.0
#: below this many characters per page it isn't a text PDF, it's a picture of one
SCANNED_CHARS_PER_PAGE = 40

HEADER_WORDS = {
    "date", "dates", "posted", "posting", "transaction", "description", "details",
    "narrative", "reference", "type", "debit", "credit", "paid", "in", "out",
    "money", "amount", "balance", "value", "withdrawal", "withdrawals", "deposit",
    "deposits", "fee", "charges", "currency", "beskrywing", "datum", "bedrag", "saldo",
}


class PdfUnavailable(RuntimeError):
    pass


class ScannedPdf(RuntimeError):
    pass


def _open(path):
    try:
        import pdfplumber
    except ImportError as e:
        raise PdfUnavailable(
            "Reading a PDF statement needs the 'pdfplumber' package, which isn't "
            "installed. Install it with:  pip install pdfplumber\n"
            "Every other format — CSV, Excel, OFX, QIF — works without it."
        ) from e
    try:
        return pdfplumber.open(str(path))
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg:
            raise RuntimeError(
                "That PDF is password-protected. Open it in a PDF reader, enter the "
                "password, and save an unprotected copy — banks often set the password "
                "to your date of birth or postcode."
            ) from e
        raise RuntimeError(f"That PDF couldn't be opened: {e}") from e


# ---------------------------------------------------------------------------
# lines and columns
# ---------------------------------------------------------------------------

def _lines(words: list[dict]) -> list[list[dict]]:
    """Group words into visual lines by vertical centre."""
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        mid = (w["top"] + w["bottom"]) / 2
        for line in reversed(out[-3:]):          # only recent lines can match
            lmid = (line[0]["top"] + line[0]["bottom"]) / 2
            if abs(mid - lmid) <= LINE_TOL:
                line.append(w)
                break
        else:
            out.append([w])
    for line in out:
        line.sort(key=lambda w: w["x0"])
    return out


def _groups(line: list[dict], gap: float = HEADER_GAP) -> list[dict]:
    """Merge adjacent words into cells: {text, x0, x1}."""
    cells = []
    for w in line:
        if cells and w["x0"] - cells[-1]["x1"] <= gap:
            cells[-1]["text"] += " " + w["text"]
            cells[-1]["x1"] = w["x1"]
        else:
            cells.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
    return cells


def _header_score(cells: list[dict]) -> int:
    if len(cells) < 3:
        return 0
    score = 0
    for c in cells:
        for token in re.findall(r"[a-z]+", c["text"].lower()):
            if token in HEADER_WORDS:
                score += 2
        if parse_amount(c["text"]) is not None or parse_date(c["text"]):
            score -= 3          # a header row holds labels, not values
    return score


def _find_header(lines: list[list[dict]]) -> tuple[int, list[dict]] | None:
    best, best_score = None, 3          # require a real signal, not a lucky word
    for i, line in enumerate(lines[:40]):
        cells = _groups(line)
        s = _header_score(cells)
        if s > best_score:
            best, best_score = (i, cells), s
    return best


def _assign(cells: list[dict], bounds: list[float]) -> list[str]:
    """Drop cells into columns by where their midpoint falls."""
    row = [""] * (len(bounds) - 1)
    for c in cells:
        mid = (c["x0"] + c["x1"]) / 2
        idx = 0
        for i in range(len(bounds) - 1):
            if bounds[i] <= mid < bounds[i + 1]:
                idx = i
                break
        else:
            idx = len(row) - 1 if mid >= bounds[-1] else 0
        row[idx] = (row[idx] + " " + c["text"]).strip() if row[idx] else c["text"]
    return row


def _bounds_from(spans: list[tuple[float, float]], page_width: float) -> list[float]:
    """Boundaries midway between neighbouring column spans."""
    spans = sorted(spans)
    edges = [0.0]
    for a, b in zip(spans, spans[1:]):
        edges.append((a[1] + b[0]) / 2)
    edges.append(page_width + 1)
    return edges


def _refit(body: list[list[dict]], bounds: list[float],
           page_width: float) -> list[float]:
    """Re-derive column edges from where the data actually landed.

    Headers are typically left-aligned and money right-aligned under them, so the
    header's own x-range is a poor description of its column. One pass of fitting the
    spans to the assigned words fixes the near-miss cases where an amount sits just
    across a boundary from its own header.
    """
    n = len(bounds) - 1
    lo = [None] * n
    hi = [None] * n
    for line in body:
        for c in _groups(line):
            mid = (c["x0"] + c["x1"]) / 2
            for i in range(n):
                if bounds[i] <= mid < bounds[i + 1]:
                    lo[i] = c["x0"] if lo[i] is None else min(lo[i], c["x0"])
                    hi[i] = c["x1"] if hi[i] is None else max(hi[i], c["x1"])
                    break
    spans = [(lo[i], hi[i]) for i in range(n) if lo[i] is not None and hi[i] is not None]
    if len(spans) < 2:
        return bounds
    return _bounds_from(spans, page_width)


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

#: a cell that IS a number, as opposed to prose that happens to contain one.
#: `parse_amount` is deliberately forgiving and reads 4471 out of "ACCOUNT ENDING 4471";
#: that is right when a column is known to hold money and wrong when deciding whether a
#: line is a transaction at all, which is what nearly lost every wrapped description.
_MONEY = re.compile(r"""^\(?\s*[-+]?\s*(?:[£$€R]|ZAR|GBP|USD|EUR)?\s*
                        \d{1,3}(?:[ ,.\u00a0]\d{3})*(?:[.,]\d{1,2})?
                        \s*(?:CR|DR)?\s*\)?$""", re.X | re.I)


def _is_money(cell: str) -> bool:
    c = (cell or "").strip()
    return bool(c) and bool(_MONEY.match(c))


def _looks_like_data(row: list[str]) -> bool:
    """A transaction row carries a real date or a cell that is itself a number."""
    if any(parse_date(c) for c in row if c and len(c.strip()) <= 24):
        return True
    return any(_is_money(c) for c in row if c)


def _is_continuation(row: list[str]) -> bool:
    """Only free text, and only in one column — the tail of the line above."""
    filled = [i for i, c in enumerate(row) if c.strip()]
    return len(filled) == 1 and not _looks_like_data(row)


def _stitch(rows: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        if out and _is_continuation(row):
            i = next(j for j, c in enumerate(row) if c.strip())
            if i < len(out[-1]):
                out[-1][i] = (out[-1][i] + " " + row[i].strip()).strip()
                continue
        out.append(row)
    return out


def _name_columns(header: list[str], rows: list[list[str]]) -> list[str]:
    """Give unnamed columns a name from what they contain.

    A statement with no header row at all still has a date column and an amount column;
    labelling them lets the existing auto-mapper do its job instead of showing the user
    'col1, col2, col3'.
    """
    n = max([len(header)] + [len(r) for r in rows] or [0])
    header = list(header) + [""] * (n - len(header))
    sample = rows[:40]
    used = set(h.lower() for h in header if h)
    for i in range(n):
        if header[i].strip():
            continue
        vals = [r[i] for r in sample if i < len(r) and r[i].strip()]
        if not vals:
            header[i] = f"Column {i + 1}"
            continue
        dates = sum(1 for v in vals if parse_date(v))
        nums = sum(1 for v in vals if parse_amount(v) is not None)
        if dates >= max(2, len(vals) * 0.6) and "date" not in used:
            header[i] = "Date"; used.add("date")
        elif nums >= max(2, len(vals) * 0.6):
            name = "Balance" if i == n - 1 and "balance" not in used else "Amount"
            while name.lower() in used:
                name += " 2"
            header[i] = name; used.add(name.lower())
        else:
            name = "Description" if "description" not in used else f"Column {i + 1}"
            header[i] = name; used.add(name.lower())
    return header


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def read_pdf(path, name: str = "") -> Table:
    name = name or getattr(path, "name", "statement.pdf")
    header: list[str] = []
    rows: list[list[str]] = []
    preamble: list[list[str]] = []
    chars = 0

    with _open(path) as pdf:
        pages = pdf.pages
        if not pages:
            raise RuntimeError("That PDF has no pages.")

        # 1. ruled tables, if the statement has them
        ruled = _try_ruled(pages)
        if ruled is not None:
            header, rows = ruled

        # 2. otherwise rebuild the grid from word positions
        if not rows:
            bounds = None
            for page in pages:
                words = page.extract_words(use_text_flow=False,
                                           keep_blank_chars=False) or []
                chars += sum(len(w["text"]) for w in words)
                if not words:
                    continue
                lines = _lines(words)
                start = 0
                if bounds is None:
                    found = _find_header(lines)
                    if found:
                        idx, cells = found
                        header = [c["text"] for c in cells]
                        bounds = _bounds_from([(c["x0"], c["x1"]) for c in cells],
                                              page.width)
                        bounds = _refit(lines[idx + 1:], bounds, page.width)
                        preamble = [[" ".join(w["text"] for w in ln)] for ln in lines[:idx]]
                        start = idx + 1
                    else:
                        # No header anywhere: take the columns from the data itself.
                        data = [ln for ln in lines
                                if _looks_like_data([w["text"] for w in ln])]
                        if not data:
                            continue
                        widest = max(data, key=lambda ln: len(_groups(ln)))
                        bounds = _bounds_from(
                            [(c["x0"], c["x1"]) for c in _groups(widest)], page.width)
                        bounds = _refit(data, bounds, page.width)
                        header = [""] * (len(bounds) - 1)
                for ln in lines[start:]:
                    cells = _groups(ln)
                    if _header_score(cells) > 3:
                        continue                       # header repeated on a later page
                    row = _assign(cells, bounds)
                    if any(c.strip() for c in row):
                        rows.append(row)

    if chars and chars < SCANNED_CHARS_PER_PAGE * max(len(rows), 1) and not rows:
        raise ScannedPdf("")
    if not rows and chars < SCANNED_CHARS_PER_PAGE:
        raise ScannedPdf(
            "That PDF has no text in it — it's a scan or a photograph of a statement, so "
            "there is nothing to read. Two ways forward: ask the bank for a CSV, OFX or "
            "QIF export instead (almost all of them offer one), or run the file through "
            "an OCR tool first and upload the result.")

    rows = _stitch(rows)
    rows = [r for r in rows if _looks_like_data(r)]
    if not rows:
        raise RuntimeError(
            "Mittens & Pence read that PDF but couldn't find anything that looks like a list of "
            "transactions — no rows with both a date and an amount. If it's a summary or "
            "a certificate rather than a statement, that's expected. If it really is a "
            "statement, send it over with the Help button and it can be taught.")
    header = _name_columns(header, rows)
    width = len(header)
    rows = [(r + [""] * width)[:width] for r in rows]
    return Table(header, rows, source=name, preamble=preamble)


def _try_ruled(pages) -> tuple[list[str], list[list[str]]] | None:
    """Use the PDF's own table lines when it has them."""
    header: list[str] = []
    rows: list[list[str]] = []
    for page in pages:
        try:
            tables = page.extract_tables() or []
        except Exception:
            return None
        for t in tables:
            clean = [[(c or "").replace("\n", " ").strip() for c in r] for r in t if r]
            if len(clean) < 2:
                continue
            first = [{"text": c, "x0": i * 10.0, "x1": i * 10.0 + 5}
                     for i, c in enumerate(clean[0])]
            body = clean[1:]
            if not header and _header_score(first) > 3:
                header = clean[0]
            elif not header:
                body = clean
            rows.extend(body)
    if len(rows) < 2:
        return None
    return header, rows
