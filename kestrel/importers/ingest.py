"""Write imported rows into the database.

Every insert is de-duplicated on a fingerprint built from the fields that identify
a row, so re-uploading a statement that overlaps last month's is harmless — which
matters, because most banks only let you download a rolling window.
"""

from __future__ import annotations

import json
import re

from .. import db
from ..market import prices as market
from . import mapping as mapping_mod
from . import profiles as profiles_mod
from .readers import Table, parse_amount, parse_date, parse_text

# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------

EXCHANGE_FROM_SUFFIX = {".L": "LON", ".AS": "AMS", ".DE": "ETR", ".PA": "EPA",
                        ".JO": "JSE", ".MI": "BIT", ".SW": "SWX", ".TO": "TSE"}

# Trading 212 encodes the venue in the ticker: AZNl_EQ, GOOGL_US_EQ, ADYENa_EQ
T212_TICKER = re.compile(r"^(?P<base>[A-Z0-9._]+?)(?P<venue>[a-z])?(?:_(?P<country>[A-Z]{2}))?_EQ$")

T212_VENUE = {"l": "LON", "a": "AMS", "d": "ETR", "p": "EPA", "m": "BIT",
              "e": "BME", "s": "SWX", "z": "SWX", "n": "NYSE", "q": "NASDAQ"}


def split_t212_ticker(raw: str) -> tuple[str, str | None]:
    """'AZNl_EQ' -> ('AZN','LON');  'GOOGL_US_EQ' -> ('GOOGL','NASDAQ');
    'BRK_B_US_EQ' -> ('BRK-B','NYSE') — US class shares use an underscore at
    Trading 212 and a hyphen everywhere a price comes from."""
    raw = (raw or "").strip()
    m = T212_TICKER.match(raw)
    if not m:
        return raw.replace("_EQ", ""), None
    base = m.group("base")
    venue = m.group("venue")
    country = m.group("country")
    if country == "US":
        return base.replace("_", "-"), "NYSE"
    if venue and venue in T212_VENUE:
        return base, T212_VENUE[venue]
    return base, None


def get_or_create_instrument(symbol: str, exchange: str | None = None, name: str | None = None,
                             isin: str | None = None, currency: str | None = None,
                             sector: str | None = None, asset_class: str = "equity") -> int:
    symbol = (symbol or "").strip().upper()
    exchange = (exchange or "").strip().upper() or None
    if not symbol and isin:
        row = db.one("SELECT id FROM instruments WHERE isin=?", (isin,))
        if row:
            return row["id"]
    if not symbol:
        raise ValueError("instrument needs a symbol or an ISIN")

    row = db.one("SELECT id FROM instruments WHERE symbol=? AND (exchange IS ? OR exchange=?)",
                 (symbol, exchange, exchange))
    if not row and isin:
        row = db.one("SELECT id FROM instruments WHERE isin=?", (isin,))
    if not row and exchange is None:
        row = db.one("SELECT id FROM instruments WHERE symbol=? ORDER BY id LIMIT 1", (symbol,))
    if row:
        iid = row["id"]
        with db.tx() as c:
            if name:
                c.execute("UPDATE instruments SET name=COALESCE(NULLIF(name,''),?) WHERE id=?", (name, iid))
            if isin:
                c.execute("UPDATE instruments SET isin=COALESCE(NULLIF(isin,''),?) WHERE id=?", (isin, iid))
            if exchange:
                c.execute("UPDATE instruments SET exchange=COALESCE(NULLIF(exchange,''),?) WHERE id=?",
                          (exchange, iid))
            if sector:
                c.execute("UPDATE instruments SET sector=COALESCE(NULLIF(sector,''),?) WHERE id=?",
                          (sector, iid))
        return iid

    qs = market.quote_symbol(symbol, exchange, asset_class)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO instruments(symbol,exchange,name,isin,currency,sector,asset_class,quote_symbol)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (symbol, exchange, name, isin, currency, sector, asset_class, qs))
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Bank transactions
# ---------------------------------------------------------------------------

def import_transactions(account_id: int, table: Table, mapping: dict, profile_key: str | None = None,
                        filename: str = "", connection_id: int | None = None) -> dict:
    prof = profiles_mod.get(profile_key or "generic")
    sign = prof.get("sign", "auto")
    join_desc = prof.get("join_description")
    acct = db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not acct:
        raise ValueError("account not found")
    acct_ccy = acct["currency"] or "GBP"

    recs = mapping_mod.apply_mapping(table, mapping)
    added = skipped = 0
    warnings: list[str] = []
    seen_signs = []

    # Decide how to read the amount when the profile says "auto".
    if sign == "auto":
        if "debit" in mapping and "credit" in mapping:
            sign = "debit_credit"
        else:
            amounts = [parse_amount(r.get("amount")) for r in recs[:300]]
            amounts = [a for a in amounts if a is not None]
            if amounts and all(a >= 0 for a in amounts):
                # everything positive and there's a type column? assume type decides
                sign = "type_signed" if "type" in mapping else "out_positive"
            else:
                sign = "signed"

    rows_to_add = []
    for r in recs:
        date = parse_date(r.get("date"))
        if not date:
            skipped += 1
            continue

        if join_desc:
            parts = [parse_text(r["_raw"].get(k)) for k in join_desc]
            desc = " ".join(p for p in parts if p).strip()
        else:
            desc = parse_text(r.get("description"))
        if not desc:
            desc = parse_text(r.get("merchant")) or parse_text(r.get("type")) or "(no description)"

        amount = None
        if sign == "debit_credit":
            d = parse_amount(r.get("debit")) or 0.0
            cr = parse_amount(r.get("credit")) or 0.0
            amount = cr - abs(d)
        else:
            amount = parse_amount(r.get("amount"))
            if amount is None:
                d = parse_amount(r.get("debit"))
                cr = parse_amount(r.get("credit"))
                if d is not None or cr is not None:
                    amount = (cr or 0.0) - abs(d or 0.0)
            if amount is None:
                skipped += 1
                continue
            if sign == "out_positive":
                amount = -abs(amount) if not _looks_like_credit(r) else abs(amount)
            elif sign == "type_signed":
                t = parse_text(r.get("type")).lower()
                if any(w in t for w in ("credit", "deposit", "in", "received", "refund",
                                        "salary", "transfer in")):
                    amount = abs(amount)
                else:
                    amount = -abs(amount)

        if amount is None:
            skipped += 1
            continue
        seen_signs.append(amount)

        ccy = parse_text(r.get("currency")) or acct_ccy
        bal = parse_amount(r.get("balance"))
        ref = parse_text(r.get("reference"))
        merchant = parse_text(r.get("merchant")) or None
        ttype = parse_text(r.get("type")) or None
        src_cat = parse_text(r.get("category")) or None

        fp = db.fingerprint(date, desc[:80], round(amount, 2), ref or "", bal if bal is not None else "")
        rows_to_add.append((account_id, date, desc, merchant, amount, ccy, bal, ref,
                            json.dumps({"type": ttype, "source_category": src_cat},
                                       default=str), fp))

    fresh_ids = []
    with db.tx() as c:
        for row in rows_to_add:
            cur = c.execute(
                "INSERT OR IGNORE INTO transactions"
                "(account_id,posted_on,description,merchant,amount,currency,balance_after,"
                " reference,raw_json,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)", row)
            if cur.rowcount:
                added += 1
                fresh_ids.append(cur.lastrowid)
            else:
                skipped += 1

    # Somebody notes a £40 cash withdrawal the day it happens; three weeks later the
    # statement carrying that same £40 is imported. Two rows, one payment, every budget
    # quietly wrong. The statement is the authority, so its row stays and the note it
    # matches goes — reported, never silent.
    from ..engine import manual as manual_engine
    superseded = manual_engine.find_superseded(account_id, fresh_ids)
    if superseded:
        manual_engine.supersede(superseded)
        warnings.append(
            f"{len(superseded)} entr{'y' if len(superseded) == 1 else 'ies'} you'd typed "
            f"in by hand matched a row in this file, so the statement's version was kept "
            f"and yours removed: "
            + ", ".join(f"{m['description']} on {m['posted_on']}" for m in superseded[:4])
            + ("…" if len(superseded) > 4 else "") + ".")

    if seen_signs and all(a <= 0 for a in seen_signs):
        warnings.append("Every row was read as money out — if that looks wrong, "
                        "flip the amount sign on the mapping screen.")
    if seen_signs and all(a >= 0 for a in seen_signs):
        warnings.append("Every row was read as money in — check the debit/credit columns.")

    batch = _record_batch(connection_id, account_id, filename, profile_key, "transactions",
                          len(recs), added, skipped, mapping, warnings)
    _refresh_account_balance(account_id)
    from ..engine import categorise
    categorised = categorise.categorise_account(account_id, only_uncategorised=True)
    return {"batch_id": batch, "rows": len(recs), "added": added, "skipped": skipped,
            "categorised": categorised, "superseded": len(superseded),
            "warnings": warnings}


def _looks_like_credit(rec: dict) -> bool:
    t = (parse_text(rec.get("type")) + " " + parse_text(rec.get("description"))).lower()
    return any(w in t for w in ("payment received", "credit", "refund", "cashback",
                                "direct credit", "payment thank you"))


def _refresh_account_balance(account_id: int):
    row = db.one("SELECT balance_after, posted_on FROM transactions WHERE account_id=? "
                 "AND balance_after IS NOT NULL ORDER BY posted_on DESC, id DESC LIMIT 1",
                 (account_id,))
    with db.tx() as c:
        if row:
            c.execute("UPDATE accounts SET balance=?, last_updated=datetime('now') WHERE id=?",
                      (row["balance_after"], account_id))
        else:
            c.execute("UPDATE accounts SET last_updated=datetime('now') WHERE id=?", (account_id,))


def _record_batch(connection_id, account_id, filename, profile, kind, seen, added, skipped,
                  mapping, warnings) -> int:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO import_batches(connection_id,account_id,filename,profile,kind,"
            "rows_seen,rows_added,rows_skipped,mapping_json,warnings_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (connection_id, account_id, filename, profile, kind, seen, added, skipped,
             json.dumps(mapping, default=str), json.dumps(warnings)))
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Broker holdings (a valuation file rather than an activity feed)
# ---------------------------------------------------------------------------

def import_holdings(account_id: int, table: Table, mapping: dict, profile_key: str | None = None,
                    filename: str = "", connection_id: int | None = None,
                    replace: bool = True) -> dict:
    prof = profiles_mod.get(profile_key or "generic")
    divisor = float(prof.get("price_divisor") or 1.0)
    acct = db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    acct_ccy = acct["currency"] if acct else "GBP"

    recs = mapping_mod.apply_mapping(table, mapping)
    added = skipped = 0
    touched: list[int] = []

    for r in recs:
        symbol = parse_text(r.get("symbol")).upper()
        isin = parse_text(r.get("isin")).upper() or None
        name = parse_text(r.get("name")) or None
        if not symbol and not isin:
            skipped += 1
            continue
        shares = parse_amount(r.get("shares"))
        if shares is None or shares == 0:
            skipped += 1
            continue
        exchange = parse_text(r.get("exchange")).upper() or None
        if exchange and exchange not in ("LON", "NYSE", "NASDAQ", "AMS", "ETR", "JSE",
                                         "EPA", "BIT", "SWX", "TSE", "ASX"):
            exchange = _guess_exchange(exchange)
        cost = parse_amount(r.get("cost"))
        value = parse_amount(r.get("value"))
        price = parse_amount(r.get("price"))
        if price is not None and divisor != 1.0:
            price = price / divisor
        if cost is None and value is not None and price:
            cost = value
        ccy = parse_text(r.get("currency")) or acct_ccy
        sector = parse_text(r.get("sector")) or None

        iid = get_or_create_instrument(symbol or isin, exchange, name, isin, ccy, sector)
        touched.append(iid)
        with db.tx() as c:
            c.execute(
                "INSERT INTO holdings(account_id,instrument_id,shares,cost,cost_currency,source,updated_at)"
                " VALUES(?,?,?,?,?, 'import', datetime('now'))"
                " ON CONFLICT(account_id,instrument_id) DO UPDATE SET"
                "   shares=excluded.shares, cost=excluded.cost,"
                "   cost_currency=excluded.cost_currency, updated_at=datetime('now')",
                (account_id, iid, shares, cost or 0.0, ccy))
        added += 1

    if replace and touched:
        with db.tx() as c:
            qmarks = ",".join("?" * len(touched))
            c.execute(f"DELETE FROM holdings WHERE account_id=? AND source='import' "
                      f"AND instrument_id NOT IN ({qmarks})", (account_id, *touched))

    batch = _record_batch(connection_id, account_id, filename, profile_key, "holdings",
                          len(recs), added, skipped, mapping, [])
    if touched:
        market.refresh_prices(touched)
    return {"batch_id": batch, "rows": len(recs), "added": added, "skipped": skipped}


def _guess_exchange(text: str) -> str | None:
    t = text.upper()
    for k, v in {"LSE": "LON", "LONDON": "LON", "NEW YORK": "NYSE", "NASDAQ": "NASDAQ",
                 "AMSTERDAM": "AMS", "XETRA": "ETR", "JOHANNESBURG": "JSE",
                 "PARIS": "EPA", "MILAN": "BIT", "TORONTO": "TSE"}.items():
        if k in t:
            return v
    return None


# ---------------------------------------------------------------------------
# Activity feeds
# ---------------------------------------------------------------------------

BUY_WORDS = ("buy", "bought", "purchase", "market buy", "limit buy", "stop buy")
SELL_WORDS = ("sell", "sold", "market sell", "limit sell", "stop sell")
DIV_WORDS = ("dividend", "div", "distribution")
INTEREST_WORDS = ("interest", "lending interest", "interest on cash", "interest_on_free_cash",
                  "interest from cash")
DEPOSIT_WORDS = ("deposit", "top up", "top_up", "topup", "transfer in", "card debit")
WITHDRAW_WORDS = ("withdraw", "withdrawal", "transfer out")


def _classify_action(text: str) -> str:
    t = (text or "").strip().lower()
    if any(w in t for w in DIV_WORDS):
        return "DIVIDEND"
    if any(w in t for w in SELL_WORDS):
        return "SELL"
    if any(w in t for w in BUY_WORDS):
        return "BUY"
    if any(w in t for w in INTEREST_WORDS):
        return "INTEREST"
    if any(w in t for w in DEPOSIT_WORDS):
        return "DEPOSIT"
    if any(w in t for w in WITHDRAW_WORDS):
        return "WITHDRAWAL"
    if "conversion" in t or "fx" in t:
        return "FX"
    if "fee" in t or "charge" in t or "cost" in t:
        return "FEE"
    if "split" in t:
        return "SPLIT"
    return "OTHER"


def import_activity(account_id: int, table: Table, profile_key: str, filename: str = "",
                    connection_id: int | None = None, mapping: dict | None = None) -> dict:
    """Dispatch to a purpose-built reader for the brokers we know, or the generic one."""
    handler = profiles_mod.get(profile_key).get("handler")
    if handler == "freetrade":
        res = _activity_freetrade(account_id, table)
    elif handler == "trading212":
        res = _activity_trading212(account_id, table)
    elif handler == "easyequities":
        res = _activity_easyequities(account_id, table)
    else:
        res = _activity_generic(account_id, table, mapping or {})

    from ..engine import portfolio
    portfolio.rebuild_holdings(account_id)
    portfolio.recompute_cash(account_id)
    _record_batch(connection_id, account_id, filename, profile_key, "activity",
                  len(table.rows), res.get("trades", 0) + res.get("dividends", 0),
                  res.get("skipped", 0), mapping or {}, res.get("warnings", []))
    ids = db.rows("SELECT DISTINCT instrument_id AS i FROM holdings WHERE account_id=?", (account_id,))
    if ids:
        market.refresh_prices([r["i"] for r in ids])
    return res


def _insert_trade(c, account_id, iid, date, side, shares, price, price_ccy, total, fees,
                  fx_rate, ref, raw):
    fp = db.fingerprint(date, side, iid, round(shares or 0, 8), round(total or 0, 2), ref or "")
    cur = c.execute(
        "INSERT OR IGNORE INTO trades(account_id,instrument_id,traded_on,side,shares,price,"
        "price_currency,total,fees,fx_rate,reference,fingerprint,raw_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, iid, date, side, shares, price, price_ccy, total, fees or 0.0,
         fx_rate, ref, fp, json.dumps(raw, default=str)[:4000]))
    return cur.rowcount


def _insert_dividend(c, account_id, iid, date, amount, gross, withheld, ccy, shares, per_share, ref):
    fp = db.fingerprint(date, iid, round(amount or 0, 4), ref or "")
    cur = c.execute(
        "INSERT OR IGNORE INTO dividends(account_id,instrument_id,paid_on,amount,gross,"
        "withheld,currency,shares,per_share,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (account_id, iid, date, amount, gross, withheld, ccy, shares, per_share, fp))
    return cur.rowcount


def _insert_cash(c, account_id, date, kind, amount, ccy, note, ref=""):
    fp = db.fingerprint(date, kind, round(amount or 0, 4), note or "", ref)
    cur = c.execute(
        "INSERT OR IGNORE INTO cash_events(account_id,happened_on,kind,amount,currency,note,fingerprint)"
        " VALUES(?,?,?,?,?,?,?)", (account_id, date, kind, amount, ccy, note, fp))
    return cur.rowcount


def _activity_freetrade(account_id: int, table: Table) -> dict:
    """Freetrade's activity CSV. Column names are stable and self-describing."""
    rows = table.dicts()
    lookup = {re.sub(r"[^a-z0-9]+", "", k.lower()): k for k in table.header if k}

    def g(row, *names):
        for n in names:
            key = lookup.get(re.sub(r"[^a-z0-9]+", "", n.lower()))
            if key and row.get(key) not in (None, ""):
                return row[key]
        return None

    trades = divs = cash = skipped = 0
    warnings: list[str] = []
    with db.tx() as c:
        for row in rows:
            typ = parse_text(g(row, "Type")).upper()
            date = parse_date(g(row, "Timestamp", "Dividend Pay Date"))
            if not date:
                skipped += 1
                continue
            ticker = parse_text(g(row, "Ticker")).upper()
            isin = parse_text(g(row, "ISIN")).upper() or None
            name = parse_text(g(row, "Title")) or None
            acct_ccy = parse_text(g(row, "Account Currency")) or "GBP"
            total = parse_amount(g(row, "Total Amount in Account Currency"))
            ref = parse_text(g(row, "Order ID"))

            if typ == "ORDER" and ticker:
                side = parse_text(g(row, "Buy / Sell")).upper() or "BUY"
                shares = parse_amount(g(row, "Quantity")) or 0.0
                price = parse_amount(g(row, "Price per Share in Account Currency"))
                stamp = parse_amount(g(row, "Stamp Duty")) or 0.0
                fxfee = parse_amount(g(row, "FX Fee Amount")) or 0.0
                fx = parse_amount(g(row, "FX Rate"))
                inst_ccy = parse_text(g(row, "Instrument Currency")) or acct_ccy
                exch = _exchange_from_isin_ccy(isin, inst_ccy)
                iid = get_or_create_instrument(ticker, exch, name, isin, inst_ccy)
                trades += _insert_trade(c, account_id, iid, date, "SELL" if side == "SELL" else "BUY",
                                        shares, price, acct_ccy, abs(total or 0.0),
                                        stamp + fxfee, fx, ref, row)
            elif typ == "DIVIDEND" and ticker:
                gross = parse_amount(g(row, "Dividend Gross Distribution Amount"))
                net = parse_amount(g(row, "Dividend Net Distribution Amount"))
                tax = parse_amount(g(row, "Dividend Withheld Tax Amount")) or 0.0
                shares = parse_amount(g(row, "Dividend Eligible Quantity"))
                per = parse_amount(g(row, "Dividend Amount Per Share"))
                amount = total if total is not None else (net if net is not None else gross)
                inst_ccy = parse_text(g(row, "Instrument Currency")) or acct_ccy
                exch = _exchange_from_isin_ccy(isin, inst_ccy)
                iid = get_or_create_instrument(ticker, exch, name, isin, inst_ccy)
                divs += _insert_dividend(c, account_id, iid, date, amount or 0.0, gross, tax,
                                         acct_ccy, shares, per, ref)
            elif typ in ("INTEREST_FROM_CASH", "INTEREST", "INTEREST_ON_CASH"):
                cash += _insert_cash(c, account_id, date, "INTEREST", total or 0.0, acct_ccy,
                                     "Freetrade cash interest")
            elif typ in ("TOP_UP", "DEPOSIT", "TOPUP"):
                cash += _insert_cash(c, account_id, date, "DEPOSIT", abs(total or 0.0), acct_ccy, name)
            elif typ in ("WITHDRAW", "WITHDRAWAL"):
                cash += _insert_cash(c, account_id, date, "WITHDRAWAL", -abs(total or 0.0), acct_ccy, name)
            elif typ in ("MONTHLY_STATEMENT", "TAX_CERTIFICATE", "ANNUAL_STATEMENT"):
                continue
            elif typ in ("STOCK_SPLIT", "SPLIT"):
                warnings.append(f"Stock split on {ticker} at {date} — check the share count.")
            else:
                skipped += 1
    return {"trades": trades, "dividends": divs, "cash": cash, "skipped": skipped,
            "warnings": warnings}


def _exchange_from_isin_ccy(isin: str | None, ccy: str | None) -> str | None:
    if isin:
        p = isin[:2].upper()
        if p == "GB":
            return "LON"
        if p == "US":
            return "NYSE"
        if p in ("NL",):
            return "AMS"
        if p in ("DE",):
            return "ETR"
        if p in ("ZA",):
            return "JSE"
        if p in ("IE", "LU"):      # UCITS ETFs, usually listed in London for UK brokers
            return "LON"
        if p == "FR":
            return "EPA"
    if ccy in ("GBP", "GBX"):
        return "LON"
    if ccy == "USD":
        return "NYSE"
    if ccy == "EUR":
        return "AMS"
    if ccy == "ZAR":
        return "JSE"
    return None


def _activity_trading212(account_id: int, table: Table) -> dict:
    rows = table.dicts()
    lookup = {re.sub(r"[^a-z0-9]+", "", k.lower()): k for k in table.header if k}

    def g(row, *names):
        for n in names:
            key = lookup.get(re.sub(r"[^a-z0-9]+", "", n.lower()))
            if key and row.get(key) not in (None, ""):
                return row[key]
        return None

    trades = divs = cash = skipped = 0
    with db.tx() as c:
        for row in rows:
            action = parse_text(g(row, "Action"))
            kind = _classify_action(action)
            date = parse_date(g(row, "Time", "Date"))
            if not date:
                skipped += 1
                continue
            raw_ticker = parse_text(g(row, "Ticker")) or ""
            isin = parse_text(g(row, "ISIN")).upper() or None
            name = parse_text(g(row, "Name")) or None
            total = parse_amount(g(row, "Total"))
            total_ccy = parse_text(g(row, "Currency (Total)")) or "GBP"
            ref = parse_text(g(row, "ID", "Notes"))

            if kind in ("BUY", "SELL"):
                symbol, exch = split_t212_ticker(raw_ticker) if "_EQ" in raw_ticker \
                    else (raw_ticker.upper(), None)
                if not symbol and not isin:
                    skipped += 1
                    continue
                shares = parse_amount(g(row, "No. of shares", "Quantity")) or 0.0
                price = parse_amount(g(row, "Price / share"))
                pccy = parse_text(g(row, "Currency (Price / share)")) or None
                fx = parse_amount(g(row, "Exchange rate"))
                fees = (parse_amount(g(row, "Charge amount")) or 0.0) \
                    + (parse_amount(g(row, "Stamp duty reserve tax")) or 0.0) \
                    + (parse_amount(g(row, "Currency conversion fee")) or 0.0)
                if not exch:
                    exch = _exchange_from_isin_ccy(isin, pccy)
                iid = get_or_create_instrument(symbol or isin, exch, name, isin, pccy)
                trades += _insert_trade(c, account_id, iid, date, kind, shares, price, pccy,
                                        abs(total or 0.0), fees, fx, ref, row)
            elif kind == "DIVIDEND":
                symbol, exch = split_t212_ticker(raw_ticker) if "_EQ" in raw_ticker \
                    else (raw_ticker.upper(), None)
                if not symbol and not isin:
                    skipped += 1
                    continue
                shares = parse_amount(g(row, "No. of shares"))
                per = parse_amount(g(row, "Price / share"))
                tax = parse_amount(g(row, "Withholding tax")) or 0.0
                pccy = parse_text(g(row, "Currency (Price / share)")) or None
                if not exch:
                    exch = _exchange_from_isin_ccy(isin, pccy)
                iid = get_or_create_instrument(symbol or isin, exch, name, isin, pccy)
                divs += _insert_dividend(c, account_id, iid, date, total or 0.0, None, tax,
                                         total_ccy, shares, per, ref)
            elif kind == "INTEREST":
                cash += _insert_cash(c, account_id, date, "INTEREST", total or 0.0, total_ccy, action)
            elif kind == "DEPOSIT":
                cash += _insert_cash(c, account_id, date, "DEPOSIT", abs(total or 0.0), total_ccy, action)
            elif kind == "WITHDRAWAL":
                cash += _insert_cash(c, account_id, date, "WITHDRAWAL", -abs(total or 0.0), total_ccy, action)
            elif kind in ("FEE", "FX"):
                cash += _insert_cash(c, account_id, date, "FEE", total or 0.0, total_ccy, action)
            else:
                skipped += 1
    return {"trades": trades, "dividends": divs, "cash": cash, "skipped": skipped, "warnings": []}


def _activity_easyequities(account_id: int, table: Table) -> dict:
    rows = table.dicts()
    lookup = {re.sub(r"[^a-z0-9]+", "", k.lower()): k for k in table.header if k}

    def g(row, *names):
        for n in names:
            key = lookup.get(re.sub(r"[^a-z0-9]+", "", n.lower()))
            if key and row.get(key) not in (None, ""):
                return row[key]
        return None

    trades = divs = cash = skipped = 0
    with db.tx() as c:
        for row in rows:
            date = parse_date(g(row, "Date", "Transaction Date", "Settlement Date"))
            if not date:
                skipped += 1
                continue
            code = parse_text(g(row, "Contract Code", "Code", "Instrument")).upper()
            name = parse_text(g(row, "Instrument", "Description", "Comments")) or None
            debit = parse_amount(g(row, "Debit")) or 0.0
            credit = parse_amount(g(row, "Credit")) or 0.0
            amount = credit - abs(debit)
            action = parse_text(g(row, "Action", "Comments", "Type"))
            kind = _classify_action(action)
            shares = parse_amount(g(row, "Shares", "Quantity", "Units"))
            price = parse_amount(g(row, "Price", "Avg Purchase Price"))
            # EasyEquities JSE codes are bare, e.g. SYGJP, and USD wallet codes are US tickers
            exch = "JSE" if code and not re.fullmatch(r"[A-Z]{1,5}", code) else None
            if kind in ("BUY", "SELL") and code:
                iid = get_or_create_instrument(code, exch, name, None, "ZAR" if exch else None)
                trades += _insert_trade(c, account_id, iid, date, kind, shares or 0.0, price,
                                        "ZAR" if exch else None, abs(amount), 0.0, None, "", row)
            elif kind == "DIVIDEND" and code:
                iid = get_or_create_instrument(code, exch, name, None, "ZAR" if exch else None)
                divs += _insert_dividend(c, account_id, iid, date, abs(amount), None, None,
                                         "ZAR", shares, price, "")
            elif kind in ("DEPOSIT", "WITHDRAWAL", "INTEREST", "FEE"):
                cash += _insert_cash(c, account_id, date, kind, amount, "ZAR", action)
            else:
                skipped += 1
    return {"trades": trades, "dividends": divs, "cash": cash, "skipped": skipped, "warnings": []}


def _activity_generic(account_id: int, table: Table, mapping: dict) -> dict:
    recs = mapping_mod.apply_mapping(table, mapping)
    trades = divs = cash = skipped = 0
    acct = db.one("SELECT currency FROM accounts WHERE id=?", (account_id,))
    acct_ccy = (acct or {}).get("currency") or "GBP"
    with db.tx() as c:
        for r in recs:
            date = parse_date(r.get("date"))
            if not date:
                skipped += 1
                continue
            kind = _classify_action(parse_text(r.get("action")))
            symbol = parse_text(r.get("symbol")).upper()
            isin = parse_text(r.get("isin")).upper() or None
            name = parse_text(r.get("name")) or None
            total = parse_amount(r.get("total"))
            ccy = parse_text(r.get("currency")) or acct_ccy
            if kind in ("BUY", "SELL") and (symbol or isin):
                shares = parse_amount(r.get("shares")) or 0.0
                price = parse_amount(r.get("price"))
                pccy = parse_text(r.get("price_currency")) or None
                iid = get_or_create_instrument(symbol or isin, None, name, isin, pccy)
                trades += _insert_trade(c, account_id, iid, date, kind, shares, price, pccy,
                                        abs(total or 0.0), parse_amount(r.get("fees")) or 0.0,
                                        parse_amount(r.get("fx_rate")), parse_text(r.get("reference")), r)
            elif kind == "DIVIDEND" and (symbol or isin):
                iid = get_or_create_instrument(symbol or isin, None, name, isin, None)
                divs += _insert_dividend(c, account_id, iid, date, total or 0.0, None,
                                         parse_amount(r.get("tax")), ccy,
                                         parse_amount(r.get("shares")), parse_amount(r.get("price")), "")
            elif kind in ("DEPOSIT", "WITHDRAWAL", "INTEREST", "FEE"):
                cash += _insert_cash(c, account_id, date, kind, total or 0.0, ccy,
                                     parse_text(r.get("action")))
            else:
                skipped += 1
    return {"trades": trades, "dividends": divs, "cash": cash, "skipped": skipped, "warnings": []}
