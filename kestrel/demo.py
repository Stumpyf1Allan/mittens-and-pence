"""Sample household.

Two purposes: it powers "Try it with sample data" in the app, so someone can look
around before connecting a real account, and it is what the test suite runs against.

The sample files are generated in the real formats — a genuine Freetrade activity
export, a Trading 212 export, a Monzo CSV, a Capitec CSV — and pushed through the same
importers a real file would use. If the demo works, the importers work.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import pathlib
import random

from . import config, db
from .engine import budgets as budget_engine
from .engine import categorise, portfolio, sync
from .importers import ingest, profiles
from .importers.readers import Table, read_csv_bytes

SEED = 20260831

FT_HOLDINGS = [
    # ticker, isin, name, exchange, currency, buys [(date, shares, price_gbp)]
    ("DGE", "GB0002374006", "Diageo plc", "LON", "GBP",
     [("2021-08-27", 40, 34.10), ("2021-11-15", 23, 36.20)]),
    ("INRG", "IE00B1XNHC34", "iShares Global Clean Energy", "LON", "GBP",
     [("2021-05-31", 54, 9.22)]),
    ("SN.", "GB0009223206", "Smith & Nephew plc", "LON", "GBP",
     [("2021-05-19", 80, 12.45), ("2022-05-03", 78, 12.68)]),
    ("JNJ", "US4781601046", "Johnson & Johnson", "NYSE", "USD",
     [("2021-08-24", 6.97, 143.42), ("2022-04-12", 6.71, 167.42)]),
    ("ADSK", "US0527691069", "Autodesk Inc", "NASDAQ", "USD",
     [("2021-10-07", 4.10, 153.24), ("2022-05-03", 6.99, 142.86)]),
    ("KO", "US1912161007", "Coca-Cola Co", "NYSE", "USD",
     [("2021-01-25", 30.80, 48.63)]),
    ("GXO", "US36262G1013", "GXO Logistics Inc", "NYSE", "USD",
     [("2022-05-19", 40.00, 41.50), ("2023-02-14", 23.16, 43.09)]),
    ("V", "US92826C8394", "Visa Inc", "NYSE", "USD",
     [("2026-08-28", 2.87, 283.20)]),
]

T212_HOLDINGS = [
    ("SPXPl_EQ", "SPXP", "LON", "IE00B3YCGJ38", "Invesco S&P 500 UCITS ETF Acc", "GBX",
     [("2022-06-14", 180.0, 810.0), ("2023-09-01", 222.7, 855.0)]),
    ("GOOGL_US_EQ", "GOOGL", "NASDAQ", "US02079K3059", "Alphabet Inc Class A", "USD",
     [("2023-03-15", 9.6, 130.20)]),
    ("ASMLa_EQ", "ASML", "AMS", "NL0010273215", "ASML Holding NV", "EUR",
     [("2023-06-20", 1.71, 1050.0)]),
    ("AZNl_EQ", "AZN", "LON", "GB0009895292", "AstraZeneca PLC", "GBX",
     [("2024-01-11", 62.0, 10480.0)]),
    ("BRK_B_US_EQ", "BRK.B", "NYSE", "US0846707026", "Berkshire Hathaway Inc Class B", "USD",
     [("2024-02-02", 1.87, 405.66), ("2026-08-28", 1.20, 505.66)]),
    ("HPROl_EQ", "HPRO", "LON", "IE00B5L01S80", "HSBC FTSE EPRA NAREIT Developed", "GBX",
     [("2023-11-03", 63.8, 1762.0)]),
    ("INFRl_EQ", "INFR", "LON", "IE00B1FZS467", "iShares Global Infrastructure", "GBX",
     [("2024-04-18", 89.7, 2787.0)]),
    ("NATPl_EQ", "NATP", "LON", "IE000OJ5TQP4", "Future of Defence UCITS ETF", "GBX",
     [("2025-02-10", 90.0, 1540.0)]),
]

SECTORS = {
    "DGE": "Consumer Staples", "INRG": "Energy & Utilities (Thematic ETF)",
    "SN.": "Healthcare", "JNJ": "Healthcare", "ADSK": "Information Technology & Software",
    "KO": "Consumer Staples", "GXO": "Industrials & Logistics", "V": "Financials",
    "SPXP": "Broad Global & US Indexes", "GOOGL": "Information Technology & Software",
    "ASML": "Semiconductors & Hardware", "AZN": "Healthcare", "BRK-B": "Financials",
    "HPRO": "Real Estate (REITs)", "INFR": "Infrastructure & Utilities (Thematic ETF)",
    "NATP": "Industrials & Defence (Thematic ETF)", "NIO": "Consumer Discretionary",
    "DOCU": "Information Technology & Software", "BABA": "Consumer Discretionary",
}

SPEND_PATTERN = [
    # (description, category hint, min, max, times per month)
    ("TESCO STORES 3411", -95, -160, 4),
    ("SAINSBURYS SMKTS", -30, -80, 2),
    ("PRET A MANGER", -4, -12, 6),
    ("COSTA COFFEE", -3, -8, 4),
    ("DELIVEROO", -18, -45, 3),
    ("SHELL PETROL", -55, -95, 2),
    ("TFL TRAVEL CHARGE", -6, -18, 8),
    ("AMAZON.CO.UK", -12, -140, 4),
    ("BOOTS 1234", -6, -35, 1),
    ("PUREGYM LTD", -26, -26, 1),
    ("NETFLIX.COM", -15.99, -15.99, 1),
    ("SPOTIFY UK", -11.99, -11.99, 1),
    ("EE LIMITED", -32, -32, 1),
    ("OCTOPUS ENERGY", -110, -190, 1),
    ("THAMES WATER", -42, -42, 1),
    ("HACKNEY COUNCIL TAX", -168, -168, 1),
    ("ADMIRAL INSURANCE", -48, -48, 1),
    ("ZARA UK", -35, -120, 1),
    ("JUSTGIVING DONATION", -20, -50, 1),
    ("SCREWFIX DIRECT", -15, -90, 1),
]


def _rng():
    return random.Random(SEED)


def _month_starts(months: int) -> list[dt.date]:
    today = dt.date.today().replace(day=1)
    out = []
    for i in range(months - 1, -1, -1):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1
        out.append(dt.date(y, m, 1))
    return out


# ---------------------------------------------------------------------------
# File builders — real formats
# ---------------------------------------------------------------------------

def freetrade_csv() -> bytes:
    header = ["Title", "Type", "Timestamp", "Account Currency",
              "Total Amount in Account Currency", "Buy / Sell", "Ticker", "ISIN",
              "Price per Share in Account Currency", "Stamp Duty", "Quantity", "Venue",
              "Order ID", "Order Type", "Instrument Currency",
              "Total Amount in Instrument Currency", "Price per Share", "FX Rate",
              "Base FX Rate", "FX Fee (BPS)", "FX Fee Amount", "Dividend Ex Date",
              "Dividend Pay Date", "Dividend Eligible Quantity",
              "Dividend Amount Per Share", "Dividend Gross Distribution Amount",
              "Dividend Net Distribution Amount", "Dividend Withheld Tax Percentage",
              "Dividend Withheld Tax Amount"]
    rng = _rng()
    rows = []
    for ticker, isin, name, exch, ccy, buys in FT_HOLDINGS:
        for date, shares, price in buys:
            total = round(shares * price, 2)
            stamp = round(total * 0.005, 2) if exch == "LON" and ccy == "GBP" else 0.0
            fxfee = 0.0 if ccy == "GBP" else round(total * 0.0039, 2)
            rows.append([name, "ORDER", f"{date}T14:03:11.000Z", "GBP", total, "BUY",
                         ticker, isin, round(price, 6), stamp, round(shares, 8), "Multiple",
                         f"ORD{rng.randint(10**7, 10**8)}", "BASIC", ccy,
                         round(total * (1.28 if ccy == "USD" else 1.0), 2),
                         round(price * (1.28 if ccy == "USD" else 1.0), 4),
                         1.28 if ccy == "USD" else "", "", 39 if fxfee else "", fxfee,
                         "", "", "", "", "", "", "", ""])
        # quarterly dividends for the payers
        if ticker in ("DGE", "JNJ", "KO", "SN.", "GXO", "V"):
            first = dt.date.fromisoformat(buys[0][0])
            shares_held = sum(b[1] for b in buys)
            d = first + dt.timedelta(days=90)
            while d < dt.date.today():
                per = round(rng.uniform(0.09, 0.34), 4)
                gross = round(shares_held * per, 2)
                tax = round(gross * (0.15 if ccy == "USD" else 0.0), 2)
                net = round(gross - tax, 2)
                rows.append([name, "DIVIDEND", f"{d.isoformat()}T16:41:00.000Z", "GBP",
                             net, "", ticker, isin, "", "", "", "", "", "", ccy, "", "",
                             "", "", "", "",
                             (d - dt.timedelta(days=21)).isoformat(), d.isoformat(),
                             round(shares_held, 6), per, gross, net,
                             0.15 if ccy == "USD" else 0.0, tax])
                d += dt.timedelta(days=91)

    for ms in _month_starts(56):
        rows.append(["Top up", "TOP_UP", f"{ms.isoformat()}T08:00:00.000Z", "GBP",
                     260.0, "", "", "", "", "", "", "", "", "", "GBP", "", "", "", "",
                     "", "", "", "", "", "", "", "", "", ""])
        rows.append(["Interest from cash", "INTEREST_FROM_CASH",
                     f"{(ms + dt.timedelta(days=27)).isoformat()}T02:00:00.000Z", "GBP",
                     round(rng.uniform(0.4, 2.4), 2), "", "", "", "", "", "", "", "",
                     "", "GBP", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])

    rows.sort(key=lambda r: r[2], reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def trading212_csv() -> bytes:
    header = ["Action", "Time", "ISIN", "Ticker", "Name", "No. of shares", "Price / share",
              "Currency (Price / share)", "Exchange rate", "Total", "Currency (Total)",
              "Withholding tax", "Currency (Withholding tax)", "Charge amount",
              "Stamp duty reserve tax", "Notes", "ID", "Currency conversion fee"]
    rng = _rng()
    rows = []
    for t212, sym, exch, isin, name, ccy, buys in T212_HOLDINGS:
        for date, shares, price in buys:
            gbp = price / 100 if ccy == "GBX" else (price * 0.78 if ccy == "USD"
                                                    else price * 0.85 if ccy == "EUR" else price)
            total = round(shares * gbp, 2)
            rows.append(["Market buy", f"{date} 14:22:07", isin, t212, name,
                         round(shares, 8), round(price, 4), ccy,
                         "" if ccy == "GBX" else (1.28 if ccy == "USD" else 1.18),
                         total, "GBP", "", "", 0.0,
                         round(total * 0.005, 2) if ccy == "GBX" else 0.0, "",
                         f"EOF{rng.randint(10**9, 10**10)}",
                         0.0 if ccy == "GBX" else round(total * 0.0015, 2)])
        if sym in ("AZN", "HPRO", "INFR", "SPXP"):
            first = dt.date.fromisoformat(buys[0][0])
            held = sum(b[1] for b in buys)
            d = first + dt.timedelta(days=100)
            while d < dt.date.today():
                per = round(rng.uniform(4.0, 18.0), 4)
                amount = round(held * per / 100.0, 2)
                rows.append(["Dividend (Ordinary)", f"{d.isoformat()} 16:12:54", isin, t212,
                             name, round(held, 6), per, "GBX", "", amount, "GBP", 0.0, "GBP",
                             "", "", "", f"DIV{rng.randint(10**8, 10**9)}", ""])
                d += dt.timedelta(days=95)

    for ms in _month_starts(50):
        rows.append(["Deposit", f"{ms.isoformat()} 09:12:00", "", "", "", "", "", "", "",
                     450.0, "GBP", "", "", "", "", "Bank transfer",
                     f"DEP{rng.randint(10**8, 10**9)}", ""])
        for k in (7, 14, 21, 28):
            rows.append(["Interest on cash", f"{(ms + dt.timedelta(days=k)).isoformat()} 02:14:24",
                         "", "", "", "", "", "", "", round(rng.uniform(0.15, 0.45), 2), "GBP",
                         "", "", "", "", "", f"INT{rng.randint(10**8, 10**9)}", ""])
    # one closed position so the Sold tab has something in it
    rows.append(["Market buy", "2023-04-11 10:02:00", "US62914V1061", "NIO_US_EQ", "NIO Inc",
                 29.126582, 9.55, "USD", 1.24, 499.25, "GBP", "", "", 0.0, 0.0, "",
                 "EOF777001", 0.75])
    rows.append(["Market sell", "2026-04-15 15:31:00", "US62914V1061", "NIO_US_EQ", "NIO Inc",
                 29.126582, 6.02, "USD", 1.27, 138.50, "GBP", "", "", 0.0, 0.0, "",
                 "EOF777002", 0.21])

    rows.sort(key=lambda r: r[1], reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def monzo_csv(months: int = 14, salary: float = 3850.0) -> bytes:
    header = ["Transaction ID", "Date", "Time", "Type", "Name", "Emoji", "Category",
              "Amount", "Currency", "Local amount", "Local currency", "Notes and #tags",
              "Address", "Receipt", "Description", "Category split", "Money Out", "Money In"]
    rng = _rng()
    rows = []
    today = dt.date.today()

    # The card payments below have always been kept to dates that have happened. The
    # four that arrive on the same day every month were not, so a copy opened before the
    # 26th showed next week's salary as money already received — a statement no bank
    # would ever hand you, and a month's income overstated on the Dashboard.
    def fixed(ms, day, time, kind, name, category, amount, note):
        when = ms + dt.timedelta(days=day - 1)
        if when > today:
            return
        rows.append([f"tx_{rng.randint(10**12, 10**13)}", when.strftime("%d/%m/%Y"), time,
                     kind, name, "", category, amount, "GBP", "", "GBP", "", "", "",
                     note, "", "", ""])

    for ms in _month_starts(months):
        fixed(ms, 26, "07:02:11", "Faster payment", "NHS BSA SALARY", "Income",
              round(salary + rng.uniform(-40, 120), 2), "SALARY")
        fixed(ms, 2, "09:00:00", "Faster payment", "TRADING 212 UK LTD", "Savings",
              -600.00, "T212 ISA")
        fixed(ms, 2, "09:01:00", "Faster payment", "FREETRADE", "Savings", -500.00, "FT ISA")
        fixed(ms, 4, "06:00:00", "Direct Debit", "SANTANDER MORTGAGE", "Bills", -1180.00,
              "MORTGAGE")
        for desc, lo, hi, times in SPEND_PATTERN:
            for _ in range(times):
                day = rng.randint(1, 27)
                when = ms + dt.timedelta(days=day - 1)
                if when > today:
                    continue
                amt = round(rng.uniform(min(lo, hi), max(lo, hi)), 2)
                rows.append([f"tx_{rng.randint(10**12, 10**13)}", when.strftime("%d/%m/%Y"),
                             f"{rng.randint(7,21):02d}:{rng.randint(0,59):02d}:00",
                             "Card payment", desc, "", "", amt, "GBP", "", "GBP", "", "", "",
                             desc, "", abs(amt), ""])
    rows.sort(key=lambda r: dt.datetime.strptime(r[1], "%d/%m/%Y"), reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def capitec_csv(months: int = 14) -> bytes:
    header = ["Posting Date", "Transaction Date", "Description", "Money In (R)",
              "Money Out (R)", "Balance (R)"]
    rng = _rng()
    rows = []
    balance = 24500.0
    entries = [("CHECKERS HYPER", -450, -1400), ("ENGEN GARAGE", -600, -950),
               ("VODACOM PREPAID", -199, -199), ("DISCOVERY HEALTH", -2450, -2450),
               ("CITY OF CAPE TOWN MUNICIPAL", -1850, -2400),
               ("WOOLWORTHS FOOD", -300, -800), ("DIS-CHEM PHARMACY", -120, -420),
               ("UBER TRIP", -60, -180), ("NETFLORIST", -350, -350),
               ("EASYEQUITIES DEPOSIT", -2000, -2000), ("SPUR STEAK RANCH", -280, -620)]
    today = dt.date.today()
    for ms in _month_starts(months):
        # Everything in the month collected first, then written in date order, so the
        # Balance column runs down the statement the way a real one does. Writing the
        # salary first and the spending afterwards gave a running balance that jumped
        # about relative to the dates beside it — and this file is the fixture the CSV
        # importer is tested against, so it has to behave like a statement.
        month = []
        pay = ms.replace(day=25)
        if pay <= today:                    # never a salary that hasn't been paid yet
            month.append((pay, "SALARY PAYMENT", round(41000 + rng.uniform(-500, 900), 2)))
        for desc, lo, hi in entries:
            when = ms + dt.timedelta(days=rng.randint(0, 26))
            if when > today:
                continue
            month.append((when, desc, round(rng.uniform(min(lo, hi), max(lo, hi)), 2)))
        for when, desc, v in sorted(month, key=lambda r: (r[0], r[1])):
            balance += v
            rows.append([when.strftime("%Y/%m/%d"), when.strftime("%Y/%m/%d"), desc,
                         f"{v:,.2f}" if v > 0 else "",
                         f"{abs(v):,.2f}" if v < 0 else "", f"{balance:,.2f}"])
    # Built oldest-first, printed newest-first, which is how Capitec send it. A sort by
    # date instead of a straight reverse shuffles rows that share a date out of the order
    # their running balances were worked out in, and the column stops adding up.
    rows.reverse()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def seed_prices() -> dict:
    """Try live quotes; if there is no network, invent plausible ones.

    A demo that shows every holding worth zero teaches nothing, and a machine with no
    internet should still be able to look around. Anything invented here is stored with
    source='demo' so the app can label it.
    """
    from .market import prices as market
    ids = [r["id"] for r in db.rows("SELECT id FROM instruments")]
    live = market.refresh_prices(ids) if ids else {"updated": 0, "failed": 0}
    if live.get("updated") and not live.get("failed"):
        return {"mode": "live", **{k: live[k] for k in ("updated", "failed")}}

    rng = random.Random(SEED + 7)
    have = {r["instrument_id"] for r in db.rows(
        "SELECT DISTINCT instrument_id FROM prices WHERE source != 'demo'")}
    now = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    made = 0
    with db.tx() as c:
        for inst in db.rows("SELECT * FROM instruments"):
            if inst["id"] in have:
                continue
            base_cost = db.scalar(
                "SELECT SUM(cost)/NULLIF(SUM(shares),0) FROM holdings WHERE instrument_id=?",
                (inst["id"],))
            if not base_cost:
                base_cost = db.scalar(
                    "SELECT AVG(total/NULLIF(shares,0)) FROM trades WHERE instrument_id=?",
                    (inst["id"],)) or 10.0
            price = round(float(base_cost) * rng.uniform(0.72, 1.85), 4)
            c.execute("INSERT OR REPLACE INTO prices"
                      "(instrument_id,as_of,price,currency,change,change_pct,source)"
                      " VALUES(?,?,?,?,?,?, 'demo')",
                      (inst["id"], now, price, config.settings["base_currency"],
                       round(price * rng.uniform(-0.02, 0.02), 4),
                       round(rng.uniform(-0.02, 0.02), 4)))
            made += 1
    return {"mode": "offline-demo", "invented": made,
            "note": "No price feed reachable, so sample prices were generated. "
                    "Press Refresh prices once you are online."}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _existing(external_id: str):
    """The demo account with this id, if a previous load already made it.

    Loading the sample data twice used to build a second copy of the whole household:
    two Freetrade ISAs, two houses, two mortgages, and a net worth twice the size. The
    transactions inside them deduplicated properly — the accounts around them did not.
    Every part of the sample data is now keyed on a fixed `demo:` id and reused, so a
    second load tops the household up rather than cloning it.
    """
    return db.one("SELECT id, connection_id FROM accounts WHERE external_id=?",
                  (external_id,))


def load(write_files_to: pathlib.Path | None = None) -> dict:
    """Build the sample household. Returns a short report.

    Safe to call more than once: it reuses whatever is already there.
    """
    db.init()
    report = {"members": [], "connections": [], "imports": [], "budgets": 0}

    # The sample household must never rename the person using the app. This line used to
    # read `SET name='Allan'`, which meant loading the sample data on anybody's machine
    # stamped the author's name over theirs — and there was no way to change it back,
    # because members could only be added, never renamed.
    with db.tx() as c:
        if not db.one("SELECT id FROM members WHERE name='Family'"):
            c.execute("INSERT INTO members(name,colour) VALUES('Family','#0f9d8e')")
    me_row = db.one("SELECT id, name FROM members WHERE is_default=1")
    me = me_row["id"]
    fam = db.one("SELECT id FROM members WHERE name='Family'")["id"]
    report["members"] = [me_row["name"], "Family"]

    files = {
        "freetrade-activity.csv": freetrade_csv(),
        "trading212-export.csv": trading212_csv(),
        "monzo-statement.csv": monzo_csv(),
        "capitec-statement.csv": capitec_csv(),
    }
    if write_files_to:
        write_files_to.mkdir(parents=True, exist_ok=True)
        for name, blob in files.items():
            (write_files_to / name).write_bytes(blob)

    plan = [
        ("gb-freetrade", "csv", me, "Freetrade ISA", "isa", "GBP", True,
         "freetrade-activity.csv", "freetrade", "activity"),
        ("gb-trading-212", "csv", me, "Trading 212 ISA", "isa", "GBP", True,
         "trading212-export.csv", "trading212", "activity"),
        ("gb-monzo", "csv", me, "Monzo current account", "current", "GBP", False,
         "monzo-statement.csv", "monzo", "transactions"),
        ("za-capitec-bank", "csv", fam, "Capitec cheque account", "current", "ZAR", False,
         "capitec-statement.csv", "capitec", "transactions"),
    ]

    for inst_id, method, member, label, acct_type, ccy, is_inv, fname, profile, kind in plan:
        have = _existing(f"demo:{inst_id}")
        if have:
            account_id, conn = have["id"], {"id": have["connection_id"]}
        else:
            conn = sync.create_connection(inst_id, method, member_id=member, label=label)
            with db.tx() as c:
                cur = c.execute(
                    "INSERT INTO accounts(connection_id,member_id,external_id,name,account_type,"
                    "currency,is_investment,balance,last_updated)"
                    " VALUES(?,?,?,?,?,?,?,NULL,datetime('now'))",
                    (conn["id"], member, f"demo:{inst_id}", label, acct_type, ccy, int(is_inv)))
                account_id = cur.lastrowid
        report["connections"].append(label)
        table = read_csv_bytes(files[fname], fname)
        if kind == "activity":
            res = ingest.import_activity(account_id, table, profile, fname, conn["id"])
        else:
            mapping = profiles.resolve_columns(profile, table.header)
            if not mapping:
                from .importers import mapping as mapmod
                mapping = mapmod.suggest(table, "transactions")["mapping"]
            res = ingest.import_transactions(account_id, table, mapping, profile, fname, conn["id"])
        report["imports"].append({"account": label, "file": fname, **res})

    # sector labels, so the charts are not one grey blob
    with db.tx() as c:
        for sym, sector in SECTORS.items():
            c.execute("UPDATE instruments SET sector=? WHERE symbol=?", (sector, sym))

    report["prices"] = seed_prices()

    # a manual asset and a credit card, to exercise the other account types
    if not _existing("demo:house"):
        conn = sync.create_connection("gb-property-manual-valuation", "manual", member_id=me,
                                      label="The house")
        with db.tx() as c:
            c.execute("INSERT INTO accounts(connection_id,member_id,external_id,name,account_type,"
                      "currency,balance,last_updated) VALUES(?,?,?,?,?,?,?,datetime('now'))",
                      (conn["id"], me, "demo:house", "The house", "asset", "GBP", 465000))

    # A house with no mortgage behind it is an unusual household, and it left the whole
    # mortgage side of the app invisible in the sample data.
    if not _existing("demo:mortgage"):
        mconn = sync.create_connection("gb-santander-uk", "manual", member_id=me,
                                       label="Santander mortgage")
        with db.tx() as c:
            cur = c.execute(
                "INSERT INTO accounts(connection_id,member_id,external_id,name,account_type,"
                "currency,balance,last_updated) VALUES(?,?,?,?,?,?,?,datetime('now'))",
                (mconn["id"], me, "demo:mortgage", "Santander mortgage", "mortgage", "GBP",
                 -191988))
            maid = cur.lastrowid
            house = c.execute("SELECT id FROM accounts WHERE external_id='demo:house'").fetchone()
            mortgage_cat = c.execute("SELECT id FROM categories WHERE parent='Home' "
                                     "AND name='Mortgage'").fetchone()
            c.execute(
                "INSERT INTO mortgages(account_id,lender,original_amount,started_on,term_months,"
                "rate,rate_type,fixed_until,revert_rate,monthly_payment,payment_day,"
                "repayment_type,statement_balance,statement_on,property_account_id,"
                "split_payments,payment_category_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                # Figures that agree with each other: £217,000 at 4.29% over 25 years
                # really is £1,180 a month, and really does leave £191,988 after four and
                # a half years. Sample data that fails the app's own consistency check
                # would be a standing bug report.
                (maid, "Santander", 217000, "2021-06-01", 300, 4.29, "fixed", "2027-06-30",
                 7.74, 1180.0, 2, "repayment", 191988, "2026-01-31",
                 house["id"] if house else None, 1,
                 mortgage_cat["id"] if mortgage_cat else None))
        report["accounts"] = report.get("accounts", 0) + 1

    categorise.categorise_all(only_uncategorised=True)
    categorise.detect_internal_transfers()

    for parent, amount in [("Food & Drink", 700), ("Transport", 320), ("Utilities", 400),
                           ("Home", 1450), ("Shopping", 250), ("Health", 120),
                           ("Subscriptions", 60), ("Giving", 80), ("Travel", 200)]:
        budget_engine.set_budget(amount, parent=parent)
        report["budgets"] += 1
    gid = db.category_id("Food & Drink", "Groceries")
    if gid:
        budget_engine.set_budget(480, category_id=gid)
        report["budgets"] += 1

    from .engine import snapshots
    snapshots.backfill(months=18, force=True)
    snapshots.take(force=True)

    db.log("demo.loaded", report)
    return report


def clear():
    """Wipe everything. Used by "start again" in the app and between tests."""
    with db.tx() as c:
        for t in ("transactions", "holdings", "trades", "dividends", "cash_events",
                  "sold_positions", "prices", "accounts", "connections", "budgets",
                  "snapshots", "import_batches", "import_mappings", "instruments", "vault"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM rules WHERE is_builtin=0")
        c.execute("DELETE FROM members WHERE is_default=0")
