"""Live prices and FX — the job GOOGLEFINANCE does in the original sheet.

Three sources, tried in order, so a single outage doesn't blank the portfolio:
  1. Yahoo Finance quote/chart endpoints (covers LON, US, AMS, XETRA, JSE, crypto)
  2. Stooq daily CSV (no key, good for US/LON, one day behind)
  3. The last price stored in the database, flagged as stale

FX uses Yahoo pairs first, then Frankfurter (ECB reference rates, no key).
Prices quoted in pence (GBX) or SA cents (ZAC) are normalised to pounds/rand,
which is exactly what the sheet's `/100` and the `IFS(B="LON", ...)` block did.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import threading
import time
import urllib.parse
import urllib.request

from .. import config, db

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_lock = threading.Lock()
_memo: dict[str, tuple[float, dict]] = {}
MEMO_TTL = 60 * 10  # seconds


# A machine with no internet should fail fast, not sit through a dozen timeouts.
# After several consecutive failures the fetchers short-circuit for a minute.
_FAILS = {"count": 0, "until": 0.0}
_FAIL_THRESHOLD = 4
_FAIL_COOLDOWN = 60.0


def network_down() -> bool:
    return _FAILS["until"] > time.time()


def reset_network_state():
    _FAILS["count"] = 0
    _FAILS["until"] = 0.0


class _Offline(Exception):
    pass


def _http_get(url: str, timeout: float = 12.0) -> bytes:
    if network_down():
        raise _Offline("no network")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/csv,*/*",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        _FAILS["count"] = 0
        return data
    except Exception:
        _FAILS["count"] += 1
        if _FAILS["count"] >= _FAIL_THRESHOLD:
            _FAILS["until"] = time.time() + _FAIL_COOLDOWN
        raise


# ---------------------------------------------------------------------------
# Symbol shaping
# ---------------------------------------------------------------------------

def quote_symbol(symbol: str, exchange: str | None, asset_class: str = "equity") -> str:
    """Turn ('DGE','LON') into 'DGE.L', ('JNJ','NYSE') into 'JNJ', ('BTC','CRYPTO') into 'BTC-GBP'."""
    symbol = (symbol or "").strip().upper()
    ex = (exchange or "").strip().upper()
    if not symbol:
        return ""
    if asset_class == "crypto" or ex == "CRYPTO":
        return symbol if "-" in symbol else f"{symbol}-GBP"
    if "." in symbol and ex in ("", "LON", "LSE"):
        # LSE tickers like SN. or BT.A already carry the dot
        base = symbol
    else:
        base = symbol
    suffix = config.EXCHANGE_MAP.get(ex, {}).get("yahoo", "")
    if ex in ("LON", "LSE"):
        base = base.rstrip(".")
        return f"{base}.L"
    return f"{base}{suffix}"


def normalise_price(price: float, currency: str) -> tuple[float, str]:
    """GBX 1723.18 -> (17.2318, 'GBP')."""
    if currency in config.MINOR_UNIT_CURRENCIES:
        major, div = config.MINOR_UNIT_CURRENCIES[currency]
        return price / div, major
    return price, currency


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _yahoo_batch(symbols: list[str]) -> dict[str, dict]:
    """v7 quote endpoint, several symbols at a time."""
    out: dict[str, dict] = {}
    if not symbols:
        return out
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
               + urllib.parse.quote(",".join(chunk)))
        try:
            payload = json.loads(_http_get(url))
            for q in (payload.get("quoteResponse", {}) or {}).get("result", []) or []:
                sym = q.get("symbol")
                px = q.get("regularMarketPrice")
                if sym and px is not None:
                    out[sym] = {
                        "price": float(px),
                        "currency": q.get("currency") or "USD",
                        "change": q.get("regularMarketChange"),
                        "change_pct": (q.get("regularMarketChangePercent") or 0) / 100.0,
                        "name": q.get("longName") or q.get("shortName"),
                        "source": "yahoo-quote",
                    }
        except Exception:
            pass
    return out


def _yahoo_chart(symbol: str) -> dict | None:
    """v8 chart endpoint — used one at a time when the batch quote is unavailable."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=5d&interval=1d")
    try:
        payload = json.loads(_http_get(url))
        res = (payload.get("chart", {}) or {}).get("result") or []
        if not res:
            return None
        meta = res[0].get("meta", {}) or {}
        px = meta.get("regularMarketPrice")
        if px is None:
            closes = ((res[0].get("indicators", {}) or {}).get("quote") or [{}])[0].get("close") or []
            closes = [c for c in closes if c is not None]
            px = closes[-1] if closes else None
        if px is None:
            return None
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        change = (float(px) - float(prev)) if prev else None
        return {
            "price": float(px),
            "currency": meta.get("currency") or "USD",
            "change": change,
            "change_pct": (change / float(prev)) if (change is not None and prev) else None,
            "name": meta.get("longName") or meta.get("shortName"),
            "source": "yahoo-chart",
        }
    except Exception:
        return None


def _stooq(symbol: str) -> dict | None:
    """Free daily CSV. 'AAPL' -> aapl.us, 'DGE.L' -> dge.uk."""
    s = symbol.lower()
    if s.endswith(".l"):
        s = s[:-2].replace(".", "-") + ".uk"
    elif "." not in s and "-" not in s:
        s = s + ".us"
    url = f"https://stooq.com/q/l/?s={urllib.parse.quote(s)}&f=sd2t2ohlcv&h&e=csv"
    try:
        text = _http_get(url, timeout=10).decode("utf-8", "ignore")
        rdr = csv.DictReader(io.StringIO(text))
        row = next(rdr, None)
        if not row or row.get("Close") in (None, "", "N/D"):
            return None
        px = float(row["Close"])
        cur = "GBX" if s.endswith(".uk") else "USD"
        op = float(row["Open"]) if row.get("Open") not in (None, "", "N/D") else None
        return {"price": px, "currency": cur,
                "change": (px - op) if op else None,
                "change_pct": ((px - op) / op) if op else None,
                "name": None, "source": "stooq"}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------

def _frankfurter(base: str, quote: str) -> float | None:
    try:
        url = f"https://api.frankfurter.app/latest?from={base}&to={quote}"
        payload = json.loads(_http_get(url, timeout=10))
        return float(payload["rates"][quote])
    except Exception:
        return None


# Last-resort rates, used only when every live source and the cache have failed —
# so an offline machine shows roughly-right totals instead of adding rand to pounds.
# They are indicative, dated, and the app labels any figure that used them.
FALLBACK_PER_GBP = {"GBP": 1.0, "USD": 1.27, "EUR": 1.17, "ZAR": 23.0,
                    "CHF": 1.12, "AUD": 1.94, "CAD": 1.74, "JPY": 190.0,
                    "SEK": 13.4, "DKK": 8.7, "HKD": 9.9, "NOK": 13.6}


def _fallback_rate(base: str, quote: str) -> float | None:
    b, q = FALLBACK_PER_GBP.get(base), FALLBACK_PER_GBP.get(quote)
    if not b or not q:
        return None
    return q / b


def fx_used_fallback() -> bool:
    return bool(db.scalar(
        "SELECT COUNT(*) FROM fx_rates WHERE source='fallback' AND as_of=date('now')", (), 0))


def fx_rate(base: str, quote: str, use_cache: bool = True) -> float:
    """1 unit of `base` expressed in `quote`. USDGBP ≈ 0.79."""
    base, quote = (base or "").upper(), (quote or "").upper()
    if base in config.MINOR_UNIT_CURRENCIES:
        major, div = config.MINOR_UNIT_CURRENCIES[base]
        return fx_rate(major, quote, use_cache) / div
    if quote in config.MINOR_UNIT_CURRENCIES:
        major, div = config.MINOR_UNIT_CURRENCIES[quote]
        return fx_rate(base, major, use_cache) * div
    if not base or not quote or base == quote:
        return 1.0

    pair = f"{base}{quote}"
    today = dt.date.today().isoformat()
    if use_cache:
        with _lock:
            hit = _memo.get("fx:" + pair)
            if hit and time.time() - hit[0] < MEMO_TTL:
                return hit[1]["rate"]
        row = db.one("SELECT rate FROM fx_rates WHERE pair=? AND as_of=?", (pair, today))
        if row:
            return row["rate"]

    rate = None
    y = _yahoo_batch([f"{pair}=X"]).get(f"{pair}=X")
    if y:
        rate = y["price"]
    if rate is None:
        y = _yahoo_chart(f"{pair}=X")
        if y:
            rate = y["price"]
    if rate is None:
        rate = _frankfurter(base, quote)
    if rate is None:
        inv = _frankfurter(quote, base)
        if inv:
            rate = 1.0 / inv
    source = "live"
    if rate is None:
        row = db.one("SELECT rate FROM fx_rates WHERE pair=? ORDER BY as_of DESC LIMIT 1", (pair,))
        if row:
            rate, source = row["rate"], "cached"
    if rate is None:
        rate = _fallback_rate(base, quote)
        source = "fallback"
    if rate is None:
        # Refusing to invent a rate: 1.0 would quietly add rand to pounds.
        raise ValueError(f"No exchange rate available for {base}->{quote}")

    try:
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO fx_rates(pair,as_of,rate,source) VALUES(?,?,?,?)",
                      (pair, today, rate, source))
    except Exception:
        pass
    with _lock:
        _memo["fx:" + pair] = (time.time(), {"rate": rate})
    return rate


#: currencies we could not price today — surfaced in the UI rather than swallowed
UNCONVERTED: set[str] = set()


def convert(amount: float, from_ccy: str, to_ccy: str) -> float:
    if amount is None:
        return 0.0
    try:
        return float(amount) * fx_rate(from_ccy, to_ccy)
    except ValueError:
        UNCONVERTED.add(f"{from_ccy}->{to_ccy}")
        return float(amount)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def refresh_prices(instrument_ids: list[int] | None = None, force: bool = False) -> dict:
    """Fetch and store the current price for every instrument (or a subset).

    Returns a small report the UI shows after a sync.
    """
    where = ""
    params: tuple = ()
    if instrument_ids:
        where = f" WHERE id IN ({','.join('?' * len(instrument_ids))})"
        params = tuple(instrument_ids)
    insts = db.rows(f"SELECT * FROM instruments{where}", params)
    if not insts:
        return {"updated": 0, "failed": 0, "stale": 0, "details": []}

    # Build the lookup list, skipping instruments priced by hand.
    wanted: dict[str, list[dict]] = {}
    manual = []
    for i in insts:
        if i.get("manual_price") is not None:
            manual.append(i)
            continue
        qs = i.get("quote_symbol") or quote_symbol(i["symbol"], i.get("exchange"),
                                                   i.get("asset_class") or "equity")
        if not qs:
            continue
        wanted.setdefault(qs, []).append(i)

    quotes = _yahoo_batch(list(wanted.keys()))
    missing = [s for s in wanted if s not in quotes]
    for s in missing:
        q = _yahoo_chart(s) or _stooq(s)
        if q:
            quotes[s] = q

    now = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    updated = failed = 0
    details = []
    with db.tx() as c:
        for qs, insts_for in wanted.items():
            q = quotes.get(qs)
            for i in insts_for:
                if not q:
                    failed += 1
                    details.append({"symbol": i["symbol"], "status": "no-quote", "quote_symbol": qs})
                    continue
                price, ccy = normalise_price(q["price"], q["currency"])
                c.execute(
                    "INSERT OR REPLACE INTO prices(instrument_id,as_of,price,currency,change,change_pct,source)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (i["id"], now, price, ccy, q.get("change"), q.get("change_pct"), q["source"]))
                if not i.get("name") and q.get("name"):
                    c.execute("UPDATE instruments SET name=? WHERE id=?", (q["name"], i["id"]))
                if not i.get("currency"):
                    c.execute("UPDATE instruments SET currency=? WHERE id=?", (ccy, i["id"]))
                if not i.get("quote_symbol"):
                    c.execute("UPDATE instruments SET quote_symbol=? WHERE id=?", (qs, i["id"]))
                updated += 1
                details.append({"symbol": i["symbol"], "status": "ok",
                                "price": price, "currency": ccy, "source": q["source"]})
        for i in manual:
            c.execute(
                "INSERT OR REPLACE INTO prices(instrument_id,as_of,price,currency,change,change_pct,source)"
                " VALUES(?,?,?,?,?,?,?)",
                (i["id"], now, i["manual_price"], i.get("currency") or "GBP", None, None, "manual"))
            updated += 1

    # Warm the FX cache for the pairs the portfolio actually needs.
    base = config.settings["base_currency"]
    for ccy in {(i.get("currency") or "") for i in insts} | {"USD", "EUR", "ZAR", "GBP"}:
        if ccy and ccy != base:
            try:
                fx_rate(ccy, base)
            except Exception:
                pass

    db.log("prices.refresh", {"updated": updated, "failed": failed})
    return {"updated": updated, "failed": failed, "at": now,
            "details": details[:200]}


def monthly_history(instrument_id: int, months: int = 36) -> dict[str, float]:
    """Month-end closes, normalised to the instrument's major currency.

    Used to reconstruct what the portfolio was actually worth before Mittens & Pence existed,
    rather than falling back to cost. Returns {'YYYY-MM': price} — possibly empty when
    the feed is unreachable, and the caller then says so instead of guessing.
    """
    inst = db.one("SELECT * FROM instruments WHERE id=?", (instrument_id,))
    if not inst:
        return {}
    qs = inst.get("quote_symbol") or quote_symbol(inst["symbol"], inst.get("exchange"),
                                                  inst.get("asset_class") or "equity")
    if not qs:
        return {}
    rng = "10y" if months > 60 else ("5y" if months > 24 else "2y")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(qs)}?range={rng}&interval=1mo")
    try:
        payload = json.loads(_http_get(url, timeout=15))
        res = (payload.get("chart", {}) or {}).get("result") or []
        if not res:
            return {}
        stamps = res[0].get("timestamp") or []
        closes = ((res[0].get("indicators", {}) or {}).get("quote") or [{}])[0].get("close") or []
        ccy = (res[0].get("meta", {}) or {}).get("currency") or inst.get("currency") or "USD"
        out = {}
        for ts, close in zip(stamps, closes):
            if close is None:
                continue
            d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
            price, cur = normalise_price(float(close), ccy)
            out[f"{d.year:04d}-{d.month:02d}"] = price
        with _lock:
            _memo[f"hist:{instrument_id}"] = (time.time(), {"ccy": ccy})
        return out
    except Exception:
        return {}


def history_currency(instrument_id: int) -> str:
    hit = _memo.get(f"hist:{instrument_id}")
    raw = hit[1]["ccy"] if hit else None
    if raw in MINOR_UNIT_CURRENCIES_KEYS:
        return config.MINOR_UNIT_CURRENCIES[raw][0]
    inst = db.one("SELECT currency FROM instruments WHERE id=?", (instrument_id,))
    return raw or (inst or {}).get("currency") or "USD"


MINOR_UNIT_CURRENCIES_KEYS = set(config.MINOR_UNIT_CURRENCIES)


def latest_price(instrument_id: int) -> dict | None:
    return db.one(
        "SELECT price, currency, change, change_pct, as_of, source FROM prices "
        "WHERE instrument_id=? ORDER BY as_of DESC LIMIT 1", (instrument_id,))


def latest_prices() -> dict[int, dict]:
    rows = db.rows(
        "SELECT p.* FROM prices p JOIN ("
        "  SELECT instrument_id, MAX(as_of) AS m FROM prices GROUP BY instrument_id"
        ") x ON x.instrument_id = p.instrument_id AND x.m = p.as_of")
    return {r["instrument_id"]: r for r in rows}


def lookup_symbol(query: str) -> list[dict]:
    """Search Yahoo for a ticker when someone types a company name."""
    try:
        url = ("https://query1.finance.yahoo.com/v1/finance/search?q="
               + urllib.parse.quote(query) + "&quotesCount=10&newsCount=0")
        payload = json.loads(_http_get(url, timeout=10))
        out = []
        for q in payload.get("quotes", []) or []:
            if not q.get("symbol"):
                continue
            out.append({
                "symbol": q["symbol"],
                "name": q.get("longname") or q.get("shortname"),
                "exchange": q.get("exchDisp") or q.get("exchange"),
                "type": q.get("quoteType"),
            })
        return out
    except Exception:
        return []
