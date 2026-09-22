#!/usr/bin/env python3
"""
Build the Mittens & Pence institution registry.

The registry is what powers the "pick your bank / broker" dropdown in the app and,
crucially, what the app shows you *next*: whether an institution can be linked and
synced automatically, or whether you need to download a statement and upload it.

Source of truth lives here as compact tables so it stays reviewable; the generated
JSON in kestrel/institutions/data/ is what ships.

Capability vocabulary
---------------------
openbanking   Regulated Open Banking / PSD2 Account Information. The app sends you to
              the bank's own login page; you approve read-only access; no password is
              ever typed into Mittens & Pence. UK/EU only. Consent expires (usually 90 days).
direct_api    The institution publishes its own API and issues you a personal key.
              You paste the key into Mittens & Pence once.
aggregator    A third-party data aggregator can reach it (South Africa mostly), but
              onboarding is business-grade, so this is "possible, not self-serve".
csv           You download a statement/activity file and upload it. Always available
              as a fallback, and the primary route for most investment platforms.
manual        Nothing automated exists; you type balances in (or maintain a sheet).
"""

import json
import pathlib
import re

# The python package is still named `kestrel` even though the product is Mittens & Pence. A
# rename pass once rewrote this path to "mithapp", and the generator cheerfully created
# that directory and wrote a perfectly good registry into it that nothing reads — the
# app kept serving the stale copy and every test passed. Hence the assertion below.
OUT = pathlib.Path(__file__).resolve().parent.parent / "kestrel" / "institutions" / "data"

# --------------------------------------------------------------------------------------
# Aggregator/provider catalogue
# --------------------------------------------------------------------------------------

PROVIDERS = {
    "truelayer": {
        "name": "TrueLayer",
        "regions": ["GB", "EU"],
        "kind": "openbanking",
        "self_serve": True,
        "free": False,
        "cost": "Free SANDBOX only. Connecting a real bank needs a paid plan.",
        "signup": "https://console.truelayer.com/",
        "notes": "Widest UK retail coverage, but the free console account is test mode: "
                 "the app shows a 'Testing mode active' banner and tells you not to enter "
                 "real bank credentials. Live access means becoming a paying client.",
        "warning": "Free accounts are sandbox only. If you see an orange 'Testing mode "
                   "active' banner, do NOT enter your real banking details — that flow "
                   "connects to mock banks, not yours.",
    },
    "plaid": {
        "name": "Plaid",
        "regions": ["GB", "EU", "US", "CA"],
        "kind": "openbanking",
        "self_serve": True,
        "free": False,
        "cost": "Free sandbox; limited free production trial (~10 live connections).",
        "signup": "https://dashboard.plaid.com/signup",
        "notes": "Production trial is usually enough for one household.",
    },
    "enablebanking": {
        "name": "Enable Banking",
        "regions": ["EU"],
        "kind": "openbanking",
        "self_serve": True,
        "free": True,
        "cost": "Free 'restricted production' for accounts you own and link yourself.",
        "signup": "https://enablebanking.com/",
        "warning": "Enable Banking covers the EEA only. Its application form has no "
                   "United Kingdom and no South Africa, so it cannot reach any bank "
                   "Mittens & Pence supports. Mittens & Pence no longer offers it for that reason.",
        "notes": "Genuinely free for a household — but EEA only. The application form "
                 "lists EEA countries and does NOT include the United Kingdom, so it "
                 "cannot reach UK banks. Useful if you hold an account in the EEA.",
    },
    "yapily": {
        "name": "Yapily",
        "regions": ["GB", "EU"],
        "kind": "openbanking",
        "self_serve": False,
        "free": False,
        "cost": "Commercial; sandbox is free.",
        "signup": "https://dashboard.yapily.com/sign-up",
        "notes": "Strong coverage, business onboarding required for live data.",
    },
    "tink": {
        "name": "Tink (Visa)",
        "regions": ["GB", "EU"],
        "kind": "openbanking",
        "self_serve": False,
        "free": False,
        "cost": "Commercial.",
        "signup": "https://console.tink.com/",
        "notes": "",
    },
    "saltedge": {
        "name": "Salt Edge",
        "regions": ["GB", "EU", "ZA"],
        "kind": "openbanking",
        "self_serve": True,
        "free": False,
        "cost": "Free test tier; paid live.",
        "signup": "https://www.saltedge.com/",
        "notes": "One of the few with any South African reach.",
    },
    "gocardless": {
        "name": "GoCardless Bank Account Data (ex-Nordigen)",
        "regions": ["GB", "EU"],
        "kind": "openbanking",
        "self_serve": False,
        "free": True,
        "cost": "Was free; new sign-ups are closed and the product is winding down.",
        "signup": "",
        "notes": "Kept for anyone with an existing key. Do not plan around it.",
        "deprecated": True,
    },
    "stitch": {
        "name": "Stitch",
        "regions": ["ZA"],
        "kind": "aggregator",
        "self_serve": False,
        "free": False,
        "cost": "Commercial, business onboarding.",
        "signup": "https://stitch.money/",
        "notes": "Best South African bank-data coverage, but not sold to individuals.",
    },
    "mono": {
        "name": "Mono",
        "regions": ["ZA", "NG", "GH", "KE"],
        "kind": "aggregator",
        "self_serve": False,
        "free": False,
        "cost": "Commercial, business onboarding.",
        "signup": "https://mono.co/",
        "notes": "",
    },
    "monzo": {
        "name": "Monzo Developer API",
        "regions": ["GB"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://developers.monzo.com/",
        "notes": "Monzo publishes an API meant for connecting to your OWN account. No "
                 "aggregator, no contract, no sandbox banner. The right way to connect Monzo.",
    },
    "starling": {
        "name": "Starling Personal Access",
        "regions": ["GB"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://developer.starlingbank.com/",
        "notes": "A personal access token for your own account with read-only scopes. "
                 "The simplest connection in the app — one value to paste, no expiry.",
    },
    "investec": {
        "name": "Investec Programmable Banking",
        "regions": ["ZA"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free to Investec Private Banking clients.",
        "signup": "https://developer.investec.com/",
        "notes": "Genuinely self-serve: enable in Investec Online, get client ID/secret/API key.",
    },
    "trading212": {
        "name": "Trading 212 API",
        "regions": ["GB", "EU"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "Trading 212 app → Settings → API (Beta) → Generate API key",
        "notes": "Read permissions only — Account, Portfolio, History, Metadata. Do NOT "
                 "tick 'Orders - Execute'. Newer keys come as a key AND a secret and use "
                 "HTTP Basic; older single-value keys still work. ISA and Invest accounts "
                 "only (no SIPP)."
    },
    "ibkr": {
        "name": "Interactive Brokers Flex Web Service",
        "regions": ["GB", "EU", "US", "ZA"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free to IBKR clients.",
        "signup": "IBKR Client Portal → Performance & Reports → Flex Queries",
        "notes": "Create a Flex Query, then a token. Mittens & Pence pulls the XML on a schedule.",
    },
    # --- crypto exchanges ---------------------------------------------------
    # All verified Sept 2026 against each exchange's own documentation: an ordinary
    # individual can mint a READ-ONLY key from account settings, free, no contract.
    # This is the same deal Monzo and Starling offer and it was missed at first pass.
    "kraken": {
        "name": "Kraken API key",
        "regions": ["GB", "ZA", "EU", "US"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://pro.kraken.com/app/settings/api",
        "notes": "Best read-only scoping of the lot: tick Query Funds only. Optional "
                 "expiry date and IP allowlist.",
    },
    "binance": {
        "name": "Binance API key",
        "regions": ["GB", "ZA", "EU"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://www.binance.com/en/my/settings/api-management",
        "notes": "Read-only is the default a new key gets. Needs 2FA and completed KYC "
                 "before a key can be created at all.",
    },
    "coinbase": {
        "name": "Coinbase API key",
        "regions": ["GB", "ZA", "EU", "US"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://portal.cdp.coinbase.com/api-keys",
        "notes": "Keys are signed certificates (ECDSA), so this one needs the "
                 "'cryptography' package. API key access is OFF by default on every "
                 "Coinbase account — switch it on first.",
    },
    "luno": {
        "name": "Luno API key",
        "regions": ["ZA", "GB"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://www.luno.com/wallet/settings/api_keys",
        "notes": "Offers a single 'Read-only access' preset — the safest key on the list.",
    },
    "valr": {
        "name": "VALR API key",
        "regions": ["ZA"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://www.valr.com/",
        "notes": "Tick View access only. Two-factor must be enabled or VALR refuses to "
                 "create a key.",
    },
    # Verified self-serve read-only keys, but no Mittens & Pence client written yet. Listed so
    # the setup screen can say "this exists, we just haven't built it" rather than
    # implying no API exists. `documented_only` in next_step() surfaces these.
    "gemini": {
        "name": "Gemini API key",
        "regions": ["GB", "EU", "US"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://exchange.gemini.com/settings/api",
        "notes": "Pick the Auditor role — it is read-only and cannot be combined with "
                 "any other role. Trader is the default, so you must change it.",
    },
    "bitstamp": {
        "name": "Bitstamp API key",
        "regions": ["GB", "EU"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://www.bitstamp.net/account/security/api/",
        "notes": "Per-action permission checkboxes; tick Account balance and User "
                 "transactions only. The key stays inert until you activate it from an "
                 "emailed confirmation link.",
    },
    "bitpanda": {
        "name": "Bitpanda API key",
        "regions": ["GB", "EU"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://web.bitpanda.com/",
        "notes": "Web app only — the key screen does not exist in the mobile app. The "
                 "classic broker key is read-only by design; newer keys have Read/Write "
                 "scopes, so pick read.",
    },
    "ig": {
        "name": "IG API key",
        "regions": ["GB", "ZA"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free.",
        "signup": "https://labs.ig.com/",
        "warning": "IG's API reaches spread betting and CFD accounts ONLY. Share dealing "
                   "and ISA accounts are not accessible — the API answers "
                   "'stockbroking-not-supported'. If your IG account is an ISA, this is "
                   "not a route, and the statement upload is.",
        "notes": "Self-serve and instant, but derivatives accounts only.",
    },
    "saxo": {
        "name": "Saxo OpenAPI",
        "regions": ["GB", "EU"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free (live market data still needs the usual subscriptions).",
        "signup": "https://www.developer.saxo/",
        "notes": "Genuinely self-serve for a direct client: free simulation account, then "
                 "a live app request that is usually auto-approved. Needs a funded live "
                 "account and prior simulation testing. Whether a UK Stocks & Shares ISA "
                 "appears as an account is not documented — unverified.",
    },
    "etoro": {
        "name": "eToro API key",
        "regions": ["GB", "EU"],
        "kind": "direct_api",
        "self_serve": True,
        "free": True,
        "cost": "Free for eligible users.",
        "signup": "https://api-portal.etoro.com/",
        "notes": "Settings → Trading → API Key Management. Needs a fully verified "
                 "account. Whether UK-entity accounts are eligible is not documented.",
    },
    "snaptrade": {
        "name": "SnapTrade",
        "regions": ["GB", "EU", "US", "CA"],
        "kind": "aggregator",
        "self_serve": True,
        "free": True,
        "cost": "Free tier for a small number of connections.",
        "signup": "https://snaptrade.com/",
        "notes": "Brokerage aggregator — reaches several UK brokers that have no API.",
    },
}

# Enable Banking is deliberately NOT here: its application form covers EEA countries
# only and does not offer the United Kingdom, so it cannot reach UK banks however
# appealing its free tier is. Verified against the live sign-up form, Aug 2026.
UK_OB = ["truelayer", "plaid", "yapily", "tink", "saltedge", "gocardless"]
UK_OB_LITE = ["truelayer", "yapily", "saltedge"]
ZA_AGG = ["stitch", "mono", "saltedge"]

# --------------------------------------------------------------------------------------
# Reusable CSV/statement-format profiles
# --------------------------------------------------------------------------------------
# 'profile' names map to importers/profiles.py. 'generic' means the auto-mapper works it
# out from the header row (and remembers your choice next time).

# --------------------------------------------------------------------------------------
# UNITED KINGDOM
# --------------------------------------------------------------------------------------
# (name, kind, group, sync, csv_where, csv_profile, aka)
#   sync: "ob" full open banking | "ob_lite" | "csv" | "manual" | "api:<provider>"
#   Multiple: "ob+api:trading212"

UK = [
    # ---- Big retail banking groups -------------------------------------------------
    ("Barclays", "bank", "Barclays", "ob",
     "Online Banking → Statements → Export → choose CSV (Excel) or QIF", "barclays", ["Barclays Bank"]),
    ("Barclaycard", "card", "Barclays", "ob",
     "Barclaycard online → Statements → Download → CSV", "generic", []),
    ("Lloyds Bank", "bank", "Lloyds Banking Group", "ob",
     "Internet Banking → Your accounts → Statements → Export (CSV)", "lloyds", ["Lloyds"]),
    ("Halifax", "bank", "Lloyds Banking Group", "ob",
     "Online Banking → Statements → Export (CSV)", "lloyds", []),
    ("Bank of Scotland", "bank", "Lloyds Banking Group", "ob",
     "Online Banking → Statements → Export (CSV)", "lloyds", ["BoS"]),
    ("MBNA", "card", "Lloyds Banking Group", "ob",
     "Online account → Statements → Download CSV", "generic", []),
    ("Birmingham Midshires", "bank", "Lloyds Banking Group", "csv",
     "Savings account online → Statement → Download", "generic", ["BM Savings"]),
    ("Scottish Widows Bank", "bank", "Lloyds Banking Group", "csv",
     "Online account → Statements", "generic", []),
    ("HSBC UK", "bank", "HSBC", "ob",
     "Online Banking → Statements → Download → CSV/OFX/QIF", "hsbc", ["HSBC"]),
    ("first direct", "bank", "HSBC", "ob",
     "Internet Banking → Statements → Download transactions (CSV)", "hsbc", ["firstdirect"]),
    ("M&S Bank", "bank", "HSBC", "ob",
     "Online Banking → Statements → Download", "hsbc", ["Marks and Spencer Bank"]),
    ("NatWest", "bank", "NatWest Group", "ob",
     "Online Banking → Statements → Download → CSV / OFX / QIF", "natwest", []),
    ("Royal Bank of Scotland", "bank", "NatWest Group", "ob",
     "Digital Banking → Statements → Download (CSV)", "natwest", ["RBS"]),
    ("Ulster Bank", "bank", "NatWest Group", "ob",
     "Anytime Banking → Statements → Download (CSV)", "natwest", []),
    ("Coutts", "bank", "NatWest Group", "ob_lite",
     "Coutts Online → Statements → Download", "generic", ["Coutts & Co"]),
    ("Mettle", "bank", "NatWest Group", "ob",
     "Mettle app → Statements → Export CSV", "generic", []),
    ("Santander UK", "bank", "Santander", "ob",
     "Online Banking → Statements → Download → CSV / Excel / QIF", "santander", ["Santander"]),
    ("Cahoot", "bank", "Santander", "csv", "Online account → Statements", "generic", []),
    ("Nationwide Building Society", "building_society", "Nationwide", "ob",
     "Internet Bank → Statements → Download (CSV)", "nationwide", ["Nationwide"]),
    ("Virgin Money", "bank", "Nationwide", "ob",
     "Online/app → Statements → Export CSV", "generic", ["Clydesdale Bank", "Yorkshire Bank"]),
    ("TSB Bank", "bank", "TSB", "ob",
     "Internet Banking → Statements → Download (CSV)", "generic", ["TSB"]),
    ("The Co-operative Bank", "bank", "Co-operative Bank", "ob",
     "Online Banking → Statements → Export (CSV)", "generic", ["Co-op Bank", "smile"]),
    ("Metro Bank", "bank", "Metro Bank", "ob",
     "Online Banking → Statements → Download (CSV)", "generic", []),
    ("Bank of Ireland UK", "bank", "Bank of Ireland", "ob",
     "Online Banking → Statements → Download", "generic", []),
    ("Danske Bank UK", "bank", "Danske", "ob",
     "eBanking → Statements → Export", "generic", []),
    ("Allied Irish Bank (GB)", "bank", "AIB", "ob",
     "Internet Banking → Statements → Export", "generic", ["AIB GB", "First Trust Bank"]),
    ("Handelsbanken UK", "bank", "Handelsbanken", "ob_lite",
     "Online banking → Statements → Download", "generic", []),

    # ---- Digital / challenger banks -------------------------------------------------
    ("Monzo", "bank", "Monzo", "api:monzo+ob",
     "Monzo app → Account → Export statement (CSV), or web.monzo.com", "monzo", ["Monzo Bank"]),
    ("Starling Bank", "bank", "Starling", "api:starling+ob",
     "Starling app → Account → Statements → Export CSV/OFX", "starling", ["Starling"]),
    ("Revolut", "ewallet", "Revolut", "ob",
     "Revolut app → Account → Statement → Excel/CSV", "revolut", ["Revolut Bank UK"]),
    ("Chase UK", "bank", "JPMorgan Chase", "ob",
     "Chase app → Account → Statements → Download", "generic", ["Chase"]),
    ("Atom Bank", "bank", "Atom", "ob_lite", "Atom app → Statements", "generic", []),
    ("Zopa Bank", "bank", "Zopa", "ob_lite", "Zopa app → Statements → Download", "generic", []),
    ("Tandem Bank", "bank", "Tandem", "ob_lite", "Tandem app → Statements", "generic", []),
    ("Kroo Bank", "bank", "Kroo", "ob", "Kroo app → Statements → Export", "generic", []),
    ("Monument Bank", "bank", "Monument", "csv", "Monument app → Statements", "generic", []),
    ("Monese", "ewallet", "Monese", "ob_lite", "Monese app → Statements → Export", "generic", []),
    ("Cashplus Bank", "bank", "Cashplus", "ob_lite", "Online → Statements → CSV", "generic", []),
    ("Pockit", "ewallet", "Pockit", "csv", "Pockit app → Statements", "generic", []),
    # Wise was pointed at SnapTrade, which is a *brokerage* aggregator and has never
    # reached a Wise balance. Wise does publish a personal API token, but Mittens & Pence has no
    # client for it — so the honest route is the per-currency statement export.
    ("Wise", "ewallet", "Wise", "csv",
     "Wise → Statements → Download CSV (per currency balance)", "wise", ["TransferWise"]),
    ("PayPal", "ewallet", "PayPal", "csv",
     "PayPal → Activity → Download → CSV (Balance affecting)", "paypal", []),
    ("Curve", "card", "Curve", "csv", "Curve app → Statements → Export", "generic", []),

    # ---- Savings / specialist / SME banks ------------------------------------------
    ("Marcus by Goldman Sachs", "bank", "Goldman Sachs", "ob_lite",
     "Online account → Statements → Download", "generic", ["Marcus"]),
    ("Aldermore Bank", "bank", "Aldermore", "csv", "Online savings → Statements", "generic", []),
    ("Shawbrook Bank", "bank", "Shawbrook", "csv", "Online savings → Statements", "generic", []),
    ("Paragon Bank", "bank", "Paragon", "csv", "Online savings → Statements", "generic", []),
    ("Charter Savings Bank", "bank", "OSB Group", "csv", "Online savings → Statements", "generic", []),
    ("Kent Reliance", "bank", "OSB Group", "csv", "Online savings → Statements", "generic", []),
    ("Close Brothers Savings", "bank", "Close Brothers", "csv", "Online savings → Statements", "generic", []),
    ("Investec Bank plc (UK)", "bank", "Investec", "csv",
     "Investec Online → Statements → Download", "generic", ["Investec UK"]),
    ("Cynergy Bank", "bank", "Cynergy", "ob_lite", "Online banking → Statements", "generic", []),
    ("OakNorth Bank", "bank", "OakNorth", "csv", "Online savings → Statements", "generic", []),
    ("Allica Bank", "bank", "Allica", "ob_lite", "Online banking → Statements", "generic", []),
    ("Redwood Bank", "bank", "Redwood", "csv", "Online banking → Statements", "generic", []),
    ("Recognise Bank", "bank", "Recognise", "csv", "Online savings → Statements", "generic", []),
    ("GB Bank", "bank", "GB Bank", "csv", "Online savings → Statements", "generic", []),
    ("Hodge Bank", "bank", "Hodge", "csv", "Online savings → Statements", "generic", []),
    ("United Trust Bank", "bank", "UTB", "csv", "Online savings → Statements", "generic", []),
    ("Secure Trust Bank", "bank", "Secure Trust", "csv", "Online savings → Statements", "generic", []),
    ("Gatehouse Bank", "bank", "Gatehouse", "csv", "Online savings → Statements", "generic", []),
    ("Al Rayan Bank", "bank", "Al Rayan", "csv", "Online banking → Statements", "generic", []),
    ("Triodos Bank UK", "bank", "Triodos", "ob_lite", "Internet Banking → Statements → CSV", "generic", []),
    ("Unity Trust Bank", "bank", "Unity Trust", "csv", "Online banking → Statements", "generic", []),
    ("Reliance Bank", "bank", "Reliance", "csv", "Online banking → Statements", "generic", []),
    ("Arbuthnot Latham", "bank", "Arbuthnot", "csv", "Online banking → Statements", "generic", []),
    ("C. Hoare & Co", "bank", "Hoare", "csv", "Online banking → Statements", "generic", []),
    ("Hampden & Co", "bank", "Hampden", "csv", "Online banking → Statements", "generic", []),
    ("Weatherbys Bank", "bank", "Weatherbys", "csv", "Online banking → Statements", "generic", []),
    ("Chetwood Bank (SmartSave)", "bank", "Chetwood", "csv", "Online savings → Statements", "generic", ["SmartSave"]),
    ("Ford Money", "bank", "Ford", "csv", "Online savings → Statements", "generic", []),
    ("Tesco Bank", "bank", "Barclays", "ob_lite", "Online banking → Statements → Download", "generic", []),
    ("Sainsbury's Bank", "bank", "NatWest Group", "ob_lite", "Online banking → Statements", "generic", []),
    ("Post Office Money", "bank", "Bank of Ireland", "csv", "Online savings → Statements", "generic", []),
    ("The AA Savings", "bank", "Bank of Ireland", "csv", "Online savings → Statements", "generic", []),
    ("Vanquis Bank", "card", "Vanquis", "ob_lite", "Online account → Statements", "generic", []),
    ("Capital One UK", "card", "Capital One", "ob_lite", "Online account → Statements → Download", "generic", []),
    ("NewDay (Aqua / Marbles / Fluid)", "card", "NewDay", "ob_lite",
     "Online account → Statements → Download", "generic", ["Aqua", "Marbles", "Fluid", "Amazon Classic"]),
    ("American Express UK", "card", "American Express", "ob",
     "Amex online → Statements & Activity → Download → CSV/QIF/OFX", "amex", ["Amex"]),
    ("Tide", "ewallet", "Tide", "ob_lite", "Tide app → Statements → CSV", "generic", []),
    ("ANNA Money", "ewallet", "ANNA", "ob_lite", "ANNA app → Statements", "generic", []),
    ("Countingup", "ewallet", "Countingup", "ob_lite", "App → Statements", "generic", []),
    ("Suits Me", "ewallet", "Suits Me", "csv", "Online → Statements", "generic", []),
    ("Ziglu (closed 2025)", "crypto", "Ziglu", "manual", "", "generic", ["Ziglu"]),

    # ---- Building societies (BSA members) ------------------------------------------
    ("Coventry Building Society", "building_society", "Coventry BS", "ob_lite",
     "Online service → Statements → Download", "generic", []),
    ("Yorkshire Building Society", "building_society", "YBS", "ob_lite",
     "Online → Statements → Download", "generic", ["Chelsea Building Society", "Norwich & Peterborough"]),
    ("Skipton Building Society", "building_society", "Skipton BS", "ob_lite",
     "Online → Statements → Download", "generic", []),
    ("Leeds Building Society", "building_society", "Leeds BS", "csv", "Online → Statements", "generic", []),
    ("Principality Building Society", "building_society", "Principality BS", "csv", "Online → Statements", "generic", []),
    ("West Bromwich Building Society", "building_society", "West Brom BS", "csv", "Online → Statements", "generic", []),
    ("Newcastle Building Society", "building_society", "Newcastle BS", "csv", "Online → Statements", "generic", []),
    ("Nottingham Building Society", "building_society", "Nottingham BS", "csv", "Online → Statements", "generic", []),
    ("Cumberland Building Society", "building_society", "Cumberland BS", "ob_lite", "Online → Statements", "generic", []),
    ("Progressive Building Society", "building_society", "Progressive BS", "csv", "Online → Statements", "generic", []),
    ("Cambridge Building Society", "building_society", "Cambridge BS", "csv", "Online → Statements", "generic", []),
    ("Furness Building Society", "building_society", "Furness BS", "csv", "Online → Statements", "generic", []),
    ("Monmouthshire Building Society", "building_society", "Monmouthshire BS", "csv", "Online → Statements", "generic", []),
    ("Family Building Society", "building_society", "Family BS", "csv", "Online → Statements", "generic", ["National Counties"]),
    ("Hinckley & Rugby Building Society", "building_society", "Hinckley & Rugby BS", "csv", "Online → Statements", "generic", []),
    ("Darlington Building Society", "building_society", "Darlington BS", "csv", "Online → Statements", "generic", []),
    ("Melton Building Society", "building_society", "Melton BS", "csv", "Online → Statements", "generic", []),
    ("Market Harborough Building Society", "building_society", "Market Harborough BS", "csv", "Online → Statements", "generic", []),
    ("Saffron Building Society", "building_society", "Saffron BS", "csv", "Online → Statements", "generic", []),
    ("Scottish Building Society", "building_society", "Scottish BS", "csv", "Online → Statements", "generic", []),
    ("Tipton & Coseley Building Society", "building_society", "Tipton BS", "csv", "Online → Statements", "generic", []),
    ("Dudley Building Society", "building_society", "Dudley BS", "csv", "Online → Statements", "generic", []),
    ("Loughborough Building Society", "building_society", "Loughborough BS", "csv", "Online → Statements", "generic", []),
    ("Mansfield Building Society", "building_society", "Mansfield BS", "csv", "Online → Statements", "generic", []),
    ("Marsden Building Society", "building_society", "Marsden BS", "csv", "Online → Statements", "generic", []),
    ("Newbury Building Society", "building_society", "Newbury BS", "csv", "Online → Statements", "generic", []),
    ("Penrith Building Society", "building_society", "Penrith BS", "manual", "", "generic", []),
    ("Stafford Railway Building Society", "building_society", "Stafford Railway BS", "manual", "", "generic", []),
    ("Swansea Building Society", "building_society", "Swansea BS", "csv", "Online → Statements", "generic", []),
    ("Teachers Building Society", "building_society", "Teachers BS", "csv", "Online → Statements", "generic", []),
    ("Vernon Building Society", "building_society", "Vernon BS", "manual", "", "generic", []),
    ("Beverley Building Society", "building_society", "Beverley BS", "manual", "", "generic", []),
    ("Buckinghamshire Building Society", "building_society", "Buckinghamshire BS", "manual", "", "generic", []),
    ("Chorley Building Society", "building_society", "Chorley BS", "csv", "Online → Statements", "generic", []),
    ("Earl Shilton Building Society", "building_society", "Earl Shilton BS", "manual", "", "generic", []),
    ("Ecology Building Society", "building_society", "Ecology BS", "csv", "Online → Statements", "generic", []),
    ("Harpenden Building Society", "building_society", "Harpenden BS", "manual", "", "generic", []),
    ("Hanley Economic Building Society", "building_society", "Hanley Economic BS", "manual", "", "generic", []),
    ("Leek Building Society", "building_society", "Leek BS", "csv", "Online → Statements", "generic", []),
    ("Bath Building Society", "building_society", "Bath BS", "csv", "Online → Statements", "generic", []),
    ("Manchester Building Society", "building_society", "Manchester BS", "manual", "", "generic", []),
    ("Suffolk Building Society", "building_society", "Suffolk BS", "csv", "Online → Statements", "generic", []),

    # ---- Investment platforms, brokers, ISAs, SIPPs ---------------------------------
    ("Trading 212", "broker", "Trading 212", "api:trading212",
     "App → Settings → History → Export → CSV (or use the API key, which is better)",
     "trading212", ["T212"]),
    ("Freetrade", "broker", "Freetrade", "csv",
     "App → Profile → Activity feed → Export → email yourself the CSV (or freetrade.io web)",
     "freetrade", ["FT"]),
    ("Hargreaves Lansdown", "broker", "Hargreaves Lansdown", "csv",
     "Account → Investments → Download (CSV) and Account → History → Download",
     "hargreaves_lansdown", ["HL"]),
    ("AJ Bell", "broker", "AJ Bell", "csv",
     "Account → Portfolio → Export, and Account → Transaction history → Export CSV",
     "aj_bell", ["AJ Bell Youinvest", "Dodl"]),
    ("interactive investor", "broker", "interactive investor", "csv",
     "Account → Portfolio → Export CSV, and Transactions → Download",
     "interactive_investor", ["ii", "The Share Centre"]),
    ("Vanguard Investor UK", "broker", "Vanguard", "csv",
     "Account → Transaction history / Holdings → Download CSV", "vanguard", ["Vanguard"]),
    ("Fidelity Personal Investing", "broker", "Fidelity", "csv",
     "Account → Investment summary → Export, Transaction history → Export", "generic", ["Fidelity"]),
    ("InvestEngine", "broker", "InvestEngine", "csv",
     "Account → Reports → Download portfolio / transactions CSV", "generic", []),
    ("Lightyear", "broker", "Lightyear", "csv",
     "App → Statements → Export CSV", "generic", []),
    ("eToro", "broker", "eToro", "api:etoro",
     "Portfolio → History → Account statement → Export XLSX", "etoro", []),
    ("Interactive Brokers", "broker", "IBKR", "api:ibkr",
     "Client Portal → Performance & Reports → Flex Queries → run and download", "ibkr_flex", ["IBKR", "IB"]),
    ("Saxo Markets UK", "broker", "Saxo", "api:saxo",
     "Account → Reports → Trades/Positions → Export", "generic", ["Saxo"]),
    ("IG", "broker", "IG Group", "api:ig",
     "My IG → Live accounts → History → Download CSV", "generic", ["IG Index"]),
    ("CMC Invest", "broker", "CMC Markets", "csv", "Account → Reports → Export", "generic", []),
    ("Charles Stanley Direct", "broker", "Charles Stanley", "csv",
     "Account → Valuation / Transactions → Export", "generic", []),
    ("Bestinvest", "broker", "Evelyn Partners", "csv", "Account → Portfolio → Export", "generic", []),
    ("Barclays Smart Investor", "broker", "Barclays", "csv",
     "Smart Investor → Portfolio / Transactions → Export CSV", "generic", []),
    ("Halifax Share Dealing", "broker", "Lloyds Banking Group", "csv",
     "Share dealing → Portfolio → Export", "generic", ["iWeb"]),
    ("Lloyds Bank Share Dealing", "broker", "Lloyds Banking Group", "csv",
     "Share dealing → Portfolio → Export", "generic", []),
    ("HSBC InvestDirect", "broker", "HSBC", "csv", "InvestDirect → Portfolio → Export", "generic", []),
    ("Santander Investment Hub", "broker", "Santander", "csv", "Investment Hub → Valuation → Export", "generic", []),
    ("NatWest Invest", "broker", "NatWest Group", "csv", "Invest → Portfolio → Download", "generic", []),
    ("Moneybox", "broker", "Moneybox", "csv", "App → Account → Statements", "generic", []),
    ("Plum", "broker", "Plum", "csv", "App → Statements → Export", "generic", []),
    ("Chip", "broker", "Chip", "csv", "App → Statements", "generic", []),
    ("Nutmeg", "broker", "JPMorgan Chase", "csv", "Account → Documents → Statements", "generic", []),
    ("Wealthify", "broker", "Aviva", "csv", "Account → Statements", "generic", []),
    ("Moneyfarm", "broker", "Moneyfarm", "csv", "Account → Documents → Statements", "generic", []),
    ("Robinhood UK", "broker", "Robinhood", "csv", "App → Account → Statements → Export", "generic", []),
    ("Webull UK", "broker", "Webull", "csv", "App → Account → Statements", "generic", []),
    ("Shares", "broker", "Shares", "csv", "App → Account → Statements", "generic", ["Shares.io"]),
    ("Fineco Bank UK", "broker", "Fineco", "csv", "Account → Reports → Export", "generic", []),
    ("Willis Owen", "broker", "Willis Owen", "csv", "Account → Valuation → Export", "generic", []),
    ("Quilter", "platform", "Quilter", "csv", "Adviser/client portal → Valuation → Export", "generic", []),
    ("Transact", "platform", "IntegraFin", "csv", "Portal → Valuation → Export", "generic", []),
    ("Fundment", "platform", "Fundment", "csv", "Portal → Valuation → Export", "generic", []),
    ("Nucleus", "platform", "Nucleus", "csv", "Portal → Valuation → Export", "generic", ["James Hay"]),
    ("Wealthtime", "platform", "Wealthtime", "csv", "Portal → Valuation → Export", "generic", ["Novia"]),
    ("Curtis Banks", "pension", "Curtis Banks", "manual", "", "generic", []),
    ("PensionBee", "pension", "PensionBee", "csv", "Account → Statements", "generic", []),
    ("Penfold", "pension", "Penfold", "csv", "Account → Statements", "generic", []),
    ("Standard Life", "pension", "abrdn", "manual", "Annual/interim statements (PDF)", "generic", []),
    ("Aviva", "pension", "Aviva", "manual", "MyAviva → Documents", "generic", []),
    ("Scottish Widows", "pension", "Lloyds Banking Group", "manual", "Online → Documents", "generic", []),
    ("Legal & General", "pension", "L&G", "manual", "Online → Documents", "generic", []),
    ("Aegon", "pension", "Aegon", "manual", "Online → Documents", "generic", []),
    ("Royal London", "pension", "Royal London", "manual", "Online → Documents", "generic", []),
    ("Prudential", "pension", "M&G", "manual", "Online → Documents", "generic", []),
    ("NEST Pensions", "pension", "NEST", "csv", "Online → Statements", "generic", []),
    ("The People's Pension", "pension", "People's Partnership", "csv", "Online → Statements", "generic", []),
    ("Smart Pension", "pension", "Smart", "csv", "Online → Statements", "generic", []),
    ("NHS Pension Scheme", "pension", "NHSBSA", "manual",
     "Total Reward Statement (TRS) — annual figure, enter by hand", "generic", ["NHS Pension"]),

    # ---- Crypto ---------------------------------------------------------------------
    ("Coinbase", "crypto", "Coinbase", "api:coinbase", "Reports → Generate report → CSV", "generic", []),
    ("Kraken", "crypto", "Kraken", "api:kraken", "History → Export → Ledgers CSV", "generic", []),
    ("Binance", "crypto", "Binance", "api:binance", "Wallet → Transaction history → Export", "generic", []),
    ("Bitstamp", "crypto", "Bitstamp", "api:bitstamp", "Account → Transactions → Export", "generic", []),
    ("Gemini", "crypto", "Gemini", "api:gemini", "Account → Statements → Export", "generic", []),
    ("Uphold", "crypto", "Uphold", "csv", "Activity → Export CSV", "generic", []),
    ("BitPanda", "crypto", "BitPanda", "api:bitpanda", "History → Export", "generic", []),

    # ---- Property / other assets ----------------------------------------------------
    ("Property (manual valuation)", "asset", "Manual", "manual",
     "Enter an estimated value; Mittens & Pence tracks it month to month", "generic", ["House", "Home"]),
    ("Premium Bonds (NS&I)", "bank", "NS&I", "manual",
     "NS&I online → holdings; enter the holding and monthly prizes", "generic", ["NS&I", "National Savings"]),
    ("Other / not listed", "other", "Manual", "csv",
     "Upload any CSV — Mittens & Pence will map the columns with you", "generic", []),
]

# --------------------------------------------------------------------------------------
# SOUTH AFRICA
# --------------------------------------------------------------------------------------

ZA = [
    # ---- Registered banks -----------------------------------------------------------
    ("Absa Bank", "bank", "Absa Group", "za_agg",
     "Absa Online → Statements → Download → CSV / OFX / QIF (Excel also available)",
     "absa", ["Absa", "ABSA"]),
    ("Standard Bank", "bank", "Standard Bank Group", "za_agg",
     "Internet Banking → Statements → Download → CSV / OFX",
     "standard_bank", ["SBSA", "Standard Bank of South Africa"]),
    ("First National Bank", "bank", "FirstRand", "za_agg",
     "FNB Online → Statements → Download → CSV / OFX / QIF",
     "fnb", ["FNB"]),
    ("Nedbank", "bank", "Nedbank Group", "za_agg",
     "Nedbank Online → Statements → Download → CSV / OFX",
     "nedbank", []),
    ("Capitec Bank", "bank", "Capitec", "za_agg",
     "Capitec app/online → Statements → Download → CSV / PDF",
     "capitec", ["Capitec"]),
    ("Investec Bank (South Africa)", "bank", "Investec", "api:investec",
     "Investec Online → Statements → Download; or enable Programmable Banking for a live API key",
     "investec", ["Investec ZA"]),
    ("Discovery Bank", "bank", "Discovery", "za_agg",
     "Discovery Bank app → Statements → Download → CSV",
     "generic", ["Discovery"]),
    ("TymeBank", "bank", "Tyme", "za_agg",
     "TymeBank app → Statements → Email/download CSV", "generic", ["Tyme"]),
    ("African Bank", "bank", "African Bank", "za_agg",
     "African Bank online → Statements → Download", "generic", []),
    ("Bank Zero", "bank", "Bank Zero", "za_csv",
     "Bank Zero app → Statements → Export CSV", "generic", []),
    ("Bidvest Bank", "bank", "Bidvest", "za_csv",
     "Bidvest Online → Statements → Download", "generic", []),
    ("Sasfin Bank", "bank", "Sasfin", "za_csv", "Sasfin online → Statements", "generic", []),
    ("Access Bank South Africa", "bank", "Access Bank", "za_csv",
     "Online banking → Statements", "generic", ["Grobank"]),
    ("Albaraka Bank", "bank", "Albaraka", "za_csv", "Online banking → Statements", "generic", []),
    ("HBZ Bank", "bank", "HBZ", "za_csv", "Online banking → Statements", "generic", []),
    ("Habib Overseas Bank", "bank", "Habib", "manual", "", "generic", []),
    ("Ithala", "bank", "Ithala SOC", "manual", "", "generic", []),
    ("South African Postbank", "bank", "Postbank", "manual",
     "Statements at a branch or by post", "generic", ["Postbank"]),
    ("Old Mutual Money Account", "bank", "Old Mutual", "za_csv",
     "Old Mutual app → Statements", "generic", ["OM Bank"]),
    ("Finbond Mutual Bank", "bank", "Finbond", "manual", "", "generic", []),
    ("GBS Mutual Bank", "bank", "GBS", "manual", "", "generic", []),
    ("YWBN Mutual Bank", "bank", "YWBN", "manual", "", "generic", []),
    ("Grindrod Bank", "bank", "Grindrod", "za_csv", "Online banking → Statements", "generic", []),
    ("Mercantile / Capitec Business", "bank", "Capitec", "za_agg",
     "Capitec Business online → Statements → CSV", "generic", ["Mercantile Bank"]),
    ("Citibank South Africa", "bank", "Citi", "za_csv", "CitiDirect → Statements", "generic", []),
    ("HSBC South Africa", "bank", "HSBC", "za_csv", "Online banking → Statements", "generic", []),
    ("Standard Chartered South Africa", "bank", "Standard Chartered", "za_csv",
     "Online banking → Statements", "generic", []),
    ("Deutsche Bank South Africa", "bank", "Deutsche Bank", "manual", "", "generic", []),
    ("JPMorgan Chase South Africa", "bank", "JPMorgan Chase", "manual", "", "generic", []),
    ("Bank of China Johannesburg", "bank", "Bank of China", "manual", "", "generic", []),
    ("ICBC South Africa", "bank", "ICBC", "manual", "", "generic", []),
    ("China Construction Bank JHB", "bank", "CCB", "manual", "", "generic", []),
    ("State Bank of India (SA)", "bank", "SBI", "manual", "", "generic", []),
    ("Bank of Baroda (SA)", "bank", "Bank of Baroda", "manual", "", "generic", []),
    ("Société Générale (SA)", "bank", "Société Générale", "manual", "", "generic", []),
    ("BNP Paribas (SA)", "bank", "BNP Paribas", "manual", "", "generic", []),
    ("Land Bank", "bank", "Land Bank", "manual", "", "generic", []),
    ("Development Bank of Southern Africa", "bank", "DBSA", "manual", "", "generic", []),

    # ---- Store cards / retail credit -------------------------------------------------
    ("Woolworths Financial Services", "card", "Woolworths", "za_csv",
     "W Rewards / WFS online → Statements", "generic", ["Woolworths Card"]),
    ("TFG Money (Foschini)", "card", "TFG", "za_csv", "TFG online account → Statements", "generic", ["Foschini"]),
    ("Truworths", "card", "Truworths", "manual", "", "generic", []),
    ("Mr Price Money", "card", "Mr Price", "manual", "", "generic", []),
    ("Edgars / Tenacity", "card", "Retailability", "manual", "", "generic", []),
    ("RCS Store Card", "card", "RCS", "za_csv", "RCS online → Statements", "generic", []),

    # ---- Investment platforms & brokers ----------------------------------------------
    ("EasyEquities", "broker", "Purple Group", "za_csv",
     "EasyEquities → My Investments → Transactions / Holdings → Download CSV (per ZAR/USD/AUD wallet)",
     "easyequities", ["Easy Equities", "EasyCrypto", "EasyProperties"]),
    ("Standard Bank Online Share Trading", "broker", "Standard Bank Group", "za_csv",
     "OST → Portfolio / Statements → Export", "generic", ["OST", "SBG Securities"]),
    ("FNB Share Investing", "broker", "FirstRand", "za_csv",
     "FNB Online → Share Investing → Portfolio → Export", "generic", ["FNB Stockbroking"]),
    ("Absa Stockbrokers", "broker", "Absa Group", "za_csv",
     "Absa Online Share Trading → Portfolio → Export", "generic", ["Absa Online Share Trading"]),
    ("Nedbank Private Wealth Stockbroking", "broker", "Nedbank Group", "za_csv",
     "Online → Portfolio → Export", "generic", []),
    ("Investec Online (Securities)", "broker", "Investec", "za_csv",
     "Investec Online → Investments → Portfolio → Export", "generic", ["Investec Securities"]),
    ("PSG Wealth / PSG Online", "broker", "PSG", "za_csv",
     "PSG Online → Portfolio / Statements → Export", "generic", ["PSG"]),
    ("Sanlam iTrade", "broker", "Sanlam", "za_csv", "iTrade → Portfolio → Export", "generic", []),
    ("Momentum Securities", "broker", "Momentum", "za_csv", "Online → Portfolio → Export", "generic", []),
    ("Trive South Africa", "broker", "Trive", "za_csv", "Platform → Reports → Export", "generic", ["GT247"]),
    ("Unum Capital", "broker", "Unum", "manual", "", "generic", []),
    ("Rand Swiss", "broker", "Rand Swiss", "manual", "", "generic", []),
    ("Anchor Capital", "broker", "Anchor", "manual", "", "generic", []),
    ("IG South Africa", "broker", "IG Group", "api:ig", "My IG → History → Download CSV", "generic", []),
    ("Interactive Brokers (ZA client)", "broker", "IBKR", "api:ibkr",
     "Client Portal → Flex Queries", "ibkr_flex", []),

    # ---- Asset managers / unit trusts / retirement ------------------------------------
    ("Allan Gray", "platform", "Allan Gray", "za_csv",
     "Online → My investments → Statements → Download", "generic", []),
    ("Coronation", "platform", "Coronation", "za_csv", "Online → Statements", "generic", []),
    ("Ninety One", "platform", "Ninety One", "za_csv", "Online → Statements", "generic", ["Investec Asset Management"]),
    ("Sygnia", "platform", "Sygnia", "za_csv", "Sygnia Alchemy → Statements → Download", "generic", []),
    ("10X Investments", "platform", "10X", "za_csv", "Online → Statements", "generic", ["10X"]),
    ("Satrix", "platform", "Sanlam", "za_csv", "SatrixNOW → Statements", "generic", ["SatrixNOW"]),
    ("Stanlib", "platform", "Standard Bank Group", "za_csv", "Online → Statements", "generic", []),
    ("M&G Investments (SA)", "platform", "M&G", "za_csv", "Online → Statements", "generic", ["Prudential SA"]),
    ("Nedgroup Investments", "platform", "Nedbank Group", "za_csv", "Online → Statements", "generic", []),
    ("Old Mutual Invest", "platform", "Old Mutual", "za_csv", "Online → Statements", "generic", ["Old Mutual Unit Trusts"]),
    ("Glacier by Sanlam", "platform", "Sanlam", "za_csv", "Glacier Web → Statements", "generic", []),
    ("Discovery Invest", "platform", "Discovery", "za_csv", "Discovery online → Statements", "generic", []),
    ("Momentum Wealth", "platform", "Momentum", "za_csv", "Online → Statements", "generic", []),
    ("Liberty", "platform", "Liberty", "manual", "Annual statements", "generic", []),
    ("Alexforbes", "pension", "Alexforbes", "manual", "Member portal → Statements", "generic", ["Alexander Forbes"]),
    ("Ashburton Investments", "platform", "FirstRand", "za_csv", "Online → Statements", "generic", []),
    ("Prescient Investment Management", "platform", "Prescient", "za_csv", "Online → Statements", "generic", []),
    ("Foord Asset Management", "platform", "Foord", "za_csv", "Online → Statements", "generic", []),
    ("Fedgroup", "platform", "Fedgroup", "za_csv", "Online → Statements", "generic", []),
    ("INN8 Invest", "platform", "INN8", "za_csv", "Portal → Statements", "generic", []),
    ("Government Employees Pension Fund", "pension", "GEPF", "manual",
     "Annual benefit statement — enter the value by hand", "generic", ["GEPF"]),

    # ---- Crypto ------------------------------------------------------------------------
    ("Luno", "crypto", "Luno", "api:luno", "Luno → Transactions → Download CSV", "generic", []),
    ("VALR", "crypto", "VALR", "api:valr", "VALR → Transaction history → Export CSV", "generic", []),
    ("AltCoinTrader", "crypto", "AltCoinTrader", "za_csv", "Account → History → Export", "generic", []),
    ("Ovex", "crypto", "Ovex", "manual", "", "generic", []),
    ("Altify", "crypto", "Altify", "za_csv", "Account → Statements", "generic", ["Revix"]),
    ("Binance (ZA)", "crypto", "Binance", "api:binance", "Wallet → Transaction history → Export", "generic", []),

    # ---- Other -------------------------------------------------------------------------
    ("Property (manual valuation)", "asset", "Manual", "manual",
     "Enter an estimated value; Mittens & Pence tracks it month to month", "generic", []),
    ("Other / not listed", "other", "Manual", "za_csv",
     "Upload any CSV — Mittens & Pence will map the columns with you", "generic", []),
]


# --------------------------------------------------------------------------------------
# Expansion
# --------------------------------------------------------------------------------------

KIND_LABEL = {
    "bank": "Bank",
    "building_society": "Building society",
    "broker": "Investment platform / broker",
    "platform": "Investment platform",
    "pension": "Pension",
    "card": "Credit / store card",
    "ewallet": "E-money / wallet",
    "crypto": "Crypto exchange",
    "asset": "Other asset",
    "other": "Other",
}

DEFAULT_ACCOUNT_TYPES = {
    "bank": ["current", "savings", "joint", "credit"],
    "building_society": ["savings", "isa_cash", "mortgage"],
    "broker": ["isa", "gia", "sipp", "tfsa", "trading"],
    "platform": ["isa", "gia", "sipp", "unit_trust", "ra", "tfsa"],
    "pension": ["pension"],
    "card": ["credit"],
    "ewallet": ["wallet", "current"],
    "crypto": ["crypto"],
    "asset": ["asset"],
    "other": ["other"],
}


def slug(country: str, name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{country.lower()}-{s}"


def build_sync(sync: str, csv_where: str, csv_profile: str, country: str) -> dict:
    """Turn the compact sync token into a full capability block."""
    out = {
        "recommended": None,
        "methods": [],
    }

    def add(method, **kw):
        d = {"method": method}
        d.update(kw)
        out["methods"].append(d)

    tokens = sync.split("+")
    for tok in tokens:
        if tok == "ob":
            add(
                "openbanking",
                label="Connect automatically (Open Banking)",
                providers=UK_OB,
                confidence="high",
                auth="You are redirected to your bank's own app or website to approve read-only access. "
                     "Mittens & Pence never sees your banking password.",
                refresh="Consent lasts up to 90 days, then the bank asks you to re-approve.",
                gets=["balances", "transactions"],
            )
        elif tok == "ob_lite":
            add(
                "openbanking",
                label="Connect automatically (Open Banking) — coverage varies",
                providers=UK_OB_LITE,
                confidence="medium",
                auth="You are redirected to the provider's own login to approve read-only access.",
                refresh="Consent lasts up to 90 days.",
                gets=["balances", "transactions"],
                note="Not every aggregator carries this institution. Mittens & Pence checks live when you connect "
                     "and falls back to file upload if it isn't there.",
            )
        elif tok == "za_agg":
            add(
                "aggregator",
                label="Automatic sync (via a South African data aggregator)",
                providers=ZA_AGG,
                confidence="low",
                auth="South Africa has no mandated Open Banking yet. Aggregators reach these banks, but "
                     "they onboard businesses rather than individuals.",
                refresh="Varies by provider.",
                gets=["balances", "transactions"],
                note="Enter aggregator credentials in Settings if you have them. Otherwise use file upload, "
                     "which works today and takes about a minute a month.",
            )
        elif tok.startswith("api:"):
            pid = tok.split(":", 1)[1]
            p = PROVIDERS[pid]
            add(
                "direct_api",
                label=f"Connect automatically ({p['name']})",
                providers=[pid],
                confidence="high",
                auth=f"Generate a personal key: {p['signup']}",
                refresh="Runs whenever you press Sync, and on the schedule you set.",
                gets=["balances", "transactions", "holdings", "dividends"],
            )
        elif tok in ("csv", "za_csv"):
            pass  # csv is added below for everyone
        elif tok == "manual":
            pass

    # Everyone gets the file-upload route unless they are pure-manual.
    if sync != "manual":
        add(
            "csv",
            label="Download a statement and upload it",
            providers=[],
            confidence="high",
            auth="No credentials needed.",
            refresh="Do it once a month — Mittens & Pence remembers the column layout and de-duplicates rows "
                    "so re-uploading an overlapping file is safe.",
            gets=["transactions", "holdings", "dividends"],
            where=csv_where,
            profile=csv_profile,
            formats=["csv", "xlsx", "ofx", "qif", "tsv", "pdf"],
        )
    add(
        "manual",
        label="Type the balance in yourself",
        providers=[],
        confidence="high",
        auth="No credentials needed.",
        refresh="Update it whenever you like; Mittens & Pence keeps the monthly history.",
        gets=["balances"],
    )

    order = {"direct_api": 0, "openbanking": 1, "aggregator": 2, "csv": 3, "manual": 4}
    out["methods"].sort(key=lambda m: order[m["method"]])
    out["recommended"] = out["methods"][0]["method"]
    return out


def expand(rows, country):
    items = []
    for (name, kind, group, sync, csv_where, csv_profile, aka) in rows:
        items.append({
            "id": slug(country, name),
            "name": name,
            "aka": aka,
            "country": country,
            "kind": kind,
            "kind_label": KIND_LABEL[kind],
            "group": group,
            "currency": "GBP" if country == "GB" else "ZAR",
            "account_types": DEFAULT_ACCOUNT_TYPES[kind],
            "search": " ".join([name] + aka + [group]).lower(),
            "sync": build_sync(sync, csv_where, csv_profile, country),
        })
    items.sort(key=lambda i: (i["kind"] != "bank", i["name"].lower()))
    return items


def main():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    # Write only where the registry actually loads from, and prove it rather than assume.
    import kestrel.institutions.registry as _reg
    expected = pathlib.Path(_reg.__file__).resolve().parent / "data"
    if OUT.resolve() != expected:
        raise SystemExit(
            f"Refusing to build: OUT is {OUT}\n"
            f"but the registry reads {expected}. Writing to the wrong directory ships a "
            f"stale registry while every test passes.")
    OUT.mkdir(parents=True, exist_ok=True)
    gb = expand(UK, "GB")
    za = expand(ZA, "ZA")

    (OUT / "institutions_gb.json").write_text(
        json.dumps({"country": "GB", "country_name": "United Kingdom",
                    "currency": "GBP", "institutions": gb}, indent=1), encoding="utf-8")
    (OUT / "institutions_za.json").write_text(
        json.dumps({"country": "ZA", "country_name": "South Africa",
                    "currency": "ZAR", "institutions": za}, indent=1), encoding="utf-8")
    # `free` decides whether a provider may be *recommended*, so a missing flag quietly
    # demotes a bank to statement upload. Investec lost its own API that way when a bulk
    # edit skipped one entry. Default it here and say so, rather than let it read as False.
    for pid, p in PROVIDERS.items():
        if "free" not in p:
            print(f"  ! {pid} does not declare 'free' — defaulting to False. "
                  f"If it is free to a household, say so explicitly.")
            p["free"] = False
    (OUT / "providers.json").write_text(json.dumps(PROVIDERS, indent=1), encoding="utf-8")

    # Re-read after the JSON is on disk, so the counts below reflect what we just wrote.
    from kestrel.institutions import registry
    from kestrel.providers import base as provider_base
    registry._load.cache_clear()
    provider_base.load_all()

    print(f"UK institutions: {len(gb)}")
    print(f"ZA institutions: {len(za)}")
    for c, items in (("GB", gb), ("ZA", za)):
        by = {}
        for i in items:
            by[i["kind"]] = by.get(i["kind"], 0) + 1
        print(f"  {c}: " + ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
        # Two different numbers, and only the second one is true. The data file says what
        # the institution *supports*; the registry says what Mittens & Pence will actually offer
        # once "is there a client for it" and "can a household afford it" are applied.
        # Printing only the first is how a build could look healthy while every UK bank
        # quietly pointed at a paid sandbox.
        raw, eff = {}, {}
        for i in items:
            r = i["sync"]["recommended"]
            raw[r] = raw.get(r, 0) + 1
            e = registry.next_step(i["id"])["recommended"]
            eff[e] = eff.get(e, 0) + 1
        print("       supported:   " + ", ".join(f"{k}={v}" for k, v in sorted(raw.items())))
        print("       offered:     " + ", ".join(f"{k}={v}" for k, v in sorted(eff.items())))


if __name__ == "__main__":
    main()
