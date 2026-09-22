"""Connections and syncing — the layer the UI talks to.

Creating a connection is the answer to "I picked my bank, now what?": the registry
says which routes exist, this module records the one you chose and tracks whether it
is ready, linked, or waiting for something from you.
"""

from __future__ import annotations

import datetime as dt
import json
import traceback

from .. import db, security
from ..institutions import registry
from ..providers import base as providers


def create_connection(institution_id: str, method: str, member_id: int | None = None,
                      provider: str | None = None, label: str | None = None,
                      settings: dict | None = None) -> dict:
    inst = registry.get(institution_id)
    if not inst:
        raise ValueError(f"unknown institution: {institution_id}")
    if member_id is None:
        row = db.one("SELECT id FROM members ORDER BY is_default DESC, id LIMIT 1")
        member_id = row["id"] if row else None

    status = "ready" if method in ("csv", "manual") else "needs_credentials"
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO connections(member_id,institution_id,institution_name,country,method,"
            "provider,label,status,settings_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (member_id, institution_id, inst["name"], inst["country"], method, provider,
             label or inst["name"], status, json.dumps(settings or {})))
        cid = cur.lastrowid
    return get_connection(cid)


def get_connection(connection_id: int) -> dict | None:
    row = db.one("""SELECT c.*, m.name AS member_name FROM connections c
                    LEFT JOIN members m ON m.id=c.member_id WHERE c.id=?""", (connection_id,))
    if not row:
        return None
    row["settings"] = json.loads(row.get("settings_json") or "{}")
    row["accounts"] = db.rows("SELECT * FROM accounts WHERE connection_id=? ORDER BY name",
                              (connection_id,))
    inst = registry.get(row["institution_id"]) or {}
    row["institution"] = inst
    row["kind"] = inst.get("kind")
    if row.get("credentials_ref"):
        row["credentials"] = security.redact(security.get(row["credentials_ref"]) or {})
    return row


def list_connections(member_id: int | None = None) -> list[dict]:
    where, params = "", ()
    if member_id:
        where, params = " WHERE c.member_id=?", (member_id,)
    rows = db.rows(f"""SELECT c.*, m.name AS member_name,
                        (SELECT COUNT(*) FROM accounts a WHERE a.connection_id=c.id) AS n_accounts
                       FROM connections c LEFT JOIN members m ON m.id=c.member_id
                       {where} ORDER BY c.created_at DESC""", params)
    for r in rows:
        r["settings"] = json.loads(r.get("settings_json") or "{}")
        inst = registry.get(r["institution_id"]) or {}
        r["kind"] = inst.get("kind")
        r["kind_label"] = inst.get("kind_label")
        if r.get("consent_expires"):
            try:
                days = (dt.date.fromisoformat(r["consent_expires"]) - dt.date.today()).days
                r["consent_days_left"] = days
                if days < 0:
                    r["status"] = "expired"
            except Exception:
                pass
    return rows


def delete_connection(connection_id: int, keep_data: bool = False):
    conn = get_connection(connection_id)
    if not conn:
        return
    if conn.get("credentials_ref"):
        security.drop(conn["credentials_ref"])
    with db.tx() as c:
        if keep_data:
            c.execute("UPDATE accounts SET connection_id=NULL WHERE connection_id=?",
                      (connection_id,))
        c.execute("DELETE FROM connections WHERE id=?", (connection_id,))


def set_credentials(connection_id: int, payload: dict) -> dict:
    conn = get_connection(connection_id)
    if not conn:
        raise ValueError("connection not found")
    ref = conn.get("credentials_ref") or f"conn:{connection_id}"
    existing = security.get(ref) or {}
    security.put(ref, {**existing, **{k: v for k, v in payload.items() if v not in (None, "")}})
    with db.tx() as c:
        c.execute("UPDATE connections SET credentials_ref=?, status='ready' WHERE id=?",
                  (ref, connection_id))
    return test_connection(connection_id)


def _provider_for(conn: dict):
    pid = conn.get("provider")
    if not pid:
        return None
    providers.load_all()          # importing the modules is what registers them
    cls = providers.get(pid)
    if not cls:
        return None
    return cls(conn)


def test_connection(connection_id: int) -> dict:
    conn = get_connection(connection_id)
    if not conn:
        raise ValueError("connection not found")
    if conn["method"] in ("csv", "manual"):
        return {"ok": True, "status": "ready",
                "message": "Ready — upload a statement whenever you like."}
    p = _provider_for(conn)
    if p is None:
        return {"ok": False, "status": "needs_credentials",
                "message": "Choose a provider for this connection first."}
    try:
        ok, message = p.validate()
    except Exception as e:
        ok, message = False, str(e)
    status = "linked" if ok else ("needs_credentials" if "needed" in message.lower()
                                  or "press link" in message.lower() else "error")
    with db.tx() as c:
        c.execute("UPDATE connections SET status=?, status_detail=? WHERE id=?",
                  (status, message, connection_id))
    return {"ok": ok, "status": status, "message": message}


def begin_link(connection_id: int, redirect_uri: str) -> dict:
    conn = get_connection(connection_id)
    p = _provider_for(conn)
    if p is None:
        raise ValueError("this connection has no linkable provider")
    return p.begin_link(redirect_uri)


def complete_link(connection_id: int, params: dict) -> dict:
    conn = get_connection(connection_id)
    p = _provider_for(conn)
    if p is None:
        raise ValueError("this connection has no linkable provider")
    return p.complete_link(params)


def sync_connection(connection_id: int) -> dict:
    conn = get_connection(connection_id)
    if not conn:
        raise ValueError("connection not found")
    if conn["method"] in ("csv", "manual"):
        return {"ok": True, "skipped": True,
                "message": "This one is file-based — use Import to bring in a statement."}
    p = _provider_for(conn)
    if p is None:
        return {"ok": False, "message": "No provider configured."}
    started = dt.datetime.now()
    try:
        result = p.sync().as_dict()
        ok, message = True, _describe(result)
        status = "linked"
    except Exception as e:
        result = {"error": str(e)}
        ok, message, status = False, str(e), "error"
        db.log("sync.error", {"connection": connection_id, "error": str(e),
                              "trace": traceback.format_exc()[-2000:]})
    with db.tx() as c:
        c.execute("UPDATE connections SET last_sync=?, last_sync_result=?, status=?, "
                  "status_detail=? WHERE id=?",
                  (started.replace(microsecond=0).isoformat(sep=" "),
                   json.dumps(result, default=str)[:4000], status, message, connection_id))
    return {"ok": ok, "message": message, "result": result,
            "seconds": round((dt.datetime.now() - started).total_seconds(), 1)}


def _describe(r: dict) -> str:
    bits = []
    for key, word in (("accounts", "account"), ("transactions", "transaction"),
                      ("holdings", "holding"), ("dividends", "dividend"),
                      ("cash_events", "cash entry")):
        n = r.get(key) or 0
        if n:
            bits.append(f"{n} {word}{'s' if n != 1 else ''}")
    return ("Synced " + ", ".join(bits)) if bits else "Nothing new"


def sync_all(only_ready: bool = True) -> dict:
    conns = db.rows("SELECT id, method, status FROM connections")
    results = []
    for c in conns:
        if c["method"] in ("csv", "manual"):
            continue
        if only_ready and c["status"] not in ("ready", "linked"):
            continue
        results.append({"connection_id": c["id"], **sync_connection(c["id"])})
    from ..market import prices as market
    market.refresh_prices()
    return {"synced": len(results), "results": results}


def repair_connections() -> dict:
    """Point any connection at a provider Mittens & Pence can actually drive.

    Early builds could save a provider that was listed in the catalogue but had no
    client behind it — the connection then looked automatic and did nothing. This runs
    at start-up: it re-points such connections at a working provider for the same
    institution, or, if there isn't one, converts them to statement upload and says so.
    """
    providers.load_all()
    moved, downgraded = [], []
    for row in db.rows("SELECT * FROM connections WHERE method NOT IN ('csv','manual')"):
        if row.get("provider") and providers.get(row["provider"]):
            continue
        step = registry.next_step(row["institution_id"])
        if "error" in step:
            continue
        mth = next((m for m in step["methods"] if m["method"] == row["method"]), None)
        ready = (mth or {}).get("ready_providers") or []
        if ready:
            with db.tx() as c:
                c.execute("UPDATE connections SET provider=?, status='needs_credentials', "
                          "status_detail=? WHERE id=?",
                          (ready[0], f"Now set up through {registry.provider(ready[0])['name']}.",
                           row["id"]))
            moved.append({"connection_id": row["id"], "institution": row["institution_name"],
                          "from": row.get("provider"), "to": ready[0]})
        else:
            has_csv = any(m["method"] == "csv" for m in step["methods"])
            with db.tx() as c:
                c.execute("UPDATE connections SET method=?, provider=NULL, status='ready', "
                          "status_detail=? WHERE id=?",
                          ("csv" if has_csv else "manual",
                           "Mittens & Pence has no automatic route for this one — upload a statement.",
                           row["id"]))
            downgraded.append({"connection_id": row["id"], "institution": row["institution_name"]})
    if moved or downgraded:
        db.log("connections.repaired", {"moved": moved, "downgraded": downgraded})
    return {"moved": moved, "downgraded": downgraded}


def health() -> dict:
    """A short list of what needs attention — the dashboard's "Needs a look" card.

    Five things end up here, and nothing else does:

      * a connection that is waiting for its API details, has failed, or whose
        consent has expired or is about to
      * a bank set to statement upload that has never had one uploaded
      * a *feed* account with no transactions, or nothing for 45 days — accounts kept
        by hand (a house, a pension) are excluded, because they have no transactions
        by design and nagging about them is noise
      * transactions still sitting in Uncategorised

    Every item carries where to go and what to open, so the card is a list of things
    to *do* rather than a list of things to know. An item nobody can act on has no
    business being on the front screen.
    """
    issues = []
    for c in list_connections():
        if c["status"] == "needs_credentials":
            issues.append({"level": "info", "connection_id": c["id"],
                           "action": {"go": "connections", "open": c["id"]},
                           "do": "Set it up",
                           "text": f"{c['institution_name']} is waiting for its API details."})
        elif c["status"] == "error":
            issues.append({"level": "warn", "connection_id": c["id"],
                           "action": {"go": "connections", "open": c["id"]},
                           "do": "Have a look",
                           "text": f"{c['institution_name']}: {c.get('status_detail') or 'sync failed'}"})
        elif c["status"] == "expired":
            issues.append({"level": "warn", "connection_id": c["id"],
                           "action": {"go": "connections", "open": c["id"]},
                           "do": "Link it again",
                           "text": f"{c['institution_name']} consent has expired — link it again."})
        elif c.get("consent_days_left") is not None and c["consent_days_left"] <= 10:
            issues.append({"level": "info", "connection_id": c["id"],
                           "action": {"go": "connections", "open": c["id"]},
                           "do": "Renew it",
                           "text": f"{c['institution_name']} consent expires in "
                                   f"{c['consent_days_left']} days."})
        elif c["method"] == "csv" and c["last_sync"] is None:
            n = db.scalar("SELECT COUNT(*) FROM import_batches WHERE connection_id=?",
                          (c["id"],), 0)
            if not n:
                issues.append({"level": "info", "connection_id": c["id"],
                               "action": {"go": "import", "connection": c["id"]},
                               "do": "Upload one",
                               "text": f"{c['institution_name']} has no statement uploaded yet."})

    # Accounts you keep by hand (a house, a pension statement) have no transactions
    # by design — nagging about those is noise, so only feed accounts are checked.
    stale = db.rows("""SELECT a.id, a.name, MAX(t.posted_on) AS last_tx FROM accounts a
                       LEFT JOIN transactions t ON t.account_id=a.id
                       LEFT JOIN connections c ON c.id=a.connection_id
                       WHERE a.closed=0 AND a.is_investment=0
                         AND a.account_type NOT IN ('asset','pension','loan','mortgage')
                         AND IFNULL(c.method,'manual') <> 'manual'
                       GROUP BY a.id HAVING last_tx IS NULL OR last_tx < date('now','-45 day')""")
    for s in stale:
        issues.append({"level": "info", "account_id": s["id"],
                       "action": {"go": "import"},
                       "do": "Upload a statement",
                       "text": (f"{s['name']} has no transactions yet — upload a statement."
                                if not s["last_tx"] else
                                f"{s['name']} has nothing since {s['last_tx']}.")})

    uncat = db.scalar("""SELECT COUNT(*) FROM transactions t JOIN categories c ON c.id=t.category_id
                         WHERE c.name='Uncategorised'""", (), 0)
    if uncat:
        issues.append({"level": "info",
                       "action": {"go": "transactions", "uncategorised": True},
                       "do": "Sort them out",
                       "text": (f"{uncat} transaction still needs a category." if uncat == 1
                                else f"{uncat} transactions still need a category.")})
    return {"issues": issues}
