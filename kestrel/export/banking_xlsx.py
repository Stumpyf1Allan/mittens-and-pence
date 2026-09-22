"""The second workbook: current accounts, where the money goes, and the budgets.

Built so the month selector on the Budgets tab actually works — change the month in
the yellow cell and every Spent figure recalculates from the Transactions tab, because
they are SUMIFS, not pasted numbers.
"""

from __future__ import annotations

import datetime as dt
import pathlib

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.formatting.rule import CellIsRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .. import config, db
from ..engine import budgets as budget_engine
from ..market import prices as market

FONT = "Arial"
INK = "1F2933"
MUTED = "6B7280"
ACCENT = "1D4ED8"
GREEN = "047857"
AMBER = "B45309"
RED = "B91C1C"
HEAD_FILL = "1F3A5F"
BAND = "F3F6FB"
INPUT_FILL = "FFF7CC"
INPUT_BLUE = "0000FF"


def _ccy(currency: str) -> str:
    sym = {"GBP": "£", "ZAR": "R ", "USD": "$", "EUR": "€"}.get(currency, "")
    return f'{sym}#,##0.00;[Red]({sym}#,##0.00);"-"'


def _style_base(ws):
    ws.sheet_view.showGridLines = False


def _title(ws, cell, text, size=16):
    ws[cell] = text
    ws[cell].font = Font(name=FONT, size=size, bold=True, color=INK)


def _label(ws, cell, text, bold=False, color=MUTED, size=10):
    ws[cell] = text
    ws[cell].font = Font(name=FONT, size=size, bold=bold, color=color)


def _num(ws, cell, value, fmt, bold=False, color=INK):
    ws[cell] = value
    ws[cell].font = Font(name=FONT, size=10, bold=bold, color=color)
    ws[cell].number_format = fmt


def _header_row(ws, row, headers, widths=None, freeze=True):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(name=FONT, size=9, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=HEAD_FILL)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    if widths:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 26
    if freeze:
        ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _table(ws, start, headers, rows, widths=None, formats=None):
    _style_base(ws)
    _header_row(ws, start, headers, widths)
    formats = formats or {}
    for r, data in enumerate(rows, start=start + 1):
        for i, v in enumerate(data, start=1):
            c = ws.cell(row=r, column=i, value=v)
            c.font = Font(name=FONT, size=10, color=INK)
            if i in formats:
                c.number_format = formats[i]
            if r % 2 == 0:
                c.fill = PatternFill("solid", fgColor=BAND)
    return start + len(rows)


# ---------------------------------------------------------------------------

def _accounts_tab(wb, base):
    ws = wb.create_sheet("Accounts")
    money = _ccy(base)
    rows = db.rows("""SELECT a.*, m.name AS member, c.institution_name, c.method, c.last_sync
                      FROM accounts a
                      LEFT JOIN members m ON m.id=a.member_id
                      LEFT JOIN connections c ON c.id=a.connection_id
                      WHERE a.closed=0 ORDER BY a.is_investment, a.account_type, a.name""")
    _title(ws, "A1", "Accounts")
    data = []
    for a in rows:
        data.append([a["name"], a["institution_name"] or "", a["account_type"],
                     a["member"] or "", a["number_masked"] or "", a["currency"],
                     a["balance"] or 0,
                     market.convert(a["balance"] or 0, a["currency"] or base, base),
                     {"openbanking": "Auto (Open Banking)", "direct_api": "Auto (API)",
                      "aggregator": "Auto (aggregator)", "csv": "Statement upload",
                      "manual": "Typed in"}.get(a["method"], a["method"] or "—"),
                     a["last_sync"] or "", "Yes" if a["include_in_net_worth"] else "No"])
    end = _table(ws, 3,
                 ["Account", "Institution", "Type", "Who", "Number", "Currency",
                  "Balance", f"Balance ({base})", "How it updates", "Last sync",
                  "In net worth"],
                 data, [28, 24, 12, 14, 12, 10, 15, 16, 20, 18, 12],
                 {7: "#,##0.00", 8: money})
    r = end + 1
    ws.cell(row=r, column=1, value="Total").font = Font(name=FONT, size=11, bold=True)
    c = ws.cell(row=r, column=8, value=f"=SUM(H4:H{end})")
    c.number_format = money
    c.font = Font(name=FONT, size=11, bold=True)
    c.border = Border(top=Side(style="thin", color=INK))
    return ws


def _transactions_tab(wb, base, months=24):
    ws = wb.create_sheet("Transactions")
    money = _ccy(base)
    since = (dt.date.today().replace(day=1) - dt.timedelta(days=31 * months)).isoformat()
    rows = db.rows("""SELECT t.posted_on, a.name AS account, m.name AS member, t.description,
                             t.merchant, t.amount, t.currency, t.is_transfer,
                             COALESCE(c.parent,'Other') AS section,
                             COALESCE(c.name,'Uncategorised') AS category
                      FROM transactions t
                      JOIN accounts a ON a.id=t.account_id
                      LEFT JOIN members m ON m.id=a.member_id
                      LEFT JOIN categories c ON c.id=t.category_id
                      WHERE t.posted_on >= ? AND IFNULL(t.is_split, 0) = 0
                      ORDER BY t.posted_on DESC, t.id DESC""", (since,))
    _title(ws, "A1", "Transactions")
    _label(ws, "A2", f"Everything since {since}. The Budgets and Spending tabs read from here.")
    data = []
    for t in rows:
        amt = market.convert(t["amount"] or 0, t["currency"] or base, base)
        data.append([t["posted_on"], t["account"], t["member"] or "", t["description"],
                     t["merchant"] or "", amt, t["currency"], t["section"], t["category"],
                     (t["posted_on"] or "")[:7], "Yes" if t["is_transfer"] else ""])
    end = _table(ws, 4,
                 ["Date", "Account", "Who", "Description", "Merchant", f"Amount ({base})",
                  "Original ccy", "Section", "Category", "Month", "Transfer"],
                 data, [12, 24, 12, 46, 24, 15, 12, 22, 26, 10, 10],
                 {6: money})
    ws.auto_filter.ref = f"A4:K{max(end, 5)}"
    if end > 4:
        ws.conditional_formatting.add(
            f"F5:F{end}", CellIsRule(operator="lessThan", formula=["0"],
                                     font=Font(name=FONT, size=10, color=RED)))
    return ws, end


def _budgets_tab(wb, base, tx_end, period):
    ws = wb.create_sheet("Budgets", 0)
    money = _ccy(base)
    _style_base(ws)
    _title(ws, "A1", "Budgets")
    _label(ws, "A2", "Change the month in the yellow cell — every figure below follows it.")

    _label(ws, "F1", "Month", bold=True, color=INK)
    ws["G1"] = period
    ws["G1"].fill = PatternFill("solid", fgColor=INPUT_FILL)
    ws["G1"].font = Font(name=FONT, size=11, bold=True, color=INPUT_BLUE)
    ws["G1"].alignment = Alignment(horizontal="center")
    periods = [r["p"] for r in db.rows(
        "SELECT DISTINCT substr(posted_on,1,7) AS p FROM transactions "
        "WHERE posted_on IS NOT NULL ORDER BY p DESC LIMIT 36") if r["p"]]
    if period not in periods:
        periods.insert(0, period)
    if periods:
        dv = DataValidation(type="list", formula1='"' + ",".join(periods) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(ws["G1"])

    budgets = budget_engine.list_budgets()
    start = 5
    _header_row(ws, start,
                ["Section", "Category", "Budget", "Spent", "Remaining", "% used",
                 "Status", "Notes"],
                [22, 26, 14, 14, 14, 11, 16, 30])

    tx_last = max(tx_end, 5)
    for i, b in enumerate(budgets):
        r = start + 1 + i
        section = b["parent_only"] or b["cat_parent"]
        category = b["cat_name"] or ""
        ws.cell(row=r, column=1, value=section).font = Font(name=FONT, size=10, bold=True, color=INK)
        ws.cell(row=r, column=2, value=category).font = Font(name=FONT, size=10, color=INK)

        amount = ws.cell(row=r, column=3, value=b["amount"])
        amount.number_format = money
        amount.font = Font(name=FONT, size=10, bold=True, color=INPUT_BLUE)
        amount.fill = PatternFill("solid", fgColor=INPUT_FILL)

        if category:
            spent = (f'=-SUMIFS(Transactions!$F$5:$F${tx_last},'
                     f'Transactions!$J$5:$J${tx_last},$G$1,'
                     f'Transactions!$H$5:$H${tx_last},$A{r},'
                     f'Transactions!$I$5:$I${tx_last},$B{r},'
                     f'Transactions!$F$5:$F${tx_last},"<0")')
        else:
            spent = (f'=-SUMIFS(Transactions!$F$5:$F${tx_last},'
                     f'Transactions!$J$5:$J${tx_last},$G$1,'
                     f'Transactions!$H$5:$H${tx_last},$A{r},'
                     f'Transactions!$F$5:$F${tx_last},"<0")')
        for cidx, formula, fmt in (
                (4, spent, money),
                (5, f"=C{r}-D{r}", money),
                (6, f'=IFERROR(D{r}/C{r},"")', "0.0%"),
                (7, f'=IF(C{r}=0,"—",IF(D{r}>C{r},"Over",IF(D{r}>C{r}*0.85,"Close","On track")))',
                 "General")):
            c = ws.cell(row=r, column=cidx, value=formula)
            c.font = Font(name=FONT, size=10, color=INK)
            c.number_format = fmt
        ws.cell(row=r, column=8, value=b["notes"] or "").font = Font(name=FONT, size=9, color=MUTED)

    end = start + len(budgets)
    if budgets:
        tot = end + 1
        ws.cell(row=tot, column=2, value="Total budgeted").font = Font(name=FONT, size=10, bold=True)
        for cidx in (3, 4, 5):
            col = get_column_letter(cidx)
            c = ws.cell(row=tot, column=cidx, value=f"=SUM({col}{start + 1}:{col}{end})")
            c.number_format = money
            c.font = Font(name=FONT, size=10, bold=True)
            c.border = Border(top=Side(style="thin", color=INK))
        c = ws.cell(row=tot, column=6, value=f'=IFERROR(D{tot}/C{tot},"")')
        c.number_format = "0.0%"
        c.font = Font(name=FONT, size=10, bold=True)

        rng = f"F{start + 1}:F{end}"
        ws.conditional_formatting.add(rng, DataBarRule(start_type="num", start_value=0,
                                                       end_type="num", end_value=1.2,
                                                       color="7BA7E8"))
        ws.conditional_formatting.add(
            f"G{start + 1}:G{end}",
            CellIsRule(operator="equal", formula=['"Over"'],
                       font=Font(name=FONT, size=10, bold=True, color=RED),
                       fill=PatternFill("solid", fgColor="FDE8E8")))
        ws.conditional_formatting.add(
            f"G{start + 1}:G{end}",
            CellIsRule(operator="equal", formula=['"Close"'],
                       font=Font(name=FONT, size=10, bold=True, color=AMBER)))
        ws.conditional_formatting.add(
            f"G{start + 1}:G{end}",
            CellIsRule(operator="equal", formula=['"On track"'],
                       font=Font(name=FONT, size=10, color=GREEN)))
    else:
        _label(ws, f"A{start + 1}",
               "No budgets set yet — add them in Mittens & Pence (Budgets → Set a budget), or type a "
               "section, a category and an amount straight into this table.")

    # unbudgeted spend for the same month
    r2 = end + 3
    _title(ws, f"A{r2}", "This month at a glance", 12)
    for i, (lbl, formula) in enumerate([
            ("Money in", f'=SUMIFS(Transactions!$F$5:$F${tx_last},'
                         f'Transactions!$J$5:$J${tx_last},$G$1,'
                         f'Transactions!$F$5:$F${tx_last},">0",'
                         f'Transactions!$K$5:$K${tx_last},"")'),
            ("Money out", f'=-SUMIFS(Transactions!$F$5:$F${tx_last},'
                          f'Transactions!$J$5:$J${tx_last},$G$1,'
                          f'Transactions!$F$5:$F${tx_last},"<0",'
                          f'Transactions!$K$5:$K${tx_last},"")'),
            ("Difference", None)]):
        rr = r2 + 1 + i
        _label(ws, f"A{rr}", lbl, bold=True)
        _num(ws, f"C{rr}", formula if formula else f"=C{r2 + 1}-C{r2 + 2}", money,
             bold=(lbl == "Difference"))
    _label(ws, f"A{r2 + 5}", "Transfers between your own accounts are excluded from both lines.")
    return ws


def _spending_tab(wb, base, months=12):
    ws = wb.create_sheet("Spending by month")
    money = _ccy(base)
    _title(ws, "A1", "Spending by section, month by month")
    hist = budget_engine.history(months=months)
    sections = sorted({s for h in hist for s in h["sections"]
                       if s not in config.NON_SPEND_PARENTS})
    headers = ["Month", "Income", "Total spend", "Saved", "Surplus"] + sections
    rows = []
    for h in hist:
        rows.append([h["period"], h["income"], h["spend"], h["saving"], h["net"]]
                    + [h["sections"].get(s, 0.0) for s in sections])
    fmts = {i: money for i in range(2, len(headers) + 1)}
    end = _table(ws, 3, headers, rows,
                 [12, 14, 14, 12, 13] + [16] * len(sections), fmts)

    if len(rows) >= 2:
        ch = LineChart()
        ch.title = "Income vs spending"
        ch.height, ch.width = 9, 22
        ch.y_axis.numFmt = money
        ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=3, max_row=end),
                    titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=4, max_row=end))
        ws.add_chart(ch, f"A{end + 3}")

        if sections:
            bar = BarChart()
            bar.type = "col"
            bar.grouping = "stacked"
            bar.overlap = 100
            bar.title = "Where it went"
            bar.height, bar.width = 9, 22
            bar.y_axis.numFmt = money
            bar.add_data(Reference(ws, min_col=6, max_col=5 + len(sections),
                                   min_row=3, max_row=end), titles_from_data=True)
            bar.set_categories(Reference(ws, min_col=1, min_row=4, max_row=end))
            ws.add_chart(bar, f"A{end + 22}")
    return ws


def _this_month_tab(wb, base, period):
    ws = wb.create_sheet("This month")
    money = _ccy(base)
    start, end_d = budget_engine.month_bounds(period)
    spend = budget_engine.spend_by_category(start, end_d)
    _title(ws, "A1", f"{dt.date.fromisoformat(start):%B %Y}")
    _label(ws, "A2", f"{start} to {end_d}")

    for i, (lbl, key, color) in enumerate([
            ("Money in", "income", GREEN), ("Spending", "spend", RED),
            ("Into savings & investments", "saving", ACCENT), ("Left over", "net", INK)]):
        r = 4 + i
        _label(ws, f"A{r}", lbl, bold=True, color=INK)
        _num(ws, f"C{r}", spend["totals"][key], money, bold=True, color=color)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["C"].width = 16

    rows = []
    for section, sec in sorted(spend["sections"].items(), key=lambda kv: -kv[1]["spend"]):
        if sec["spend"] <= 0:
            continue
        rows.append([section, "", sec["spend"], sec["count"]])
        for line in sorted(sec["lines"], key=lambda x: x["amount"]):
            amt = max(-line["amount"], 0.0)
            if amt <= 0:
                continue
            rows.append(["", line["name"], amt, line["count"]])
    end = _table(ws, 10, ["Section", "Category", f"Spent ({base})", "Transactions"],
                 rows, [24, 28, 16, 13], {3: money})
    for r in range(11, end + 1):
        if ws.cell(row=r, column=1).value:
            for cidx in range(1, 5):
                ws.cell(row=r, column=cidx).font = Font(name=FONT, size=10, bold=True, color=INK)

    top = [r for r in rows if r[0]][:10]
    if top:
        anchor = 10
        pie = PieChart()
        pie.title = "Spending by section"
        pie.height, pie.width = 10, 15
        srow = 10
        # build a compact block for the chart to read
        ws.cell(row=srow, column=7, value="Section").font = Font(name=FONT, size=9, bold=True)
        ws.cell(row=srow, column=8, value="Spent").font = Font(name=FONT, size=9, bold=True)
        for i, t in enumerate(top, start=1):
            ws.cell(row=srow + i, column=7, value=t[0]).font = Font(name=FONT, size=9)
            c = ws.cell(row=srow + i, column=8, value=t[2])
            c.number_format = money
            c.font = Font(name=FONT, size=9)
        pie.add_data(Reference(ws, min_col=8, min_row=srow, max_row=srow + len(top)),
                     titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=7, min_row=srow + 1, max_row=srow + len(top)))
        ws.add_chart(pie, "J10")
        ws.column_dimensions["G"].width = 24
        ws.column_dimensions["H"].width = 14
    return ws


def _categories_tab(wb):
    ws = wb.create_sheet("Categories")
    _title(ws, "A1", "The category list")
    _label(ws, "A2", "Sections and the lines inside them. Mittens & Pence sorts transactions into these; "
                     "you can add your own in the app.")
    rows = db.rows("SELECT parent, name, is_income, is_transfer, is_saving FROM categories "
                   "ORDER BY sort_order, parent, name")
    data = [[r["parent"], r["name"],
             "Income" if r["is_income"] else ("Transfer" if r["is_transfer"]
                                              else ("Saving" if r["is_saving"] else "Spending"))]
            for r in rows]
    _table(ws, 4, ["Section", "Category", "Counts as"], data, [24, 30, 14])
    return ws


def _readme(wb, base, period):
    ws = wb.create_sheet("Read me")
    _style_base(ws)
    _title(ws, "A1", "About this workbook")
    lines = [
        "",
        "The banking side of Mittens & Pence: what is in each account, where the money went, and how "
        "each budget is doing.",
        "",
        "TABS",
        "  Budgets            budget vs spent per section and per line. Change the month in the",
        "                     yellow cell (G1) and everything recalculates.",
        "  This month         a snapshot of the current month, with a chart.",
        "  Spending by month  the last twelve months side by side.",
        "  Transactions       the source data. Filters are on — use them.",
        "  Accounts           balances and how each one updates.",
        "  Categories         the list Mittens & Pence sorts things into.",
        "",
        "HOW SPENT IS CALCULATED",
        "  Spent = -SUMIFS over the Transactions tab, filtered on the month, the section, the",
        "  category, and amount < 0. Money out is stored negative, so the leading minus makes",
        "  the figure read as a positive amount spent.",
        "",
        "WHAT IS EXCLUDED",
        "  Transfers between your own accounts (column K says Yes) never count as spending or",
        "  income — otherwise moving £500 from current to savings would look like both.",
        "  Money into an ISA, pension or TFSA is counted as saving, on its own line.",
        "",
        "YELLOW CELLS ARE YOURS",
        "  The month selector and the Budget column. Everything else is a formula.",
        "",
        f"Base currency: {base}. Foreign amounts converted at the rate on the day of export.",
        f"Exported by Mittens & Pence {config.APP_VERSION} on {dt.datetime.now():%d %B %Y at %H:%M}.",
    ]
    for i, line in enumerate(lines, start=2):
        c = ws.cell(row=i, column=1, value=line)
        bold = line.isupper() and len(line) < 40
        c.font = Font(name=FONT, size=10, bold=bold, color=INK if bold else MUTED)
    ws.column_dimensions["A"].width = 105
    return ws


def build(path: pathlib.Path | None = None, period: str | None = None) -> pathlib.Path:
    base = config.settings["base_currency"]
    period = period or budget_engine.current_period()
    path = pathlib.Path(path) if path else (
        config.exports_dir() / f"Banking & Budgets {dt.date.today():%Y-%m-%d}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)

    _, tx_end = _transactions_tab(wb, base)
    _accounts_tab(wb, base)
    _categories_tab(wb)
    _spending_tab(wb, base)
    _this_month_tab(wb, base, period)
    _readme(wb, base, period)
    _budgets_tab(wb, base, tx_end, period)
    wb.active = 0
    wb.save(path)
    db.log("export.banking", {"path": str(path)})
    return path
