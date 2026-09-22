"""Known statement layouts.

A profile pins field → column *name* (matched case- and space-insensitively), so it
keeps working when a bank reorders or renames columns slightly. Anything not listed
here still imports — the auto-mapper in mapping.py handles it and the app remembers
the answer.

`sign` tells the importer how to read the amount column:
  "signed"        one column, negative means money out (most banks)
  "debit_credit"  two columns
  "out_positive"  one column where a positive number means money out
"""

from __future__ import annotations

import re

PROFILES: dict[str, dict] = {

    # ------------------------------------------------------------------ UK banks
    "monzo": {
        "label": "Monzo",
        "kind": "transactions",
        "must": ["transaction id", "date", "amount"],
        "any": ["emoji", "category split", "money out"],
        "fields": {"date": "Date", "description": "Name", "amount": "Amount",
                   "currency": "Currency", "category": "Category", "type": "Type",
                   "reference": "Transaction ID", "merchant": "Name"},
        "sign": "signed",
        "notes": "Monzo also gives Local amount/Local currency for foreign spend.",
    },
    "starling": {
        "label": "Starling Bank",
        "kind": "transactions",
        "must": ["date", "counter party"],
        "fields": {"date": "Date", "description": "Counter Party", "reference": "Reference",
                   "type": "Type", "amount": "Amount (GBP)", "balance": "Balance (GBP)",
                   "category": "Spending Category"},
        "sign": "signed",
    },
    "barclays": {
        "label": "Barclays",
        "kind": "transactions",
        "must": ["date", "amount", "memo"],
        "fields": {"date": "Date", "amount": "Amount", "description": "Memo",
                   "category": "Subcategory", "reference": "Number"},
        "sign": "signed",
    },
    "lloyds": {
        "label": "Lloyds / Halifax / Bank of Scotland",
        "kind": "transactions",
        "must": ["transaction date", "transaction description"],
        "fields": {"date": "Transaction Date", "description": "Transaction Description",
                   "type": "Transaction Type", "debit": "Debit Amount",
                   "credit": "Credit Amount", "balance": "Balance"},
        "sign": "debit_credit",
    },
    "natwest": {
        "label": "NatWest / RBS / Ulster",
        "kind": "transactions",
        "must": ["date", "type", "description", "value"],
        "fields": {"date": "Date", "type": "Type", "description": "Description",
                   "amount": "Value", "balance": "Balance"},
        "sign": "signed",
    },
    "hsbc": {
        "label": "HSBC / first direct / M&S Bank",
        "kind": "transactions",
        "must": ["date", "description"],
        "any": ["amount", "paid out", "paid in"],
        "fields": {"date": "Date", "description": "Description", "amount": "Amount",
                   "balance": "Balance"},
        "sign": "signed",
    },
    "santander": {
        "label": "Santander UK",
        "kind": "transactions",
        "must": ["date", "description", "amount"],
        "any": ["balance"],
        "fields": {"date": "Date", "description": "Description", "amount": "Amount",
                   "balance": "Balance"},
        "sign": "signed",
    },
    "nationwide": {
        "label": "Nationwide Building Society",
        "kind": "transactions",
        "must": ["date", "transaction type", "description"],
        "fields": {"date": "Date", "type": "Transaction type", "description": "Description",
                   "debit": "Paid out", "credit": "Paid in", "balance": "Balance"},
        "sign": "debit_credit",
    },
    "revolut": {
        "label": "Revolut",
        "kind": "transactions",
        "must": ["type", "product", "started date", "amount"],
        "fields": {"date": "Completed Date", "description": "Description", "amount": "Amount",
                   "fees": "Fee", "currency": "Currency", "type": "Type",
                   "balance": "Balance"},
        "sign": "signed",
    },
    "amex": {
        "label": "American Express",
        "kind": "transactions",
        "must": ["date", "description", "amount"],
        "any": ["appears on your statement as", "extended details"],
        "fields": {"date": "Date", "description": "Description", "amount": "Amount",
                   "category": "Category", "reference": "Reference"},
        "sign": "out_positive",
        "notes": "Amex writes spending as a positive number; Mittens & Pence flips it.",
    },
    "wise": {
        "label": "Wise",
        "kind": "transactions",
        "must": ["id", "status", "direction"],
        "fields": {"date": "Created on", "description": "Target name", "amount": "Source amount (after fees)",
                   "currency": "Source currency", "reference": "ID"},
        "sign": "signed",
    },
    "paypal": {
        "label": "PayPal",
        "kind": "transactions",
        "must": ["date", "name", "gross"],
        "fields": {"date": "Date", "description": "Name", "amount": "Net",
                   "currency": "Currency", "type": "Type", "balance": "Balance",
                   "reference": "Transaction ID"},
        "sign": "signed",
    },

    # ------------------------------------------------------------ South African banks
    "capitec": {
        "label": "Capitec Bank",
        "kind": "transactions",
        "must": ["description"],
        "any": ["posting date", "transaction date", "money in", "money out"],
        "fields": {"date": "Transaction Date", "description": "Description",
                   "debit": "Money Out (R)", "credit": "Money In (R)",
                   "balance": "Balance (R)", "amount": "Amount"},
        "sign": "auto",
    },
    "fnb": {
        "label": "First National Bank",
        "kind": "transactions",
        "must": ["date", "amount"],
        "any": ["description1", "description2", "accrued charges"],
        "fields": {"date": "Date", "description": "Description1", "amount": "Amount",
                   "balance": "Balance", "reference": "Description2"},
        "sign": "signed",
        "notes": "FNB splits the narrative over Description1..3; Mittens & Pence joins them.",
        "join_description": ["Description1", "Description2", "Description3"],
    },
    "absa": {
        "label": "Absa Bank",
        "kind": "transactions",
        "must": ["description"],
        "any": ["transaction date", "amount", "balance"],
        "fields": {"date": "Transaction Date", "description": "Description",
                   "amount": "Amount", "balance": "Balance"},
        "sign": "auto",
    },
    "standard_bank": {
        "label": "Standard Bank",
        "kind": "transactions",
        "must": ["date", "description"],
        "any": ["amount", "balance"],
        "fields": {"date": "Date", "description": "Description", "amount": "Amount",
                   "balance": "Balance"},
        "sign": "auto",
    },
    "nedbank": {
        "label": "Nedbank",
        "kind": "transactions",
        "must": ["date", "description"],
        "any": ["amount", "balance", "reference"],
        "fields": {"date": "Date", "description": "Description", "amount": "Amount",
                   "balance": "Balance", "reference": "Reference"},
        "sign": "auto",
    },
    "investec": {
        "label": "Investec",
        "kind": "transactions",
        "must": ["date", "description"],
        "any": ["debit amount", "credit amount", "card number"],
        "fields": {"date": "Date", "description": "Description",
                   "debit": "Debit Amount", "credit": "Credit Amount", "balance": "Balance"},
        "sign": "debit_credit",
    },

    # ------------------------------------------------------------------- brokers
    "freetrade": {
        "label": "Freetrade — activity export",
        "kind": "activity",
        "must": ["title", "type", "timestamp", "total amount in account currency"],
        "handler": "freetrade",
        "notes": "The full activity feed: orders, dividends, top-ups, interest and splits.",
    },
    "trading212": {
        "label": "Trading 212 — export",
        "kind": "activity",
        "must": ["action", "time"],
        "any": ["no. of shares", "price / share", "ticker"],
        "handler": "trading212",
    },
    "easyequities": {
        "label": "EasyEquities",
        "kind": "activity",
        "must": ["contract code"],
        "any": ["debit", "credit", "comments", "action"],
        "handler": "easyequities",
    },
    "hargreaves_lansdown": {
        "label": "Hargreaves Lansdown",
        "kind": "holdings",
        "must": ["stock"],
        "any": ["units held", "value (£)", "price (pence)"],
        "fields": {"symbol": "Code", "name": "Stock", "shares": "Units held",
                   "price": "Price (pence)", "value": "Value (£)", "cost": "Cost (£)"},
        "price_divisor": 100.0,
    },
    "aj_bell": {
        "label": "AJ Bell",
        "kind": "holdings",
        "must": ["investment"],
        "any": ["quantity", "value", "book cost"],
        "fields": {"name": "Investment", "symbol": "EPIC", "shares": "Quantity",
                   "price": "Price", "value": "Value", "cost": "Book cost"},
    },
    "interactive_investor": {
        "label": "interactive investor",
        "kind": "holdings",
        "must": ["symbol"],
        "any": ["quantity", "book cost", "market value"],
        "fields": {"symbol": "Symbol", "name": "Name", "shares": "Quantity",
                   "price": "Last Price", "value": "Market Value", "cost": "Book Cost"},
    },
    "vanguard": {
        "label": "Vanguard Investor UK",
        "kind": "holdings",
        "must": ["fund"],
        "any": ["units", "value", "cost"],
        "fields": {"name": "Fund", "shares": "Units", "price": "Price",
                   "value": "Value", "cost": "Cost"},
    },
    "etoro": {
        "label": "eToro",
        "kind": "activity",
        "must": ["date"],
        "any": ["action", "units", "realized equity"],
        "fields": {"date": "Date", "action": "Action", "symbol": "Details",
                   "shares": "Units", "total": "Amount"},
    },
    "ibkr_flex": {
        "label": "Interactive Brokers — Flex statement",
        "kind": "activity",
        "must": ["symbol"],
        "any": ["tradedate", "quantity", "tradeprice"],
        "fields": {"date": "TradeDate", "symbol": "Symbol", "isin": "ISIN",
                   "shares": "Quantity", "price": "TradePrice",
                   "total": "Proceeds", "fees": "IBCommission", "currency": "CurrencyPrimary",
                   "action": "Buy/Sell"},
    },
    "generic": {
        "label": "Generic — Mittens & Pence will map the columns",
        "kind": "transactions",
        "must": [],
    },
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def detect(header: list[str]) -> str | None:
    """Pick the best-matching profile for this header row.

    Specificity wins. "Transaction Date" matching Lloyds' exact column name counts for
    more than it matching Santander's looser "date", which is what stops a Lloyds export
    being read with Santander's rules.
    """
    hs = {_norm(h) for h in header if h}
    joined = " | ".join(sorted(hs))
    best, best_score = None, 0.0
    for key, p in PROFILES.items():
        must = [_norm(m) for m in p.get("must", [])]
        if not must:
            continue
        score = 0.0
        ok = True
        for m in must:
            if m in hs:
                score += 3.0                      # the exact column name
            elif any(m in h for h in hs):
                score += 1.5                      # only a fragment of one
            else:
                ok = False
                break
        if not ok:
            continue
        for a in p.get("any", []):
            if _norm(a) in joined:
                score += 1.0
        for col in (p.get("fields") or {}).values():
            if _norm(col) in hs:
                score += 0.6
        if score > best_score:
            best, best_score = key, score
    return best


def resolve_columns(profile_key: str, header: list[str]) -> dict:
    """Turn a profile's name-based fields into index-based mapping for this file."""
    p = PROFILES.get(profile_key) or {}
    index = {}
    lookup = {_norm(h): i for i, h in enumerate(header)}
    for field, col in (p.get("fields") or {}).items():
        i = lookup.get(_norm(col))
        if i is not None:
            index[field] = i
    return index


def get(profile_key: str) -> dict:
    return PROFILES.get(profile_key) or PROFILES["generic"]
