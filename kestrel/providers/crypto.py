"""Crypto exchanges — the forgotten free APIs.

Mittens & Pence shipped with all thirteen exchanges marked "download a CSV", which was simply
wrong: most major exchanges hand any individual a read-only API key from the account
settings page, free, with no business account and no contract. That is the same deal
Monzo and Starling offer, and it was sitting there unused.

What they have in common, and why one base class covers them:

  * one credential pair (key + secret), minted by the user, scoped read-only
  * one call that lists what you hold, per asset
  * no OAuth, no redirect, no consent expiry

What differs is only the signing ritual, so each subclass implements `balances()` and
inherits everything else. Balances become holdings in an investment account; Mittens & Pence's
price feed already understands crypto symbols (`BTC` → `BTC-GBP` at Yahoo), so they get
valued and roll into net worth like any other holding.

Deliberately NOT here:
  * Uphold — production API needs a business account (its self-serve tier is sandbox).
  * Ziglu — entered special administration in July 2025.
  * Altify — a retail app with no developer surface at all.
  * AltCoinTrader, Ovex — an API exists but the read-only story could not be verified.
Those stay on statement upload rather than be promised something unverified.

SAFETY NOTE, and it is the important one: Mittens & Pence only ever reads. Every set of steps
below tells you to tick read permissions only. A key that cannot trade cannot lose you
money if it leaks, and there is no reason for this app to hold one that can.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse

from .. import db
from ..importers import ingest
from .base import Field, Provider, ProviderError, SyncResult, register

#: Ticker aliases exchanges use for the same asset. Kraken is the worst offender.
ALIASES = {
    "XBT": "BTC", "XXBT": "BTC", "XETH": "ETH", "XXRP": "XRP", "XLTC": "LTC",
    "XXLM": "XLM", "XXDG": "DOGE", "XDG": "DOGE", "ZUSD": "USD", "ZGBP": "GBP",
    "ZEUR": "EUR", "ZZAR": "ZAR", "XXMR": "XMR", "XZEC": "ZEC",
}
#: Held as cash rather than as a position.
FIAT = {"GBP", "USD", "EUR", "ZAR", "CHF", "AUD", "CAD", "JPY", "NZD", "SGD"}
#: Stablecoins are spendable balances in practice, but they are still positions —
#: they can and do break their peg, so Mittens & Pence prices them rather than assuming 1.00.
DUST = 1e-8


def canonical(asset: str) -> str:
    """Fold an exchange's ticker spelling onto one canonical symbol.

    Without this, Kraken alone would give you BTC three times over — XXBT for the spot
    balance, XBT in some endpoints, and XBT.M for the earning variant — and they would
    sit in the portfolio as three unrelated holdings.
    """
    a = (asset or "").strip().upper()
    # Staking / earning variants: ETH2.S, DOT28.S, USDC.M, XBT.M
    a = re.sub(r"\.(S|M|F|B|P)\d*$", "", a)
    # ...which can leave a bonded-period number behind: ETH2, DOT28, ATOM21
    a = re.sub(r"(?<=[A-Z])\d+$", "", a)
    return ALIASES.get(a, a)


class CryptoExchange(Provider):
    """One credential pair, one balances call, no redirect."""

    kind = "direct_api"
    #: subclasses set this so the shared setup text can name the right screen
    where_to_get_key = ""

    credential_fields = [
        Field("api_key", "API key", secret=True),
        Field("api_secret", "API secret", secret=True,
              help="Shown once when you create the key — copy it then."),
    ]

    # ---- subclass contract ---------------------------------------------
    def balances(self) -> dict[str, float]:
        """{asset: quantity}. Raise ProviderError with a human sentence on failure."""
        raise NotImplementedError

    def account_currency(self) -> str:
        return "GBP"

    # ---- shared --------------------------------------------------------
    def _key(self) -> str:
        return (self.creds.get("api_key") or "").strip()

    def _secret(self) -> str:
        return (self.creds.get("api_secret") or "").strip()

    def validate(self) -> tuple[bool, str]:
        ok, msg = super().validate()
        if not ok:
            return ok, msg
        try:
            bal = self.balances()
        except ProviderError as e:
            return False, str(e)
        held = {k: v for k, v in bal.items() if v > DUST}
        if not held:
            return True, (f"Connected to {self.name}, but the account holds nothing. "
                          f"If that's wrong, check the key was made on the right account.")
        return True, f"Connected — {len(held)} asset(s): " + ", ".join(sorted(held)[:6])

    def sync(self) -> SyncResult:
        res = SyncResult()
        bal = self.balances()
        ccy = self.account_currency()
        label = self.connection.get("label") or self.name

        cash = 0.0
        positions: dict[str, float] = {}
        for asset, qty in bal.items():
            if qty <= DUST:
                continue
            sym = canonical(asset)
            if sym in FIAT:
                # Only fiat in the account's own currency is cash; other fiat is a
                # position, because it carries FX exposure the household should see.
                if sym == ccy:
                    cash += qty
                    continue
                positions[sym + "=X"] = positions.get(sym + "=X", 0.0) + qty
                continue
            positions[sym] = positions.get(sym, 0.0) + qty

        account_id = self.upsert_account(
            external_id=f"{self.id}:{self._key()[:10]}", name=label,
            account_type="crypto", currency=ccy,
            balance=cash if cash else None, is_investment=True)
        res.accounts = 1

        touched = []
        with db.tx() as c:
            for sym, qty in positions.items():
                iid = ingest.get_or_create_instrument(
                    sym, "CRYPTO", None, None, ccy, "Crypto", asset_class="crypto")
                touched.append(iid)
                # An exchange balance endpoint says what you hold, never what you paid.
                # So a new row records cost 0 with cost_known=0 — which makes the return
                # column show a dash rather than claiming the whole position is profit —
                # and an existing row keeps whatever cost a statement import worked out,
                # along with its cost_known flag. Only `shares` is ever overwritten.
                c.execute(
                    "INSERT INTO holdings(account_id,instrument_id,shares,cost,cost_known,"
                    "cost_currency,source,updated_at)"
                    " VALUES(?,?,?,0,0,?, 'api', datetime('now'))"
                    " ON CONFLICT(account_id,instrument_id) DO UPDATE SET"
                    " shares=excluded.shares, updated_at=datetime('now'), source='api'",
                    (account_id, iid, qty, ccy))
                res.holdings += 1
            if touched:
                qmarks = ",".join("?" * len(touched))
                c.execute(f"DELETE FROM holdings WHERE account_id=? AND source='api' "
                          f"AND instrument_id NOT IN ({qmarks})", (account_id, *touched))

        res.warnings.append(
            "Balances only — exchanges don't publish what you paid. Cost basis stays as "
            "whatever you've imported from a statement, so the return figure needs one "
            "trade-history upload to be meaningful.")
        return res


# ---------------------------------------------------------------------------

@register
class Kraken(CryptoExchange):
    id = "kraken"
    name = "Kraken"
    countries = ["GB", "ZA"]
    docs = "https://support.kraken.com/articles/360000919966-how-to-create-an-api-key"
    setup_steps = [
        "Sign in at kraken.com, then open Settings → API (or pro.kraken.com → Settings → API).",
        "Press Create API key. Description: Mittens & Pence.",
        "Tick ONLY these permissions: Query Funds, Query Ledger Entries, Query Closed "
        "Orders & Trades. Leave every trading and withdrawal box unticked — Mittens & Pence only "
        "reads, and a key that can't trade can't cost you anything if it leaks.",
        "Optionally set an expiry date. Mittens & Pence will tell you plainly when a key expires.",
        "Copy the API Key and the Private Key and paste both below.",
    ]

    def _post(self, path: str, data: dict | None = None):
        data = dict(data or {})
        data["nonce"] = str(int(time.time() * 1000))
        post = urllib.parse.urlencode(data)
        try:
            # validate=True matters: without it b64decode silently discards every
            # character outside the alphabet, so a pasted-wrong secret decodes to
            # garbage and the user gets "invalid signature" instead of "that isn't a key".
            secret = base64.b64decode(self._secret(), validate=True)
        except Exception as e:
            raise ProviderError(
                "That doesn't look like a Kraken private key — it should be a long "
                "base64 string ending in '=='. Copy it again from Settings → API.") from e
        sha = hashlib.sha256((data["nonce"] + post).encode()).digest()
        sig = base64.b64encode(
            hmac.new(secret, path.encode() + sha, hashlib.sha512).digest()).decode()
        out = self.http("https://api.kraken.com" + path, "POST",
                        headers={"API-Key": self._key(), "API-Sign": sig,
                                 "Content-Type": "application/x-www-form-urlencoded"},
                        body=post)
        if isinstance(out, bytes):
            out = json.loads(out or b"{}")
        errs = (out or {}).get("error") or []
        if errs:
            joined = "; ".join(errs)
            if "Invalid key" in joined or "Invalid signature" in joined:
                raise ProviderError(
                    "Kraken rejected the key. The two values are easy to swap — the API "
                    "Key is the shorter one, the Private Key is the long base64 string.")
            if "Permission denied" in joined:
                raise ProviderError(
                    "Kraken says this key lacks permission. Edit it and tick Query Funds.")
            raise ProviderError(f"Kraken: {joined}")
        return (out or {}).get("result") or {}

    def balances(self) -> dict[str, float]:
        return {k: float(v) for k, v in (self._post("/0/private/Balance") or {}).items()}


@register
class Binance(CryptoExchange):
    id = "binance"
    name = "Binance"
    countries = ["GB", "ZA"]
    docs = "https://www.binance.com/en/support/faq/360002502072"
    setup_steps = [
        "Sign in at binance.com. You need 2FA on and identity verification complete, or "
        "the key option won't appear.",
        "Profile → Account → API Management → Create API → System generated.",
        "Label: Mittens & Pence.",
        "Leave it with 'Enable Reading' only — that is what a new key gets by default, "
        "and it is all Mittens & Pence wants. Do not enable trading or withdrawals.",
        "Copy the API Key and Secret Key and paste both below. The secret is shown once.",
    ]

    def balances(self) -> dict[str, float]:
        qs = urllib.parse.urlencode({"timestamp": int(time.time() * 1000),
                                     "recvWindow": 20000})
        sig = hmac.new(self._secret().encode(), qs.encode(), hashlib.sha256).hexdigest()
        try:
            out = self.http(f"https://api.binance.com/api/v3/account?{qs}&signature={sig}",
                            headers={"X-MBX-APIKEY": self._key()})
        except ProviderError as e:
            s = str(e)
            if "-2015" in s or " 401" in s:
                raise ProviderError(
                    "Binance rejected the key. Three usual causes: the secret was clipped "
                    "when pasted, the key has an IP restriction that doesn't include this "
                    "computer, or it was made on Binance.US rather than Binance.com.") from e
            if "-1021" in s:
                raise ProviderError(
                    "Binance says the request timestamp is out of sync. Your computer's "
                    "clock is off by more than a few seconds — fix the system clock and "
                    "try again.") from e
            raise
        if isinstance(out, bytes):
            out = json.loads(out or b"{}")
        res = {}
        for b in (out or {}).get("balances", []) or []:
            qty = float(b.get("free") or 0) + float(b.get("locked") or 0)
            if qty > DUST:
                res[b.get("asset") or ""] = qty
        return res


@register
class Luno(CryptoExchange):
    id = "luno"
    name = "Luno"
    countries = ["ZA", "GB"]
    docs = "https://www.luno.com/en/developers/api"
    setup_steps = [
        "Sign in at luno.com and go to luno.com/wallet/settings/api_keys.",
        "Create a new API key. Name: Mittens & Pence.",
        "Permission preset: choose Read-only access. Luno offers this as a single "
        "option, which makes it the safest key on this list.",
        "Copy the API Key ID and the API Key Secret and paste both below.",
    ]

    def account_currency(self) -> str:
        return (self.connection.get("country") or "ZA") == "ZA" and "ZAR" or "GBP"

    def balances(self) -> dict[str, float]:
        token = base64.b64encode(f"{self._key()}:{self._secret()}".encode()).decode()
        try:
            out = self.http("https://api.luno.com/api/1/balance",
                            headers={"Authorization": f"Basic {token}"})
        except ProviderError as e:
            if " 401" in str(e):
                raise ProviderError(
                    "Luno rejected the key. Check the Key ID and Secret are the right way "
                    "round, and that the key hasn't been revoked in Settings → API keys.") from e
            raise
        if isinstance(out, bytes):
            out = json.loads(out or b"{}")
        res = {}
        for b in (out or {}).get("balance", []) or []:
            qty = float(b.get("balance") or 0) + float(b.get("reserved") or 0)
            if qty > DUST:
                a = b.get("asset") or ""
                res[a] = res.get(a, 0.0) + qty
        return res


@register
class Valr(CryptoExchange):
    id = "valr"
    name = "VALR"
    countries = ["ZA"]
    docs = "https://docs.valr.com/"
    setup_steps = [
        "Sign in at valr.com. Two-factor authentication must be on, or VALR will not let "
        "you create a key at all.",
        "Account menu → API Keys → New API Key. Label: Mittens & Pence.",
        "Permissions: tick View access only. Do not tick Trade or Withdraw.",
        "Copy the API Key and the API Secret and paste both below.",
    ]

    def account_currency(self) -> str:
        return "ZAR"

    def balances(self) -> dict[str, float]:
        ts = str(int(time.time() * 1000))
        path = "/v1/account/balances"
        payload = ts + "GET" + path
        sig = hmac.new(self._secret().encode(), payload.encode(), hashlib.sha512).hexdigest()
        try:
            out = self.http("https://api.valr.com" + path, headers={
                "X-VALR-API-KEY": self._key(),
                "X-VALR-SIGNATURE": sig,
                "X-VALR-TIMESTAMP": ts})
        except ProviderError as e:
            if " 401" in str(e) or " 403" in str(e):
                raise ProviderError(
                    "VALR rejected the key. Check it has View access, that two-factor is "
                    "still enabled on the account, and that the secret pasted in full.") from e
            raise
        if isinstance(out, bytes):
            out = json.loads(out or b"[]")
        res = {}
        for b in out or []:
            qty = float(b.get("total") or 0)
            if qty > DUST:
                res[b.get("currency") or ""] = qty
        return res


@register
class Coinbase(CryptoExchange):
    id = "coinbase"
    name = "Coinbase"
    countries = ["GB", "ZA"]
    docs = "https://docs.cdp.coinbase.com/coinbase-app/introduction/get-started"
    setup_steps = [
        "Go to portal.cdp.coinbase.com and sign in with your Coinbase account.",
        "API keys → Create API key → choose the Coinbase App (not Exchange) key type.",
        "Nickname: Mittens & Pence.",
        "Permissions: tick View only. Leave Trade and Transfer off.",
        "Important: choose an ECDSA key, not Ed25519 — Coinbase App keys only accept "
        "ECDSA, and an Ed25519 key will be rejected with a signing error.",
        "Download or copy the key name and the private key. The private key is a block "
        "beginning '-----BEGIN EC PRIVATE KEY-----'. Paste the whole thing, including "
        "the BEGIN and END lines.",
    ]
    credential_fields = [
        Field("api_key", "Key name", secret=False,
              help="Looks like organizations/…/apiKeys/… — copy it from the portal."),
        Field("api_secret", "Private key (PEM)", secret=True,
              help="The whole -----BEGIN EC PRIVATE KEY----- block, BEGIN and END lines "
                   "included."),
    ]

    def _jwt(self, method: str, path: str) -> str:
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils
        except ImportError as e:
            raise ProviderError(
                "Coinbase keys are signed with a certificate, which needs the "
                "'cryptography' package. Install it with:  pip install cryptography") from e
        import secrets as _secrets

        pem = self._secret().replace("\\n", "\n").strip()
        try:
            key = serialization.load_pem_private_key(pem.encode(), password=None)
        except Exception as e:
            raise ProviderError(
                "That private key could not be read. It should be the whole PEM block "
                "starting '-----BEGIN EC PRIVATE KEY-----'. If you copied it out of a "
                "JSON file, the \\n sequences need to be real line breaks.") from e
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ProviderError(
                "That key isn't an ECDSA key. Coinbase App keys must be ECDSA — create a "
                "new key and pick ECDSA rather than Ed25519.")

        now = int(time.time())
        header = {"alg": "ES256", "kid": self._key(), "typ": "JWT",
                  "nonce": _secrets.token_hex(16)}
        claims = {"sub": self._key(), "iss": "cdp", "nbf": now, "exp": now + 110,
                  "uri": f"{method} api.coinbase.com{path}"}

        def b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
        der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{signing_input}.{b64(raw_sig)}"

    def balances(self) -> dict[str, float]:
        res: dict[str, float] = {}
        path, guard = "/v2/accounts?limit=100", 0
        while path and guard < 25:
            guard += 1
            token = self._jwt("GET", path.split("?")[0])
            try:
                out = self.http("https://api.coinbase.com" + path,
                                headers={"Authorization": f"Bearer {token}"})
            except ProviderError as e:
                if " 401" in str(e):
                    raise ProviderError(
                        "Coinbase rejected the key. Two common causes: API key access is "
                        "switched off on the account by default — turn it on in the CDP "
                        "portal — or the key name was pasted without its full "
                        "organizations/… prefix.") from e
                raise
            if isinstance(out, bytes):
                out = json.loads(out or b"{}")
            for a in (out or {}).get("data", []) or []:
                bal = a.get("balance") or {}
                qty = float(bal.get("amount") or 0)
                if qty > DUST:
                    cur = bal.get("currency") or ""
                    res[cur] = res.get(cur, 0.0) + qty
            nxt = ((out or {}).get("pagination") or {}).get("next_uri")
            path = nxt if nxt and nxt != path else None
        return res
