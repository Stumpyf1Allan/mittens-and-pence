"""Interactive Brokers — Flex Web Service.

Not a live API in the usual sense: you build a Flex Query in Client Portal (positions,
trades, cash report, dividends), IBKR gives you a token, and Mittens & Pence asks for the
report on demand. Two HTTP calls: request a reference code, then fetch the XML.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from .. import db
from ..importers import ingest
from .base import Field, Provider, ProviderError, SyncResult, register

SEND = ("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/"
        "SendRequest?t={token}&q={query}&v=3")
GET = ("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/"
       "GetStatement?t={token}&q={ref}&v=3")


@register
class IBKR(Provider):
    id = "ibkr"
    name = "Interactive Brokers (Flex)"
    kind = "direct_api"
    countries = ["GB", "EU", "ZA", "US"]
    docs = "https://www.ibkrguides.com/clientportal/performanceandstatements/flex.htm"
    setup_steps = [
        "Client Portal → Performance & Reports → Flex Queries.",
        "Create an Activity Flex Query including Open Positions, Trades, Cash Report "
        "and Change in Dividend Accruals. Set the format to XML.",
        "Note the Query ID.",
        "Under Flex Web Service, generate a token (valid for a year) and note it.",
        "Paste the token and query ID below.",
    ]
    credential_fields = [
        Field("token", "Flex token", secret=True),
        Field("query_id", "Query ID"),
    ]

    def _fetch(self) -> ET.Element:
        first = self.http(SEND.format(token=self.creds.get("token", ""),
                                      query=self.creds.get("query_id", "")))
        root = ET.fromstring(first if isinstance(first, bytes) else str(first).encode())
        status = (root.findtext("Status") or "").strip()
        if status != "Success":
            raise ProviderError(root.findtext("ErrorMessage") or "IBKR refused the request")
        ref = root.findtext("ReferenceCode")
        for attempt in range(8):
            time.sleep(2 + attempt)
            raw = self.http(GET.format(token=self.creds.get("token", ""), ref=ref))
            body = raw if isinstance(raw, bytes) else str(raw).encode()
            if b"<FlexQueryResponse" in body:
                return ET.fromstring(body)
            if b"Statement generation in progress" not in body:
                r = ET.fromstring(body)
                msg = r.findtext("ErrorMessage")
                if msg:
                    raise ProviderError(msg)
        raise ProviderError("IBKR is still generating the statement — try again shortly.")

    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        try:
            self._fetch()
            return True, "Flex query reachable"
        except ProviderError as e:
            return False, str(e)

    def sync(self) -> SyncResult:
        res = SyncResult()
        root = self._fetch()
        stmt = root.find(".//FlexStatement")
        if stmt is None:
            raise ProviderError("No statement in the Flex response")
        acct_no = stmt.get("accountId") or "IBKR"
        currency = "GBP"

        cash_el = root.find(".//CashReportCurrency[@currency='BASE_SUMMARY']")
        cash = float(cash_el.get("endingCash") or 0) if cash_el is not None else 0.0
        if cash_el is not None:
            currency = cash_el.get("currency") or currency

        account_id = self.upsert_account(
            external_id=f"ibkr:{acct_no}", name=self.connection.get("label") or f"IBKR {acct_no}",
            account_type=(self.connection.get("settings") or {}).get("account_type") or "gia",
            currency=currency, balance=cash, is_investment=True, masked=_mask(acct_no))
        res.accounts = 1

        touched = []
        with db.tx() as c:
            for pos in root.findall(".//OpenPosition"):
                symbol = pos.get("symbol")
                if not symbol:
                    continue
                iid = ingest.get_or_create_instrument(
                    symbol, _exch(pos.get("listingExchange")), pos.get("description"),
                    pos.get("isin"), pos.get("currency"))
                touched.append(iid)
                qty = float(pos.get("position") or 0)
                cost = float(pos.get("costBasisMoney") or 0)
                c.execute(
                    "INSERT INTO holdings(account_id,instrument_id,shares,cost,cost_currency,"
                    "source,updated_at) VALUES(?,?,?,?,?, 'api', datetime('now'))"
                    " ON CONFLICT(account_id,instrument_id) DO UPDATE SET shares=excluded.shares,"
                    " cost=excluded.cost, updated_at=datetime('now'), source='api'",
                    (account_id, iid, qty, cost, pos.get("currency") or currency))
                res.holdings += 1

            for tr in root.findall(".//Trade"):
                symbol = tr.get("symbol")
                if not symbol:
                    continue
                iid = ingest.get_or_create_instrument(
                    symbol, _exch(tr.get("exchange")), tr.get("description"),
                    tr.get("isin"), tr.get("currency"))
                qty = float(tr.get("quantity") or 0)
                side = "SELL" if qty < 0 else "BUY"
                res.detail["trades"] = res.detail.get("trades", 0) + ingest._insert_trade(
                    c, account_id, iid, (tr.get("tradeDate") or "")[:10], side, abs(qty),
                    float(tr.get("tradePrice") or 0), tr.get("currency"),
                    abs(float(tr.get("proceeds") or 0)),
                    abs(float(tr.get("ibCommission") or 0)),
                    float(tr.get("fxRateToBase") or 0) or None, tr.get("tradeID"), tr.attrib)

            for cd in root.findall(".//ChangeInDividendAccrual"):
                symbol = cd.get("symbol")
                if not symbol or (cd.get("code") or "") != "Po":
                    continue
                iid = ingest.get_or_create_instrument(symbol, None, cd.get("description"),
                                                      cd.get("isin"), cd.get("currency"))
                res.dividends += ingest._insert_dividend(
                    c, account_id, iid, (cd.get("payDate") or "")[:10],
                    float(cd.get("netAmount") or 0), float(cd.get("grossAmount") or 0),
                    float(cd.get("tax") or 0), cd.get("currency"),
                    float(cd.get("quantity") or 0), float(cd.get("grossRate") or 0),
                    cd.get("payDate"))

        from ..engine import portfolio
        portfolio.rebuild_holdings(account_id)
        from ..market import prices as market
        if touched:
            market.refresh_prices(touched)
        return res


def _exch(code: str | None) -> str | None:
    return {"LSE": "LON", "LSEETF": "LON", "NASDAQ": "NASDAQ", "NYSE": "NYSE",
            "ARCA": "NYSEARCA", "AEB": "AMS", "IBIS": "ETR", "SBF": "EPA",
            "JSE": "JSE"}.get((code or "").upper())


def _mask(n: str | None) -> str | None:
    if not n:
        return None
    return "••••" + str(n)[-3:]
