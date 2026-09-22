"""Work out which column is which, and remember the answer.

Two layers:
  * `suggest()` scores every column against a vocabulary of synonyms and returns a
    best-guess mapping plus a confidence per field. The UI shows this for a quick
    confirm; nothing is imported until the mapping is accepted.
  * Accepted mappings are stored against a hash of the header row, so the second
    month's file from the same bank imports with no questions at all.
"""

from __future__ import annotations

import json
import re

from .. import db
from .readers import Table, parse_amount, parse_date

# ---------------------------------------------------------------------------
# Field vocabularies
# ---------------------------------------------------------------------------

TRANSACTION_FIELDS = {
    "date": ["date", "transaction date", "posted", "posting date", "value date",
             "completed date", "started date", "date posted", "trans date",
             "transaction_date", "processed date", "effective date", "datum"],
    "description": ["description", "details", "narrative", "name", "counter party",
                    "counterparty", "payee", "merchant", "transaction description",
                    "memo", "particulars", "description1", "reference", "beskrywing",
                    "appears on your statement as", "transaction"],
    "amount": ["amount", "value", "transaction amount", "amount (gbp)", "amount(gbp)",
               "amount (zar)", "local amount", "money", "trnamt", "bedrag"],
    "debit": ["debit", "debit amount", "paid out", "money out", "withdrawal",
              "money out (r)", "debits", "out", "payments", "spent"],
    "credit": ["credit", "credit amount", "paid in", "money in", "deposit",
               "money in (r)", "credits", "in", "receipts", "received"],
    "balance": ["balance", "running balance", "balance (gbp)", "balance (r)",
                "closing balance", "account balance", "saldo"],
    "currency": ["currency", "ccy", "account currency", "local currency", "curdef"],
    "type": ["type", "transaction type", "trntype", "action", "method"],
    "category": ["category", "spending category", "subcategory", "classification"],
    "merchant": ["merchant", "payee", "counter party", "counterparty", "vendor"],
    "reference": ["reference", "transaction id", "id", "fitid", "cheque", "notes",
                  "order id", "trans id", "number"],
}

HOLDING_FIELDS = {
    "symbol": ["symbol", "ticker", "code", "instrument", "contract code", "epic",
               "stock", "share", "security"],
    "name": ["name", "stock name", "instrument name", "security name", "description",
             "company", "investment"],
    "isin": ["isin", "isin code", "sedol"],
    "exchange": ["exchange", "market", "venue", "listing"],
    "shares": ["shares", "quantity", "qty", "no. of shares", "units", "holding",
               "share count", "number of shares", "no of shares"],
    "cost": ["cost", "book cost", "total cost", "purchase value", "invested",
             "cost basis", "amount invested", "cost gbp", "consideration"],
    "price": ["price", "current price", "market price", "last price", "price per share",
              "avg purchase price", "average price", "unit price"],
    "value": ["value", "market value", "current value", "total value", "valuation",
              "value gbp", "holding value"],
    "currency": ["currency", "ccy", "instrument currency", "price currency"],
    "sector": ["sector", "asset class", "category", "classification"],
}

ACTIVITY_FIELDS = {
    "date": ["date", "time", "timestamp", "trade date", "settlement date", "paid on",
             "completed date", "transaction date"],
    "action": ["action", "type", "buy / sell", "buy/sell", "direction", "side",
               "transaction type", "event"],
    "symbol": ["ticker", "symbol", "code", "instrument", "contract code", "epic"],
    "name": ["name", "title", "stock name", "instrument name", "description", "security"],
    "isin": ["isin", "sedol"],
    "shares": ["quantity", "shares", "no. of shares", "units", "no of shares",
               "dividend eligible quantity"],
    "price": ["price / share", "price per share", "price", "price per share in account currency",
              "unit price", "gross/share", "dividend amount per share"],
    "price_currency": ["currency (price / share)", "instrument currency", "price currency"],
    "total": ["total", "total amount in account currency", "amount", "value",
              "consideration", "amount gbp", "net amount"],
    "currency": ["currency (total)", "account currency", "currency", "ccy"],
    "fees": ["charge amount", "fees", "commission", "stamp duty", "stamp duty reserve tax",
             "fx fee amount", "transaction fee"],
    "fx_rate": ["exchange rate", "fx rate", "base fx rate", "rate"],
    "tax": ["withholding tax", "dividend withheld tax amount", "tax"],
    "reference": ["id", "order id", "reference", "notes"],
}

KIND_FIELDS = {
    "transactions": TRANSACTION_FIELDS,
    "holdings": HOLDING_FIELDS,
    "activity": ACTIVITY_FIELDS,
}


def _norm(s: str) -> str:
    s = re.sub(r"\(.*?\)", lambda m: m.group(0), str(s or ""))
    return re.sub(r"[^a-z0-9 /]+", " ", str(s or "").lower()).strip()


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", _norm(s))


def score_column(header: str, synonyms: list[str]) -> float:
    h = _squash(header)
    if not h:
        return 0.0
    best = 0.0
    for syn in synonyms:
        s = _squash(syn)
        if h == s:
            best = max(best, 1.0)
        elif h.startswith(s) or s.startswith(h):
            best = max(best, 0.85)
        elif s in h:
            best = max(best, 0.7)
        elif h in s:
            best = max(best, 0.6)
        else:
            hw, sw = set(h.split()), set(s.split())
            if hw and sw:
                j = len(hw & sw) / len(hw | sw)
                if j >= 0.5:
                    best = max(best, 0.5 + j * 0.2)
    return best


def _value_hints(table: Table, col: int, sample: int = 40) -> dict:
    """Look at the data, not just the header — catches unlabelled columns."""
    vals = [r[col] for r in table.rows[:sample] if col < len(r)]
    vals = [v for v in vals if v is not None and str(v).strip() != ""]
    if not vals:
        return {"dateish": 0.0, "numeric": 0.0, "texty": 0.0, "n": 0}
    dateish = sum(1 for v in vals if parse_date(v)) / len(vals)
    numeric = sum(1 for v in vals if parse_amount(v) is not None) / len(vals)
    texty = sum(1 for v in vals if isinstance(v, str) and len(v) > 6
                and not re.fullmatch(r"[\d\s.,()£$R€-]+", v)) / len(vals)
    return {"dateish": dateish, "numeric": numeric, "texty": texty, "n": len(vals)}


def suggest(table: Table, kind: str = "transactions") -> dict:
    fields = KIND_FIELDS[kind]
    hints = {i: _value_hints(table, i) for i in range(len(table.header))}

    scores: dict[str, list[tuple[float, int]]] = {}
    for field, syns in fields.items():
        row = []
        for i, h in enumerate(table.header):
            s = score_column(h, syns)
            hv = hints[i]
            if field in ("date",):
                s = s * 0.6 + hv["dateish"] * 0.4
            elif field in ("amount", "debit", "credit", "balance", "shares", "cost",
                           "price", "value", "total", "fees", "fx_rate", "tax"):
                s = s * 0.75 + hv["numeric"] * 0.25
                if hv["numeric"] < 0.3:
                    s *= 0.3
            elif field in ("description", "name", "merchant"):
                s = s * 0.8 + hv["texty"] * 0.2
            if s > 0.35:
                row.append((s, i))
        row.sort(reverse=True)
        scores[field] = row

    # Greedy assignment: a column can only serve one field.
    mapping: dict[str, int] = {}
    confidence: dict[str, float] = {}
    used: set[int] = set()
    order = sorted(scores.keys(), key=lambda f: -(scores[f][0][0] if scores[f] else 0))
    for field in order:
        for s, i in scores[field]:
            if i in used:
                continue
            mapping[field] = i
            confidence[field] = round(s, 3)
            used.add(i)
            break

    # A file with separate debit/credit columns doesn't need `amount`.
    if kind == "transactions":
        if "debit" in mapping and "credit" in mapping and "amount" in mapping:
            if confidence.get("amount", 0) < max(confidence["debit"], confidence["credit"]):
                used.discard(mapping.pop("amount"))
                confidence.pop("amount", None)
        # Sanity: if amount column is always positive and a type column exists, warn later.

    required = {"transactions": ["date"], "holdings": ["symbol"], "activity": ["date"]}[kind]
    missing = [f for f in required if f not in mapping]
    if kind == "transactions" and "amount" not in mapping and not (
            "debit" in mapping and "credit" in mapping):
        missing.append("amount")
    if kind == "holdings" and "shares" not in mapping:
        missing.append("shares")

    return {
        "kind": kind,
        "columns": table.header,
        "mapping": mapping,
        "confidence": confidence,
        "missing": missing,
        "signature": table.signature(),
        "sample": [dict(zip(table.header, r)) for r in table.rows[:5]],
        "row_count": len(table.rows),
    }


# ---------------------------------------------------------------------------
# Remembered mappings
# ---------------------------------------------------------------------------

def remember(signature: str, kind: str, mapping: dict, institution_id: str | None = None,
             label: str | None = None):
    with db.tx() as c:
        c.execute(
            "INSERT INTO import_mappings(signature,institution_id,kind,mapping_json,label,uses)"
            " VALUES(?,?,?,?,?,1)"
            " ON CONFLICT(signature) DO UPDATE SET mapping_json=excluded.mapping_json,"
            " kind=excluded.kind, institution_id=COALESCE(excluded.institution_id, institution_id),"
            " label=COALESCE(excluded.label, label), uses=uses+1",
            (signature, institution_id, kind, json.dumps(mapping), label))


def recall(signature: str) -> dict | None:
    row = db.one("SELECT * FROM import_mappings WHERE signature=?", (signature,))
    if not row:
        return None
    return {"kind": row["kind"], "mapping": json.loads(row["mapping_json"]),
            "label": row["label"], "institution_id": row["institution_id"],
            "uses": row["uses"]}


def apply_mapping(table: Table, mapping: dict) -> list[dict]:
    """Project the raw rows through the mapping into field-named dicts."""
    out = []
    for r in table.rows:
        rec = {}
        for field, idx in mapping.items():
            if isinstance(idx, str):
                # a literal default rather than a column
                rec[field] = idx
                continue
            rec[field] = r[idx] if idx is not None and idx < len(r) else None
        rec["_raw"] = {table.header[i]: (r[i] if i < len(r) else None)
                       for i in range(len(table.header)) if table.header[i]}
        out.append(rec)
    return out
