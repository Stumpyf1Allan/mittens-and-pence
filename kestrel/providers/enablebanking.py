"""Enable Banking — EEA Open Banking, free for your own accounts. Not UK, not ZA.

Kept, but not offered. On paper this was the best free route in the app: a "restricted
production" mode where you whitelist your *own* bank accounts and get real production
data with no contract and no fee. In practice the application form lists EEA countries
and the United Kingdom is not among them (verified against the live form, August 2026),
and South Africa is nowhere near it. So no institution in Mittens & Pence's registry routes here
any more — the code stays for anyone who holds an EEA account and wants to wire one up
by hand, and because deleting a working client to re-derive it later is wasteful.

The credential is the awkward part. TrueLayer gives you a client ID and secret to paste.
Enable Banking gives you an application ID and an RSA private key file, and every
request carries a JWT signed with it. Mittens & Pence does the signing; you paste the key once.

Signing needs the `cryptography` package. It ships in requirements.txt; if it is missing
this provider says so plainly instead of failing somewhere obscure.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import time
import urllib.parse
import uuid

from .. import db
from .base import Field, Provider, ProviderError, SyncResult, register

API = "https://api.enablebanking.com"
JWT_ISS = "enablebanking.com"
JWT_AUD = "api.enablebanking.com"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@register
class EnableBanking(Provider):
    id = "enablebanking"
    name = "Enable Banking"
    kind = "openbanking"
    countries = ["GB", "EU"]
    redirect_flow = True
    docs = "https://enablebanking.com/docs/api/quick-start/"
    setup_steps = [
        "Read this first: Enable Banking covers the EEA only. Its country list has no "
        "United Kingdom and no South Africa, so it cannot reach a UK or SA bank however "
        "the rest of the form looks. Only carry on if the account you want is held in "
        "an EEA country.",
        "Register at enablebanking.com and create an application.",
        "Application name: Mittens & Pence. It appears on your own bank's consent screen and "
        "nowhere else, so any name works.",
        "Country: the country the bank account is held in.",
        "Type of authorisation: choose the option that says you rely on an authorised "
        "third party (Enable Banking's own licence) — NOT 'own authorisation'. 'Own "
        "authorisation' means you hold a regulator's licence yourself, which a household "
        "does not. AIS/AISP means read-only account information, which is all Mittens & Pence "
        "wants; PIS/PISP means initiating payments, which Mittens & Pence never does — leave it "
        "unticked.",
        "Set the application's redirect URL to http://127.0.0.1:8765/oauth/callback "
        "— it must match exactly.",
        "Download the private key (.pem) when the application is created. It is offered "
        "once, and Enable Banking never shows it again.",
        "In the control panel, activate production in RESTRICTED mode and add your own "
        "bank accounts to it. That gives you real data, free, with no contract — it just "
        "means the application can only ever see your own accounts.",
        "Paste the application ID and the whole contents of the .pem file below "
        "(including the BEGIN and END lines).",
    ]
    credential_fields = [
        Field("application_id", "Application ID",
              help="The UUID shown on your Enable Banking application."),
        Field("private_key", "Private key (.pem contents)", secret=True,
              help="Open the .pem in Notepad and paste the whole thing, BEGIN and END "
                   "lines included."),
        Field("country", "Country", required=False,
              help="GB by default; use the bank's country if it isn't the UK.",
              placeholder="GB"),
    ]

    # ---- JWT -----------------------------------------------------------
    def _jwt(self) -> str:
        cached, exp = self.creds.get("_jwt"), self.creds.get("_jwt_exp") or 0
        if cached and time.time() < float(exp) - 60:
            return cached
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
        except Exception as e:
            raise ProviderError(
                "Enable Banking signs every request with your private key, which needs "
                "the 'cryptography' package. Install it with:  pip install cryptography"
            ) from e

        app_id = (self.creds.get("application_id") or "").strip()
        pem = (self.creds.get("private_key") or "").strip()
        if not app_id or not pem:
            raise ProviderError("Enable Banking needs both the application ID and the "
                                "private key.")
        if "BEGIN" not in pem:
            raise ProviderError("That doesn't look like a private key — paste the whole "
                                ".pem file, including the BEGIN and END lines.")
        try:
            key = serialization.load_pem_private_key(pem.encode(), password=None)
        except Exception as e:
            raise ProviderError(f"Couldn't read the private key: {e}. If it asks for a "
                                f"password, export an unencrypted copy.") from e
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ProviderError("Enable Banking needs an RSA private key.")

        now = int(time.time())
        header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
        payload = {"iss": JWT_ISS, "aud": JWT_AUD, "iat": now, "exp": now + 3600}
        signing_input = (_b64(json.dumps(header, separators=(",", ":")).encode()) + "." +
                         _b64(json.dumps(payload, separators=(",", ":")).encode()))
        signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        token = f"{signing_input}.{_b64(signature)}"
        self.save_creds({"_jwt": token, "_jwt_exp": now + 3600})
        return token

    def _call(self, path: str, method: str = "GET", body=None):
        return self.http(f"{API}{path}", method=method, body=body,
                         headers={"Authorization": f"Bearer {self._jwt()}"})

    # ---- bank matching --------------------------------------------------
    def _country(self) -> str:
        return (self.creds.get("country") or self.connection.get("country") or "GB").upper()

    def list_banks(self, country: str | None = None) -> list[dict]:
        c = country or self._country()
        try:
            out = self._call(f"/aspsps?country={urllib.parse.quote(c)}")
        except ProviderError:
            return []
        return (out or {}).get("aspsps", []) or []

    def _match_bank(self) -> dict:
        """Find the Enable Banking ASPSP that corresponds to the chosen institution."""
        saved = (self.connection.get("settings") or {}).get("aspsp")
        if saved:
            return saved
        wanted = (self.connection.get("institution_name") or "").lower()
        banks = self.list_banks()
        if not banks:
            raise ProviderError("Enable Banking returned no banks for "
                                f"{self._country()} — check the application is activated.")

        def score(b):
            n = (b.get("name") or "").lower()
            if n == wanted:
                return 0
            if wanted.startswith(n) or n.startswith(wanted):
                return 1
            first = wanted.split()[0] if wanted else ""
            if first and first in n:
                return 2
            return 99

        best = min(banks, key=score)
        if score(best) == 99:
            names = ", ".join(sorted({b.get("name", "") for b in banks})[:12])
            raise ProviderError(
                f"Enable Banking doesn't list {self.connection.get('institution_name')} "
                f"for {self._country()}. It does have: {names}…")
        chosen = {"name": best.get("name"), "country": best.get("country") or self._country()}
        settings = dict(self.connection.get("settings") or {})
        settings["aspsp"] = chosen
        with db.tx() as c:
            c.execute("UPDATE connections SET settings_json=? WHERE id=?",
                      (json.dumps(settings), self.connection["id"]))
        self.connection["settings"] = settings
        return chosen

    # ---- link -----------------------------------------------------------
    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        try:
            self._jwt()
        except ProviderError as e:
            return False, str(e)
        if self.creds.get("_session_id"):
            n = len(self.creds.get("_accounts") or [])
            return True, f"Linked — {n} account{'' if n == 1 else 's'}"
        try:
            banks = self.list_banks()
        except ProviderError as e:
            return False, str(e)
        if not banks:
            return False, ("The key works, but Enable Banking listed no banks for "
                           f"{self._country()}. Activate the application first.")
        return False, (f"Credentials accepted — {len(banks)} banks available. "
                       f"Press Link to approve access at your bank.")

    def begin_link(self, redirect_uri: str) -> dict:
        aspsp = self._match_bank()
        state = str(uuid.uuid4())
        valid_until = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(days=90)).replace(
            microsecond=0).isoformat() + "Z"
        payload = {
            "access": {"valid_until": valid_until},
            "aspsp": aspsp,
            "state": state,
            "redirect_url": redirect_uri,
            "psu_type": "personal",
        }
        out = self._call("/auth", method="POST", body=payload)
        url = (out or {}).get("url")
        if not url:
            raise ProviderError("Enable Banking didn't return an authorisation link.")
        self.save_creds({"_state": state, "_redirect": redirect_uri})
        return {"url": url, "state": state}

    def complete_link(self, params: dict) -> dict:
        code = params.get("code")
        if not code:
            raise ProviderError(params.get("error_description")
                                or "The bank didn't return an authorisation code.")
        out = self._call("/sessions", method="POST", body={"code": code})
        session_id = (out or {}).get("session_id")
        accounts = (out or {}).get("accounts") or []
        uids = [a.get("uid") for a in accounts if a.get("uid")] or \
               [a for a in accounts if isinstance(a, str)]
        if not session_id or not uids:
            raise ProviderError("The bank approved access but returned no accounts.")
        self.save_creds({"_session_id": session_id, "_accounts": uids,
                         "_account_meta": [a for a in accounts if isinstance(a, dict)]})
        with db.tx() as c:
            c.execute("UPDATE connections SET status='linked', status_detail=?, "
                      "consent_expires=? WHERE id=?",
                      (f"Linked — {len(uids)} account(s)",
                       (dt.date.today() + dt.timedelta(days=90)).isoformat(),
                       self.connection["id"]))
        return {"ok": True, "accounts": len(uids)}

    # ---- sync -----------------------------------------------------------
    def sync(self) -> SyncResult:
        res = SyncResult()
        uids = self.creds.get("_accounts") or []
        if not uids:
            raise ProviderError("Not linked yet — press Link and approve access at your bank.")
        meta = {m.get("uid"): m for m in (self.creds.get("_account_meta") or [])
                if isinstance(m, dict)}
        since = (dt.date.today() - dt.timedelta(days=400)).isoformat()

        for uid in uids:
            m = meta.get(uid, {})
            name = (m.get("name") or m.get("product")
                    or (m.get("account_id") or {}).get("iban") or "Account")
            currency = m.get("currency") or "GBP"
            balance = None
            try:
                bals = (self._call(f"/accounts/{uid}/balances") or {}).get("balances") or []
                for b in bals:
                    if b.get("name") in ("CLBD", "XPCD", "ITAV", "interimAvailable",
                                         "closingBooked") or not balance:
                        amt = (b.get("balance_amount") or {})
                        balance = float(amt.get("amount") or 0)
                        currency = amt.get("currency") or currency
            except ProviderError as e:
                res.warnings.append(f"Balance for {name}: {e}")

            account_id = self.upsert_account(
                external_id=f"eb:{uid}", name=name,
                account_type=_map_type(m.get("cash_account_type")),
                currency=currency, balance=balance,
                masked=_mask((m.get("account_id") or {}).get("iban")))
            res.accounts += 1

            rows, cont, pages = [], None, 0
            while pages < 25:
                q = f"?date_from={since}" + (f"&continuation_key={urllib.parse.quote(cont)}"
                                             if cont else "")
                try:
                    page = self._call(f"/accounts/{uid}/transactions{q}")
                except ProviderError as e:
                    res.warnings.append(f"Transactions for {name}: {e}")
                    break
                for it in (page or {}).get("transactions", []) or []:
                    amt = it.get("transaction_amount") or {}
                    value = float(amt.get("amount") or 0)
                    if (it.get("credit_debit_indicator") or "").upper() == "DBIT":
                        value = -abs(value)
                    else:
                        value = abs(value)
                    party = (it.get("creditor") or it.get("debtor") or {}).get("name")
                    desc = " ".join(it.get("remittance_information") or []) or party or ""
                    rows.append({
                        "date": (it.get("booking_date") or it.get("value_date") or "")[:10],
                        "description": desc or "(no description)",
                        "merchant": party,
                        "amount": value,
                        "currency": amt.get("currency") or currency,
                        "balance": None,
                        "reference": it.get("entry_reference") or it.get("transaction_id"),
                        "raw": it,
                    })
                cont = (page or {}).get("continuation_key")
                pages += 1
                if not cont:
                    break
                time.sleep(0.3)
            res.transactions += self.add_transactions(account_id, rows)

        from ..engine import categorise
        categorise.categorise_all(only_uncategorised=True)
        return res


def _map_type(t: str | None) -> str:
    return {"CACC": "current", "SVGS": "savings", "CARD": "credit",
            "LOAN": "loan", "CHAR": "current"}.get((t or "").upper(), "current")


def _mask(iban) -> str | None:
    if not iban:
        return None
    s = str(iban)
    return "••••" + s[-4:] if len(s) > 4 else s
