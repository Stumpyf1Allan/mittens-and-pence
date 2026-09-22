"""Starling's own developer API — a personal access token, no OAuth dance.

The simplest connection in the whole app. Starling lets you mint a token for your own
account in their developer portal, with read-only scopes. Paste it in and you're done:
no client ID, no redirect URL, no aggregator, no 90-day consent expiry.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse

from .base import Field, Provider, ProviderError, SyncResult, register

API = "https://api.starlingbank.com/api/v2"
SANDBOX = "https://api-sandbox.starlingbank.com/api/v2"


@register
class Starling(Provider):
    id = "starling"
    name = "Starling (personal access token)"
    kind = "direct_api"
    countries = ["GB"]
    docs = "https://developer.starlingbank.com/"
    setup_steps = [
        "Go to developer.starlingbank.com and register, then sign in.",
        "Open 'Personal Access' (it may be under your account menu).",
        "Create a token. Name: anything — 'Mittens & Pence' is fine.",
        "Tick these read scopes and no others: account:read, balance:read, "
        "transaction:read, space:read. Mittens & Pence only reads.",
        "Copy the token and paste it below. That is the whole setup — no redirect URL, "
        "no client secret, and no 90-day re-approval.",
    ]
    credential_fields = [
        Field("access_token", "Personal access token", secret=True,
              help="developer.starlingbank.com → Personal Access → create a token."),
        Field("environment", "Environment", required=False,
              help="live (default) or sandbox", placeholder="live"),
    ]

    def _base(self) -> str:
        return SANDBOX if (self.creds.get("environment") or "live").lower() == "sandbox" \
            else API

    def _get(self, path: str):
        return self.http(f"{self._base()}{path}", headers={
            "Authorization": f"Bearer {self.creds.get('access_token', '')}",
            "Accept": "application/json"})

    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        try:
            accounts = (self._get("/accounts") or {}).get("accounts", []) or []
        except ProviderError as e:
            if " 403" in str(e):
                return False, ("Starling accepted the token but refused the request — the "
                               "token is missing a scope. It needs account:read, "
                               "balance:read, transaction:read and space:read.")
            if " 401" in str(e):
                return False, ("Starling rejected the token. Check the whole thing was "
                               "pasted, and that it hasn't been revoked in the portal.")
            return False, str(e)
        return True, f"Connected — {len(accounts)} Starling account(s)"

    def sync(self) -> SyncResult:
        res = SyncResult()
        accounts = (self._get("/accounts") or {}).get("accounts", []) or []
        since = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=400)).replace(
            microsecond=0).isoformat() + ".000Z"
        until = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).replace(microsecond=0).isoformat() + ".000Z"

        for a in accounts:
            uid = a.get("accountUid")
            cat = a.get("defaultCategory")
            if not uid or not cat:
                continue
            currency = a.get("currency") or "GBP"
            name = a.get("name") or ("Starling " + (a.get("accountType") or "account").lower())

            balance = None
            try:
                b = self._get(f"/accounts/{uid}/balance")
                eff = (b or {}).get("effectiveBalance") or (b or {}).get("clearedBalance") or {}
                balance = float(eff.get("minorUnits", 0)) / 100.0
                currency = eff.get("currency") or currency
            except ProviderError as e:
                res.warnings.append(f"Balance for {name}: {e}")

            account_id = self.upsert_account(
                external_id=f"starling:{uid}", name=name,
                account_type="joint" if (a.get("accountType") or "").upper() == "JOINT"
                             else "current",
                currency=currency, balance=balance)
            res.accounts += 1

            try:
                feed = self._get(
                    f"/feed/account/{uid}/category/{cat}/transactions-between"
                    f"?minTransactionTimestamp={urllib.parse.quote(since)}"
                    f"&maxTransactionTimestamp={urllib.parse.quote(until)}")
            except ProviderError as e:
                res.warnings.append(f"Transactions for {name}: {e}")
                continue

            rows = []
            for it in (feed or {}).get("feedItems", []) or []:
                if (it.get("status") or "").upper() == "DECLINED":
                    continue
                minor = float(it.get("amount", {}).get("minorUnits", 0)) / 100.0
                # OUT means money left the account.
                amount = -minor if (it.get("direction") or "").upper() == "OUT" else minor
                party = it.get("counterPartyName")
                rows.append({
                    "date": (it.get("transactionTime") or it.get("settlementTime") or "")[:10],
                    "description": (party or it.get("reference")
                                    or it.get("spendingCategory") or "(no description)"),
                    "merchant": party,
                    "amount": amount,
                    "currency": it.get("amount", {}).get("currency") or currency,
                    "balance": None,
                    "reference": it.get("feedItemUid"),
                    "raw": {k: it.get(k) for k in
                            ("spendingCategory", "source", "reference", "status")},
                })
            res.transactions += self.add_transactions(account_id, rows)

        from ..engine import categorise
        categorise.categorise_all(only_uncategorised=True)
        return res
