"""Monzo's own developer API — for your own account, free, no aggregator.

This is the right way to connect Monzo. Aggregators like TrueLayer exist so that a
*business* can reach anyone's bank; Monzo publishes an API meant for connecting to your
own account, and says so explicitly: "You may only connect to your own account or those
of a small set of users you explicitly allow." No contract, no paid tier, no sandbox
banner — just a client you create yourself at developers.monzo.com.

Two Monzo quirks worth knowing, both handled below:

  * After you approve in the browser you must ALSO approve in the Monzo app. Until you
    do, every call returns 403 with `forbidden.verification_required`. Mittens & Pence says so
    in plain English rather than showing a bare 403.
  * You can read your whole history for the first five minutes after authorising; after
    that Monzo only serves the last 90 days. So the first sync pulls everything, and
    later syncs top up. Since Mittens & Pence keeps its own history, that works out fine — but
    it means the first sync matters.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse

from .. import db
from .base import Field, Provider, ProviderError, SyncResult, register

AUTH = "https://auth.monzo.com/"
API = "https://api.monzo.com"


@register
class Monzo(Provider):
    id = "monzo"
    name = "Monzo (your own developer client)"
    kind = "direct_api"
    countries = ["GB"]
    redirect_flow = True
    docs = "https://docs.monzo.com/"
    setup_steps = [
        "Go to developers.monzo.com and sign in with the email on your Monzo account. "
        "You approve the sign-in from the Monzo app.",
        "Click 'Clients' → 'New OAuth Client'.",
        "Name: anything you like — 'Mittens & Pence' is fine. It is only shown to you.",
        "Redirect URL: paste the address shown below. It must match exactly.",
        "Confidentiality: choose CONFIDENTIAL. That is what lets Mittens & Pence refresh the "
        "token by itself instead of asking you to log in every few hours.",
        "Submit, then open the client and copy the Client ID and Client Secret in here.",
        "Press Save & link. Approve in the browser, then — importantly — approve the "
        "second prompt that appears in your Monzo app. Nothing works until you do.",
    ]
    credential_fields = [
        Field("client_id", "Client ID", help="From developers.monzo.com → Clients."),
        Field("client_secret", "Client secret", secret=True),
    ]

    # ---- oauth ---------------------------------------------------------
    def begin_link(self, redirect_uri: str) -> dict:
        ok, msg = super().validate()
        if not ok:
            raise ProviderError(msg)
        state = f"mithapp-monzo-{self.connection['id']}"
        self.save_creds({"redirect_uri": redirect_uri})
        q = urllib.parse.urlencode({
            "client_id": self.creds["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        })
        return {"url": f"{AUTH}?{q}", "state": state}

    def complete_link(self, params: dict) -> dict:
        code = params.get("code")
        if not code:
            raise ProviderError(params.get("error_description")
                                or "Monzo didn't return an authorisation code.")
        payload = self.http(f"{API}/oauth2/token", method="POST",
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            body=urllib.parse.urlencode({
                                "grant_type": "authorization_code",
                                "client_id": self.creds["client_id"],
                                "client_secret": self.creds["client_secret"],
                                "redirect_uri": self.creds.get("redirect_uri"),
                                "code": code,
                            }))
        self._store(payload)
        with db.tx() as c:
            c.execute("UPDATE connections SET status='linked', status_detail=? WHERE id=?",
                      ("Linked — now approve the prompt in your Monzo app, then press Sync.",
                       self.connection["id"]))
        return {"ok": True,
                "message": "Approve the prompt in your Monzo app, then press Sync."}

    def _store(self, payload: dict):
        ttl = int(payload.get("expires_in") or 21600)
        self.save_creds({
            "_access": payload.get("access_token"),
            "_refresh": payload.get("refresh_token") or self.creds.get("_refresh"),
            "_expires": (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=ttl - 120)).isoformat(),
            "_first_sync_done": self.creds.get("_first_sync_done", False),
        })

    def _token(self) -> str:
        if self.creds.get("_access") and \
                (self.creds.get("_expires") or "") > dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat():
            return self.creds["_access"]
        if not self.creds.get("_refresh"):
            raise ProviderError("This connection needs linking again — press Link.")
        payload = self.http(f"{API}/oauth2/token", method="POST",
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            body=urllib.parse.urlencode({
                                "grant_type": "refresh_token",
                                "client_id": self.creds["client_id"],
                                "client_secret": self.creds["client_secret"],
                                "refresh_token": self.creds["_refresh"],
                            }))
        self._store(payload)
        return self.creds["_access"]

    def _get(self, path: str):
        try:
            return self.http(f"{API}{path}",
                             headers={"Authorization": f"Bearer {self._token()}"})
        except ProviderError as e:
            if "verification_required" in str(e) or " 403" in str(e):
                raise ProviderError(
                    "Monzo needs you to approve this from inside the Monzo app. Open the "
                    "app — there should be a notification waiting — tap it, allow access, "
                    "then press Sync again. Until you do, Monzo refuses every request.") from e
            raise

    # ---- lifecycle ------------------------------------------------------
    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        if not self.creds.get("_refresh"):
            return False, "Credentials saved — press Link to approve access at Monzo."
        try:
            accounts = (self._get("/accounts") or {}).get("accounts", [])
        except ProviderError as e:
            return False, str(e)
        open_accounts = [a for a in accounts if not a.get("closed")]
        return True, f"Linked — {len(open_accounts)} Monzo account(s)"

    def sync(self) -> SyncResult:
        res = SyncResult()
        accounts = (self._get("/accounts") or {}).get("accounts", [])
        first = not self.creds.get("_first_sync_done")
        # The whole history is only available in the first few minutes after linking.
        since = None if first else \
            (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=89)).replace(
                microsecond=0).isoformat() + "Z"

        for a in accounts:
            if a.get("closed"):
                continue
            aid = a.get("id")
            desc = a.get("description") or ""
            kind = a.get("type") or ""
            name = ("Monzo joint account" if "joint" in kind.lower()
                    else "Monzo current account" if "retail" in kind.lower() or "uk_retail" in kind
                    else f"Monzo {desc}" if desc else "Monzo account")
            currency = a.get("currency") or "GBP"

            balance = None
            try:
                b = self._get(f"/balance?account_id={urllib.parse.quote(aid)}")
                balance = float(b.get("balance", 0)) / 100.0
                currency = b.get("currency") or currency
            except ProviderError as e:
                res.warnings.append(f"Balance for {name}: {e}")

            account_id = self.upsert_account(
                external_id=f"monzo:{aid}", name=name, account_type="current",
                currency=currency, balance=balance,
                masked=_mask(a.get("account_number")))
            res.accounts += 1

            q = f"?account_id={urllib.parse.quote(aid)}&expand[]=merchant&limit=100"
            if since:
                q += f"&since={urllib.parse.quote(since)}"
            rows, seen_ids, pages = [], set(), 0
            while pages < 60:
                try:
                    page = self._get(f"/transactions{q}")
                except ProviderError as e:
                    res.warnings.append(f"Transactions for {name}: {e}")
                    break
                items = (page or {}).get("transactions", []) or []
                if not items:
                    break
                for t in items:
                    if t.get("id") in seen_ids or t.get("decline_reason"):
                        continue
                    seen_ids.add(t.get("id"))
                    merchant = t.get("merchant") or {}
                    mname = merchant.get("name") if isinstance(merchant, dict) else None
                    rows.append({
                        "date": (t.get("created") or "")[:10],
                        "description": (mname or t.get("description")
                                        or t.get("notes") or "(no description)"),
                        "merchant": mname,
                        # Monzo amounts are in minor units: 1234 means £12.34.
                        "amount": float(t.get("amount", 0)) / 100.0,
                        "currency": t.get("currency") or currency,
                        "balance": None,
                        "reference": t.get("id"),
                        "raw": {k: t.get(k) for k in
                                ("category", "notes", "scheme", "settled")},
                    })
                pages += 1
                last = items[-1].get("id")
                if len(items) < 100 or not last:
                    break
                base = q.split("&since=")[0].split("&before=")[0]
                q = f"{base}&since={urllib.parse.quote(last)}"
            res.transactions += self.add_transactions(account_id, rows)

        if first:
            self.save_creds({"_first_sync_done": True})
            res.detail["note"] = ("First sync pulled your whole history. From now on Monzo "
                                  "only serves the last 90 days, which Mittens & Pence adds to what "
                                  "it already holds.")
        from ..engine import categorise
        categorise.categorise_all(only_uncategorised=True)
        return res


def _mask(number) -> str | None:
    if not number:
        return None
    s = str(number)
    return "••••" + s[-4:] if len(s) > 4 else s
