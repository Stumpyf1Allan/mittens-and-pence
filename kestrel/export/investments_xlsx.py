"""Rebuild the Investments workbook.

Same tabs, same column headers and the same formulas as Investments 2, but fed from
Mittens & Pence's database instead of hand-maintained cross-sheet references — so adding a
holding or a new broker doesn't mean rewiring forty rows of `=Freetrade!G14`.

Everything derived is a real Excel formula, so the workbook still recalculates if you
edit a cost or a share count by hand. The raw numbers live on the *_Data tabs.
"""

from __future__ import annotations

import datetime as dt
import pathlib

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import config, db
from ..engine import portfolio, snapshots
from ..market import prices as market

FONT = "Arial"

INK = "1F2933"
MUTED = "6B7280"
ACCENT = "1D4ED8"
GREEN = "047857"
RED = "B91C1C"
HEAD_FILL = "1F3A5F"
BAND = "F3F6FB"
INPUT_BLUE = "0000FF"

MAX_ROWS = 400          # room to add positions by hand
FIRST_DATA_ROW = 13     # keeps the familiar header block above the table

WRAPPER_TITLES = {
    "isa": "ISA", "gia": "GIA", "sipp": "SIPP", "tfsa": "TFSA (SA)",
    "ra": "Retirement Annuity (SA)", "trading": "Trading", "pension": "Pension",
    "unit_trust": "Unit Trusts", "crypto": "Crypto",
}

COLUMNS = ["Buy Dates", "Symbol", "Exchange", "Stock name", "TOTAL Money made",
           "TOTAL Percent change", "# shares", "Cost", "Current Price", "Price change",
           "% change", "Inv now", "Divs", "Overall change", "Total value", "Sector"]
WIDTHS = [20, 10, 10, 34, 15, 15, 13, 12, 13, 13, 11, 13, 11, 14, 13, 30]


def _ccy_format(currency: str) -> str:
    sym = {"GBP": "£", "ZAR": "R ", "USD": "$", "EUR": "€"}.get(currency, "")
    return f'{sym}#,##0.00;[Red]-{sym}#,##0.00;"-"'


def _sheet_ref(name: str) -> str:
    return f"'{name}'" if any(ch in name for ch in " &-()") else name


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------

def _style_base(ws):
    ws.sheet_view.showGridLines = False


def _title(ws, cell: str, text: str, size: int = 16):
    ws[cell] = text
    ws[cell].font = Font(name=FONT, size=size, bold=True, color=INK)


def _label(ws, cell: str, text: str, bold: bool = False, color: str = MUTED):
    ws[cell] = text
    ws[cell].font = Font(name=FONT, size=10, bold=bold, color=color)


def _num(ws, cell: str, value, fmt: str, bold: bool = False, color: str = INK):
    ws[cell] = value
    ws[cell].font = Font(name=FONT, size=10, bold=bold, color=color)
    ws[cell].number_format = fmt


def _header_row(ws, row: int, headers: list[str], widths: list[int] | None = None):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(name=FONT, size=9, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=HEAD_FILL)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(bottom=Side(style="thin", color="FFFFFF"))
    if widths:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 28
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _write_table(ws, start_row: int, headers: list[str], rows: list[list],
                 widths: list[int] | None = None, formats: dict | None = None):
    _style_base(ws)
    _header_row(ws, start_row, headers, widths)
    formats = formats or {}
    for r, data in enumerate(rows, start=start_row + 1):
        for i, v in enumerate(data, start=1):
            c = ws.cell(row=r, column=i, value=v)
            c.font = Font(name=FONT, size=10, color=INK)
            if i in formats:
                c.number_format = formats[i]
            if r % 2 == 0:
                c.fill = PatternFill("solid", fgColor=BAND)
    return start_row + len(rows)


# ---------------------------------------------------------------------------
# Data tabs
# ---------------------------------------------------------------------------

def _settings_tab(wb, base: str):
    ws = wb.create_sheet("Settings")
    _style_base(ws)
    _title(ws, "A1", "Mittens & Pence — workbook settings")
    _label(ws, "A3", "Anything in blue is yours to change; the rest is calculated.", color=MUTED)

    _label(ws, "A5", "Base currency", bold=True)
    ws["B5"] = base
    ws["B5"].font = Font(name=FONT, size=10, bold=True, color=INPUT_BLUE)

    _label(ws, "A6", "Portfolio start date", bold=True)
    ws["B6"] = dt.date.fromisoformat(config.settings["portfolio_start_date"])
    ws["B6"].number_format = "dd/mm/yyyy"
    ws["B6"].font = Font(name=FONT, size=10, bold=True, color=INPUT_BLUE)

    _label(ws, "A7", "Today", bold=True)
    ws["B7"] = "=TODAY()"
    ws["B7"].number_format = "dd/mm/yyyy"

    _label(ws, "A8", "Years invested", bold=True)
    ws["B8"] = "=MAX((B7-B6)/365,0.01)"
    ws["B8"].number_format = "0.00"

    _label(ws, "A10", "Exchange rates used (1 unit = ? " + base + ")", bold=True)
    _header_row(ws, 11, ["Pair", "Rate", "As of"], [12, 14, 14])
    r = 12
    for ccy in ("USD", "EUR", "ZAR", "GBP"):
        if ccy == base:
            continue
        try:
            rate = market.fx_rate(ccy, base)
        except Exception:
            rate = None
        ws.cell(row=r, column=1, value=f"{ccy}{base}").font = Font(name=FONT, size=10)
        c = ws.cell(row=r, column=2, value=rate)
        c.number_format = "0.000000"
        c.font = Font(name=FONT, size=10, color=INPUT_BLUE)
        ws.cell(row=r, column=3, value=dt.date.today()).number_format = "dd/mm/yyyy"
        r += 1
    _label(ws, f"A{r + 1}",
           "Source: live quotes at export time (Yahoo Finance, with Stooq and ECB as fallbacks).")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 18
    return ws


def _prices_tab(wb, rows: list[dict], base: str):
    ws = wb.create_sheet("Prices")
    data = {}
    for r in rows:
        data[r["symbol"]] = [r["symbol"], r["exchange"] or "", r["name"], r["sector"],
                             r["price"], base, r["price_as_of"], r["day_change_pct"],
                             r["price_native"], r["price_native_currency"]]
    _title(ws, "A1", "Live prices at export time")
    _label(ws, "A2", "One row per instrument. The presentation tabs look prices up from here.")
    end = _write_table(
        ws, 4,
        ["Symbol", "Exchange", "Name", "Sector", f"Price ({base})", "Currency",
         "As of", "Day change", "Native price", "Native currency"],
        sorted(data.values(), key=lambda x: x[0]),
        [12, 10, 34, 30, 14, 10, 20, 12, 14, 14],
        {5: _ccy_format(base), 8: "0.0%", 9: "#,##0.0000"})
    return ws, end


def _holdings_data_tab(wb, rows: list[dict], base: str):
    ws = wb.create_sheet("Holdings_Data")
    _title(ws, "A1", "Holdings — one row per position per account")
    _label(ws, "A2", "This is the raw data Mittens & Pence exported. The ISA/GIA and platform tabs "
                     "add these up with SUMIFS.")
    data = [[r["account_name"], r["account_type"], r["institution"] or "", r["member"] or "",
             r["symbol"], r["exchange"] or "", r["name"], r["sector"], r["shares"], r["cost"],
             r["buy_dates"] or "", r["isin"] or ""] for r in rows]
    _write_table(ws, 4,
                 ["Account", "Wrapper", "Institution", "Member", "Symbol", "Exchange",
                  "Name", "Sector", "# shares", f"Cost ({base})", "Buy dates", "ISIN"],
                 data, [26, 10, 22, 14, 12, 10, 34, 30, 14, 14, 24, 16],
                 {9: "#,##0.000000", 10: _ccy_format(base)})
    return ws


def _dividends_data_tab(wb, base: str):
    ws = wb.create_sheet("Dividends_Data")
    rows = db.rows("""SELECT d.paid_on, a.name AS account, a.account_type, i.symbol,
                             d.amount, d.currency, d.shares, d.per_share
                      FROM dividends d
                      JOIN accounts a ON a.id=d.account_id
                      JOIN instruments i ON i.id=d.instrument_id
                      ORDER BY d.paid_on DESC""")
    data = []
    for r in rows:
        data.append([r["paid_on"], r["account"], r["account_type"], r["symbol"],
                     market.convert(r["amount"] or 0, r["currency"] or base, base),
                     r["currency"], r["shares"], r["per_share"]])
    _title(ws, "A1", "Dividends received")
    _write_table(ws, 3,
                 ["Paid on", "Account", "Wrapper", "Symbol", f"Amount ({base})",
                  "Original currency", "Shares", "Per share"],
                 data, [12, 26, 10, 12, 14, 16, 14, 12],
                 {5: _ccy_format(base), 7: "#,##0.0000", 8: "#,##0.000000"})
    return ws


def _trades_data_tab(wb, base: str):
    ws = wb.create_sheet("Trades_Data")
    rows = db.rows("""SELECT t.traded_on, a.name AS account, a.account_type, i.symbol, t.side,
                             t.shares, t.price, t.price_currency, t.total, t.fees
                      FROM trades t JOIN accounts a ON a.id=t.account_id
                      JOIN instruments i ON i.id=t.instrument_id
                      ORDER BY t.traded_on DESC""")
    data = [[r["traded_on"], r["account"], r["account_type"], r["symbol"], r["side"],
             r["shares"], r["price"], r["price_currency"], r["total"], r["fees"]] for r in rows]
    _title(ws, "A1", "Every buy and sell Mittens & Pence has seen")
    _write_table(ws, 3,
                 ["Date", "Account", "Wrapper", "Symbol", "Side", "Shares", "Price",
                  "Price currency", "Total", "Fees"],
                 data, [12, 26, 10, 12, 8, 14, 12, 14, 12, 10],
                 {6: "#,##0.000000", 7: "#,##0.0000", 9: "#,##0.00", 10: "#,##0.00"})
    return ws


def _cash_data_tab(wb, cash: list[dict], base: str):
    ws = wb.create_sheet("Cash_Data")
    data = [[c["account"], c["account_type"], c["institution"] or "", c["cash"],
             c["net_deposits"], c["interest"]] for c in cash]
    _title(ws, "A1", "Cash held on the investment platforms")
    _write_table(ws, 3,
                 ["Account", "Wrapper", "Institution", f"Cash ({base})",
                  "Net deposits", "Interest earned"],
                 data, [26, 10, 22, 14, 14, 16],
                 {4: _ccy_format(base), 5: _ccy_format(base), 6: _ccy_format(base)})
    return ws


# ---------------------------------------------------------------------------
# Presentation tabs
# ---------------------------------------------------------------------------

def _positions_tab(wb, title: str, scope_kind: str, scope_key: str, symbols: list[str],
                   meta: dict, base: str):
    """A wrapper (ISA/GIA) or platform tab: header block + the familiar table."""
    ws = wb.create_sheet(title[:31])
    _style_base(ws)
    money = _ccy_format(base)

    _title(ws, "A1", title)
    ws["B1"] = scope_key
    ws["B1"].font = Font(name=FONT, size=9, color="FFFFFF")   # the key SUMIFS filters on
    _label(ws, "C1", meta.get("subtitle", ""), color=MUTED)

    hd = "Holdings_Data"
    dd = "Dividends_Data"
    cd = "Cash_Data"
    col = "$B" if scope_kind == "wrapper" else "$A"           # wrapper column vs account column
    last = FIRST_DATA_ROW + MAX_ROWS - 1

    _label(ws, "A3", "Invested value", bold=True)
    _num(ws, "B3", f"=SUM(L{FIRST_DATA_ROW}:L{last})", money, bold=True)
    _label(ws, "A4", "Cost value", bold=True)
    _num(ws, "B4", f"=SUM(H{FIRST_DATA_ROW}:H{last})", money, bold=True)
    _label(ws, "A5", "Gross returns", bold=True)
    _num(ws, "B5", f"=B3+SUM(M{FIRST_DATA_ROW}:M{last})", money, bold=True)
    _label(ws, "A6", "Cash interest", bold=True)
    _num(ws, "B6", f"=SUMIFS({cd}!$F:$F,{cd}!{col}:{col},$B$1)", money)
    _label(ws, "A7", "Cash held", bold=True)
    _num(ws, "B7", f"=SUMIFS({cd}!$D:$D,{cd}!{col}:{col},$B$1)", money)
    _label(ws, "A8", "Net returns", bold=True)
    _num(ws, "B8", "=B5-B4", money, bold=True, color=GREEN)
    _num(ws, "C8", '=IFERROR(B8/B5,"")', "0.0%", bold=True)

    _label(ws, "E5", "OVERALL", bold=True, color=INK)
    _label(ws, "F5", "/YR", bold=True, color=INK)
    _label(ws, "D7", "Balanced Gain/Loss", bold=True)
    _num(ws, "E7", "=B5-B4", money)
    _num(ws, "F7", '=IFERROR(E7/B5,"")', "0.0%")
    _num(ws, "G7", '=IFERROR(F7/Settings!$B$8,"")', "0.0%")
    _label(ws, "D8", "Investment Gain/Loss", bold=True)
    _num(ws, "E8", f"=SUM(J{FIRST_DATA_ROW}:J{last})", money)
    _num(ws, "F8", '=IFERROR(E8/B3,"")', "0.0%")
    _num(ws, "G8", '=IFERROR(F8/Settings!$B$8,"")', "0.0%")
    _label(ws, "D9", "Dividends", bold=True)
    _num(ws, "E9", f"=SUM(M{FIRST_DATA_ROW}:M{last})", money)
    _num(ws, "F9", '=IFERROR(E9/B4,"")', "0.0%")

    _label(ws, "I3", "Positions", bold=True)
    _num(ws, "J3", f'=COUNTIF(B{FIRST_DATA_ROW}:B{last},"?*")', "#,##0")
    _label(ws, "I4", "Total with cash", bold=True)
    _num(ws, "J4", "=B5+B7", money, bold=True)

    _header_row(ws, FIRST_DATA_ROW - 1, COLUMNS, WIDTHS)

    for i, sym in enumerate(symbols):
        r = FIRST_DATA_ROW + i
        ws.cell(row=r, column=2, value=sym).font = Font(name=FONT, size=10, bold=True, color=INK)
        f = {
            1: f'=IFERROR(INDEX(Holdings_Data!$K:$K,MATCH($B{r},Holdings_Data!$E:$E,0)),"")',
            3: f'=IFERROR(INDEX(Prices!$B:$B,MATCH($B{r},Prices!$A:$A,0)),"")',
            4: f'=IFERROR(INDEX(Prices!$C:$C,MATCH($B{r},Prices!$A:$A,0)),"")',
            5: f"=O{r}-H{r}",
            6: f'=IFERROR(E{r}/H{r},"")',
            7: f"=SUMIFS({hd}!$I:$I,{hd}!$E:$E,$B{r},{hd}!{col}:{col},$B$1)",
            8: f"=SUMIFS({hd}!$J:$J,{hd}!$E:$E,$B{r},{hd}!{col}:{col},$B$1)",
            9: f'=IFERROR(INDEX(Prices!$E:$E,MATCH($B{r},Prices!$A:$A,0)),0)',
            10: f"=L{r}-H{r}",
            11: f'=IFERROR(J{r}/H{r},"")',
            12: f"=G{r}*I{r}",
            13: f"=SUMIFS({dd}!$E:$E,{dd}!$D:$D,$B{r},{dd}!$C:$C,$B$1)"
                if scope_kind == "wrapper" else
                f"=SUMIFS({dd}!$E:$E,{dd}!$D:$D,$B{r},{dd}!$B:$B,$B$1)",
            14: f'=IFERROR((J{r}+M{r})/H{r},"")',
            15: f"=L{r}+M{r}",
            16: f'=IFERROR(INDEX(Prices!$D:$D,MATCH($B{r},Prices!$A:$A,0)),"")',
        }
        for cidx, formula in f.items():
            cell = ws.cell(row=r, column=cidx, value=formula)
            cell.font = Font(name=FONT, size=10, color=INK)
        for cidx in (5, 8, 9, 10, 12, 13, 15):
            ws.cell(row=r, column=cidx).number_format = money
        for cidx in (6, 11, 14):
            ws.cell(row=r, column=cidx).number_format = "0.0%"
        ws.cell(row=r, column=7).number_format = "#,##0.000000"
        if i % 2 == 1:
            for cidx in range(1, len(COLUMNS) + 1):
                ws.cell(row=r, column=cidx).fill = PatternFill("solid", fgColor=BAND)

    n = len(symbols)
    if n:
        rng = f"E{FIRST_DATA_ROW}:F{FIRST_DATA_ROW + n - 1}"
        ws.conditional_formatting.add(
            rng, CellIsRule(operator="lessThan", formula=["0"],
                            font=Font(name=FONT, size=10, color=RED)))
        ws.conditional_formatting.add(
            rng, CellIsRule(operator="greaterThan", formula=["0"],
                            font=Font(name=FONT, size=10, color=GREEN)))
    _label(ws, f"A{FIRST_DATA_ROW + max(n, 1) + 2}",
           "Add a row by typing a symbol in column B — every other column fills itself in.")
    return ws


def _overall_tab(wb, ov: dict, wrappers: list[str], base: str):
    ws = wb.create_sheet("Overall", 0)
    _style_base(ws)
    money = _ccy_format(base)

    _title(ws, "A1", "OVERALL", 20)
    _label(ws, "A2", f"Every wrapper and platform Mittens & Pence tracks · exported "
                     f"{dt.datetime.now():%d %b %Y at %H:%M}")

    _label(ws, "H1", "Start date", bold=True)
    ws["I1"] = "=Settings!B6"
    ws["I1"].number_format = "dd/mm/yyyy"
    _label(ws, "H2", "Today", bold=True)
    ws["I2"] = "=TODAY()"
    ws["I2"].number_format = "dd/mm/yyyy"
    _label(ws, "H3", "Years", bold=True)
    _num(ws, "I3", "=Settings!B8", "0.00")

    heads = ["Money Total", "Dividends", "Div %", "Invested (cost)", "Net", "Net %",
             "Net / Yr", "Cash", "Total incl. cash"]
    _header_row(ws, 4, heads, [16, 14, 10, 16, 14, 10, 10, 14, 18])

    refs = [_sheet_ref(WRAPPER_TITLES.get(w, w.upper())) for w in wrappers]
    total_gross = "+".join(f"{r}!$B$5" for r in refs) or "0"
    total_cost = "+".join(f"{r}!$B$4" for r in refs) or "0"
    total_divs = "+".join(f"{r}!$E$9" for r in refs) or "0"
    total_cash = "+".join(f"{r}!$B$7" for r in refs) or "0"

    _num(ws, "A5", f"={total_gross}", money, bold=True)
    _num(ws, "B5", f"={total_divs}", money)
    _num(ws, "C5", '=IFERROR(B5/A5,"")', "0.0%")
    _num(ws, "D5", f"={total_cost}", money)
    _num(ws, "E5", "=A5-D5", money, bold=True, color=GREEN)
    _num(ws, "F5", '=IFERROR(E5/D5,"")', "0.0%", bold=True)
    _num(ws, "G5", '=IFERROR(F5/Settings!$B$8,"")', "0.0%")
    _num(ws, "H5", f"={total_cash}", money)
    _num(ws, "I5", "=A5+H5", money, bold=True)

    _title(ws, "A8", "By wrapper", 12)
    _header_row(ws, 9, ["Wrapper", "Cost", "Invested now", "Dividends", "Gross", "Net",
                        "Net %", "Cash", "Positions"], [22, 14, 15, 13, 14, 14, 10, 13, 11])
    r = 10
    for w, ref in zip(wrappers, refs):
        ws.cell(row=r, column=1, value=WRAPPER_TITLES.get(w, w.upper())).font = \
            Font(name=FONT, size=10, bold=True, color=ACCENT)
        for cidx, formula, fmt in (
                (2, f"={ref}!$B$4", money), (3, f"={ref}!$B$3", money),
                (4, f"={ref}!$E$9", money), (5, f"={ref}!$B$5", money),
                (6, f"={ref}!$B$8", money), (7, f"={ref}!$C$8", "0.0%"),
                (8, f"={ref}!$B$7", money), (9, f"={ref}!$J$3", "#,##0")):
            c = ws.cell(row=r, column=cidx, value=formula)
            c.font = Font(name=FONT, size=10, color=INK)
            c.number_format = fmt
        r += 1
    ws.cell(row=r, column=1, value="Total").font = Font(name=FONT, size=10, bold=True)
    for cidx in range(2, 10):
        col = get_column_letter(cidx)
        c = ws.cell(row=r, column=cidx,
                    value=f"=SUM({col}10:{col}{r - 1})" if cidx != 7
                    else '=IFERROR(F%d/B%d,"")' % (r, r))
        c.font = Font(name=FONT, size=10, bold=True)
        c.number_format = "0.0%" if cidx == 7 else money
        c.border = Border(top=Side(style="thin", color=INK))

    # ---- realised, from the Sold tab -----------------------------------
    _title(ws, f"A{r + 2}", "Realised (sold positions)", 12)
    _label(ws, f"A{r + 3}", "Made after dividends")
    _num(ws, f"C{r + 3}", "=IFERROR(Sold!$H$3,0)", money, bold=True)
    _label(ws, f"A{r + 4}", "Average return per position")
    _num(ws, f"C{r + 4}", "=IFERROR(Sold!$H$4,0)", "0.0%")
    _label(ws, f"A{r + 6}",
           "Headline figure the original workbook showed (net % plus the average realised %):",
           color=MUTED)
    _num(ws, f"C{r + 6}", f"=IFERROR(F5+C{r + 4},0)", "0.0%", bold=True, color=ACCENT)

    ws.column_dimensions["A"].width = 26
    return ws


def _sold_tab(wb, sold: list[dict], base: str):
    ws = wb.create_sheet("Sold")
    money = _ccy_format(base)
    _title(ws, "A1", "Sold positions")
    _label(ws, "A2", "Closed automatically when a holding reaches zero shares, plus anything "
                     "you added by hand.")
    _label(ws, "G3", "Total made", bold=True)
    _label(ws, "G4", "Average return", bold=True)

    start = 6
    heads = ["Symbol", "Exchange", "Bought", "Sold", "Shares", "Cost", "Proceeds",
             "Dividends", "Gain", "Gain %", "Total made", "Total %", "If left in today",
             "Missed out?"]
    rows = []
    for s in sold:
        rows.append([s["symbol"], s["exchange"] or "", s["bought_on"], s["sold_on"],
                     s["sell_shares"], s["cost"], s["proceeds"], s["dividends"],
                     None, None, None, None, s["if_left_in"], None])
    _write_table(ws, start, heads, rows,
                 [12, 10, 12, 12, 14, 13, 13, 12, 13, 10, 13, 10, 16, 14],
                 {6: money, 7: money, 8: money, 13: money})
    for i in range(len(sold)):
        r = start + 1 + i
        for cidx, formula, fmt in (
                (9, f"=G{r}-F{r}", money),
                (10, f'=IFERROR(I{r}/F{r},"")', "0.0%"),
                (11, f"=G{r}+H{r}-F{r}", money),
                (12, f'=IFERROR(K{r}/F{r},"")', "0.0%"),
                (14, f'=IFERROR(G{r}-M{r},"")', money)):
            c = ws.cell(row=r, column=cidx, value=formula)
            c.font = Font(name=FONT, size=10, color=INK)
            c.number_format = fmt
    end = start + len(sold)
    _num(ws, "H3", f"=SUM(K{start + 1}:K{max(end, start + 1)})", money, bold=True)
    _num(ws, "H4", f'=IFERROR(AVERAGE(L{start + 1}:L{max(end, start + 1)}),0)', "0.0%", bold=True)
    _label(ws, f"A{end + 2}",
           '"If left in today" values the shares you sold at today\'s price, so "Missed out?" '
           "is what selling cost you (negative means selling was the right call).")
    return ws


def _sectors_tab(wb, sectors: list[dict], base: str):
    ws = wb.create_sheet("Sectors")
    money = _ccy_format(base)
    _title(ws, "A1", "Where the money actually is")
    rows = [[s["sector"], s["value"], None] for s in sectors]
    _write_table(ws, 3, ["Sector", f"Value ({base})", "Share"], rows, [36, 16, 12], {2: money})
    total_row = 3 + len(rows) + 1
    for i in range(len(rows)):
        r = 4 + i
        c = ws.cell(row=r, column=3, value=f'=IFERROR(B{r}/$B${total_row},"")')
        c.number_format = "0.0%"
        c.font = Font(name=FONT, size=10, color=INK)
    ws.cell(row=total_row, column=1, value="Total").font = Font(name=FONT, size=10, bold=True)
    tc = ws.cell(row=total_row, column=2, value=f"=SUM(B4:B{total_row - 1})")
    tc.number_format = money
    tc.font = Font(name=FONT, size=10, bold=True)

    if rows:
        pie = PieChart()
        pie.title = "Allocation by sector"
        pie.height, pie.width = 10, 16
        pie.add_data(Reference(ws, min_col=2, min_row=3, max_row=3 + len(rows)), titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=1, min_row=4, max_row=3 + len(rows)))
        ws.add_chart(pie, "E3")
    return ws


def _monthly_tab(wb, base: str):
    ws = wb.create_sheet("Monthly")
    money = _ccy_format(base)
    _title(ws, "A1", "Month by month")
    _label(ws, "A2", "Mittens & Pence freezes these figures on the first of each month. "
                     "They are history — nothing recalculates them.")
    series = snapshots.series("household", "net_worth", months=36)
    rows = []
    for s in series:
        m = s["metrics"]
        rows.append([s["period"], m.get("net_worth"), m.get("investments"),
                     m.get("investment_cost"), m.get("dividends_to_date"), m.get("cash"),
                     m.get("income"), m.get("spend"), m.get("saving"), m.get("surplus")])
    _write_table(ws, 4,
                 ["Month", "Net worth", "Investments", "Cost", "Dividends to date",
                  "Cash", "Income", "Spending", "Saved", "Surplus"],
                 rows, [12, 16, 15, 14, 16, 13, 13, 13, 12, 13],
                 {i: money for i in range(2, 11)})
    if len(rows) >= 2:
        ch = LineChart()
        ch.title = "Net worth and investments"
        ch.height, ch.width = 9, 20
        ch.y_axis.numFmt = money
        data = Reference(ws, min_col=2, max_col=3, min_row=4, max_row=4 + len(rows))
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=5, max_row=4 + len(rows)))
        ws.add_chart(ch, "L4")
    elif not rows:
        _label(ws, "A6", "No snapshots yet — Mittens & Pence takes the first one automatically, "
                         "or press Take snapshot on the dashboard.")
    return ws


def _readme_tab(wb, base: str, notes: list[str]):
    ws = wb.create_sheet("Read me")
    _style_base(ws)
    _title(ws, "A1", "How this workbook is put together")
    lines = [
        "",
        "Mittens & Pence rebuilds this file from its database every time you export. Nothing here needs "
        "hand-maintaining — no deleting a tab and re-importing a CSV every three months.",
        "",
        "TABS",
        "  Overall            everything, plus the same headline figures the original showed.",
        "  ISA / GIA / …      one tab per wrapper. Same columns as before.",
        "  <platform>         one tab per broker account.",
        "  Sold               closed positions, with what they'd be worth if you'd held on.",
        "  Sectors            allocation, with a pie chart.",
        "  Monthly            the frozen month-end figures.",
        "  *_Data             the raw exported numbers. The other tabs read from these.",
        "  Settings           base currency, start date and the FX rates used.",
        "",
        "HOW THE SUMS WORK",
        "  # shares and Cost come from Holdings_Data with SUMIFS, filtered on the key in B1.",
        "  Current Price comes from the Prices tab with INDEX/MATCH.",
        "  Divs come from Dividends_Data with SUMIFS.",
        "  Everything else is arithmetic on those four, exactly as before:",
        "     Inv now = shares × price · Price change = Inv now − Cost · Total value = Inv now + Divs",
        "",
        "TO ADD A HOLDING BY HAND",
        "  Type the symbol into column B of any wrapper tab. The rest fills itself in — though "
        "  it will only find shares and cost if that symbol exists in Holdings_Data.",
        "",
        "NOTES ON THE ORIGINAL",
    ]
    for n in notes:
        lines.append(f"  • {n}")
    lines += [
        "",
        f"Base currency: {base}. Prices converted at the rates on the Settings tab.",
        f"Exported by Mittens & Pence {config.APP_VERSION} on {dt.datetime.now():%d %B %Y at %H:%M}.",
    ]
    for i, line in enumerate(lines, start=2):
        c = ws.cell(row=i, column=1, value=line)
        bold = line.isupper() and len(line) < 40
        c.font = Font(name=FONT, size=10, bold=bold,
                      color=INK if bold else MUTED)
    ws.column_dimensions["A"].width = 118
    return ws


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build(path: pathlib.Path | None = None) -> pathlib.Path:
    base = config.settings["base_currency"]
    path = pathlib.Path(path) if path else (
        config.exports_dir() / f"Investments {dt.date.today():%Y-%m-%d}.xlsx")

    rows = portfolio.holdings_rows()
    cash = portfolio.cash_rows()
    ov = portfolio.overall()
    sold = portfolio.sold_rows()

    wb = Workbook()
    wb.remove(wb.active)

    _settings_tab(wb, base)
    _prices_tab(wb, rows, base)
    _holdings_data_tab(wb, rows, base)
    _dividends_data_tab(wb, base)
    _trades_data_tab(wb, base)
    _cash_data_tab(wb, cash, base)

    wrappers = []
    for wt in ["isa", "gia", "sipp", "tfsa", "ra", "trading", "crypto", "unit_trust", "pension"]:
        syms = sorted({r["symbol"] for r in rows if r["account_type"] == wt})
        if syms:
            wrappers.append(wt)
            _positions_tab(wb, WRAPPER_TITLES.get(wt, wt.upper()), "wrapper", wt, syms,
                           {"subtitle": f"Everything held in a {WRAPPER_TITLES.get(wt, wt)}"}, base)

    seen = set()
    for acct in sorted({r["account_name"] for r in rows}):
        title = acct[:31]
        if title.lower() in seen or title.lower() in {w.lower() for w in WRAPPER_TITLES.values()}:
            title = (acct + " (acct)")[:31]
        seen.add(title.lower())
        syms = sorted({r["symbol"] for r in rows if r["account_name"] == acct})
        _positions_tab(wb, title, "account", acct, syms,
                       {"subtitle": "One platform"}, base)

    _sold_tab(wb, sold, base)
    _sectors_tab(wb, ov["sectors"], base)
    _monthly_tab(wb, base)

    notes = [
        "The original's 'Cash Interest' on the Freetrade tab summed column U of FT_Activity, "
        "which is the FX fee column — not interest. Mittens & Pence sums the INTEREST_FROM_CASH rows "
        "instead, so this figure will differ from the old sheet.",
        "The original capped each platform at ~30 rows (SUM(L11:L41)); this one has no cap.",
        "GOOGLEFINANCE has been replaced with live quotes fetched at export time and written "
        "to the Prices tab, so the file opens with real numbers even offline.",
    ]
    _readme_tab(wb, base, notes)

    _overall_tab(wb, ov, wrappers, base)
    wb.active = 0
    wb.save(path)
    db.log("export.investments", {"path": str(path), "positions": len(rows)})
    return path
