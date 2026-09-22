"""TrueLayer — UK Open Banking.

Redirect flow: Mittens & Pence opens TrueLayer's page, which sends you to your own bank to
log in and approve read-only access. Mittens & Pence never sees the banking password; it gets
back a token that expires and a refresh token it stores encrypted.

You need a free TrueLayer console account for the client ID and secret. Add
`http://127.0.0.1:8765/oauth/callback` as a redirect URI in the console — Mittens & Pence
listens on that address while the link is in progress.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse

from .. import db
from .base import Field, Provider, ProviderError, SyncResult, register

AUTH = "https://auth.truelayer.com"
API = "https://api.truelayer.com/data/v1"
SCOPES = "info accounts balance cards transactions offline_access"


@register
class TrueLayer(Provider):
    id = "truelayer"
    name = "TrueLayer (UK Open Banking)"
    kind = "openbanking"
    countries = ["GB"]
    redirect_flow = True
    docs = "https://docs.truelayer.com/docs/data-api-basics"
    setup_steps = [
        "Only carry on if you already pay for TrueLayer. Otherwise close this and use a "
        "statement upload — it takes a minute a month and costs nothing.",
        "Sign in at console.truelayer.com.",
        "Click Create application. Application name: Mittens & Pence — it is only shown to you "
        "on your own bank's consent screen, so any name works.",
        "Open the application, then Settings → Redirect URIs → Add. Paste "
        "http://127.0.0.1:8765/oauth/callback exactly and save.",
        "On the same page copy the Client ID and the Client Secret (the secret is shown "
        "once — copy it now).",
        "Paste both below and press Link. Your bank's own login page opens; you approve "
        "read-only access there. Mittens & Pence never sees your banking password.",
        "The approval lasts up to 90 days. Mittens & Pence reminds you before it lapses.",
    ]
    credential_fields = [
        Field("client_id", "Client ID"),
        Field("client_secret", "Client secret", secret=True),
        Field("environment", "Environment", required=False,
              help="live (default) or sandbox", placeholder="live"),
    ]

    def _auth_host(self) -> str:
        return AUTH if (self.creds.get("environment") or "live") == "live" \
            else "https://auth.truelayer-sandbox.com"

    def _api_host(self) -> str:
        return API if (self.creds.get("environment") or "live") == "live" \
            else "https://api.truelayer-sandbox.com/data/v1"

    # ---- link ----------------------------------------------------------
    def begin_link(self, redirect_uri: str) -> dict:
        ok, msg = super().validate()
        if not ok:
            raise ProviderError(msg)
        state = f"conn-{self.connection['id']}"
        providers = "uk-ob-all uk-oauth-all"
        if (self.creds.get("environment") or "live") != "live":
            providers = "uk-cs-mock"
        q = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.creds["client_id"],
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
            "providers": providers,
            "state": state,
        })
        self.save_creds({"redirect_uri": redirect_uri})
        return {"url": f"{self._auth_host()}/?{q}", "state": state}

    def complete_link(self, params: dict) -> dict:
        code = params.get("code")
        if not code:
            raise ProviderError(params.get("error_description") or "No authorisation code returned")
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "client_id": self.creds["client_id"],
            "client_secret": self.creds["client_secret"],
            "redirect_uri": self.creds.get("redirect_uri"),
            "code": code,
        })
        payload = self.http(f"{self._auth_host()}/connect/token", method="POST",
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            body=body)
        self._store_token(payload)
        with db.tx() as c:
            c.execute("UPDATE connections SET status='linked', status_detail=?, consent_expires=? "
                      "WHERE id=?",
                      ("Linked via TrueLayer",
                       (dt.date.today() + dt.timedelta(days=90)).isoformat(),
                       self.connection["id"]))
        return {"ok": True}

    def _store_token(self, payload: dict):
        ttl = int(payload.get("expires_in") or 3600)
        self.save_creds({
            "_access": payload.get("access_token"),
            "_refresh": payload.get("refresh_token") or self.creds.get("_refresh"),
            "_expires": (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=ttl - 60)).isoformat(),
        })

    def _token(self) -> str:
        exp = self.creds.get("_expires")
        if self.creds.get("_access") and exp and dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() < exp:
            return self.creds["_access"]
        if not self.creds.get("_refresh"):
            raise ProviderError("This connection needs re-linking — press Link again.")
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": self.creds["client_id"],
            "client_secret": self.creds["client_secret"],
            "refresh_token": self.creds["_refresh"],
        })
        payload = self.http(f"{self._auth_host()}/connect/token", method="POST",
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            body=body)
        self._store_token(payload)
        return self.creds["_access"]

    def _get(self, path: str):
        return self.http(f"{self._api_host()}{path}",
                         headers={"Authorization": f"Bearer {self._token()}"})

    # ---- sync ----------------------------------------------------------
    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        if not self.creds.get("_refresh"):
            return False, "Credentials saved — press Link to approve access at your bank."
        try:
            self._get("/me")
            return True, "Linked"
        except ProviderError as e:
            return False, str(e)

    def sync(self) -> SyncResult:
        res = SyncResult()
        since = (dt.date.today() - dt.timedelta(days=400)).isoformat()
        today = dt.date.today().isoformat()

        for kind, path in (("account", "/accounts"), ("card", "/cards")):
            try:
                listing = self._get(path)
            except ProviderError as e:
                res.warnings.append(f"{kind}s: {e}")
                continue
            for a in (listing or {}).get("results", []) or []:
                ext = a.get("account_id")
                name = a.get("display_name") or a.get("provider", {}).get("display_name") or "Account"
                currency = a.get("currency") or "GBP"
                masked = (a.get("account_number") or {}).get("number") \
                    or a.get("partial_card_number")
                balance = None
                try:
                    b = self._get(f"{path}/{ext}/balance")
                    r0 = ((b or {}).get("results") or [{}])[0]
                    balance = r0.get("current") if kind == "account" else -abs(r0.get("current") or 0)
                except ProviderError as e:
                    res.warnings.append(f"Balance for {name}: {e}")

                acct_type = "credit" if kind == "card" else _map_type(a.get("account_type"))
                account_id = self.upsert_account(
                    external_id=f"tl:{ext}", name=name, account_type=acct_type,
                    currency=currency, balance=balance, masked=_mask(masked))
                res.accounts += 1

                try:
                    t = self._get(f"{path}/{ext}/transactions?from={since}&to={today}")
                    items = (t or {}).get("results", []) or []
                except ProviderError as e:
                    res.warnings.append(f"Transactions for {name}: {e}")
                    continue
                rows = []
                for it in items:
                    rows.append({
                        "date": (it.get("timestamp") or "")[:10],
                        "description": it.get("description") or it.get("merchant_name") or "",
                        "merchant": it.get("merchant_name"),
                        "amount": float(it.get("amount") or 0),
                        "currency": it.get("currency") or currency,
                        "balance": (it.get("running_balance") or {}).get("amount"),
                        "reference": it.get("transaction_id"),
                        "raw": it,
                    })
                res.transactions += self.add_transactions(account_id, rows)

        from ..engine import categorise
        categorise.categorise_all(only_uncategorised=True)
        return res


def _map_type(t: str | None) -> str:
    t = (t or "").upper()
    return {"TRANSACTION": "current", "SAVINGS": "savings", "BUSINESS_TRANSACTION": "current",
            "CREDIT_CARD": "credit"}.get(t, "current")


def _mask(number) -> str | None:
    if not number:
        return None
    s = str(number)
    return "••••" + s[-4:] if len(s) > 4 else s
