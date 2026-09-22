"""GoCardless Bank Account Data (formerly Nordigen).

Kept because plenty of people still hold a working key from the free era, and the
API is the simplest of the lot. New sign-ups have been closed, so the app labels it
"existing keys only" and steers new setups to TrueLayer or Enable Banking instead.

Flow: token → pick institution → create a requisition → open the returned link →
approve at the bank → come back and read accounts, balances and transactions.
"""

from __future__ import annotations

import datetime as dt

from .. import db
from .base import Field, Provider, ProviderError, SyncResult, register

API = "https://bankaccountdata.gocardless.com/api/v2"


@register
class GoCardless(Provider):
    id = "gocardless"
    name = "GoCardless Bank Account Data (existing keys only)"
    kind = "openbanking"
    countries = ["GB", "EU"]
    redirect_flow = True
    docs = "https://developer.gocardless.com/bank-account-data/overview"
    setup_steps = [
        "This one is only useful if you already hold a secret ID and key — new "
        "sign-ups are closed and the product is winding down.",
        "Paste the Secret ID and Secret Key below.",
        "Press Link; approve access at your bank; come back to this window.",
    ]
    credential_fields = [
        Field("secret_id", "Secret ID", secret=True),
        Field("secret_key", "Secret key", secret=True),
    ]

    def _token(self) -> str:
        exp = self.creds.get("_expires")
        if self.creds.get("_access") and exp and dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() < exp:
            return self.creds["_access"]
        payload = self.http(f"{API}/token/new/", method="POST",
                            body={"secret_id": self.creds.get("secret_id"),
                                  "secret_key": self.creds.get("secret_key")})
        self.save_creds({
            "_access": payload.get("access"),
            "_refresh": payload.get("refresh"),
            "_expires": (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                         + dt.timedelta(seconds=int(payload.get("access_expires") or 86400) - 60)
                         ).isoformat(),
        })
        return self.creds["_access"]

    def _get(self, path: str):
        return self.http(f"{API}{path}", headers={"Authorization": f"Bearer {self._token()}"})

    def _post(self, path: str, body: dict):
        return self.http(f"{API}{path}", method="POST", body=body,
                         headers={"Authorization": f"Bearer {self._token()}"})

    def list_institutions(self, country: str = "gb") -> list[dict]:
        try:
            return self._get(f"/institutions/?country={country.lower()}") or []
        except ProviderError:
            return []

    def begin_link(self, redirect_uri: str) -> dict:
        inst = (self.connection.get("settings") or {}).get("gocardless_institution_id")
        if not inst:
            # try to match the chosen institution by name against their catalogue
            wanted = (self.connection.get("institution_name") or "").lower()
            for i in self.list_institutions(self.connection.get("country", "GB")):
                if wanted and wanted.split()[0] in (i.get("name") or "").lower():
                    inst = i.get("id")
                    break
        if not inst:
            raise ProviderError("Pick the matching bank from the GoCardless list first.")
        req = self._post("/requisitions/", {
            "redirect": redirect_uri,
            "institution_id": inst,
            "reference": f"mithapp-{self.connection['id']}-{int(dt.datetime.now().timestamp())}",
            "user_language": "EN",
        })
        self.save_creds({"_requisition": req.get("id")})
        return {"url": req.get("link"), "state": req.get("id")}

    def complete_link(self, params: dict) -> dict:
        rid = self.creds.get("_requisition")
        if not rid:
            raise ProviderError("No link in progress")
        req = self._get(f"/requisitions/{rid}/")
        accounts = req.get("accounts") or []
        if not accounts:
            raise ProviderError("The bank did not return any accounts — try linking again.")
        self.save_creds({"_accounts": accounts})
        with db.tx() as c:
            c.execute("UPDATE connections SET status='linked', status_detail=?, consent_expires=? "
                      "WHERE id=?", (f"Linked — {len(accounts)} account(s)",
                                     (dt.date.today() + dt.timedelta(days=90)).isoformat(),
                                     self.connection["id"]))
        return {"ok": True, "accounts": len(accounts)}

    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        if not self.creds.get("_accounts"):
            return False, "Credentials saved — press Link to approve access at your bank."
        return True, f"Linked — {len(self.creds['_accounts'])} account(s)"

    def sync(self) -> SyncResult:
        res = SyncResult()
        for ext in self.creds.get("_accounts") or []:
            try:
                details = (self._get(f"/accounts/{ext}/details/") or {}).get("account", {})
            except ProviderError as e:
                res.warnings.append(str(e))
                continue
            name = details.get("name") or details.get("product") or "Account"
            currency = details.get("currency") or "GBP"
            balance = None
            try:
                bals = (self._get(f"/accounts/{ext}/balances/") or {}).get("balances") or []
                for b in bals:
                    if b.get("balanceType") in ("interimAvailable", "closingBooked", "expected"):
                        balance = float((b.get("balanceAmount") or {}).get("amount") or 0)
                        break
            except ProviderError as e:
                res.warnings.append(f"Balance for {name}: {e}")

            account_id = self.upsert_account(
                external_id=f"gc:{ext}", name=name,
                account_type=_map_type(details.get("cashAccountType")),
                currency=currency, balance=balance, masked=_mask(details.get("iban")))
            res.accounts += 1

            try:
                tx = (self._get(f"/accounts/{ext}/transactions/") or {}).get("transactions") or {}
            except ProviderError as e:
                res.warnings.append(f"Transactions for {name}: {e}")
                continue
            rows = []
            for bucket in ("booked", "pending"):
                for it in tx.get(bucket) or []:
                    amt = float((it.get("transactionAmount") or {}).get("amount") or 0)
                    desc = (it.get("remittanceInformationUnstructured")
                            or " ".join(it.get("remittanceInformationUnstructuredArray") or [])
                            or it.get("creditorName") or it.get("debtorName") or "")
                    rows.append({
                        "date": it.get("bookingDate") or it.get("valueDate") or "",
                        "description": desc,
                        "merchant": it.get("creditorName") or it.get("debtorName"),
                        "amount": amt,
                        "currency": (it.get("transactionAmount") or {}).get("currency") or currency,
                        "balance": (it.get("balanceAfterTransaction") or {}).get("balanceAmount", {}).get("amount"),
                        "reference": it.get("internalTransactionId") or it.get("transactionId"),
                        "raw": it,
                    })
            res.transactions += self.add_transactions(account_id, rows)

        from ..engine import categorise
        categorise.categorise_all(only_uncategorised=True)
        return res


def _map_type(t: str | None) -> str:
    return {"CACC": "current", "SVGS": "savings", "CARD": "credit",
            "LOAN": "loan"}.get((t or "").upper(), "current")


def _mask(iban) -> str | None:
    if not iban:
        return None
    s = str(iban)
    return "••••" + s[-4:] if len(s) > 4 else s
