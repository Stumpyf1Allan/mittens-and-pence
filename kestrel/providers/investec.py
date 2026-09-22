"""Investec Programmable Banking — the self-serve API on the South African side.

Investec Private Banking clients enable it in Investec Online and get a client ID,
secret and API key. That is unusual: every other South African bank routes third-party
access through a commercial aggregator. If you bank with Investec, this is the one
South African connection that syncs on its own.
"""

from __future__ import annotations

import base64
import datetime as dt
import urllib.parse

from .base import Field, Provider, ProviderError, SyncResult, register

BASE_PB = "https://openapi.investec.com/za/pb/v1"
TOKEN_URL = "https://openapi.investec.com/identity/v2/oauth2/token"


@register
class Investec(Provider):
    id = "investec"
    name = "Investec Programmable Banking"
    kind = "direct_api"
    countries = ["ZA"]
    docs = "https://developer.investec.com/"
    setup_steps = [
        "Log in to Investec Online (South Africa).",
        "Go to Programmable Banking → enable it for your account.",
        "Create API credentials — you get a Client ID, a Client Secret and an API key (x-api-key).",
        "Paste all three below. They are stored encrypted on this computer.",
        "Read access is all Mittens & Pence uses; it never initiates a payment.",
    ]
    credential_fields = [
        Field("client_id", "Client ID"),
        Field("client_secret", "Client secret", secret=True),
        Field("api_key", "API key (x-api-key)", secret=True),
    ]

    def _token(self) -> str:
        cached = self.creds.get("_token")
        exp = self.creds.get("_token_expires")
        if cached and exp and dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() < exp:
            return cached
        basic = base64.b64encode(
            f"{self.creds.get('client_id','')}:{self.creds.get('client_secret','')}".encode()
        ).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"})
        payload = self.http(
            TOKEN_URL, method="POST",
            headers={"Authorization": f"Basic {basic}",
                     "x-api-key": self.creds.get("api_key", ""),
                     "Content-Type": "application/x-www-form-urlencoded"},
            body=body)
        token = payload.get("access_token")
        if not token:
            raise ProviderError("Investec did not return an access token")
        ttl = int(payload.get("expires_in") or 1800)
        self.save_creds({
            "_token": token,
            "_token_expires": (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=ttl - 60)).isoformat(),
        })
        return token

    def _get(self, path: str):
        return self.http(f"{BASE_PB}{path}",
                         headers={"Authorization": f"Bearer {self._token()}"})

    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        try:
            data = self._get("/accounts")
            n = len(((data or {}).get("data") or {}).get("accounts") or [])
            return True, f"Connected — {n} account(s) visible"
        except ProviderError as e:
            return False, str(e)

    def sync(self) -> SyncResult:
        res = SyncResult()
        data = self._get("/accounts")
        accounts = ((data or {}).get("data") or {}).get("accounts") or []
        since = (dt.date.today() - dt.timedelta(days=400)).isoformat()
        today = dt.date.today().isoformat()

        for a in accounts:
            aid = a.get("accountId")
            if not aid:
                continue
            name = a.get("accountName") or a.get("productName") or "Investec account"
            number = a.get("accountNumber")
            currency = a.get("currency") or "ZAR"
            balance = None
            try:
                b = self._get(f"/accounts/{aid}/balance")
                bd = (b or {}).get("data") or {}
                balance = float(bd.get("currentBalance") or bd.get("availableBalance") or 0)
                currency = bd.get("currency") or currency
            except ProviderError as e:
                res.warnings.append(f"Balance for {name}: {e}")

            acct_type = "credit" if "credit" in (a.get("productName") or "").lower() else "current"
            account_id = self.upsert_account(
                external_id=f"investec:{aid}", name=name, account_type=acct_type,
                currency=currency, balance=balance, masked=_mask(number))
            res.accounts += 1

            try:
                t = self._get(f"/accounts/{aid}/transactions?fromDate={since}&toDate={today}")
                items = ((t or {}).get("data") or {}).get("transactions") or []
            except ProviderError as e:
                res.warnings.append(f"Transactions for {name}: {e}")
                continue

            rows = []
            for it in items:
                amount = float(it.get("amount") or 0)
                if (it.get("type") or "").upper() == "DEBIT":
                    amount = -abs(amount)
                else:
                    amount = abs(amount)
                rows.append({
                    "date": (it.get("transactionDate") or it.get("postingDate") or "")[:10],
                    "description": " ".join(x for x in [it.get("description"),
                                                        it.get("cardNumber")] if x).strip(),
                    "amount": amount,
                    "currency": currency,
                    "balance": it.get("runningBalance"),
                    "reference": it.get("uuid") or it.get("postedOrder"),
                    "merchant": it.get("description"),
                    "raw": it,
                })
            res.transactions += self.add_transactions(account_id, rows)

        from ..engine import categorise
        categorise.categorise_all(only_uncategorised=True)
        return res


def _mask(number: str | None) -> str | None:
    if not number:
        return None
    s = str(number)
    return "••••" + s[-4:] if len(s) > 4 else s
