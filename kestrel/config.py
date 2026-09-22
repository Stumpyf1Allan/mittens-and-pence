"""Paths, constants and user settings for Mittens & Pence."""

from __future__ import annotations

import json
import os
import pathlib
import sys

APP_NAME = "Mittens & Pence"
#: The same name with nothing a shell can misread. The ampersand is a command separator
#: in Windows batch and needs escaping in HTML, so anything that becomes a folder, a
#: filename or an executable uses this form; only text a person reads uses APP_NAME.
APP_FILE_NAME = "Mittens and Pence"
APP_SLUG = "mittens-and-pence"
#: Every name this app has had, newest first. The data directory is NOT renamed on an
#: existing install — real accounts, credentials and history live in the old folder, and
#: quietly moving somebody's data to prove a point about naming is how you lose it. New
#: installs get the new names; old ones keep working exactly where they are. This is a
#: list rather than a single value because there has now been more than one rename.
LEGACY_NAMES = ("Mithapp", "Kestrel")
LEGACY_SLUGS = ("mithapp", "kestrel")
#: Kept so anything that still refers to the previous single-value names keeps working.
LEGACY_NAME = LEGACY_NAMES[0]
LEGACY_SLUG = LEGACY_SLUGS[0]
APP_VERSION = "1.1.0"
APP_TAGLINE = "Budget and investment app"

#: Where the app looks to find out whether a newer version exists. Leave it empty and
#: the whole update feature switches itself off and says so — nothing breaks, nothing
#: phones home. Fill it in before building the copy you send out; see docs/PUBLISHING.md
#: for the five-minute version. It must be https, and it must point at a small JSON
#: file, not at a web page.
#:
#: GitHub keeps a permanent address pointing at whatever the newest release is, so this
#: is set once and never changes again:
#:
#: Upload latest.json alongside the .exe on every release and this address follows it.
#:
#: Set to Allan's repository. It is deliberately filled in even before that repository
#: exists: a copy pointed at an address with nothing at it yet starts working the moment
#: something is published there, whereas a copy built with this BLANK can never be told
#: about updates at all, on anybody's machine, ever. Of the two ways to be wrong, only
#: one is recoverable.
UPDATE_MANIFEST_URL = (
    "https://github.com/Stumpyf1Allan/mittens-and-pence"
    "/releases/latest/download/latest.json")


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> pathlib.Path:
    """Where read-only bundled files live (institution lists, web assets)."""
    if _is_frozen():
        return pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    return pathlib.Path(__file__).resolve().parent


def _data_root() -> tuple[pathlib.Path, list[pathlib.Path]]:
    """(preferred path, every older path) for this platform, oldest install last."""
    if sys.platform.startswith("win"):
        base = pathlib.Path(os.environ.get("LOCALAPPDATA")
                            or os.path.expanduser("~\\AppData\\Local"))
        return base / APP_FILE_NAME, [base / n for n in LEGACY_NAMES]
    if sys.platform == "darwin":
        base = pathlib.Path.home() / "Library" / "Application Support"
        return base / APP_FILE_NAME, [base / n for n in LEGACY_NAMES]
    base = pathlib.Path(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"))
    return base / APP_SLUG, [base / s for s in LEGACY_SLUGS]


def data_dir() -> pathlib.Path:
    """Per-user writable directory: database, exports, logs, uploads.

    If a folder from the old name is already there and the new one isn't, that folder is
    the answer — an existing install keeps its data exactly where it is.
    """
    override = (os.environ.get("MITTENS_DATA_DIR")
                or os.environ.get("MITHAPP_DATA_DIR")
                or os.environ.get("KESTREL_DATA_DIR"))
    if override:
        p = pathlib.Path(override)
    else:
        p, older = _data_root()
        if not p.exists():
            # Newest name that actually has a folder wins, so a second rename does not
            # send somebody back past the install they have been using.
            p = next((old for old in older if old.exists()), p)
    p.mkdir(parents=True, exist_ok=True)
    return p


#: Database filenames this app has used, newest first.
DB_NAMES = ("mittens.db", "mithapp.db", "kestrel.db")


def db_path() -> pathlib.Path:
    """Whichever database file is actually there, so a rename never orphans one."""
    d = data_dir()
    for name in DB_NAMES:
        if (d / name).exists():
            return d / name
    return d / DB_NAMES[0]


def exports_dir() -> pathlib.Path:
    p = data_dir() / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def uploads_dir() -> pathlib.Path:
    p = data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> pathlib.Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_dir() -> pathlib.Path:
    p = data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Defaults that the household can change in Settings
# ---------------------------------------------------------------------------

DEFAULTS = {
    "base_currency": "GBP",
    "secondary_currency": "ZAR",
    "portfolio_start_date": "2021-08-24",   # matches the Overall!L1 anchor in Investments 2
    "fiscal_year_start": "04-06",           # UK tax year, 6 April
    "za_fiscal_year_start": "03-01",        # SA tax year, 1 March
    "isa_allowance": 20000.0,
    "tfsa_annual_allowance": 36000.0,       # South African tax-free savings, ZAR
    "tfsa_lifetime_allowance": 500000.0,
    "price_refresh_minutes": 30,
    "auto_sync_on_open": True,
    "monthly_snapshot_day": 1,
    "budget_period": "monthly",
    "theme": "auto",
    "hide_amounts": False,
    "sheets_enabled": False,
    # Set once the introductory tour has been finished or skipped. Kept here rather
    # than in the browser so it survives a cleared cache and behaves the same in the
    # desktop window as in a browser tab.
    "tour_done": False,
    # Updates. The check is a few hundred bytes against a fixed address and carries
    # nothing about the household; turning it off means this app never touches the
    # internet except to price investments.
    "check_for_updates": True,
    "auto_download_updates": True,
    # Overrides UPDATE_MANIFEST_URL on this machine only — useful for testing a
    # release before it goes out, and for anyone running their own copy.
    "update_url": "",
}

BASE_CURRENCIES = ["GBP", "ZAR", "USD", "EUR"]

# Exchange suffixes used when asking a price source for a quote.
EXCHANGE_MAP = {
    "LON": {"yahoo": ".L", "currency": "GBX", "name": "London Stock Exchange"},
    "LSE": {"yahoo": ".L", "currency": "GBX", "name": "London Stock Exchange"},
    "NYSE": {"yahoo": "", "currency": "USD", "name": "New York Stock Exchange"},
    "NASDAQ": {"yahoo": "", "currency": "USD", "name": "NASDAQ"},
    "NYSEARCA": {"yahoo": "", "currency": "USD", "name": "NYSE Arca"},
    "BATS": {"yahoo": "", "currency": "USD", "name": "Cboe BZX"},
    "AMS": {"yahoo": ".AS", "currency": "EUR", "name": "Euronext Amsterdam"},
    "EPA": {"yahoo": ".PA", "currency": "EUR", "name": "Euronext Paris"},
    "ETR": {"yahoo": ".DE", "currency": "EUR", "name": "Xetra"},
    "FRA": {"yahoo": ".F", "currency": "EUR", "name": "Frankfurt"},
    "BIT": {"yahoo": ".MI", "currency": "EUR", "name": "Borsa Italiana"},
    "BME": {"yahoo": ".MC", "currency": "EUR", "name": "Bolsa de Madrid"},
    "SWX": {"yahoo": ".SW", "currency": "CHF", "name": "SIX Swiss Exchange"},
    "STO": {"yahoo": ".ST", "currency": "SEK", "name": "Nasdaq Stockholm"},
    "CPH": {"yahoo": ".CO", "currency": "DKK", "name": "Nasdaq Copenhagen"},
    "JSE": {"yahoo": ".JO", "currency": "ZAC", "name": "Johannesburg Stock Exchange"},
    "TSE": {"yahoo": ".TO", "currency": "CAD", "name": "Toronto Stock Exchange"},
    "ASX": {"yahoo": ".AX", "currency": "AUD", "name": "Australian Securities Exchange"},
    "HKG": {"yahoo": ".HK", "currency": "HKD", "name": "Hong Kong Stock Exchange"},
    "TYO": {"yahoo": ".T", "currency": "JPY", "name": "Tokyo Stock Exchange"},
    "CRYPTO": {"yahoo": "-GBP", "currency": "GBP", "name": "Crypto"},
}

# Currencies quoted in minor units (pence / cents) that must be divided by 100.
MINOR_UNIT_CURRENCIES = {"GBX": ("GBP", 100.0), "GBp": ("GBP", 100.0),
                         "ZAC": ("ZAR", 100.0), "ZAc": ("ZAR", 100.0)}

# The sector taxonomy carried over from the Investments 2 workbook, extended a little.
SECTORS = [
    "Broad Global & US Indexes",
    "Information Technology & Software",
    "Semiconductors & Hardware",
    "Healthcare",
    "Financials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials & Logistics",
    "Industrials & Defence (Thematic ETF)",
    "Energy & Utilities (Thematic ETF)",
    "Infrastructure & Utilities (Thematic ETF)",
    "Energy & Materials",
    "Real Estate (REITs)",
    "Communication Services",
    "Emerging Markets",
    "Bonds & Fixed Income",
    "Cash & Money Market",
    "Commodities & Gold",
    "Crypto",
    "Other",
]

# Spending sectors for the banking / budget side. Parent → children.
SPEND_CATEGORIES = {
    "Income": ["Salary", "Bonus", "Dividends received", "Interest received",
               "Refunds", "Gifts received", "Rental income", "Other income"],
    "Home": ["Rent", "Mortgage", "Council tax / rates", "Home insurance",
             "Repairs & maintenance", "Furniture & homeware", "Cleaning & garden",
             "Levies (SA)", "Security (SA)"],
    # "Energy" exists because almost every British supplier sells both gas and
    # electricity on one bill, and nothing in "BRITISH GAS DD 12345" says which. It used
    # to be filed as Electricity, which is a guess that is wrong about half the time.
    # Electricity and Gas are kept for the bills that really are only one of them —
    # Eskom, City Power, a prepaid meter, a Calor delivery.
    "Utilities": ["Energy", "Electricity", "Gas", "Water", "Broadband", "Mobile phone",
                  "TV licence & streaming", "Waste"],
    "Food & Drink": ["Groceries", "Restaurants & takeaway", "Coffee & snacks", "Alcohol"],
    "Transport": ["Fuel", "Public transport", "Taxi & rideshare", "Car insurance",
                  "Car finance", "Servicing & MOT", "Parking & tolls", "Road tax / licence"],
    "Health": ["Medical aid / health insurance", "Pharmacy", "Dentist & optician",
               "Gym & fitness", "Therapy & wellbeing"],
    "Family": ["Childcare", "School fees", "Children's activities", "Pets", "Family support"],
    # Things you buy and keep. What you eat or drink is Food & Drink, whichever shop
    # it came from — so a supermarket trip is Groceries even when half of it was a
    # frying pan. "Household & general" is for the shop that sells everything.
    "Shopping": ["Clothing", "Electronics", "Household & general", "Hobbies",
                 "Books & media", "Gifts given"],
    "Travel": ["Flights", "Accommodation", "Holiday spending", "Travel insurance"],
    "Insurance & Protection": ["Life cover", "Income protection", "Contents insurance",
                               "Funeral cover (SA)"],
    "Financial": ["Bank charges", "Interest & fees", "Loan repayment", "Credit card payment",
                  "Foreign exchange fees", "Tax"],
    "Saving & Investing": ["ISA contribution", "SIPP / pension contribution",
                           "GIA contribution", "TFSA contribution (SA)",
                           "Retirement annuity (SA)", "Emergency fund", "Other savings"],
    "Giving": ["Charity", "Tithing", "Sponsorship"],
    "Subscriptions": ["Software", "Memberships", "News & magazines", "Music & video"],
    "Transfers": ["Between own accounts", "Cross-border transfer", "Cash withdrawal"],
    "Other": ["Uncategorised", "One-off", "Business expenses"],
}

# Categories that should never count as spending in budget totals.
NON_SPEND_PARENTS = {"Income", "Transfers", "Saving & Investing"}


class Settings:
    """Thin JSON-backed settings store, kept beside the database."""

    def __init__(self, path: pathlib.Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self._data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return self

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def __getitem__(self, k):
        return self._data.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, v):
        self._data[k] = v
        self.save()

    def get(self, k, default=None):
        return self._data.get(k, DEFAULTS.get(k, default))

    def update(self, d: dict):
        self._data.update(d)
        self.save()

    def as_dict(self) -> dict:
        return dict(self._data)


settings = Settings()
