"""Where each institution's own website is, so the setup screen can link to it.

**Every URL here was checked by fetching it and confirming the site says whose it is.**
That is not fussiness: sending somebody to a *nearly* right banking domain is how people
get phished, and a finance app that links to the wrong bank has done real harm. An
institution with no verified entry simply has no link — the written "where to find your
statements" instructions still tell them what to click once they are logged in.

So this list is short on purpose. Adding to it means fetching the site and confirming it
identifies itself as that institution, not recalling a domain that looks plausible.
"""

from __future__ import annotations

#: institution id -> official website. Verified September 2026.
WEBSITES: dict[str, str] = {
    # ---- United Kingdom: banks and building societies ---------------------------
    "gb-barclays": "https://www.barclays.co.uk",
    "gb-hsbc-uk": "https://www.hsbc.co.uk",
    "gb-first-direct": "https://www.firstdirect.com",
    "gb-lloyds-bank": "https://www.lloydsbank.com",
    "gb-halifax": "https://www.halifax.co.uk",
    "gb-bank-of-scotland": "https://www.bankofscotland.co.uk",
    "gb-natwest": "https://www.natwest.com",
    "gb-santander-uk": "https://www.santander.co.uk",
    "gb-nationwide-building-society": "https://www.nationwide.co.uk",
    "gb-tsb-bank": "https://www.tsb.co.uk",
    "gb-monzo": "https://monzo.com",
    "gb-starling-bank": "https://www.starlingbank.com",
    "gb-revolut": "https://www.revolut.com",
    "gb-wise": "https://wise.com",
    "gb-american-express-uk": "https://www.americanexpress.com/uk/",

    # ---- United Kingdom: investment platforms -----------------------------------
    "gb-freetrade": "https://www.freetrade.io",
    "gb-trading-212": "https://www.trading212.com",
    "gb-hargreaves-lansdown": "https://www.hl.co.uk",
    "gb-aj-bell": "https://www.ajbell.co.uk",
    "gb-interactive-investor": "https://www.ii.co.uk",
    "gb-vanguard-investor-uk": "https://www.vanguardinvestor.co.uk",

    # ---- South Africa ------------------------------------------------------------
    "za-absa-bank": "https://www.absa.co.za",
    "za-standard-bank": "https://www.standardbank.co.za",
    "za-first-national-bank": "https://www.fnb.co.za",
    "za-nedbank": "https://personal.nedbank.co.za/home.html",
    "za-capitec-bank": "https://www.capitecbank.co.za",
    "za-investec-bank-south-africa": "https://www.investec.com",
    "za-investec-online-securities": "https://www.investec.com",
    "gb-investec-bank-plc-uk": "https://www.investec.com",
    "za-discovery-bank": "https://www.discovery.co.za",
    "za-tymebank": "https://www.tymebank.co.za",
    "za-easyequities": "https://www.easyequities.co.za",
    "za-luno": "https://www.luno.com",
}


def website(institution_id: str) -> str | None:
    return WEBSITES.get(institution_id)
