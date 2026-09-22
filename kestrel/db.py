"""SQLite storage for Mittens & Pence.

One file, no server, easy to back up (Settings → Back up now copies it with a
timestamp). Every table that holds imported rows carries a `fingerprint` so the
same statement can be uploaded twice without doubling anything up.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import sqlite3
import threading

from . import config

SCHEMA_VERSION = 4

_local = threading.local()


def _connect(path: pathlib.Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def conn() -> sqlite3.Connection:
    """Thread-local connection (uvicorn serves requests on a worker pool)."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect(config.db_path())
        _local.conn = c
    return c


@contextlib.contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Who the accounts belong to. A household has one or more members.
CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    colour      TEXT DEFAULT '#4f7cff',
    is_default  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- A link to one institution for one member. Type says how data arrives.
CREATE TABLE IF NOT EXISTS connections (
    id              INTEGER PRIMARY KEY,
    member_id       INTEGER REFERENCES members(id) ON DELETE CASCADE,
    institution_id  TEXT NOT NULL,
    institution_name TEXT NOT NULL,
    country         TEXT NOT NULL,
    method          TEXT NOT NULL,          -- openbanking | direct_api | aggregator | csv | manual
    provider        TEXT,                   -- truelayer, trading212, investec, ...
    label           TEXT,
    status          TEXT DEFAULT 'new',     -- new | needs_credentials | ready | linked | error | expired
    status_detail   TEXT,
    credentials_ref TEXT,                   -- key into the encrypted credential vault
    consent_expires TEXT,
    last_sync       TEXT,
    last_sync_result TEXT,
    settings_json   TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    connection_id   INTEGER REFERENCES connections(id) ON DELETE CASCADE,
    member_id       INTEGER REFERENCES members(id) ON DELETE SET NULL,
    external_id     TEXT,
    name            TEXT NOT NULL,
    account_type    TEXT NOT NULL,          -- current savings credit isa gia sipp tfsa ra pension crypto asset loan mortgage
    currency        TEXT NOT NULL DEFAULT 'GBP',
    number_masked   TEXT,
    sort_code       TEXT,
    -- NULL, not 0. An account whose statement carries no running balance knows nothing
    -- about its balance, and "£0.00" next to 686 transactions is a lie the UI can't
    -- distinguish from a genuinely empty account. NULL renders as "—".
    balance         REAL,
    available       REAL,
    is_investment   INTEGER DEFAULT 0,
    include_in_net_worth INTEGER DEFAULT 1,
    opened_on       TEXT,
    closed          INTEGER DEFAULT 0,
    last_updated    TEXT,
    notes           TEXT,
    UNIQUE(connection_id, external_id)
);

-- Spending categories, two levels (parent -> child).
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY,
    parent      TEXT NOT NULL,
    name        TEXT NOT NULL,
    is_income   INTEGER DEFAULT 0,
    is_transfer INTEGER DEFAULT 0,
    is_saving   INTEGER DEFAULT 0,
    colour      TEXT,
    sort_order  INTEGER DEFAULT 0,
    UNIQUE(parent, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    posted_on     TEXT NOT NULL,            -- YYYY-MM-DD
    booked_at     TEXT,
    description   TEXT NOT NULL,
    merchant      TEXT,
    amount        REAL NOT NULL,            -- negative = money out
    currency      TEXT NOT NULL,
    amount_base   REAL,                     -- converted to the household base currency
    balance_after REAL,
    category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    category_source TEXT DEFAULT 'auto',    -- auto | rule | manual
    is_transfer   INTEGER DEFAULT 0,
    transfer_pair INTEGER,
    reference     TEXT,
    raw_json      TEXT,
    fingerprint   TEXT NOT NULL,
    entry_source  TEXT NOT NULL DEFAULT 'import',   -- import | manual | split
    -- A payment split across categories: the parent keeps the bank's figure and is
    -- excluded from every total; the children carry the categories and the money.
    is_split      INTEGER NOT NULL DEFAULT 0,
    split_of      INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(account_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(posted_on);
CREATE INDEX IF NOT EXISTS ix_tx_acct ON transactions(account_id, posted_on);
CREATE INDEX IF NOT EXISTS ix_tx_cat ON transactions(category_id);

-- Categorisation rules. Highest priority wins.
CREATE TABLE IF NOT EXISTS rules (
    id           INTEGER PRIMARY KEY,
    match_type   TEXT NOT NULL DEFAULT 'contains',  -- contains | startswith | regex | exact | amount
    pattern      TEXT NOT NULL,
    field        TEXT NOT NULL DEFAULT 'description',
    category_id  INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    merchant     TEXT,
    account_id   INTEGER,
    priority     INTEGER DEFAULT 100,
    is_builtin   INTEGER DEFAULT 0,
    hits         INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY,
    scope        TEXT NOT NULL DEFAULT 'household',  -- household | member | account
    scope_id     INTEGER,
    category_id  INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    parent_only  TEXT,                       -- set instead of category_id for a whole section
    period       TEXT NOT NULL DEFAULT 'monthly',
    amount       REAL NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'GBP',
    rollover     INTEGER DEFAULT 0,
    starts_on    TEXT,
    ends_on      TEXT,
    notes        TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- investments

CREATE TABLE IF NOT EXISTS instruments (
    id           INTEGER PRIMARY KEY,
    symbol       TEXT NOT NULL,
    exchange     TEXT,
    name         TEXT,
    isin         TEXT,
    currency     TEXT,
    sector       TEXT,
    asset_class  TEXT DEFAULT 'equity',      -- equity etf fund bond crypto cash
    quote_symbol TEXT,                       -- what we ask the price source for
    manual_price REAL,
    UNIQUE(symbol, exchange)
);
CREATE INDEX IF NOT EXISTS ix_inst_isin ON instruments(isin);

CREATE TABLE IF NOT EXISTS holdings (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    instrument_id INTEGER REFERENCES instruments(id) ON DELETE CASCADE,
    shares        REAL NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0,   -- total cost in the account currency
    -- 0 when nobody has ever told us what this position cost. A crypto exchange
    -- reports what you hold and never what you paid, so cost sits at 0 — and a
    -- £5,000 holding with a £0 cost would otherwise be reported as £5,000 of pure
    -- profit. Unknown must look different from free.
    cost_known    INTEGER NOT NULL DEFAULT 1,
    cost_currency TEXT DEFAULT 'GBP',
    buy_dates     TEXT,
    source        TEXT DEFAULT 'import',     -- import | api | manual
    updated_at    TEXT,
    UNIQUE(account_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    instrument_id INTEGER REFERENCES instruments(id) ON DELETE CASCADE,
    traded_on     TEXT NOT NULL,
    side          TEXT NOT NULL,             -- BUY | SELL
    shares        REAL NOT NULL,
    price         REAL,
    price_currency TEXT,
    total         REAL NOT NULL,             -- in account currency, positive
    fees          REAL DEFAULT 0,
    fx_rate       REAL,
    reference     TEXT,
    fingerprint   TEXT NOT NULL,
    raw_json      TEXT,
    UNIQUE(account_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_trades_inst ON trades(instrument_id, traded_on);

CREATE TABLE IF NOT EXISTS dividends (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    instrument_id INTEGER REFERENCES instruments(id) ON DELETE CASCADE,
    paid_on       TEXT NOT NULL,
    amount        REAL NOT NULL,             -- net, in account currency
    gross         REAL,
    withheld      REAL,
    currency      TEXT,
    shares        REAL,
    per_share     REAL,
    fingerprint   TEXT NOT NULL,
    UNIQUE(account_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_div_inst ON dividends(instrument_id, paid_on);

CREATE TABLE IF NOT EXISTS cash_events (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    happened_on TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- DEPOSIT WITHDRAWAL INTEREST FEE FX TAX
    amount      REAL NOT NULL,
    currency    TEXT,
    note        TEXT,
    fingerprint TEXT NOT NULL,
    UNIQUE(account_id, fingerprint)
);

-- Closed positions: what the Sold tab holds.
CREATE TABLE IF NOT EXISTS sold_positions (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    instrument_id INTEGER REFERENCES instruments(id) ON DELETE SET NULL,
    symbol        TEXT NOT NULL,
    exchange      TEXT,
    bought_on     TEXT,
    buy_shares    REAL,
    buy_price     REAL,
    cost          REAL,
    sold_on       TEXT,
    sell_shares   REAL,
    sell_price    REAL,
    proceeds      REAL,
    dividends     REAL DEFAULT 0,
    currency      TEXT DEFAULT 'GBP',
    note          TEXT,
    auto          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prices (
    instrument_id INTEGER REFERENCES instruments(id) ON DELETE CASCADE,
    as_of         TEXT NOT NULL,
    price         REAL NOT NULL,
    currency      TEXT NOT NULL,
    change        REAL,
    change_pct    REAL,
    source        TEXT,
    PRIMARY KEY (instrument_id, as_of)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    pair    TEXT NOT NULL,                   -- e.g. USDGBP
    as_of   TEXT NOT NULL,
    rate    REAL NOT NULL,
    source  TEXT,
    PRIMARY KEY (pair, as_of)
);

-- One row per month per scope: the monthly tracking Allan asked for.
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY,
    taken_on    TEXT NOT NULL,               -- YYYY-MM-DD
    period      TEXT NOT NULL,               -- YYYY-MM
    scope       TEXT NOT NULL,               -- household | member:<id> | account:<id> | wrapper:<name>
    metrics_json TEXT NOT NULL,
    UNIQUE(period, scope)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id            INTEGER PRIMARY KEY,
    connection_id INTEGER REFERENCES connections(id) ON DELETE SET NULL,
    account_id    INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    filename      TEXT,
    profile       TEXT,
    kind          TEXT,                      -- transactions | holdings | activity | dividends
    rows_seen     INTEGER DEFAULT 0,
    rows_added    INTEGER DEFAULT 0,
    rows_skipped  INTEGER DEFAULT 0,
    mapping_json  TEXT,
    warnings_json TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- Remembered column mappings, so month two is a single click.
CREATE TABLE IF NOT EXISTS import_mappings (
    id            INTEGER PRIMARY KEY,
    signature     TEXT NOT NULL UNIQUE,      -- hash of the header row
    institution_id TEXT,
    kind          TEXT,
    mapping_json  TEXT NOT NULL,
    label         TEXT,
    uses          INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vault (
    ref         TEXT PRIMARY KEY,
    blob        BLOB NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY,
    at         TEXT DEFAULT (datetime('now')),
    kind       TEXT,
    detail     TEXT
);
"""


def fingerprint(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p if p is not None else "").strip().lower().encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


#: Columns added after a version shipped. SQLite's CREATE TABLE IF NOT EXISTS never
#: alters an existing table, so a new column in SCHEMA is invisible to anyone who
#: already has a database. Only additive changes belong here — a column with a
#: default is safe to add to a live table and safe to run repeatedly.
MORTGAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS mortgages (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    lender          TEXT,
    -- What was borrowed, and when. Everything else can be derived from these plus a rate.
    original_amount REAL,
    started_on      TEXT,                    -- YYYY-MM-DD
    term_months     INTEGER,
    rate            REAL,                    -- annual %, e.g. 4.29
    rate_type       TEXT DEFAULT 'fixed',    -- fixed | variable | tracker | discount
    -- The single most actionable date a mortgage holder has: the day the cheap rate ends.
    fixed_until     TEXT,
    revert_rate     REAL,                    -- the lender's standard rate afterwards
    monthly_payment REAL,
    payment_day     INTEGER,
    repayment_type  TEXT DEFAULT 'repayment',-- repayment | interest_only | part_and_part
    -- The last balance somebody actually saw, and when. A mortgage statement arrives once
    -- a year, so between statements the balance on screen is a projection, and Mittens & Pence
    -- says which is which rather than presenting a guess as fact.
    statement_balance REAL,
    statement_on    TEXT,
    property_value  REAL,                    -- optional; makes equity and LTV possible
    property_valued_on TEXT,
    property_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    -- Only the interest part of a payment is spending; the capital part moves the money
    -- from one pocket to another, exactly like paying into an ISA. Which category the
    -- payments land in has to be named, or the adjustment would have to guess.
    split_payments  INTEGER DEFAULT 1,
    payment_category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mortgage_events (
    id           INTEGER PRIMARY KEY,
    mortgage_id  INTEGER NOT NULL REFERENCES mortgages(id) ON DELETE CASCADE,
    happened_on  TEXT NOT NULL,
    kind         TEXT NOT NULL,              -- statement | overpayment | rate_change | valuation
    amount       REAL,
    rate         REAL,
    note         TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_mortgage_events ON mortgage_events(mortgage_id, happened_on);
"""


ADDED_COLUMNS = [
    ("holdings", "cost_known", "INTEGER NOT NULL DEFAULT 1"),
    # Who put this row here. 'import' came out of a file or an API and is identified by
    # its fingerprint; 'manual' was typed in by hand. The distinction matters twice: a
    # typed row may be edited and deleted (an imported one may not, or re-uploading the
    # statement stops being safe), and a typed row is *superseded* when the real
    # statement arrives carrying the same payment, rather than sitting beside it.
    ("transactions", "entry_source", "TEXT NOT NULL DEFAULT 'import'"),
    # Splitting one payment across categories. £100 at Tesco is £50 of groceries, £20 of
    # electronics and £30 of liquor, and the bank has no idea. The parent row is left
    # EXACTLY as the statement sent it — same amount, same fingerprint, so re-uploading
    # the file is still safe — and is marked `is_split`. The parts become child rows.
    #
    # The rule everywhere, without exception: **a split parent is never counted; its
    # children are.** Count both and every total silently doubles.
    ("transactions", "is_split", "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "split_of", "INTEGER"),
]

#: Reused by every query that sums or counts transactions. A bare string rather than a
#: clever query builder, because the failure mode of forgetting it is invisible.
NOT_SPLIT_PARENT = "IFNULL(t.is_split, 0) = 0"


def _apply_added_columns(c):
    for table, column, decl in ADDED_COLUMNS:
        try:
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        if not existing or column in existing:
            continue
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except Exception:
            pass          # never let a migration stop the app from opening
    c.commit()


def init(seed: bool = True):
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(MORTGAGE_SCHEMA)
    _apply_added_columns(c)
    cur = c.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        c.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    c.commit()
    if seed:
        seed_categories()
        seed_members()
        from .engine import categorise
        categorise.seed_builtin_rules()
        # Both of these are no-ops on a database that already has them, and neither
        # undoes anything the household has changed.
        _apply_added_categories(c)
        categorise.apply_rule_updates()
    return c


def meta_get(key: str, default=None):
    row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str):
    c = conn()
    c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    c.commit()


def meta_done(key: str) -> bool:
    """Has this one-off change already been applied to this database?"""
    return meta_get("did:" + key) is not None


def mark_done(key: str):
    meta_set("did:" + key, "1")


#: Categories added after the first release.
#:
#: `seed_categories()` deliberately does nothing once a database has any category in
#: it, because re-running it would resurrect a section the household deleted and undo
#: a rename. So a category added later needs its own one-off, recorded step — this
#: list — or it only ever reaches brand-new installs, and every existing copy silently
#: lacks it. Each entry is applied once and remembered, so deleting it makes it stay
#: deleted.
ADDED_CATEGORIES = [
    # Shopping had only specific children — Clothing, Electronics, Hobbies, Books,
    # Gifts — so a trip to a general shop (John Lewis, Wilko, Poundland, B&M), or
    # anything from a marketplace that sells everything, had nowhere to land. Amazon
    # was filed under Electronics for want of anywhere better, which is wrong more
    # often than it is right.
    ("Shopping", "Household & general"),
    # Dual-fuel energy bills had to be either Electricity or Gas, and the supplier's
    # name doesn't say which. See the note beside CATEGORIES in config.py.
    ("Utilities", "Energy"),
]


def _apply_added_categories(c):
    for parent, name in ADDED_CATEGORIES:
        key = f"category:{parent}/{name}"
        if meta_done(key):
            continue
        # Only add it to a section that is actually still there under that name.
        if c.execute("SELECT 1 FROM categories WHERE parent=? LIMIT 1", (parent,)).fetchone():
            order = c.execute("SELECT IFNULL(MAX(sort_order),0)+1 n FROM categories "
                              "WHERE parent=?", (parent,)).fetchone()["n"]
            c.execute("INSERT OR IGNORE INTO categories(parent,name,sort_order) VALUES(?,?,?)",
                      (parent, name, order))
        mark_done(key)
    c.commit()


def seed_members():
    c = conn()
    if c.execute("SELECT COUNT(*) n FROM members").fetchone()["n"] == 0:
        c.execute("INSERT INTO members(name,is_default,colour) VALUES(?,1,?)", ("Me", "#4f7cff"))
        c.commit()


def seed_categories():
    c = conn()
    if c.execute("SELECT COUNT(*) n FROM categories").fetchone()["n"] > 0:
        return
    order = 0
    for parent, children in config.SPEND_CATEGORIES.items():
        for name in children:
            order += 1
            c.execute(
                "INSERT OR IGNORE INTO categories(parent,name,is_income,is_transfer,is_saving,sort_order)"
                " VALUES(?,?,?,?,?,?)",
                (parent, name,
                 1 if parent == "Income" else 0,
                 1 if parent == "Transfers" else 0,
                 1 if parent == "Saving & Investing" else 0,
                 order),
            )
    c.commit()


def category_id(parent: str, name: str) -> int | None:
    row = conn().execute("SELECT id FROM categories WHERE parent=? AND name=?", (parent, name)).fetchone()
    return row["id"] if row else None


def uncategorised_id() -> int:
    cid = category_id("Other", "Uncategorised")
    if cid is None:
        with tx() as c:
            c.execute("INSERT INTO categories(parent,name) VALUES('Other','Uncategorised')")
        cid = category_id("Other", "Uncategorised")
    return cid


def log(kind: str, detail):
    try:
        with tx() as c:
            c.execute("INSERT INTO audit(kind,detail) VALUES(?,?)",
                      (kind, detail if isinstance(detail, str) else json.dumps(detail, default=str)))
    except Exception:
        pass


def backup() -> pathlib.Path:
    """Snapshot the database file. Cheap insurance before an import or a rebuild."""
    src = config.db_path()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = config.backups_dir() / f"mittens-{stamp}.db"
    conn().commit()
    with _connect(src) as c:
        with sqlite3.connect(str(dest)) as out:
            c.backup(out)
    # keep the 20 most recent
    # Older builds wrote backups under previous names; prune across all of them or the
    # folder grows for ever after a rename.
    files = sorted(sum((list(config.backups_dir().glob(p))
                        for p in ("mittens-*.db", "mithapp-*.db", "kestrel-*.db")), []))
    for old in files[:-20]:
        with contextlib.suppress(Exception):
            old.unlink()
    return dest


def restore(path: pathlib.Path):
    global _local
    with contextlib.suppress(Exception):
        conn().close()
    _local = threading.local()
    shutil.copy2(path, config.db_path())
    init(seed=False)


def rows(sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, params=()) -> dict | None:
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params=(), default=None):
    r = conn().execute(sql, params).fetchone()
    if r is None:
        return default
    v = r[0]
    return default if v is None else v
