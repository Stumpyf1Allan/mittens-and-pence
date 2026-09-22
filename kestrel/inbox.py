"""A folder you drop statements into, emptied by the Sync everything button.

The monthly ritual for an account that can't sync on its own is: log in, download last
month's statement, open the app, find Import, pick the file, check the account, commit.
Six steps, eleven accounts, once a month. This removes five of them — download the file
into one folder, and the next time you press **Sync everything** it goes in.

Two things this deliberately does NOT do.

**It does not guess.** A file is imported unattended only when *both* gates pass: the
layout is one the app has been taught, **and** the filename names an account
distinctively. Either one alone is not enough — four banks can share a column layout,
and a remembered layout says nothing about whose account a file belongs to. That is the
same pair of gates the Import screen uses, and it exists because skipping the second one
once put a Barclays PDF into a Capitec account. Anything that fails a gate stays exactly
where it is and gets reported, so a person can deal with it.

**It does not read receipts.** A photograph of a till receipt is not a statement, and
pretending to extract figures from one would put invented numbers into somebody's
budget. Images are filed by month and kept, so they are there when you want to look
something up — that is all, and the app says so rather than implying more.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil

from . import config, db

#: Files worth trying to read as a statement.
STATEMENT_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls",
                      ".ofx", ".qfx", ".qif", ".pdf"}
#: Kept, never parsed. See the module docstring.
RECEIPT_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tiff"}

#: Words in a filename that identify nothing, so they must not vote for an account.
#: "bank" used to match "Capitec Bank" and send everything there.
NOISE_WORDS = {
    "bank", "account", "accounts", "statement", "statements", "export", "exports",
    "download", "downloads", "transactions", "transaction", "history", "data", "file",
    "copy", "final", "new", "the", "and", "for", "csv", "pdf", "xls", "xlsx", "ofx",
    "qif", "txt", "current", "savings", "card", "credit", "debit", "report",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}

FOLDER_NAME = "Statements to import"
FILED = "Filed"
RECEIPTS = "Receipts"

README = """Drop your statements in here
============================

This folder is where {app} looks for anything new.

Each month, download the statement for every account your bank or broker can't
update on its own, and put the files in here. Any format they offer is fine:
CSV, Excel, OFX, QIF, or a PDF.

Then open {app} and press "Sync everything". Whatever is in here gets read and
added, and the files are moved into the "{filed}" folder so you can see at a
glance what has already gone in. Nothing is deleted, ever.

Two things worth knowing:

  * Leave the bank's own filename alone if you can. It usually contains the
    account name, which is how {app} works out where the transactions belong.
    If a file can't be matched to an account it is left here and the app tells
    you, so nothing is ever guessed into the wrong account.

  * Photos and scans of receipts can go in here too. They are sorted by month
    into the "{receipts}" folder and kept, so you can find one when you need it.
    They are NOT read — no figures are taken from a photograph.

Importing the same file twice is safe. Every transaction is fingerprinted, so a
row that is already in cannot go in a second time.
"""


# ---------------------------------------------------------------------------
# Where the folder is
# ---------------------------------------------------------------------------

def _documents() -> pathlib.Path | None:
    """The user's Documents folder, if there is an obvious one."""
    for candidate in (pathlib.Path.home() / "Documents", pathlib.Path.home() / "My Documents"):
        if candidate.is_dir():
            return candidate
    return None


def default_folder() -> pathlib.Path:
    """Documents if there is one, because a folder nobody can find is no use.

    The app's own data directory lives somewhere like AppData\\Local, which is exactly
    where a person will never think to drop a file.
    """
    base = _documents()
    if base:
        return base / config.APP_FILE_NAME / FOLDER_NAME
    return config.data_dir() / FOLDER_NAME


def folder(create: bool = True) -> pathlib.Path:
    chosen = str(config.settings.get("inbox_folder") or "").strip()
    p = pathlib.Path(chosen).expanduser() if chosen else default_folder()
    if create:
        try:
            p.mkdir(parents=True, exist_ok=True)
            readme = p / "Read me — what goes in here.txt"
            if not readme.exists():
                readme.write_text(
                    README.format(app=config.APP_NAME, filed=FILED, receipts=RECEIPTS),
                    encoding="utf-8")
        except OSError:
            logging.exception("could not create the statements folder")
    return p


def _state_path() -> pathlib.Path:
    return config.data_dir() / "inbox-seen.json"


def _seen() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _remember(digest: str, entry: dict):
    seen = _seen()
    seen[digest] = entry
    try:
        _state_path().write_text(json.dumps(seen, indent=2), encoding="utf-8")
    except OSError:
        logging.exception("could not record what has been imported")


def digest_of(path: pathlib.Path) -> str:
    """By content, not by name. Renaming a file is not a reason to import it again."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# What is waiting
# ---------------------------------------------------------------------------

def _is_ours(path: pathlib.Path, root: pathlib.Path) -> bool:
    if not path.is_file() or path.name.startswith(("~$", ".")):
        return False
    if path.name.lower().startswith("read me"):
        return False
    # Anything already filed is done with.
    return FILED not in path.relative_to(root).parts \
        and RECEIPTS not in path.relative_to(root).parts


def waiting() -> list[pathlib.Path]:
    root = folder()
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if _is_ours(p, root))


def _file_away(path: pathlib.Path, root: pathlib.Path, into: str, when: dt.date) -> pathlib.Path:
    dest_dir = root / into / (f"{when:%Y}" if into == FILED else f"{when:%Y-%m}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    n = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem} ({n}){path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))
    return dest


# ---------------------------------------------------------------------------
# Matching a file to an account
# ---------------------------------------------------------------------------

def guess_account(filename: str, accounts: list[dict]) -> tuple[dict | None, bool]:
    """(account, confident). Confident means the name said so and said it once.

    The filename only. A detected *profile* is a guess about the format, and using it
    here once profiled a Barclays PDF as "Standard Bank", whose words then voted for
    the wrong account.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", filename.lower())
             if len(w) > 2 and w not in NOISE_WORDS and not w.isdigit()]
    best, best_score, runner_up = None, 0, 0
    for a in accounts:
        hay = f"{a.get('name') or ''} {a.get('institution_name') or ''}".lower()
        score = sum(len(w) for w in words if w in hay)
        if score > best_score:
            best, best_score, runner_up = a, score, best_score
        elif score > runner_up:
            runner_up = score
    return best, bool(best and best_score >= 4 and best_score > runner_up)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def import_waiting() -> dict:
    """Read everything in the folder that can be read unattended. Never raises."""
    from .importers import ingest, mapping as mapping_mod, profiles as profiles_mod
    from .importers.readers import read_any
    from .engine import categorise

    root = folder()
    files = waiting()
    out = {"folder": str(root), "looked_at": len(files), "imported": [],
           "receipts": [], "skipped": [], "added": 0}
    if not files:
        return out

    accounts = db.rows("""SELECT a.id, a.name, a.connection_id, c.institution_name
                          FROM accounts a LEFT JOIN connections c ON c.id=a.connection_id
                          WHERE a.closed=0""")
    seen = _seen()
    today = dt.date.today()

    for path in files:
        try:
            digest = digest_of(path)
        except OSError as e:
            out["skipped"].append({"file": path.name, "why": f"could not be read ({e})"})
            continue

        if path.suffix.lower() in RECEIPT_SUFFIXES:
            when = dt.date.fromtimestamp(path.stat().st_mtime)
            dest = _file_away(path, root, RECEIPTS, when)
            _remember(digest, {"file": path.name, "kind": "receipt",
                               "at": today.isoformat(), "to": str(dest)})
            out["receipts"].append(path.name)
            continue

        if path.suffix.lower() not in STATEMENT_SUFFIXES:
            out["skipped"].append({"file": path.name,
                                   "why": "not a kind of file that can be read"})
            continue

        if digest in seen:
            # Already in. Move it out of the way so the folder empties, but do not
            # import it again.
            _file_away(path, root, FILED, today)
            continue

        if not accounts:
            out["skipped"].append({"file": path.name, "why": "there are no accounts yet"})
            continue

        try:
            table = read_any(path)
        except Exception as e:
            out["skipped"].append({"file": path.name, "why": _plainly(e)})
            continue
        if not table or not table.header:
            out["skipped"].append({"file": path.name, "why": "no readable header row"})
            continue

        # Gate one: a layout we have been taught.
        remembered = mapping_mod.recall(table.signature())
        detected = profiles_mod.detect(table.header)
        profile = profiles_mod.get(detected) if detected else None
        if remembered:
            mapping, kind, profile_key = remembered["mapping"], remembered["kind"], detected
        elif profile and profile.get("fields"):
            mapping = profiles_mod.resolve_columns(detected, table.header)
            kind, profile_key = profile.get("kind", "transactions"), detected
            # suggest() returns a whole report — kind, columns, confidence, a sample —
            # and the field-to-column map is one key inside it. Iterating the report
            # itself puts a list of column NAMES where a column index belongs.
            gaps = (mapping_mod.suggest(table, kind) or {}).get("mapping") or {}
            for field, col in gaps.items():
                mapping.setdefault(field, col)
        else:
            out["skipped"].append({
                "file": path.name,
                "why": "the layout isn't one it has seen before — import it once by "
                       "hand and it will recognise the next one"})
            continue

        if kind == "transactions" and not _has_amount(mapping):
            out["skipped"].append({"file": path.name,
                                   "why": "no amount column could be identified"})
            continue

        # Gate two: a filename that names its account, distinctively.
        account, sure = guess_account(path.name, accounts)
        if not sure:
            out["skipped"].append({
                "file": path.name,
                "why": ("the filename doesn't say which account it belongs to"
                        if not account else
                        f"the filename could mean more than one account "
                        f"(closest: {account['name']})")})
            continue

        try:
            db.backup()
            cid = account.get("connection_id")
            if kind == "activity":
                res = ingest.import_activity(account["id"], table, profile_key or "generic",
                                             path.name, cid, mapping)
            elif kind == "holdings":
                res = ingest.import_holdings(account["id"], table, mapping, profile_key,
                                             path.name, cid)
            else:
                res = ingest.import_transactions(account["id"], table, mapping, profile_key,
                                                 path.name, cid)
        except Exception as e:
            logging.exception("inbox import failed for %s", path.name)
            out["skipped"].append({"file": path.name, "why": _plainly(e)})
            continue

        added = int(res.get("added") or 0)
        out["added"] += added
        dest = _file_away(path, root, FILED, today)
        _remember(digest, {"file": path.name, "account": account["name"],
                           "added": added, "at": today.isoformat(), "to": str(dest)})
        out["imported"].append({"file": path.name, "account": account["name"],
                                "added": added, "skipped": int(res.get("skipped") or 0)})
        if cid:
            with db.tx() as c:
                c.execute("UPDATE connections SET last_sync=datetime('now'), status='ready' "
                          "WHERE id=?", (cid,))

    if out["imported"]:
        try:
            categorise.categorise_all(only_uncategorised=True)
            categorise.detect_internal_transfers()
        except Exception:
            logging.exception("categorising after an inbox import failed")
    return out


def _has_amount(mapping: dict) -> bool:
    """The names in mapping.TRANSACTION_FIELDS, not invented ones.

    A statement with Money In / Money Out columns resolves to `credit` and `debit`;
    without this check a profile that matched on Date + Description alone would import
    every row with no amount at all.
    """
    return any(mapping.get(k) is not None for k in ("amount", "debit", "credit"))


def _plainly(e: Exception) -> str:
    text = str(e).strip()
    return text[:200] if text else e.__class__.__name__


def status() -> dict:
    """What the Import screen and Settings show without doing any work."""
    root = folder(create=False)
    files = waiting() if root.is_dir() else []
    return {
        "folder": str(root),
        "exists": root.is_dir(),
        "waiting": [f.name for f in files][:40],
        "waiting_count": len(files),
        "filed": _count(root / FILED),
        "receipts": _count(root / RECEIPTS),
    }


def _count(p: pathlib.Path) -> int:
    try:
        return sum(1 for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0
