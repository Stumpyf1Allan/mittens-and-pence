"""Provider interface.

A provider is anything that can put data into Mittens & Pence without a file: an Open
Banking aggregator, a broker's own API, an aggregator. They all answer the same
three questions — what do you need from me, where do I approve it, and what did
you find — so the UI can drive any of them with the same three screens.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .. import db, security


class ProviderError(RuntimeError):
    pass


@dataclass
class Field:
    key: str
    label: str
    secret: bool = False
    help: str = ""
    required: bool = True
    placeholder: str = ""


@dataclass
class SyncResult:
    accounts: int = 0
    transactions: int = 0
    holdings: int = 0
    dividends: int = 0
    cash_events: int = 0
    warnings: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"accounts": self.accounts, "transactions": self.transactions,
                "holdings": self.holdings, "dividends": self.dividends,
                "cash_events": self.cash_events, "warnings": self.warnings,
                "detail": self.detail}


class Provider:
    id = "base"
    name = "Provider"
    kind = "direct_api"
    countries: list[str] = []
    #: what the user has to paste in before anything can happen
    credential_fields: list[Field] = []
    #: True when the flow sends the user to the institution's own site
    redirect_flow = False
    docs = ""
    setup_steps: list[str] = []

    def __init__(self, connection: dict):
        self.connection = connection
        self.creds = security.get(connection.get("credentials_ref") or "") or {}

    # -- lifecycle -------------------------------------------------------
    def validate(self) -> tuple[bool, str]:
        missing = [f.label for f in self.credential_fields
                   if f.required and not self.creds.get(f.key)]
        if missing:
            return False, "Still needed: " + ", ".join(missing)
        return True, "Ready"

    def begin_link(self, redirect_uri: str) -> dict:
        """Redirect-flow providers return {'url': ...} to send the browser to."""
        raise ProviderError(f"{self.name} does not use a redirect flow")

    def complete_link(self, params: dict) -> dict:
        raise ProviderError(f"{self.name} does not use a redirect flow")

    def sync(self) -> SyncResult:
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------
    def http(self, url: str, method: str = "GET", headers: dict | None = None,
             body=None, timeout: float = 30.0):
        data = None
        headers = dict(headers or {})
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode()
                headers.setdefault("Content-Type", "application/json")
            elif isinstance(body, str):
                data = body.encode()
            else:
                data = body
        headers.setdefault("Accept", "application/json")
        headers.setdefault("User-Agent", "Mittens & Pence/1.0 (personal finance app)")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
                if "json" in ctype:
                    return json.loads(raw or b"{}")
                return raw
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore").strip()[:600]
            except Exception:
                pass
            # Several of these APIs answer 401 with an empty body, which used to produce
            # the useless message "returned 401:" with nothing after the colon.
            reason = detail or e.reason or {
                400: "bad request", 401: "unauthorised — the credentials were rejected",
                403: "forbidden — the credentials are valid but not allowed to do this",
                404: "not found", 429: "too many requests", 500: "server error",
                502: "bad gateway", 503: "service unavailable",
            }.get(e.code, "no reason given")
            raise ProviderError(f"{self.name} returned {e.code}: {reason}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Could not reach {self.name}: {e.reason}") from e

    def save_creds(self, payload: dict):
        ref = self.connection.get("credentials_ref") or f"conn:{self.connection['id']}"
        merged = {**self.creds, **payload}
        security.put(ref, merged)
        self.creds = merged
        with db.tx() as c:
            c.execute("UPDATE connections SET credentials_ref=? WHERE id=?",
                      (ref, self.connection["id"]))
        return ref

    def upsert_account(self, external_id: str, name: str, account_type: str,
                       currency: str, balance: float | None = None,
                       is_investment: bool = False, masked: str | None = None) -> int:
        conn_id = self.connection["id"]
        member_id = self.connection.get("member_id")
        with db.tx() as c:
            c.execute(
                "INSERT INTO accounts(connection_id,member_id,external_id,name,account_type,"
                "currency,number_masked,balance,is_investment,last_updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))"
                " ON CONFLICT(connection_id,external_id) DO UPDATE SET"
                "   name=excluded.name, account_type=excluded.account_type,"
                "   currency=excluded.currency, balance=COALESCE(excluded.balance, accounts.balance),"
                "   number_masked=COALESCE(excluded.number_masked, accounts.number_masked),"
                "   is_investment=excluded.is_investment, last_updated=datetime('now')",
                (conn_id, member_id, external_id, name, account_type, currency, masked,
                 balance, int(is_investment)))
        row = db.one("SELECT id FROM accounts WHERE connection_id=? AND external_id=?",
                     (conn_id, external_id))
        return row["id"]

    def add_transactions(self, account_id: int, items: list[dict]) -> int:
        """items: {date, description, amount, currency, balance, reference, merchant, raw}"""
        n = 0
        with db.tx() as c:
            for it in items:
                fp = db.fingerprint(it.get("date"), (it.get("description") or "")[:80],
                                    round(float(it.get("amount") or 0), 2),
                                    it.get("reference") or "")
                cur = c.execute(
                    "INSERT OR IGNORE INTO transactions(account_id,posted_on,description,merchant,"
                    "amount,currency,balance_after,reference,raw_json,fingerprint)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (account_id, it.get("date"), it.get("description") or "(no description)",
                     it.get("merchant"), float(it.get("amount") or 0),
                     it.get("currency") or "GBP", it.get("balance"), it.get("reference"),
                     json.dumps(it.get("raw") or {}, default=str)[:4000], fp))
                n += cur.rowcount
        return n


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]):
    _REGISTRY[cls.id] = cls
    return cls


def get(provider_id: str) -> type[Provider] | None:
    return _REGISTRY.get(provider_id)


def available() -> list[dict]:
    return [{
        "id": c.id, "name": c.name, "kind": c.kind, "countries": c.countries,
        "redirect_flow": c.redirect_flow, "docs": c.docs,
        "setup_steps": c.setup_steps,
        "fields": [{"key": f.key, "label": f.label, "secret": f.secret, "help": f.help,
                    "required": f.required, "placeholder": f.placeholder}
                   for f in c.credential_fields],
    } for c in _REGISTRY.values()]


def is_built(provider_id: str) -> bool:
    """Is there a client class behind this provider id?"""
    return provider_id in _REGISTRY


_loaded = False


def load_all():
    """Import every provider module so the classes register themselves.

    Also tells the institution registry which providers really exist, so it never
    recommends a route Mittens & Pence has no code to drive.
    """
    global _loaded
    from . import (crypto, enablebanking, gocardless, ibkr,  # noqa: F401
                   investec, monzo, starling, trading212, truelayer)
    if not _loaded:
        from ..institutions import registry
        registry.set_built_providers(is_built)
        _loaded = True
