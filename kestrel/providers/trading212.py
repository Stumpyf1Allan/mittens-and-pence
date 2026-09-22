"""Trading 212 — the one broker in this list with a proper self-serve API.

READ-ONLY, AND IT REALLY IS OPTIONAL TO GRANT MORE. An earlier version of this file told
people to tick every permission when generating the key, including "Orders - Execute",
on the strength of a claim that Trading 212 answers 401 unless they are all ticked. That
claim came from AlgoCloud's setup page — AlgoCloud is an automated *trading* platform
whose own account-connection step needs order rights, and its instruction was never a
statement about this API. Trading 212 added the ability to untick "Orders - Execute" and
"Pies - Write" in October 2025 specifically so read-only keys are possible, and a
permission a key does not hold produces 403, not 401. Mittens & Pence reads and never places an
order, so it asks for read permissions only. Do not grant more than that to a program
that only looks at numbers.

What the 401 actually was: the API moved. It now wants HTTP Basic with an API key AND a
secret, and several endpoint paths changed —

    /equity/account/info + /equity/account/cash  ->  /equity/account/summary
    /equity/portfolio                            ->  /equity/positions
    /history/*                                   ->  /equity/history/*

Old keys issued under the legacy scheme still work with the raw key in the Authorization
header and no secret, so both styles are probed and whichever answers is remembered.

Response shapes are parsed defensively: Trading 212 publishes endpoint paths but not
field-level schemas, so every value is looked up under several plausible names rather
than assuming one.
"""

from __future__ import annotations

import base64
import datetime as dt
import time

from .. import db
from ..importers import ingest
from .base import Field, Provider, ProviderError, SyncResult, register

LIVE = "https://live.trading212.com/api/v0"
DEMO = "https://demo.trading212.com/api/v0"


@register
class Trading212(Provider):
    id = "trading212"
    name = "Trading 212"
    kind = "direct_api"
    countries = ["GB", "EU"]
    docs = "https://t212public-api-docs.redoc.ly/"
    setup_steps = [
        "Open the Trading 212 app or web app.",
        "Menu → Settings → API (Beta) → Generate API key.",
        "Tick the READ permissions only — Account, Portfolio, History and Metadata. "
        "Leave 'Orders - Execute' and 'Pies - Write' UNTICKED. Mittens & Pence only reads; a key "
        "that cannot trade cannot cost you anything if it ever leaks.",
        "Leave the IP restriction box empty unless you have a fixed IP address.",
        "Copy BOTH values — the API key and the secret. They are shown once. Newer keys "
        "have two values; if yours has only one, paste it as the key and leave the "
        "secret blank.",
        "Keys belong to one account. If you hold both an ISA and an Invest account, "
        "generate a key for each and add a connection per account.",
        "A key made while the app was in Practice mode only works against the demo "
        "environment. Mittens & Pence tries both and tells you which one answered.",
    ]
    credential_fields = [
        Field("api_key", "API key", secret=True,
              help="Trading 212 → Settings → API (Beta). Read permissions are enough — "
                   "do not tick Orders - Execute."),
        Field("api_secret", "API secret", secret=True, required=False,
              help="Shown next to the key when you generate it. Older keys don't have "
                   "one — leave this blank if yours didn't."),
        Field("environment", "Account", required=False,
              help="live (default) or demo — leave blank and Mittens & Pence works it out",
              placeholder="live"),
    ]

    #: current path first, the pre-2025 one second, so an older tenant still syncs
    P_SUMMARY = ("/equity/account/summary", "/equity/account/info")
    P_POSITIONS = ("/equity/positions", "/equity/portfolio")
    P_DIVIDENDS = ("/equity/history/dividends", "/history/dividends")
    P_TRANSACTIONS = ("/equity/history/transactions", "/history/transactions")
    P_ORDERS = ("/equity/history/orders", "/history/orders")

    def _base(self) -> str:
        return DEMO if (self.creds.get("environment") or "live").lower() == "demo" else LIVE

    @staticmethod
    def _basic(key: str, secret: str) -> dict:
        # b64encode never wraps, but be explicit: a newline inside the header is a
        # documented cause of a 401 here, and it is invisible when you look at it.
        raw = base64.b64encode(f"{key}:{secret}".encode()).decode().replace("\n", "")
        return {"Authorization": f"Basic {raw}"}

    def _auth_header(self, style: str | None = None) -> dict:
        key = (self.creds.get("api_key") or "").strip()
        secret = (self.creds.get("api_secret") or "").strip()
        style = style or self.creds.get("_auth_style") or ("basic" if secret else "legacy")
        if style == "basic" and secret:
            return self._basic(key, secret)
        if style == "bearer":
            return {"Authorization": f"Bearer {key}"}
        return {"Authorization": key}          # legacy: raw key, no scheme

    def _auth_styles(self) -> list[str]:
        """Which header styles are worth trying, best first."""
        return ["basic", "legacy", "bearer"] if (self.creds.get("api_secret") or "").strip() \
            else ["legacy", "bearer"]

    def _get(self, *paths, params: str = ""):
        """Try each candidate path; return the first that answers."""
        last = None
        for p in paths:
            url = f"{self._base()}{p}{params}"
            try:
                return self.http(url, headers=self._auth_header())
            except ProviderError as e:
                last = e
                msg = str(e)
                if " 401" in msg or " 403" in msg:
                    raise
                if " 429" in msg:
                    time.sleep(2)
                    try:
                        return self.http(url, headers=self._auth_header())
                    except ProviderError as e2:
                        last = e2
                continue
        raise last or ProviderError("Trading 212 gave no usable response")

    # ---- diagnosis -------------------------------------------------------
    def _probe(self) -> tuple[bool, str, dict | None]:
        """Work out which host and header style this key wants, and remember it.

        Order matters: a wrong *scheme* and a missing *permission* both fail, but they
        need opposite advice, so try every scheme before concluding anything about
        permissions. A 403 anywhere means the credentials were accepted — that is a
        permission problem and there is no point trying the other environment.
        """
        chosen = (self.creds.get("environment") or "").lower()
        hosts = [("live", LIVE), ("demo", DEMO)]
        if chosen == "demo":
            hosts.reverse()
        seen, forbidden = [], None
        for env, host in hosts:
            for style in self._auth_styles():
                hdr = self._auth_header(style)
                for path in self.P_SUMMARY:
                    try:
                        info = self.http(f"{host}{path}", headers=hdr)
                    except ProviderError as e:
                        msg = str(e)
                        seen.append(f"{env}/{style}{path}: {msg}")
                        if " 403" in msg:
                            forbidden = msg
                        continue
                    self.save_creds({"environment": env, "_auth_style": style})
                    return True, env, info
        if forbidden:
            return False, f"403 {forbidden}", None
        return False, "; ".join(seen[-4:]), None

    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        key = (self.creds.get("api_key") or "").strip()
        if key != self.creds.get("api_key"):
            self.save_creds({"api_key": key})       # stray paste whitespace
        found, detail, info = self._probe()
        if found:
            cur = (info or {}).get("currencyCode") or (info or {}).get("currency") or "GBP"
            where = "your live account" if detail == "live" else "your practice account"
            return True, f"Connected to {where} — currency {cur}"
        return False, self._explain_failure(detail)

    @staticmethod
    def _explain_failure(detail: str) -> str:
        d = detail or ""
        if "403" in d:
            return ("Trading 212 accepted the key but refused the request (403). That is a "
                    "missing permission — the key needs the read permissions for Account, "
                    "Portfolio and History. It does NOT need 'Orders - Execute'; Mittens & Pence "
                    "never places an order. Regenerate the key with the read boxes ticked.\n"
                    "If they are already ticked, an IP restriction on the key is blocking "
                    "this computer.")
        if "401" in d:
            return ("Trading 212 rejected the credentials (401). A 401 is about the "
                    "credentials themselves, not permissions — a missing permission gives "
                    "403. Check, in this order:\n"
                    "• Newer keys come as a KEY and a SECRET, and both are needed. If you "
                    "only pasted one value, go back and copy the other.\n"
                    "• Only part of a value was pasted — they are shown once, so they are "
                    "easy to clip.\n"
                    "• The key belongs to your practice account but you're pointing at "
                    "live, or the other way round. Mittens & Pence tried both.\n"
                    "• The key was deleted or regenerated in the app since you pasted it. "
                    "If several old keys are lying around, remove them all and make one.")
        if "429" in d:
            return "Trading 212 is rate-limiting. Wait a minute and press Save & test again."
        if "404" in d:
            return ("Trading 212 didn't recognise the address Mittens & Pence asked for, which "
                    "means their API has moved again. Send this to Allan with the Help "
                    "button so the paths can be updated.")
        return f"Trading 212 wouldn't answer. {d}" if d else \
               "Trading 212 wouldn't answer, and gave no reason."

    @staticmethod
    def _pick(payload, *names, default=None):
        """Read a value that may sit at the top level or one nest down.

        Trading 212 publishes its endpoint paths but not its field schemas, and the
        summary endpoint absorbed what used to be a separate cash call. Rather than bet
        on one shape, look for the value where it plausibly lives.
        """
        if not isinstance(payload, dict):
            return default
        for n in names:
            v = payload.get(n)
            # "cash" is both a field name and a container name depending on the shape,
            # so a dict here is a place to look inside, not the answer.
            if v is not None and not isinstance(v, dict):
                return v
        for nest in ("cash", "account", "summary", "result", "data"):
            inner = payload.get(nest)
            if isinstance(inner, dict):
                for n in names:
                    if inner.get(n) is not None:
                        return inner[n]
        return default

    def sync(self) -> SyncResult:
        res = SyncResult()
        info = self._get(*self.P_SUMMARY) or {}
        currency = self._pick(info, "currencyCode", "currency", default="GBP")
        ext = str(self._pick(info, "id", "accountId", default=self.connection["id"]))

        # Cash used to be its own endpoint; it is part of the summary now. `free` is the
        # uninvested balance — `total` includes the positions and would double-count.
        cash = self._pick(info, "free", "freeCash", "cash")
        if cash is None:
            res.warnings.append(
                "Trading 212 didn't report a cash balance in the shape Mittens & Pence expected, "
                "so the account's spare cash is left unknown rather than guessed at zero.")

        label = self.connection.get("label") or "Trading 212"
        acct_type = (self.connection.get("settings") or {}).get("account_type") or "isa"
        account_id = self.upsert_account(
            external_id=f"t212:{ext}", name=label, account_type=acct_type,
            currency=currency,
            balance=float(cash) if cash is not None else None, is_investment=True)
        res.accounts = 1

        # ---- positions -------------------------------------------------
        try:
            positions = self._get(*self.P_POSITIONS) or []
        except ProviderError as e:
            positions = []
            res.warnings.append(f"Positions unavailable: {e}")

        touched = []
        with db.tx() as c:
            for p in positions if isinstance(positions, list) else positions.get("items", []):
                raw_ticker = p.get("ticker") or p.get("instrumentCode") or ""
                symbol, exchange = ingest.split_t212_ticker(raw_ticker)
                qty = float(p.get("quantity") or 0)
                avg = float(p.get("averagePrice") or 0)
                if not symbol or qty == 0:
                    continue
                iid = ingest.get_or_create_instrument(symbol, exchange, None, None, None)
                touched.append(iid)
                c.execute(
                    "INSERT INTO holdings(account_id,instrument_id,shares,cost,cost_currency,"
                    "source,updated_at) VALUES(?,?,?,?,?, 'api', datetime('now'))"
                    " ON CONFLICT(account_id,instrument_id) DO UPDATE SET"
                    " shares=excluded.shares, cost=excluded.cost, updated_at=datetime('now'),"
                    " source='api'",
                    (account_id, iid, qty, avg * qty, currency))
                res.holdings += 1
            if touched:
                qmarks = ",".join("?" * len(touched))
                c.execute(f"DELETE FROM holdings WHERE account_id=? AND source='api' "
                          f"AND instrument_id NOT IN ({qmarks})", (account_id, *touched))

        # ---- dividends -------------------------------------------------
        res.dividends += self._paged(
            self.P_DIVIDENDS,
            lambda item: self._save_dividend(account_id, item, currency))

        # ---- cash transactions ----------------------------------------
        res.cash_events += self._paged(
            self.P_TRANSACTIONS,
            lambda item: self._save_cash(account_id, item, currency))

        # ---- orders (so cost basis and the Sold tab can be rebuilt) ----
        res.detail["orders"] = self._paged(
            self.P_ORDERS,
            lambda item: self._save_order(account_id, item, currency))

        from ..engine import portfolio
        if res.detail.get("orders"):
            portfolio.rebuild_holdings(account_id)
        from ..market import prices as market
        if touched:
            market.refresh_prices(touched)
        return res

    # ---- paging ---------------------------------------------------------
    def _paged(self, paths, handler, limit: int = 50, max_pages: int = 40) -> int:
        n = 0
        cursor = None
        for _ in range(max_pages):
            params = f"?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
            try:
                payload = self._get(*paths, params=params)
            except ProviderError:
                break
            items = payload.get("items") if isinstance(payload, dict) else payload
            if not items:
                break
            for it in items:
                try:
                    n += handler(it) or 0
                except Exception:
                    continue
            cursor = (payload or {}).get("nextPagePath") or (payload or {}).get("cursor")
            if isinstance(cursor, str) and "cursor=" in cursor:
                cursor = cursor.split("cursor=")[1].split("&")[0]
            if not cursor:
                break
            time.sleep(0.4)
        return n

    # ---- row handlers ---------------------------------------------------
    def _save_dividend(self, account_id, item, currency) -> int:
        symbol, exchange = ingest.split_t212_ticker(item.get("ticker") or "")
        if not symbol:
            return 0
        iid = ingest.get_or_create_instrument(symbol, exchange, None, None, None)
        paid = (item.get("paidOn") or item.get("dateTime") or "")[:10]
        with db.tx() as c:
            return ingest._insert_dividend(
                c, account_id, iid, paid, float(item.get("amount") or 0),
                float(item.get("grossAmountPerShare") or 0) * float(item.get("quantity") or 0)
                if item.get("grossAmountPerShare") else None,
                None, item.get("amountInEuro") and "EUR" or currency,
                item.get("quantity"), item.get("grossAmountPerShare"),
                str(item.get("reference") or item.get("type") or ""))

    def _save_cash(self, account_id, item, currency) -> int:
        kind_raw = (item.get("type") or "").upper()
        kind = {"DEPOSIT": "DEPOSIT", "WITHDRAW": "WITHDRAWAL", "WITHDRAWAL": "WITHDRAWAL",
                "INTEREST_ON_FREE_CASH": "INTEREST", "LENDING_INTEREST": "INTEREST",
                "FEE": "FEE"}.get(kind_raw, "OTHER")
        if kind == "OTHER":
            return 0
        when = (item.get("dateTime") or item.get("date") or "")[:10]
        amt = float(item.get("amount") or 0)
        if kind == "WITHDRAWAL":
            amt = -abs(amt)
        with db.tx() as c:
            return ingest._insert_cash(c, account_id, when, kind, amt, currency,
                                       kind_raw, str(item.get("reference") or ""))

    def _save_order(self, account_id, item, currency) -> int:
        symbol, exchange = ingest.split_t212_ticker(item.get("ticker") or "")
        if not symbol:
            return 0
        qty = item.get("filledQuantity") or item.get("orderedQuantity") or 0
        try:
            qty = float(qty)
        except Exception:
            return 0
        if qty == 0:
            return 0
        side = "SELL" if qty < 0 else "BUY"
        value = abs(float(item.get("fillResult") or item.get("filledValue") or 0)) or None
        price = item.get("fillPrice")
        when = (item.get("dateModified") or item.get("dateCreated") or "")[:10]
        if not when or value is None:
            return 0
        iid = ingest.get_or_create_instrument(symbol, exchange, None, None, None)
        with db.tx() as c:
            return ingest._insert_trade(c, account_id, iid, when, side, abs(qty), price,
                                        currency, value, 0.0, None,
                                        str(item.get("id") or ""), item)
