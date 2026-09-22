"""The local web server behind the desktop window.

Deliberately built on the standard library rather than a framework: the finished
Windows executable is small and there is nothing to go wrong at freeze time. It binds
to 127.0.0.1 only — nothing is exposed to the network.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import unicodedata
import os
import pathlib
import re
import socket
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import config, db, demo, inbox, reminders, security, updates
from ..engine import budgets as budget_engine
from ..engine import categorise, manual, mortgage as mortgage_engine
from ..engine import portfolio, sections as sections_engine
from ..engine import snapshots, split as split_engine, sync
from ..export import banking_xlsx, gsheets, investments_xlsx
from ..export import phone as phone_export
from ..importers import budgetsheet, ingest, mapping as mapping_mod, pdfstatement
from ..importers import profiles as profiles_mod
from ..importers.readers import Table, read_any, read_csv_bytes
from ..institutions import registry
from ..market import prices as market
from ..providers import base as providers

STATIC = pathlib.Path(__file__).resolve().parent / "static"
PREFERRED_PORT = 8765          # TrueLayer redirect URIs are registered against this


def _static_dir() -> pathlib.Path:
    frozen = config.resource_dir() / "web" / "static"
    return frozen if frozen.exists() else STATIC


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status
        self.message = message


ROUTES: list[tuple[str, re.Pattern, callable]] = []


def route(method: str, pattern: str):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


# ===========================================================================
# Bootstrap / dashboard
# ===========================================================================

@route("GET", "/api/bootstrap")
def api_bootstrap(req, m, q, body):
    has_data = bool(db.scalar("SELECT COUNT(*) FROM accounts", (), 0))
    return {
        "app": {"name": config.APP_NAME, "version": config.APP_VERSION,
                "tagline": config.APP_TAGLINE},
        "settings": config.settings.as_dict(),
        "members": db.rows("SELECT * FROM members ORDER BY is_default DESC, name"),
        "countries": registry.countries(),
        "has_data": has_data,
        "credential_store": security.storage_backend(),
        "port": req.server.server_address[1],
        "data_dir": str(config.data_dir()),
        "network": {"down": market.network_down(),
                    "fx_fallback": market.fx_used_fallback()},
    }


@route("GET", "/api/dashboard")
def api_dashboard(req, m, q, body):
    rows = portfolio.holdings_rows()
    cash = portfolio.cash_rows()
    period = q.get("period") or budget_engine.current_period()
    bstat = budget_engine.status(period)
    nw = portfolio.net_worth()
    ov = portfolio.overall()
    accounts = db.rows("""SELECT a.*, m.name AS member, c.institution_name, c.method
                          FROM accounts a LEFT JOIN members m ON m.id=a.member_id
                          LEFT JOIN connections c ON c.id=a.connection_id
                          WHERE a.closed=0 ORDER BY a.is_investment, a.name""")
    for a in accounts:
        # An unknown balance stays unknown after conversion — otherwise the row shows
        # "—" in its own currency and a confident "£0.00" in the base one.
        a["balance_base"] = (None if a["balance"] is None else
                             market.convert(a["balance"], a["currency"] or nw["currency"],
                                            nw["currency"]))
    return {
        "net_worth": nw,
        "overall": ov["all"],
        "isa": ov["isa"],
        "sold": ov["sold"],
        "sectors": ov["sectors"][:10],
        "budget": {"period": bstat["period"], "totals": bstat["totals"],
                   "items": bstat["items"][:8], "pace": bstat["pace"]},
        "accounts": accounts,
        "cash": cash,
        "movers": sorted([r for r in rows if r["day_change_pct"] is not None],
                         key=lambda r: -abs(r["day_change_pct"] or 0))[:6],
        "top": sorted(rows, key=lambda r: -(r["value"] or 0))[:8],
        "health": sync.health()["issues"],
        "history": snapshots.series("household", "net_worth", months=18),
        "spend_history": budget_engine.history(months=12),
        "recent": db.rows("""SELECT t.id, t.posted_on, t.description, t.amount, t.currency,
                                    a.name AS account, c.parent, c.name AS category
                             FROM transactions t JOIN accounts a ON a.id=t.account_id
                             LEFT JOIN categories c ON c.id=t.category_id
                             ORDER BY t.posted_on DESC, t.id DESC LIMIT 12"""),
        "currency": nw["currency"],
        "prices_stale": any(r["stale"] for r in rows),
        "network": {"down": market.network_down(), "fx_fallback": market.fx_used_fallback()},
    }


# ===========================================================================
# Institutions — the dropdown
# ===========================================================================

@route("GET", "/api/institutions")
def api_institutions(req, m, q, body):
    country = q.get("country")
    kinds = [k for k in (q.get("kind") or "").split(",") if k]
    limit = int(q.get("limit") or 60)
    items = registry.search(q.get("q", ""), country or None, kinds or None, limit)
    return {"count": len(items),
            "items": [{"id": i["id"], "name": i["name"], "kind": i["kind"],
                       "kind_label": i["kind_label"], "country": i["country"],
                       "group": i["group"], "aka": i["aka"],
                       "recommended": registry.effective_recommendation(i)} for i in items]}


@route("GET", "/api/institutions/{iid}/next-step")
def api_next_step(req, m, q, body):
    out = registry.next_step(m["iid"])
    if "error" in out:
        raise ApiError(out["error"], 404)
    return out


@route("GET", "/api/providers")
def api_providers(req, m, q, body):
    providers.load_all()
    return {"providers": providers.available(), "catalogue": registry.providers()}


# ===========================================================================
# Connections
# ===========================================================================

@route("GET", "/api/connections")
def api_connections(req, m, q, body):
    return {"items": sync.list_connections()}


@route("POST", "/api/connections")
def api_create_connection(req, m, q, body):
    conn = sync.create_connection(
        body["institution_id"], body["method"], body.get("member_id"),
        body.get("provider"), body.get("label"), body.get("settings"))
    # A connection with no accounts is useless for file uploads, so make one up front.
    if body.get("create_account", True) and body["method"] in ("csv", "manual"):
        inst = registry.get(body["institution_id"]) or {}
        acct_type = body.get("account_type") or (inst.get("account_types") or ["current"])[0]
        ccy = body.get("currency") or inst.get("currency") or config.settings["base_currency"]
        is_inv = acct_type in ("isa", "gia", "sipp", "tfsa", "ra", "trading", "crypto",
                               "unit_trust", "pension")
        with db.tx() as c:
            c.execute("INSERT INTO accounts(connection_id,member_id,external_id,name,"
                      "account_type,currency,is_investment,balance,last_updated)"
                      " VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                      (conn["id"], conn["member_id"], f"manual:{conn['id']}",
                       body.get("label") or conn["institution_name"], acct_type, ccy,
                       # Blank means "I don't know yet", not zero.
                       int(is_inv), body.get("balance")))
    return sync.get_connection(conn["id"])


@route("GET", "/api/connections/{cid}")
def api_connection(req, m, q, body):
    conn = sync.get_connection(int(m["cid"]))
    if not conn:
        raise ApiError("not found", 404)
    return conn


@route("DELETE", "/api/connections/{cid}")
def api_delete_connection(req, m, q, body):
    sync.delete_connection(int(m["cid"]), keep_data=(q.get("keep") == "1"))
    return {"ok": True}


@route("PATCH", "/api/connections/{cid}")
def api_patch_connection(req, m, q, body):
    """Change which provider or route a connection uses, or rename it.

    `method` is here so someone who *does* pay for an aggregator can move a connection
    off statement upload without deleting and recreating it. It is validated against the
    institution's own method list, so it can only ever be set to a route that exists.
    """
    allowed = {"provider", "label", "member_id", "method"}
    if "method" in (body or {}):
        conn = sync.get_connection(int(m["cid"]))
        step = registry.next_step((conn or {}).get("institution_id") or "")
        valid = {x["method"] for x in step.get("methods", [])}
        if body["method"] not in valid:
            raise ApiError(f"{body['method']} is not a route {(conn or {}).get('institution_name')} offers")
    sets, params = [], []
    for k, v in (body or {}).items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        raise ApiError("nothing to change")
    if "provider" in (body or {}):
        # Switching provider invalidates whatever was stored for the old one.
        conn = sync.get_connection(int(m["cid"]))
        if conn and conn.get("credentials_ref"):
            security.drop(conn["credentials_ref"])
        sets += ["credentials_ref=NULL", "status='needs_credentials'", "status_detail=NULL"]
    params.append(int(m["cid"]))
    with db.tx() as c:
        c.execute(f"UPDATE connections SET {','.join(sets)} WHERE id=?", params)
    return sync.get_connection(int(m["cid"]))


@route("POST", "/api/connections/{cid}/credentials")
def api_credentials(req, m, q, body):
    return sync.set_credentials(int(m["cid"]), body or {})


@route("POST", "/api/connections/{cid}/test")
def api_test(req, m, q, body):
    return sync.test_connection(int(m["cid"]))


@route("POST", "/api/connections/{cid}/link")
def api_link(req, m, q, body):
    port = req.server.server_address[1]
    redirect = f"http://127.0.0.1:{port}/oauth/callback"
    out = sync.begin_link(int(m["cid"]), redirect)
    _PENDING["connection_id"] = int(m["cid"])
    return out


@route("POST", "/api/connections/{cid}/sync")
def api_sync_one(req, m, q, body):
    return sync.sync_connection(int(m["cid"]))


@route("POST", "/api/connections/repair")
def api_repair(req, m, q, body):
    return sync.repair_connections()


@route("POST", "/api/sync-all")
def api_sync_all(req, m, q, body):
    out = sync.sync_all()
    # The folder is part of "sync everything" — for most households it IS the sync,
    # because most banks here cannot be connected at all.
    try:
        out["inbox"] = inbox.import_waiting()
    except Exception as e:
        db.log("inbox.error", {"error": str(e), "trace": traceback.format_exc()[-2000:]})
        out["inbox"] = {"error": str(e), "imported": [], "skipped": [], "added": 0}
    return out


# ===========================================================================
# The statements folder
# ===========================================================================

@route("GET", "/api/inbox")
def api_inbox(req, m, q, body):
    return inbox.status()


@route("POST", "/api/inbox/import")
def api_inbox_import(req, m, q, body):
    return inbox.import_waiting()


@route("POST", "/api/inbox/open")
def api_inbox_open(req, m, q, body):
    return api_open_folder(req, m, q, {"path": str(inbox.folder())})


@route("POST", "/api/inbox/folder")
def api_inbox_folder(req, m, q, body):
    chosen = str((body or {}).get("folder") or "").strip()
    if chosen:
        p = pathlib.Path(chosen).expanduser()
        if not p.is_absolute():
            raise ApiError("Give the whole path to the folder.")
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ApiError(f"That folder can't be used: {e}")
    config.settings["inbox_folder"] = chosen
    inbox.folder()
    return inbox.status()


# ===========================================================================
# The monthly reminder
# ===========================================================================

@route("GET", "/api/reminder")
def api_reminder(req, m, q, body):
    return reminders.state()


@route("POST", "/api/reminder")
def api_reminder_save(req, m, q, body):
    body = body or {}
    if "reminder_email" in body:
        address = str(body["reminder_email"] or "").strip()
        if address and not reminders.valid_email(address):
            raise ApiError("That email address doesn't look right — check it over.")
        config.settings["reminder_email"] = address
    if "reminder_day" in body:
        try:
            day = int(body["reminder_day"])
        except (TypeError, ValueError):
            raise ApiError("Pick a day between 1 and 31.")
        if not 1 <= day <= 31:
            raise ApiError("Pick a day between 1 and 31.")
        config.settings["reminder_day"] = day
    if "reminder_on" in body:
        config.settings["reminder_on"] = bool(body["reminder_on"])
    return reminders.state()


@route("POST", "/api/reminder/dismiss")
def api_reminder_dismiss(req, m, q, body):
    reminders.dismiss()
    return reminders.state()


@route("GET", "/api/reminder/calendar")
def api_reminder_calendar(req, m, q, body):
    return {"_file": reminders.ics().encode("utf-8"),
            "_name": f"{config.APP_FILE_NAME} - monthly statement reminder.ics",
            "_type": "text/calendar; charset=utf-8"}


@route("POST", "/api/reminder/smtp")
def api_reminder_smtp(req, m, q, body):
    body = body or {}
    if body.get("forget"):
        reminders.forget_smtp()
        return reminders.state()
    if not body.get("host") or not body.get("username"):
        raise ApiError("A mail server needs at least a host and a username.")
    reminders.save_smtp(body["host"], body.get("port") or 587, body["username"],
                        body.get("password") or "", bool(body.get("tls", True)))
    return reminders.state()


@route("POST", "/api/reminder/test")
def api_reminder_test(req, m, q, body):
    return reminders.send_email((body or {}).get("to"))


_PENDING: dict = {}


@route("GET", "/oauth/callback")
def oauth_callback(req, m, q, body):
    # One callback address serves every provider; the state says who it belongs to.
    if q.get("state") == "mithapp-google":
        try:
            gsheets.complete_link(q.get("code"))
            return {"_html": _page("Google Drive connected",
                                   "Close this tab and go back to Mittens & Pence — the "
                                   "Spreadsheets page can now publish to Google Sheets.")}
        except Exception as e:
            return {"_html": _page("That didn't work", str(e))}

    cid = _PENDING.get("connection_id")
    if not cid:
        return {"_html": _page("Nothing to finish",
                               "No link was in progress. You can close this tab.")}
    try:
        sync.complete_link(cid, q)
        return {"_html": _page("Connected",
                               "Your bank has confirmed access. Close this tab and go back "
                               "to Mittens & Pence — press Sync to pull everything in.")}
    except Exception as e:
        return {"_html": _page("That didn't work", str(e))}


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><meta charset="utf-8">
<title>{title} — Mittens & Pence</title>
<style>body{{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:34rem;
margin:14vh auto;padding:0 1.5rem;color:#1f2933}}h1{{font-size:1.4rem}}
p{{color:#52606d}}</style><h1>{title}</h1><p>{body}</p>"""


# ===========================================================================
# Accounts
# ===========================================================================

@route("GET", "/api/accounts")
def api_accounts(req, m, q, body):
    rows = db.rows("""SELECT a.*, m.name AS member, c.institution_name, c.method,
                             c.institution_id, c.id AS conn_id
                      FROM accounts a LEFT JOIN members m ON m.id=a.member_id
                      LEFT JOIN connections c ON c.id=a.connection_id
                      ORDER BY a.closed, a.is_investment, a.name""")
    base = config.settings["base_currency"]
    for a in rows:
        a["balance_base"] = (None if a["balance"] is None else
                             market.convert(a["balance"], a["currency"] or base, base))
        a["n_transactions"] = db.scalar(
            "SELECT COUNT(*) FROM transactions WHERE account_id=?", (a["id"],), 0)
        a["last_transaction"] = db.scalar(
            "SELECT MAX(posted_on) FROM transactions WHERE account_id=?", (a["id"],))
    return {"items": rows, "currency": base}


@route("POST", "/api/accounts")
def api_create_account(req, m, q, body):
    """An account with no institution behind it — a mortgage, a property, a pension
    somebody types in themselves."""
    body = body or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError("Give the account a name.")
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO accounts(name, account_type, currency, balance, member_id,"
            " is_investment, include_in_net_worth) VALUES(?,?,?,?,?,?,?)",
            (name, body.get("account_type") or "other",
             (body.get("currency") or config.settings["base_currency"]).upper(),
             body.get("balance"),
             int(body["member_id"]) if body.get("member_id") else None,
             1 if body.get("is_investment") else 0,
             0 if body.get("include_in_net_worth") == 0 else 1))
    return db.one("SELECT * FROM accounts WHERE id=?", (cur.lastrowid,))


@route("PATCH", "/api/accounts/{aid}")
def api_patch_account(req, m, q, body):
    allowed = {"name", "account_type", "currency", "balance", "member_id", "closed",
               "include_in_net_worth", "is_investment", "notes", "number_masked"}
    sets, params = [], []
    for k, v in (body or {}).items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        raise ApiError("nothing to change")
    params.append(int(m["aid"]))
    with db.tx() as c:
        c.execute(f"UPDATE accounts SET {','.join(sets)}, last_updated=datetime('now') "
                  f"WHERE id=?", params)
    return db.one("SELECT * FROM accounts WHERE id=?", (int(m["aid"]),))


@route("DELETE", "/api/accounts/{aid}")
def api_delete_account(req, m, q, body):
    with db.tx() as c:
        c.execute("DELETE FROM accounts WHERE id=?", (int(m["aid"]),))
    return {"ok": True}


@route("GET", "/api/members")
def api_members(req, m, q, body):
    rows = db.rows("SELECT * FROM members ORDER BY is_default DESC, name")
    for r in rows:
        # What removing this person would actually disturb — worth knowing before you
        # press the button, not after.
        r["accounts"] = db.scalar("SELECT COUNT(*) FROM accounts WHERE member_id=?",
                                  (r["id"],), 0)
        r["budgets"] = db.scalar("SELECT COUNT(*) FROM budgets WHERE scope='member' "
                                 "AND scope_id=?", (r["id"],), 0)
    return {"items": rows,
            # A household still called "Me" has never been told who lives in it.
            "unnamed": any(r["is_default"] and r["name"].strip().lower() in ("me", "")
                           for r in rows)}


def _member_name(value) -> str:
    name = (value or "").strip()
    if not name:
        raise ApiError("Give the person a name.")
    if len(name) > 60:
        raise ApiError("That name is too long.")
    return name


@route("POST", "/api/members")
def api_add_member(req, m, q, body):
    body = body or {}
    name = _member_name(body.get("name"))
    if db.one("SELECT id FROM members WHERE lower(name)=lower(?)", (name,)):
        raise ApiError(f"There's already someone called {name}.")
    with db.tx() as c:
        cur = c.execute("INSERT INTO members(name, colour) VALUES(?,?)",
                        (name, body.get("colour") or "#4f7cff"))
    return db.one("SELECT * FROM members WHERE id=?", (cur.lastrowid,))


@route("PATCH", "/api/members/{mid}")
def api_edit_member(req, m, q, body):
    body = body or {}
    mid = int(m["mid"])
    row = db.one("SELECT * FROM members WHERE id=?", (mid,))
    if not row:
        raise ApiError("That person is no longer in the household.")
    sets, params = [], []
    if "name" in body:
        name = _member_name(body["name"])
        clash = db.one("SELECT id FROM members WHERE lower(name)=lower(?) AND id<>?",
                       (name, mid))
        if clash:
            raise ApiError(f"There's already someone called {name}.")
        sets.append("name=?"); params.append(name)
    if "colour" in body and body["colour"]:
        sets.append("colour=?"); params.append(str(body["colour"])[:9])
    with db.tx() as c:
        if sets:
            c.execute(f"UPDATE members SET {', '.join(sets)} WHERE id=?",
                      tuple(params) + (mid,))
        if body.get("is_default"):
            # Exactly one main person, or "whose is this by default" has no answer.
            c.execute("UPDATE members SET is_default=0")
            c.execute("UPDATE members SET is_default=1 WHERE id=?", (mid,))
    return db.one("SELECT * FROM members WHERE id=?", (mid,))


@route("DELETE", "/api/members/{mid}")
def api_delete_member(req, m, q, body):
    mid = int(m["mid"])
    row = db.one("SELECT * FROM members WHERE id=?", (mid,))
    if not row:
        raise ApiError("That person is no longer in the household.")
    if db.scalar("SELECT COUNT(*) FROM members", (), 0) <= 1:
        raise ApiError("A household needs at least one person in it.")
    move_to = q.get("move_to")
    move_to = int(move_to) if move_to and move_to.isdigit() else None
    if move_to == mid:
        move_to = None
    with db.tx() as c:
        # Accounts fall to NULL on delete, which quietly unassigns them. Offering
        # somewhere for them to go means the household doesn't lose track of whose
        # things they are.
        c.execute("UPDATE accounts SET member_id=? WHERE member_id=?", (move_to, mid))
        if move_to:
            c.execute("UPDATE budgets SET scope_id=? WHERE scope='member' AND scope_id=?",
                      (move_to, mid))
        else:
            c.execute("DELETE FROM budgets WHERE scope='member' AND scope_id=?", (mid,))
        c.execute("DELETE FROM members WHERE id=?", (mid,))
        if row["is_default"]:
            nxt = c.execute("SELECT id FROM members ORDER BY id LIMIT 1").fetchone()
            if nxt:
                c.execute("UPDATE members SET is_default=1 WHERE id=?", (nxt["id"],))
    return {"ok": True, "moved_to": move_to}


# ===========================================================================
# Importing files
# ===========================================================================

_UPLOADS: dict[str, Table] = {}


@route("POST", "/api/import/analyse")
def api_analyse(req, m, q, body):
    """Raw file body; ?filename=&account_id=&kind="""
    raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    filename = q.get("filename") or "upload.csv"
    tmp = config.uploads_dir() / f"{abs(hash(filename)) % 10**8}-{filename}"
    tmp.write_bytes(raw)
    try:
        table = read_any(tmp, q.get("sheet"))
    except (pdfstatement.ScannedPdf, pdfstatement.PdfUnavailable) as e:
        # These carry a written explanation of what to do instead — a 500 and a stack
        # trace would throw that away.
        raise ApiError(str(e) or "That PDF has no readable text in it.")
    except ValueError as e:
        # Somebody's own budget workbook has no statement header and will fail here. It
        # is still a perfectly good file — read the plan out of it instead of refusing.
        blocks = _budget_blocks(tmp)
        if blocks:
            token = f"bud{len(_BUDGET_UPLOADS)}-{abs(hash(filename)) % 10**6}"
            _BUDGET_UPLOADS[token] = {"filename": filename, "blocks": blocks}
            return _budget_payload(token, filename, blocks)
        raise ApiError(str(e))
    if not table.header:
        blocks = _budget_blocks(tmp)
        if blocks:
            token = f"bud{len(_BUDGET_UPLOADS)}-{abs(hash(filename)) % 10**6}"
            _BUDGET_UPLOADS[token] = {"filename": filename, "blocks": blocks}
            return _budget_payload(token, filename, blocks)
        raise ApiError("That file has no readable header row.")

    token = f"up{len(_UPLOADS)}-{abs(hash(filename)) % 10**6}"
    _UPLOADS[token] = table

    detected = profiles_mod.detect(table.header)
    prof = profiles_mod.get(detected) if detected else None
    kind = q.get("kind") or (prof or {}).get("kind") or "transactions"
    remembered = mapping_mod.recall(table.signature())

    if remembered:
        mapping, source, kind = remembered["mapping"], "remembered", remembered["kind"]
        confidence, missing = {}, []
    elif detected and prof.get("handler"):
        mapping, source, confidence, missing = {}, "profile-handler", {}, []
    elif detected and prof.get("fields"):
        # A profile can match on a couple of header names and then resolve to a mapping
        # that is *worse* than reading the columns. Standard Bank's profile needs only
        # Date + Description and names an "Amount" column; a statement with Money In /
        # Money Out matched it, resolved to date+description+balance, and would have
        # imported every row with no amount at all. So the profile is a starting point,
        # not the last word: fill its gaps from the content-based reading, and if the
        # result still cannot produce an amount, discard it entirely.
        mapping = profiles_mod.resolve_columns(detected, table.header) or {}
        source, confidence, missing = "profile", {}, []
        sug = mapping_mod.suggest(table, kind)
        for key, col in (sug["mapping"] or {}).items():
            if key not in mapping:
                mapping[key] = col
                source = "profile+auto"
        if kind == "transactions" and not _has_amount(mapping):
            mapping, confidence, missing, source = (sug["mapping"], sug["confidence"],
                                                    sug["missing"], "auto")
    else:
        sug = mapping_mod.suggest(table, kind)
        mapping, confidence, missing, source = (sug["mapping"], sug["confidence"],
                                                sug["missing"], "auto")

    # A workbook with a heading that says "Budget P/M" over a list of categories is
    # somebody's own budget, not a bank export — no bank has ever produced one. Left to
    # itself the statement reader finds *a* header row in there and offers to import a
    # few hundred rows out of a working sheet, which would double-count every pound
    # against the real statements. So a strong budget list wins, and the screen offers
    # the way back for the rare workbook that is genuinely both.
    readable = bool(table.header) and _has_amount(mapping) and mapping.get("date") is not None
    if q.get("as") != "transactions":
        blocks = _budget_blocks(tmp)
        if [b for b in blocks if b["score"] >= 6]:
            _UPLOADS.pop(token, None)
            btoken = f"bud{len(_BUDGET_UPLOADS)}-{abs(hash(filename)) % 10**6}"
            _BUDGET_UPLOADS[btoken] = {"filename": filename, "blocks": blocks}
            payload = _budget_payload(btoken, filename, blocks)
            payload["also_statement"] = readable
            return payload

    return {
        "token": token,
        "filename": filename,
        "profile": detected,
        "profile_label": (prof or {}).get("label"),
        "profile_notes": (prof or {}).get("notes"),
        "handler": bool((prof or {}).get("handler")),
        "kind": kind,
        "columns": table.header,
        "mapping": mapping,
        "confidence": confidence,
        "missing": missing,
        "source": source,
        "rows": len(table.rows),
        "sample": [dict(zip(table.header, r)) for r in table.rows[:6]],
        "sheets": _maybe_sheets(tmp),
        "signature": table.signature(),
        "where": (prof or {}).get("where"),
        # A workbook can be both — a statement export with a budget tab beside it. Say
        # so, and let the person switch, rather than picking for them.
        "has_budget": _has_budget(tmp),
    }


def _has_amount(mapping: dict) -> bool:
    """Can this mapping produce a number for every row? Without one, an import writes
    a list of dates and descriptions worth nothing."""
    return bool(mapping) and (
        mapping.get("amount") is not None
        or mapping.get("debit") is not None
        or mapping.get("credit") is not None)


def _maybe_sheets(path: pathlib.Path):
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            from ..importers.readers import excel_sheets
            return excel_sheets(path)
        except Exception:
            return []
    return []


@route("POST", "/api/import/commit")
def api_commit(req, m, q, body):
    token = body["token"]
    table = _UPLOADS.get(token)
    if table is None:
        raise ApiError("That upload has expired — pick the file again.")
    account_id = int(body["account_id"])
    kind = body.get("kind") or "transactions"
    profile = body.get("profile")
    mapping = {k: int(v) if isinstance(v, (int, str)) and str(v).isdigit() else v
               for k, v in (body.get("mapping") or {}).items()}
    conn = db.one("SELECT connection_id FROM accounts WHERE id=?", (account_id,))
    cid = (conn or {}).get("connection_id")

    db.backup()
    if kind == "activity":
        res = ingest.import_activity(account_id, table, profile or "generic",
                                     body.get("filename", ""), cid, mapping)
    elif kind == "holdings":
        res = ingest.import_holdings(account_id, table, mapping, profile,
                                     body.get("filename", ""), cid)
    else:
        res = ingest.import_transactions(account_id, table, mapping, profile,
                                         body.get("filename", ""), cid)
    if body.get("remember", True) and mapping:
        mapping_mod.remember(table.signature(), kind, mapping,
                             body.get("institution_id"), body.get("filename"))
    categorise.detect_internal_transfers()
    with db.tx() as c:
        c.execute("UPDATE connections SET last_sync=datetime('now'), status='ready' WHERE id=?",
                  (cid,))
    _UPLOADS.pop(token, None)
    return res


# ---------------------------------------------------------------------------
# Importing somebody's existing budget spreadsheet
# ---------------------------------------------------------------------------

_BUDGET_UPLOADS: dict[str, dict] = {}


def _budget_blocks(path: pathlib.Path) -> list[dict]:
    """Budget lists in a workbook, or nothing. Never raises: this runs speculatively
    beside the statement reader, and a workbook that isn't a budget is the normal case."""
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return []
    try:
        return budgetsheet.find_budget_lists(path)
    except Exception:
        return []


def _has_budget(path: pathlib.Path) -> bool:
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return False
    try:
        return budgetsheet.has_budget_list(path)
    except Exception:
        return False


def _budget_payload(token: str, filename: str, blocks: list[dict]) -> dict:
    existing = {s["name"]: [ln["name"] for ln in s["lines"]]
                for s in sections_engine.list_sections()}
    special = set(sections_engine.special_sections()) | {"Income", "Transfers"}
    out = []
    for i, b in enumerate(blocks):
        rows = []
        for e in b["entries"]:
            section, line, is_new = budgetsheet.suggest_section(e["label"], existing)
            note = ""
            if not e["amount"]:
                note = "budgeted at zero — nothing to track"
            elif section in special:
                # A budget is a ceiling on spending. Putting one on a salary line makes
                # a category that is permanently 400% over, which is noise, not a number.
                note = "looks like income, so it isn't a spending budget"
            rows.append({"label": e["label"], "amount": round(abs(e["amount"]), 2),
                         "section": section, "line": line, "section_is_new": is_new,
                         "note": note, "include": bool(e["amount"]) and section not in special})
        out.append({
            "index": i, "sheet": b["sheet"], "header": b["header"],
            "first_row": b["first_row"], "marker": b["marker"],
            "sign": b["sign"], "currency": b.get("currency"),
            "excluded_totals": b["excluded_totals"],
            "total": round(sum(r["amount"] for r in rows if r["include"]), 2),
            "rows": rows,
        })
    return {
        "token": token, "filename": filename, "kind": "budget",
        "blocks": out,
        "sections": existing,
        "special": sections_engine.special_sections(),
        "base_currency": config.settings["base_currency"],
    }


@route("POST", "/api/import/budget/analyse")
def api_budget_analyse(req, m, q, body):
    """Raw workbook body; ?filename=. Reads the *plan* out of somebody's own sheet."""
    raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    filename = q.get("filename") or "budget.xlsx"
    tmp = config.uploads_dir() / f"{abs(hash(filename)) % 10**8}-{filename}"
    tmp.write_bytes(raw)
    blocks = _budget_blocks(tmp)
    if not blocks:
        raise ApiError(
            "I couldn't find a budget list in that file. Mittens & Pence looks for a column of "
            "category names beside a column of monthly amounts — usually under a heading "
            "with the word 'budget' in it. If the amounts are somewhere else in the "
            "workbook, tell me which sheet and I'll widen the search.")
    token = f"bud{len(_BUDGET_UPLOADS)}-{abs(hash(filename)) % 10**6}"
    _BUDGET_UPLOADS[token] = {"filename": filename, "blocks": blocks}
    return _budget_payload(token, filename, blocks)


@route("POST", "/api/import/budget/commit")
def api_budget_commit(req, m, q, body):
    body = body or {}
    token = body.get("token")
    if token and token not in _BUDGET_UPLOADS:
        raise ApiError("That upload has expired — pick the file again.")
    rows = [r for r in (body.get("rows") or []) if r.get("include", True)]
    if not rows:
        raise ApiError("Nothing was ticked, so there is nothing to import.")
    db.backup()
    res = budgetsheet.apply_budget(rows, body.get("currency") or None)
    _BUDGET_UPLOADS.pop(token, None)
    res["currency"] = body.get("currency") or config.settings["base_currency"]
    return res


@route("GET", "/api/import/history")
def api_import_history(req, m, q, body):
    return {"items": db.rows("""SELECT b.*, a.name AS account FROM import_batches b
                                LEFT JOIN accounts a ON a.id=b.account_id
                                ORDER BY b.id DESC LIMIT 40""")}


# ===========================================================================
# Investments
# ===========================================================================

@route("GET", "/api/investments")
def api_investments(req, m, q, body):
    wrapper = q.get("wrapper") or None
    rows = portfolio.holdings_rows(wrapper=wrapper)
    return {
        "rows": rows,
        "summary": portfolio.summarise(rows, portfolio.cash_rows()),
        "wrappers": {k: v["summary"] for k, v in portfolio.wrapper_breakdown().items()},
        "platforms": {k: v["summary"] for k, v in portfolio.platform_breakdown().items()},
        "sectors": portfolio.sector_allocation(rows),
        "sold": portfolio.sold_rows(),
        "overall": portfolio.overall(),
        "currency": config.settings["base_currency"],
    }


@route("POST", "/api/investments/holding")
def api_add_holding(req, m, q, body):
    iid = ingest.get_or_create_instrument(
        body["symbol"], body.get("exchange"), body.get("name"), body.get("isin"),
        body.get("currency"), body.get("sector"), body.get("asset_class") or "equity")
    with db.tx() as c:
        c.execute("INSERT INTO holdings(account_id,instrument_id,shares,cost,cost_currency,"
                  "buy_dates,source,updated_at) VALUES(?,?,?,?,?,?, 'manual', datetime('now'))"
                  " ON CONFLICT(account_id,instrument_id) DO UPDATE SET shares=excluded.shares,"
                  " cost=excluded.cost, buy_dates=excluded.buy_dates, source='manual',"
                  " updated_at=datetime('now')",
                  (int(body["account_id"]), iid, float(body["shares"]), float(body["cost"]),
                   body.get("cost_currency") or config.settings["base_currency"],
                   body.get("buy_dates")))
    market.refresh_prices([iid])
    return {"ok": True, "instrument_id": iid}


@route("DELETE", "/api/investments/holding/{hid}")
def api_delete_holding(req, m, q, body):
    with db.tx() as c:
        c.execute("DELETE FROM holdings WHERE id=?", (int(m["hid"]),))
    return {"ok": True}


@route("PATCH", "/api/instruments/{iid}")
def api_patch_instrument(req, m, q, body):
    allowed = {"name", "sector", "exchange", "currency", "quote_symbol", "manual_price",
               "asset_class", "isin"}
    sets, params = [], []
    for k, v in (body or {}).items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        raise ApiError("nothing to change")
    params.append(int(m["iid"]))
    with db.tx() as c:
        c.execute(f"UPDATE instruments SET {','.join(sets)} WHERE id=?", params)
    return db.one("SELECT * FROM instruments WHERE id=?", (int(m["iid"]),))


@route("GET", "/api/instruments")
def api_instruments(req, m, q, body):
    return {"items": db.rows("SELECT * FROM instruments ORDER BY symbol"),
            "sectors": config.SECTORS}


@route("GET", "/api/symbol-search")
def api_symbol_search(req, m, q, body):
    return {"items": market.lookup_symbol(q.get("q", ""))}


@route("POST", "/api/prices/refresh")
def api_refresh_prices(req, m, q, body):
    market.reset_network_state()
    return market.refresh_prices(force=True)


@route("POST", "/api/investments/sold")
def api_add_sold(req, m, q, body):
    # Pressing Save on the empty form used to write a row with no symbol and no figures,
    # which then sat in the Sold table for ever: every column read "—" and there was no
    # way to take it out again. Nothing gets in without the four things the maths needs.
    symbol = str(body.get("symbol") or "").strip().upper()
    if not symbol:
        raise ApiError("A sale needs a symbol.")
    sold_on = body.get("sold_on") or None
    if not sold_on:
        raise ApiError("A sale needs the date you sold it.")
    for field, label in (("cost", "what it cost you"), ("proceeds", "what you got for it")):
        if body.get(field) in (None, ""):
            raise ApiError(f"A sale needs {label}.")
    if body.get("bought_on") and body["bought_on"] > sold_on:
        raise ApiError("That says it was sold before it was bought.")

    with db.tx() as c:
        cur = c.execute("""INSERT INTO sold_positions(symbol,exchange,bought_on,buy_shares,cost,
                     sold_on,sell_shares,proceeds,dividends,currency,note,auto)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""",
                        (symbol, body.get("exchange"), body.get("bought_on"),
                         body.get("buy_shares"), body.get("cost"), sold_on,
                         body.get("sell_shares"), body.get("proceeds"),
                         body.get("dividends") or 0,
                         body.get("currency") or config.settings["base_currency"],
                         body.get("note")))
    return {"ok": True, "id": cur.lastrowid}


@route("DELETE", "/api/investments/sold/{sid}")
def api_delete_sold(req, m, q, body):
    """Take a recorded sale back out. Typing one in by hand is the only way to get one
    wrong, and until this existed a mistake was permanent."""
    sid = int(m["sid"])
    row = db.one("SELECT * FROM sold_positions WHERE id=?", (sid,))
    if not row:
        raise ApiError("That sale is no longer on file.")
    with db.tx() as c:
        c.execute("DELETE FROM sold_positions WHERE id=?", (sid,))
    # A sale found in an imported activity file comes back the next time that file is
    # imported. Saying so beats the person deleting it three times.
    return {"ok": True, "auto": bool(row["auto"])}


# ===========================================================================
# Banking
# ===========================================================================

@route("GET", "/api/transactions")
def api_transactions(req, m, q, body):
    where, params = ["1=1"], []
    if q.get("account_id"):
        where.append("t.account_id=?")
        params.append(int(q["account_id"]))
    if q.get("member_id"):
        where.append("a.member_id=?")
        params.append(int(q["member_id"]))
    if q.get("from"):
        where.append("t.posted_on >= ?")
        params.append(q["from"])
    if q.get("to"):
        where.append("t.posted_on <= ?")
        params.append(q["to"])
    if q.get("period"):
        s, e = budget_engine.month_bounds(q["period"])
        where.append("t.posted_on BETWEEN ? AND ?")
        params += [s, e]
    if q.get("category_id"):
        where.append("t.category_id=?")
        params.append(int(q["category_id"]))
    if q.get("section"):
        where.append("c.parent=?")
        params.append(q["section"])
    if q.get("uncategorised") == "1":
        where.append("c.name='Uncategorised'")
    if q.get("q"):
        where.append("(t.description LIKE ? OR t.merchant LIKE ?)")
        params += [f"%{q['q']}%", f"%{q['q']}%"]
    limit = min(int(q.get("limit") or 300), 2000)
    offset = int(q.get("offset") or 0)

    # A split payment is shown as its parts, never as both the parts and the whole —
    # the same rule the totals follow. `p` is the original, so a part can say what it
    # came from.
    sql = f"""SELECT t.*, a.name AS account, a.currency AS account_currency,
                     mm.name AS member, c.parent AS section, c.name AS category,
                     p.amount AS split_total, p.description AS split_description
              FROM transactions t JOIN accounts a ON a.id=t.account_id
              LEFT JOIN members mm ON mm.id=a.member_id
              LEFT JOIN categories c ON c.id=t.category_id
              LEFT JOIN transactions p ON p.id=t.split_of
              WHERE {' AND '.join(where)} AND {db.NOT_SPLIT_PARENT}
              ORDER BY t.posted_on DESC, t.id DESC LIMIT ? OFFSET ?"""
    rows = db.rows(sql, tuple(params) + (limit, offset))
    total = db.scalar(f"""SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id=t.account_id
                          LEFT JOIN categories c ON c.id=t.category_id
                          WHERE {' AND '.join(where)} AND {db.NOT_SPLIT_PARENT}""",
                      tuple(params), 0)
    base = config.settings["base_currency"]
    for r in rows:
        r["amount_base"] = market.convert(r["amount"] or 0, r["currency"] or base, base)
    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "currency": base}


@route("POST", "/api/transactions/{tid}/category")
def api_set_category(req, m, q, body):
    return categorise.set_category(int(m["tid"]), int(body["category_id"]),
                                   bool(body.get("make_rule")))


@route("POST", "/api/transactions")
def api_add_transaction(req, m, q, body):
    """One transaction typed in by hand — cash, a payment the statement hasn't reached
    yet, or an account Mittens & Pence can't read at all."""
    body = body or {}
    try:
        res = manual.add(
            account_id=body.get("account_id"),
            posted_on=body.get("posted_on"),
            description=body.get("description"),
            amount=body.get("amount"),
            direction=body.get("direction") or "out",
            category_id=(int(body["category_id"]) if body.get("category_id") else None),
            currency=body.get("currency"),
            reference=body.get("reference"),
            notes=body.get("notes"),
            transfer_to_account_id=(int(body["transfer_to_account_id"])
                                    if body.get("transfer_to_account_id") else None),
            transfer_amount=body.get("transfer_amount"),
        )
    except manual.ManualError as e:
        raise ApiError(str(e))
    rows = db.rows("""SELECT t.*, a.name AS account, c.parent AS section, c.name AS category
                      FROM transactions t JOIN accounts a ON a.id=t.account_id
                      LEFT JOIN categories c ON c.id=t.category_id
                      WHERE t.id IN (%s)""" % ",".join("?" * len(res["ids"])),
                   tuple(res["ids"]))
    return {"ok": True, "ids": res["ids"], "rows": rows, "transfer": res["transfer"]}


@route("PATCH", "/api/transactions/{tid}")
def api_edit_transaction(req, m, q, body):
    try:
        row = manual.update(int(m["tid"]), **(body or {}))
    except manual.ManualError as e:
        raise ApiError(str(e))
    return {"ok": True, "row": row}


@route("DELETE", "/api/transactions/{tid}")
def api_delete_transaction(req, m, q, body):
    try:
        return manual.delete(int(m["tid"]))
    except manual.ManualError as e:
        raise ApiError(str(e))


@route("GET", "/api/transactions/{tid}/split")
def api_get_split(req, m, q, body):
    out = split_engine.get(int(m["tid"]))
    if not out:
        raise ApiError("That transaction is no longer there.")
    if not out["is_split"]:
        out["suggested"] = split_engine.suggest(out["parent"]["id"])
    return out


@route("POST", "/api/transactions/{tid}/split")
def api_split(req, m, q, body):
    try:
        return split_engine.split(int(m["tid"]), (body or {}).get("parts") or [])
    except split_engine.SplitError as e:
        raise ApiError(str(e))


@route("DELETE", "/api/transactions/{tid}/split")
def api_unsplit(req, m, q, body):
    try:
        return split_engine.unsplit(int(m["tid"]))
    except split_engine.SplitError as e:
        raise ApiError(str(e))


@route("GET", "/api/transactions/guess-category")
def api_guess_category(req, m, q, body):
    """What the categoriser makes of a description as it is being typed."""
    cid = manual.guess_category(q.get("description") or "",
                               float(q.get("amount") or 0) or 0,
                               int(q["account_id"]) if q.get("account_id") else None)
    if not cid:
        return {"category_id": None}
    cat = db.one("SELECT id, parent, name FROM categories WHERE id=?", (cid,))
    return {"category_id": cid, "category": cat}


# ===========================================================================
# Mortgages
# ===========================================================================

_MORTGAGE_FIELDS = (
    "lender", "original_amount", "started_on", "term_months", "rate", "rate_type",
    "fixed_until", "revert_rate", "monthly_payment", "payment_day", "repayment_type",
    "statement_balance", "statement_on", "property_value", "property_valued_on",
    "property_account_id", "split_payments", "payment_category_id", "notes")

_NUMERIC = {"original_amount", "rate", "revert_rate", "monthly_payment",
            "statement_balance", "property_value"}
_INTEGER = {"term_months", "payment_day", "property_account_id", "split_payments",
            "payment_category_id"}


def _mortgage_values(body: dict) -> dict:
    out = {}
    for f in _MORTGAGE_FIELDS:
        if f not in body:
            continue
        v = body[f]
        if v in ("", None):
            out[f] = None
            continue
        if f in _NUMERIC:
            try:
                out[f] = float(str(v).replace(",", "").replace("%", "").strip())
            except ValueError:
                raise ApiError(f"“{v}” isn't a number — check the {f.replace('_', ' ')}.")
        elif f in _INTEGER:
            try:
                out[f] = int(float(v))
            except ValueError:
                raise ApiError(f"“{v}” isn't a whole number.")
        else:
            out[f] = str(v).strip() or None
    return out


@route("GET", "/api/mortgages")
def api_mortgages(req, m, q, body):
    items = [mortgage_engine.summarise(x) for x in mortgage_engine.all_mortgages()]
    return {"items": items, "currency": config.settings["base_currency"]}


@route("GET", "/api/mortgages/{aid}")
def api_mortgage(req, m, q, body):
    row = mortgage_engine.get(int(m["aid"]))
    if not row:
        raise ApiError("No mortgage details saved for that account yet.")
    acct = db.one("SELECT * FROM accounts WHERE id=?", (int(m["aid"]),))
    summary = mortgage_engine.summarise(dict(row, currency=acct["currency"]))
    months = min(summary["months_left"] or 0, 480) or 0
    schedule = []
    if summary["balance"] is not None and summary["monthly_payment"] and months:
        schedule = mortgage_engine.amortise(
            summary["balance"], row["rate"], summary["monthly_payment"], months,
            dt.date.today().replace(day=1),
            interest_only=(row["repayment_type"] == "interest_only"))
    return {
        "account": acct, "record": row, "summary": summary,
        "schedule": schedule[:480],
        # One point a year keeps the payload small; the last month is always included so
        # the curve actually reaches zero instead of stopping short at whatever the
        # sampling happened to land on.
        "curve": _yearly(schedule),
        "events": db.rows("SELECT * FROM mortgage_events WHERE mortgage_id=? "
                          "ORDER BY happened_on DESC, id DESC LIMIT 40", (row["id"],)),
    }


def _yearly(schedule: list[dict]) -> list[dict]:
    points = [r for i, r in enumerate(schedule) if i % 12 == 0][:41]
    if schedule and (not points or points[-1] is not schedule[-1]):
        points.append(schedule[-1])
    return points


@route("POST", "/api/mortgages/{aid}")
def api_save_mortgage(req, m, q, body):
    aid = int(m["aid"])
    acct = db.one("SELECT * FROM accounts WHERE id=?", (aid,))
    if not acct:
        raise ApiError("That account no longer exists.")
    vals = _mortgage_values(body or {})
    existing = mortgage_engine.get(aid)

    if not existing and "payment_category_id" not in vals:
        # Default the split to wherever the categoriser already puts mortgage payments,
        # so the sums are right without anybody having to find the setting.
        cat = db.one("SELECT id FROM categories WHERE lower(name) IN ('mortgage','bond') "
                     "ORDER BY id LIMIT 1")
        if cat:
            vals["payment_category_id"] = cat["id"]

    with db.tx() as c:
        if existing:
            if vals:
                c.execute(f"UPDATE mortgages SET {', '.join(f'{k}=?' for k in vals)} "
                          "WHERE account_id=?", tuple(vals.values()) + (aid,))
        else:
            cols = ["account_id"] + list(vals)
            c.execute(f"INSERT INTO mortgages({','.join(cols)}) "
                      f"VALUES({','.join('?' * len(cols))})", (aid,) + tuple(vals.values()))
        # A mortgage is a debt: the account balance mirrors what is owed, so net worth is
        # right whichever screen you look at.
        if vals.get("statement_balance") is not None:
            c.execute("UPDATE accounts SET balance=?, last_updated=datetime('now') WHERE id=?",
                      (-abs(vals["statement_balance"]), aid))
        if acct["account_type"] not in ("mortgage", "loan"):
            c.execute("UPDATE accounts SET account_type='mortgage' WHERE id=?", (aid,))

    row = mortgage_engine.get(aid)
    if vals.get("statement_balance") is not None and vals.get("statement_on"):
        _log_event(row["id"], vals["statement_on"], "statement",
                   vals["statement_balance"], None, "Balance from a statement")
    return {"ok": True, "summary": mortgage_engine.summarise(dict(row, currency=acct["currency"]))}


def _log_event(mortgage_id, on, kind, amount=None, rate=None, note=None):
    with db.tx() as c:
        dup = c.execute("SELECT id FROM mortgage_events WHERE mortgage_id=? AND happened_on=? "
                        "AND kind=? AND IFNULL(amount,-1)=IFNULL(?,-1)",
                        (mortgage_id, on, kind, amount)).fetchone()
        if dup:
            return dup["id"]
        cur = c.execute("INSERT INTO mortgage_events(mortgage_id,happened_on,kind,amount,"
                        "rate,note) VALUES(?,?,?,?,?,?)",
                        (mortgage_id, on, kind, amount, rate, note))
        return cur.lastrowid


@route("POST", "/api/mortgages/{aid}/events")
def api_mortgage_event(req, m, q, body):
    body = body or {}
    row = mortgage_engine.get(int(m["aid"]))
    if not row:
        raise ApiError("Save the mortgage details first.")
    kind = body.get("kind") or "overpayment"
    if kind not in ("statement", "overpayment", "rate_change", "valuation"):
        raise ApiError("Mittens & Pence doesn't know that kind of event.")
    on = str(body.get("happened_on") or dt.date.today().isoformat())[:10]
    amount = body.get("amount")
    amount = abs(float(amount)) if amount not in ("", None) else None
    rate = body.get("rate")
    rate = float(rate) if rate not in ("", None) else None
    _log_event(row["id"], on, kind, amount, rate, body.get("note"))

    # An event that changes what is true now updates the record too, or the figures on
    # screen would disagree with the history right beneath them.
    with db.tx() as c:
        if kind == "statement" and amount is not None:
            c.execute("UPDATE mortgages SET statement_balance=?, statement_on=? WHERE id=?",
                      (amount, on, row["id"]))
            c.execute("UPDATE accounts SET balance=?, last_updated=datetime('now') WHERE id=?",
                      (-abs(amount), int(m["aid"])))
        elif kind == "rate_change" and rate is not None:
            c.execute("UPDATE mortgages SET rate=? WHERE id=?", (rate, row["id"]))
        elif kind == "valuation" and amount is not None:
            c.execute("UPDATE mortgages SET property_value=?, property_valued_on=? WHERE id=?",
                      (amount, on, row["id"]))
    acct = db.one("SELECT * FROM accounts WHERE id=?", (int(m["aid"]),))
    return {"ok": True, "summary": mortgage_engine.summarise(
        dict(mortgage_engine.get(int(m["aid"])), currency=acct["currency"]))}


@route("DELETE", "/api/mortgages/{aid}")
def api_delete_mortgage(req, m, q, body):
    with db.tx() as c:
        c.execute("DELETE FROM mortgages WHERE account_id=?", (int(m["aid"]),))
    return {"ok": True}


@route("GET", "/api/mortgages/{aid}/overpayment")
def api_overpayment(req, m, q, body):
    row = mortgage_engine.get(int(m["aid"]))
    if not row:
        raise ApiError("Save the mortgage details first.")
    try:
        amount = float(q.get("amount") or 0)
    except ValueError:
        raise ApiError("That overpayment isn't a number.")
    return mortgage_engine.overpayment_effect(row, amount, q.get("kind") or "monthly")


@route("GET", "/api/mortgages/{aid}/rate-change")
def api_rate_change(req, m, q, body):
    row = mortgage_engine.get(int(m["aid"]))
    if not row:
        raise ApiError("Save the mortgage details first.")
    try:
        rate = float(q.get("rate") or row["revert_rate"] or 0)
    except ValueError:
        raise ApiError("That rate isn't a number.")
    return mortgage_engine.rate_change_effect(row, rate)


@route("GET", "/api/categories")
def api_categories(req, m, q, body):
    rows = db.rows("SELECT * FROM categories ORDER BY sort_order, parent, name")
    tree = {}
    for r in rows:
        tree.setdefault(r["parent"], []).append(r)
    return {"items": rows, "tree": tree}


@route("GET", "/api/sections")
def api_sections(req, m, q, body):
    return {"items": sections_engine.list_sections(),
            "special": sections_engine.special_sections()}


@route("POST", "/api/sections")
def api_add_section(req, m, q, body):
    try:
        return sections_engine.add_section((body or {}).get("name"),
                                           (body or {}).get("lines"))
    except ValueError as e:
        raise ApiError(str(e))


@route("PATCH", "/api/sections/{name}")
def api_rename_section(req, m, q, body):
    try:
        return sections_engine.rename_section(urllib.parse.unquote(m["name"]),
                                              (body or {}).get("name"))
    except ValueError as e:
        raise ApiError(str(e))


@route("DELETE", "/api/sections/{name}")
def api_delete_section(req, m, q, body):
    try:
        return sections_engine.delete_section(urllib.parse.unquote(m["name"]),
                                              q.get("move_to"))
    except ValueError as e:
        raise ApiError(str(e))


@route("POST", "/api/sections/{name}/lines")
def api_add_line(req, m, q, body):
    try:
        return sections_engine.add_line(urllib.parse.unquote(m["name"]),
                                        (body or {}).get("name"))
    except ValueError as e:
        raise ApiError(str(e))


@route("PATCH", "/api/categories/{cid}")
def api_rename_line(req, m, q, body):
    try:
        return sections_engine.rename_line(int(m["cid"]), (body or {}).get("name"))
    except ValueError as e:
        raise ApiError(str(e))


@route("DELETE", "/api/categories/{cid}")
def api_delete_line(req, m, q, body):
    try:
        return sections_engine.delete_line(
            int(m["cid"]), int(q["move_to"]) if q.get("move_to") else None)
    except ValueError as e:
        raise ApiError(str(e))


@route("POST", "/api/categories")
def api_add_category(req, m, q, body):
    with db.tx() as c:
        cur = c.execute("INSERT OR IGNORE INTO categories(parent,name,is_income,is_transfer,"
                        "is_saving,sort_order) VALUES(?,?,?,?,?,999)",
                        (body["parent"], body["name"], int(bool(body.get("is_income"))),
                         int(bool(body.get("is_transfer"))), int(bool(body.get("is_saving")))))
    return db.one("SELECT * FROM categories WHERE parent=? AND name=?",
                  (body["parent"], body["name"]))


@route("GET", "/api/rules")
def api_rules(req, m, q, body):
    return {"items": db.rows("""SELECT r.*, c.parent, c.name FROM rules r
                                JOIN categories c ON c.id=r.category_id
                                ORDER BY r.is_builtin, r.priority DESC, r.id DESC""")}


@route("POST", "/api/rules")
def api_add_rule(req, m, q, body):
    with db.tx() as c:
        c.execute("""INSERT INTO rules(match_type,pattern,field,category_id,priority,is_builtin)
                     VALUES(?,?,?,?,?,0)""",
                  (body.get("match_type") or "contains", body["pattern"],
                   body.get("field") or "description", int(body["category_id"]),
                   int(body.get("priority") or 200)))
    n = categorise.backfill(body["pattern"], int(body["category_id"])) \
        if body.get("match_type", "contains") == "contains" else categorise.categorise_all()
    return {"ok": True, "applied": n}


@route("DELETE", "/api/rules/{rid}")
def api_delete_rule(req, m, q, body):
    with db.tx() as c:
        c.execute("DELETE FROM rules WHERE id=?", (int(m["rid"]),))
    return {"ok": True}


@route("POST", "/api/recategorise")
def api_recategorise(req, m, q, body):
    n = categorise.categorise_all(only_uncategorised=not bool(body.get("all")))
    t = categorise.detect_internal_transfers()
    return {"categorised": n, "transfers_paired": t}


# ===========================================================================
# Budgets
# ===========================================================================

@route("GET", "/api/budgets")
def api_budgets(req, m, q, body):
    period = q.get("period") or budget_engine.current_period()
    member = int(q["member_id"]) if q.get("member_id") else None
    st = budget_engine.status(period, member)
    st["periods"] = [budget_engine.shift_period(budget_engine.current_period(), -i)
                     for i in range(0, 18)]
    st["history"] = budget_engine.history(months=12, member_id=member)
    st["suggestions"] = budget_engine.suggest_budgets(months=3, member_id=member)
    return st


@route("POST", "/api/budgets")
def api_set_budget(req, m, q, body):
    bid = budget_engine.set_budget(
        float(body["amount"]),
        category_id=int(body["category_id"]) if body.get("category_id") else None,
        parent=body.get("parent"), scope=body.get("scope") or "household",
        scope_id=body.get("scope_id"), rollover=bool(body.get("rollover")),
        notes=body.get("notes"))
    return {"ok": True, "id": bid}


@route("DELETE", "/api/budgets/{bid}")
def api_delete_budget(req, m, q, body):
    budget_engine.delete_budget(int(m["bid"]))
    return {"ok": True}


@route("POST", "/api/budgets/apply-suggestions")
def api_apply_suggestions(req, m, q, body):
    n = 0
    for s in budget_engine.suggest_budgets(months=int(body.get("months") or 3)):
        budget_engine.set_budget(s["suggested"], parent=s["parent"])
        n += 1
    return {"created": n}


@route("GET", "/api/spending")
def api_spending(req, m, q, body):
    period = q.get("period") or budget_engine.current_period()
    start, end = budget_engine.month_bounds(period)
    member = int(q["member_id"]) if q.get("member_id") else None
    out = budget_engine.spend_by_category(start, end, member_id=member)
    out["period"] = period
    out["history"] = budget_engine.history(months=12, member_id=member)
    return out


# ===========================================================================
# Snapshots, settings, exports
# ===========================================================================

@route("GET", "/api/snapshots")
def api_snapshots(req, m, q, body):
    scope = q.get("scope") or "household"
    return {"periods": snapshots.all_periods(),
            "series": snapshots.series(scope, q.get("metric") or "net_worth", 36)}


@route("POST", "/api/snapshots")
def api_take_snapshot(req, m, q, body):
    return snapshots.take((body or {}).get("period"), force=True)


@route("POST", "/api/snapshots/backfill")
def api_backfill(req, m, q, body):
    """Reconstruct the months before Mittens & Pence existed, from the imported history."""
    out = snapshots.backfill(months=int((body or {}).get("months") or 24), force=True)
    snapshots.take(force=True)
    return out


@route("GET", "/api/settings")
def api_get_settings(req, m, q, body):
    return {"settings": config.settings.as_dict(),
            "sectors": config.SECTORS,
            "currencies": config.BASE_CURRENCIES,
            "credential_store": security.storage_backend(),
            "data_dir": str(config.data_dir()),
            "exports_dir": str(config.exports_dir())}


@route("POST", "/api/settings")
def api_set_settings(req, m, q, body):
    incoming = dict(body or {})
    # The update address is the one setting that decides where a downloadable file
    # comes from, so it is the one setting that gets checked before it is stored.
    if "update_url" in incoming:
        url = str(incoming["update_url"] or "").strip()
        if url and not url.lower().startswith("https://"):
            raise ApiError("The update address has to start with https://")
        incoming["update_url"] = url
    config.settings.update(incoming)
    if {"update_url", "check_for_updates"} & set(incoming):
        updates.start_check(force=True)
    return {"settings": config.settings.as_dict()}


@route("POST", "/api/export")
def api_export(req, m, q, body):
    what = (body or {}).get("what") or "both"
    out = []
    if what in ("both", "investments"):
        out.append(str(investments_xlsx.build()))
    if what in ("both", "banking"):
        out.append(str(banking_xlsx.build(period=(body or {}).get("period"))))
    # Deliberately not part of "both": the phone copy is a different kind of thing and
    # somebody pressing "Build both" for spreadsheets shouldn't get an HTML file too.
    if what == "phone":
        out.append(str(phone_export.build()))
    return {"files": out, "folder": str(config.exports_dir()),
            "names": [pathlib.Path(f).name for f in out]}


@route("GET", "/api/gsheets/status")
def api_gsheets_status(req, m, q, body):
    return gsheets.status()


@route("POST", "/api/gsheets/client")
def api_gsheets_client(req, m, q, body):
    gsheets.save_client(body.get("client_id", ""), body.get("client_secret", ""))
    return gsheets.status()


@route("POST", "/api/gsheets/link")
def api_gsheets_link(req, m, q, body):
    port = req.server.server_address[1]
    return {"url": gsheets.begin_link(f"http://127.0.0.1:{port}/oauth/callback")}


@route("POST", "/api/gsheets/unlink")
def api_gsheets_unlink(req, m, q, body):
    gsheets.unlink()
    return gsheets.status()


@route("POST", "/api/gsheets/publish")
def api_gsheets_publish(req, m, q, body):
    what = (body or {}).get("what") or "both"
    inv = investments_xlsx.build() if what in ("both", "investments") else None
    bank = banking_xlsx.build() if what in ("both", "banking") else None
    return gsheets.publish_both(inv, bank)


@route("POST", "/api/gsheets/share")
def api_gsheets_share(req, m, q, body):
    return gsheets.share(body["file_id"], body["email"], body.get("role") or "reader")


@route("GET", "/api/exports")
def api_list_exports(req, m, q, body):
    """Everything the Export screen has made, whatever kind of file it is.

    This used to glob *.xlsx only, so the phone copy — an .html file — never appeared.
    Pressing "Make one" put a green box on the screen saying the file had been written
    and left the list underneath it reading "Nothing built yet".
    """
    out = []
    for pattern in ("*.xlsx", "*.html"):
        out += config.exports_dir().glob(pattern)
    files = sorted(out, key=lambda p: -p.stat().st_mtime)
    return {"items": [{"name": f.name, "size": f.stat().st_size,
                       "modified": int(f.stat().st_mtime),
                       "kind": "phone" if f.suffix == ".html" else "workbook"}
                      for f in files[:40]],
            "folder": str(config.exports_dir())}


@route("POST", "/api/open-url")
def api_open_url(req, m, q, body):
    """Open an external link in the system browser.

    The app may be running inside a native window where target="_blank" does nothing,
    so links are routed through here. Only http/https, so a crafted link can't be used
    to launch something local.
    """
    url = ((body or {}).get("url") or "").strip()
    # mailto is allowed so the Help button can open the user's own mail client. Still a
    # closed list of schemes — a crafted link must never be able to launch a local file.
    if not url.lower().startswith(("http://", "https://", "mailto:")):
        raise ApiError("Only web links and email addresses can be opened.")
    if any(c in url for c in "\r\n\x00"):
        raise ApiError("That link isn't valid.")
    import webbrowser
    try:
        webbrowser.open(url)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}


@route("POST", "/api/open-folder")
def api_open_folder(req, m, q, body):
    target = (body or {}).get("path") or str(config.exports_dir())
    try:
        if os.name == "nt":
            os.startfile(target)              # noqa: S606
        elif sys_is_mac():
            import subprocess
            subprocess.Popen(["open", target])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", target])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": target}


def sys_is_mac() -> bool:
    import sys
    return sys.platform == "darwin"


@route("POST", "/api/backup")
def api_backup(req, m, q, body):
    return {"path": str(db.backup())}


@route("POST", "/api/demo/load")
def api_demo_load(req, m, q, body):
    return demo.load()


@route("POST", "/api/demo/clear")
def api_demo_clear(req, m, q, body):
    # Where the backup went, so the app can say so rather than asking people to take the
    # word "a backup is taken first" on trust.
    path = db.backup()
    demo.clear()
    return {"ok": True, "backup": str(path)}


# ===========================================================================
# Updates
# ===========================================================================

@route("GET", "/api/update")
def api_update_state(req, m, q, body):
    if q.get("check") == "1":
        updates.start_check(force=True)
    return updates.state()


@route("POST", "/api/update/check")
def api_update_check(req, m, q, body):
    return updates.check(force=True)


@route("POST", "/api/update/download")
def api_update_download(req, m, q, body):
    st = updates.state()
    if st.get("status") == "downloading":
        return st
    if not st.get("download"):
        raise ApiError("There's nothing to download — check for an update first.")
    updates.start_download()
    return updates.state()


@route("POST", "/api/update/dismiss")
def api_update_dismiss(req, m, q, body):
    updates.dismiss((body or {}).get("version"))
    return updates.state()


@route("GET", "/api/health")
def api_health(req, m, q, body):
    return sync.health()


# ===========================================================================
# HTTP plumbing
# ===========================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = f"{config.APP_SLUG}/{config.APP_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("KESTREL_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, payload: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            # Belt and braces after the em-dash incident: anything that cannot go down
            # the wire as latin-1 is not worth failing the whole response over.
            self.send_header(k, str(v).encode("latin-1", "replace").decode("latin-1"))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, default=str).encode(), "application/json")

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype == "application/json" and raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise ApiError("That request wasn't valid JSON.")
        return raw

    # -- who is allowed to talk to us ------------------------------------
    #
    # This server listens on 127.0.0.1, which sounds like it means "only this app can
    # reach it". It doesn't. Every web page the household opens in Chrome is also
    # running on this machine, and a page can quietly POST to 127.0.0.1:8765 in the
    # background. Without these two checks, any site could wipe the database through
    # /api/demo/clear, and a site that points its own domain at 127.0.0.1 (DNS
    # rebinding) could read every transaction in the house.
    #
    # Two cheap checks close both doors:
    #   Host   must name the loopback address — a rebound domain fails this
    #   Origin must be our own, on anything that changes state
    LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return True                    # HTTP/1.0 and some local tooling send none
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return name.lower() in self.LOCAL_HOSTS

    def _origin_ok(self, method: str) -> bool:
        # Browsers label every request with where it came from, and only a browser
        # can be tricked into making this request on somebody else's behalf. A tool
        # driving the API directly — curl, a test, the desktop shell — sends neither
        # header, and that is the one case allowed through.
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site in ("cross-site", "same-site"):
            return False
        if method == "GET":
            return True
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        if origin.lower() == "null":
            # A sandboxed iframe reports its origin as "null". Treating that as
            # "no origin, must be a local tool" would hand any web page a way in.
            return False
        try:
            parsed = urllib.parse.urlparse(origin)
        except ValueError:
            return False
        return (parsed.hostname or "").lower() in self.LOCAL_HOSTS

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if not self._host_ok() or not self._origin_ok(method):
            return self._json({"error": "Mittens & Pence only answers this computer."}, 403)

        if not path.startswith("/api") and path != "/oauth/callback":
            return self._serve_static(path)

        for meth, rx, fn in ROUTES:
            if meth != method:
                continue
            mt = rx.match(path)
            if not mt:
                continue
            try:
                body = self._read_body() if method in ("POST", "PATCH", "PUT") else None
                out = fn(self, mt.groupdict(), q, body)
                if isinstance(out, dict) and "_html" in out:
                    return self._send(200, out["_html"].encode(), "text/html; charset=utf-8")
                if isinstance(out, dict) and "_file" in out:
                    # A route handing back a file to save rather than JSON.
                    #
                    # HTTP headers are latin-1. Our filename has an em dash in it, which
                    # is not, and putting it in raw produced a response the browser
                    # rejected outright as "header without colon" — a broken download
                    # with nothing in the log to say why. So: a plain ASCII name for
                    # every client, and RFC 5987 `filename*` alongside it for the real
                    # one, which is what modern browsers actually use.
                    name = out.get("_name", "download")
                    ascii_name = (unicodedata.normalize("NFKD", name)
                                  .encode("ascii", "ignore").decode() or "download")
                    ascii_name = re.sub(r'[\\"]', "", ascii_name)
                    quoted = urllib.parse.quote(name, safe="")
                    return self._send(
                        200, out["_file"], out.get("_type", "application/octet-stream"),
                        {"Content-Disposition":
                         f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'})
                return self._json(out)
            except ApiError as e:
                return self._json({"error": e.message}, e.status)
            except Exception as e:
                db.log("api.error", {"path": path, "error": str(e),
                                     "trace": traceback.format_exc()[-3000:]})
                if os.environ.get("KESTREL_VERBOSE"):
                    traceback.print_exc()
                return self._json({"error": str(e),
                                   "detail": "Something went wrong — the details are in "
                                             "the log file in your Mittens & Pence folder."}, 500)
        self._json({"error": f"No route for {method} {path}"}, 404)

    def _serve_static(self, path: str):
        if path.startswith("/exports/"):
            return self._serve_export(path[len("/exports/"):])
        if path in ("/", ""):
            path = "/index.html"
        target = (_static_dir() / path.lstrip("/")).resolve()
        root = _static_dir().resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            target = root / "index.html"
            if not target.is_file():
                return self._send(404, b"Mittens & Pence UI files are missing.", "text/plain")
        ctype, _ = mimetypes.guess_type(str(target))
        self._send(200, target.read_bytes(), ctype or "application/octet-stream")

    def _serve_export(self, name: str):
        """Let the app open something it just built — the phone copy, mostly.

        Only files directly inside the exports folder, and only by name: the resolved
        path is checked against the folder afterwards, so no amount of ../ in the URL
        reaches anything else on the disk.
        """
        root = config.exports_dir().resolve()
        try:
            target = (root / urllib.parse.unquote(name)).resolve()
        except (OSError, ValueError):
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        if target.parent != root or not target.is_file():
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        ctype, _ = mimetypes.guess_type(str(target))
        self._send(200, target.read_bytes(), ctype or "application/octet-stream",
                   {"Content-Disposition": "inline"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_HEAD(self):
        self._dispatch("GET")


def _free_port(preferred: int = PREFERRED_PORT) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 40)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int | None = None, block: bool = True):
    providers.load_all()
    db.init()
    try:
        sync.repair_connections()
    except Exception:
        pass
    port = port or _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    if block:
        httpd.serve_forever()
        return httpd, port
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port
