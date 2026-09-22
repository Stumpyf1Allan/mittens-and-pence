# The two workbooks

Every derived figure is a real Excel formula, not a pasted number. Edit a cost or a share
count by hand and the rest of the sheet recalculates. The raw numbers live on the
`*_Data` tabs and everything else reads from them.

---

## Investments workbook

### Tabs

| Tab | What's on it |
|---|---|
| **Overall** | The headline block, then one row per wrapper, then the realised figures from Sold. |
| **ISA / GIA / SIPP / TFSA …** | One per wrapper you actually hold. Same columns as before. |
| **&lt;platform&gt;** | One per broker account — Freetrade, Trading 212, and so on. |
| **Sold** | Closed positions, with what they'd be worth if you'd held on. |
| **Sectors** | Allocation, with a pie chart. |
| **Monthly** | Frozen month-end figures, with a line chart. |
| **Prices** | Every instrument's live price at export time. |
| **Holdings_Data** | One row per position per account: shares, cost, sector, buy dates. |
| **Dividends_Data** | Every dividend, converted to the base currency. |
| **Trades_Data** | Every buy and sell. |
| **Cash_Data** | Cash and interest per platform. |
| **Settings** | Base currency, start date, and the FX rates used. |
| **Read me** | How it all fits together. |

### What each column means

The wrapper tabs keep the original header names and order:

| Original (ISA tab) | Mittens & Pence | How it's worked out |
|---|---|---|
| Buy Dates | A | Looked up from `Holdings_Data` |
| Symbol | B | **Typed by you** — this is the only cell a new row needs |
| Exchange | C | `INDEX/MATCH` into `Prices` |
| Stock name | D | `INDEX/MATCH` into `Prices` |
| TOTAL Money made | E | `=O−H` (total value − cost) |
| TOTAL Percent change | F | `=E/H` |
| # shares | G | `SUMIFS(Holdings_Data, symbol, wrapper)` |
| Cost | H | `SUMIFS(Holdings_Data, symbol, wrapper)` |
| Current Price | I | `INDEX/MATCH` into `Prices` |
| Price change | J | `=L−H` |
| % change | K | `=J/H` |
| Inv now | L | `=G×I` |
| Divs | M | `SUMIFS(Dividends_Data, symbol, wrapper)` |
| Overall change | N | `=(J+M)/H` |
| Total value | O | `=L+M` |
| Sector | P | `INDEX/MATCH` into `Prices` |

Identical arithmetic to the original — only the *sources* changed. Where the old sheet
wrote `=Freetrade!G14`, Mittens & Pence writes a `SUMIFS` keyed on the symbol, so adding a
holding never means rewiring anything.

### Header block

| Original | Mittens & Pence | Formula |
|---|---|---|
| Invested value | B3 | `SUM` of the Inv now column |
| Cost value | B4 | `SUM` of the Cost column |
| Gross returns | B5 | `=B3 + SUM(Divs)` |
| Cash interest | B6 | `SUMIFS(Cash_Data)` — **see the note below** |
| Cash held | B7 | `SUMIFS(Cash_Data)` (new) |
| Net Returns | B8, C8 | `=B5−B4`, and as a percentage of gross |
| Balanced Gain/Loss | E7:G7 | `=B5−B4`, ÷ gross, ÷ years |
| Investment Gain/Loss | E8:G8 | `SUM` of Price change, ÷ invested, ÷ years |
| Dividends | E9:F9 | `SUM` of Divs, ÷ cost (new) |

`Settings!B8` holds the years elapsed, so every "/YR" figure comes from one place
instead of repeating `DATEDIF(Overall!L1, Overall!L2, "D")/365` in a dozen cells.

### One number that will differ

The original's **Cash Interest** on the Freetrade tab was `=SUM(FT_Activity!$U:$U)`.
Column U of the Freetrade export is **FX Fee Amount**, not interest. Mittens & Pence sums the
rows whose Type is `INTEREST_FROM_CASH`. Both figures are on the Cash_Data tab if you
want to compare them.

### The Sold tab

Same idea as before, plus the arithmetic spelled out:

* **Gain** = proceeds − cost
* **Total made** = proceeds + dividends − cost
* **If held today** = the shares you sold, at today's price
* **Missed out?** = proceeds − if-held-today. **Negative means selling was right.**

Rows appear automatically when a holding reaches zero shares. Anything older than your
export goes back can be added by hand (Investments → Record a sale).

---

## Banking & Budgets workbook

| Tab | What's on it |
|---|---|
| **Budgets** | Budget vs spent, per section and per line. The month selector in the yellow cell drives everything below it. |
| **This month** | A snapshot of the current month, with a chart. |
| **Spending by month** | Twelve months side by side, with two charts. |
| **Transactions** | The source data. AutoFilter is on. |
| **Accounts** | Balances and how each one updates. |
| **Categories** | The list Mittens & Pence sorts things into. |
| **Read me** | How it fits together. |

### How "Spent" is calculated

```
=-SUMIFS(Transactions!F:F,          ← the amount column
         Transactions!J:J, $G$1,     ← the month, from the selector
         Transactions!H:H, $A5,      ← the section
         Transactions!I:I, $B5,      ← the category (omitted for a whole section)
         Transactions!F:F, "<0")     ← money out only
```

Money out is stored negative, so the leading minus makes the figure read as a positive
amount spent. Change the month in **G1** and every row recalculates — it isn't a
picture of one month baked into the file.

### What's excluded, and why

* **Transfers between your own accounts** (column K says `Yes`) never count as
  spending or income. Without this, moving £500 from current to savings would appear
  as both.
* **Money into an ISA, SIPP or TFSA** counts as *saving*, on its own line. It isn't
  spending, and it isn't gone.

### The yellow cells

Only two things are yours to type: the **month selector** (G1) and the **Budget**
column. Everything else is a formula. That's the standard financial-model convention —
blue text for a typed input, black for a calculation.

---

## Getting them into Google Sheets

**By hand:** drag the `.xlsx` into Drive and open it with Sheets. Formulas, formatting
and charts all carry across. The file names are stable, so importing next month's over
the top keeps the same sheet.

**Automatically:** Spreadsheets → Google Sheets → set up publishing. You make a free
Google Cloud project and paste a Client ID and secret; Mittens & Pence then publishes into
your own Drive, replacing the same file each time. Anyone you've shared the sheet with
keeps their link and sees the current figures.

Mittens & Pence asks for `drive.file` — the narrowest scope Google offers. It can only see and
change the files it created itself, never the rest of your Drive.

---

## Verifying the numbers

The test suite checks the workbook against the engine on every build: `Holdings_Data`
row counts and values, the `Prices` tab, the ISA header block, and every budget row's
`SUMIFS` result. It also refuses any formula LibreOffice can't evaluate — `XLOOKUP`,
`FILTER`, `UNIQUE` and friends — which is why nothing here uses them.

To check by hand: open the workbook and compare the Overall tab against the app's
Investments screen. They come from the same numbers by two different routes, so they
should agree to the penny.
