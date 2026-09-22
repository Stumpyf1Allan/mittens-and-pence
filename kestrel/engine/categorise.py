"""Put every transaction in a category so the budgets mean something.

Order of precedence:
  1. A category set by hand — never overwritten.
  2. A rule you created (highest priority number wins).
  3. The built-in UK/SA merchant patterns below.
  4. "Uncategorised", which the app nags you about on the Transactions screen.

Teaching it is one click: categorising a transaction offers to make a rule from the
merchant name, which then back-fills every matching row.
"""

from __future__ import annotations

import re

from .. import db

# (parent, child, [patterns]) — matched against description + merchant, lowercased.
BUILTIN = [
    # ---------------------------------------------------------------- income
    ("Income", "Salary", [r"\bsalary\b", r"\bpayroll\b", r"\bwages\b", r"nhs\s*bsa",
                          r"\bsal\b", r"\bpay\s*ref", r"\bstipend\b", r"salaris"]),
    ("Income", "Interest received", [r"\binterest\b", r"gross int", r"credit interest",
                                     r"rente"]),
    ("Income", "Dividends received", [r"\bdividend\b", r"\bdiv\b\s*pmt"]),
    ("Income", "Refunds", [r"\brefund\b", r"\breversal\b", r"chargeback"]),
    ("Income", "Rental income", [r"\brent received\b", r"tenant"]),

    # ---------------------------------------------------------------- home
    ("Home", "Rent", [r"\brent\b(?!al car)", r"letting", r"landlord"]),
    ("Home", "Mortgage", [r"mortgage", r"\bhalifax mtg\b", r"\bbond repayment\b",
                          r"home loan"]),
    ("Home", "Council tax / rates", [r"council tax", r"\bcouncil\b", r"municipal",
                                     r"rates.*municipal", r"\bcity of \w+"]),
    ("Home", "Home insurance", [r"home ins", r"buildings ins", r"contents ins"]),
    ("Home", "Repairs & maintenance", [r"\bb&q\b", r"screwfix", r"toolstation", r"wickes",
                                       r"homebase", r"builders warehouse", r"leroy merlin",
                                       r"plumber", r"electrician"]),
    ("Home", "Furniture & homeware", [r"\bikea\b", r"dunelm", r"the range", r"wayfair",
                                      r"\bmr price home\b", r"coricraft"]),
    ("Home", "Levies (SA)", [r"\blevy\b", r"levies", r"body corporate", r"\bhoa\b"]),
    ("Home", "Security (SA)", [r"\badt\b", r"fidelity adt", r"beagle watch", r"cs?s security",
                               r"\barmed response\b"]),

    # ---------------------------------------------------------------- utilities
    # Energy first, so a dual-fuel supplier is claimed before the bare \bgas\b pattern
    # below can take "BRITISH GAS" for a gas bill. Built-in rules of equal priority are
    # tried in the order they appear here.
    ("Utilities", "Energy", [r"octopus energy", r"british gas", r"edf energy", r"e\.?on",
                             r"ovo energy", r"scottish power", r"sse\b", r"utilita",
                             r"\bbulb energy\b", r"shell energy", r"so energy",
                             r"utility warehouse", r"\bovo\b", r"\bedf\b"]),
    ("Utilities", "Electricity", [r"\beskom\b", r"prepaid electricity", r"\bcity power\b"]),
    ("Utilities", "Gas", [r"^(?!.*(petrol|fuel|station|garage|forecourt)).*\bgas\b", r"calor"]),
    ("Utilities", "Water", [r"thames water", r"severn trent", r"anglian water", r"yorkshire water",
                            r"united utilities", r"south west water", r"\brand water\b",
                            r"\bwater\b.*(bill|utility)"]),
    ("Utilities", "Broadband", [r"\bbt\b", r"virgin media", r"sky broadband", r"talktalk",
                                r"plusnet", r"hyperoptic", r"community fib", r"\bafrihost\b",
                                r"\bwebafrica\b", r"\brain\b", r"\bmweb\b", r"vumatel"]),
    ("Utilities", "Mobile phone", [r"\bee\b", r"\bo2\b", r"vodafone", r"three\b", r"giffgaff",
                                   r"tesco mobile", r"\bid mobile\b", r"lebara", r"lyca",
                                   r"\bmtn\b", r"\btelkom\b", r"cell ?c", r"\bvodacom\b"]),
    ("Utilities", "TV licence & streaming", [r"tv licen[cs]e", r"\bnetflix\b", r"disney ?\+",
                                             r"amazon prime video", r"\bnow tv\b", r"\bhayu\b",
                                             r"\bshowmax\b", r"\bdstv\b", r"multichoice",
                                             r"apple tv", r"\bmubi\b", r"paramount\+"]),

    # Before the grocers below, because a supermarket forecourt is a petrol station
    # that happens to share a name with a shop. "TESCO PETROL 4021" was filed as the
    # week's groceries, which quietly moved a tank of fuel into the food budget.
    ("Transport", "Fuel", [r"(tesco|sainsbury\w*|asda|morrisons|waitrose|costco|pick n pay|engen|sasol)\b.{0,20}\b(petrol|fuel|filling|forecourt|pfs)\b"]),

    # ---------------------------------------------------------------- food
    ("Food & Drink", "Groceries", [r"tesco", r"sainsbury", r"asda", r"morrisons", r"aldi",
                                   r"\blidl\b", r"waitrose", r"\bco-?op\b", r"iceland",
                                   r"marks ?&? ?spencer", r"\bm&s\b", r"ocado", r"budgens",
                                   r"\bwoolworths\b", r"\bcheckers\b", r"\bpick n pay\b",
                                   r"\bshoprite\b", r"\bspar\b", r"food ?lover", r"\bmakro\b"]),
    ("Food & Drink", "Restaurants & takeaway", [r"deliveroo", r"just ?eat", r"uber ?eats",
                                                r"\bnando", r"mcdonald", r"\bkfc\b", r"burger king",
                                                r"pizza", r"\bwagamama\b", r"greggs", r"subway",
                                                r"restaurant", r"\bmr d food\b", r"\bsteers\b",
                                                r"\bwimpy\b", r"\bocean basket\b", r"\bspur\b"]),
    ("Food & Drink", "Coffee & snacks", [r"costa", r"starbucks", r"pret", r"caffe nero",
                                         r"\bcafe\b", r"coffee", r"\bvida e\b", r"seattle coffee",
                                         r"bootlegger"]),
    ("Food & Drink", "Alcohol", [r"majestic wine", r"\bbwsw?\b", r"wine", r"brewdog",
                                 r"\bliquor\b", r"\btops\b", r"\bultra liquors\b"]),

    # ---------------------------------------------------------------- transport
    ("Transport", "Fuel", [r"\bshell\b", r"\bbp\b", r"\besso\b", r"texaco", r"\bgulf\b",
                           r"\bjet\b\s*fuel", r"\bengen\b", r"\bsasol\b", r"\bcaltex\b",
                           r"\btotal\b.*garage", r"petrol", r"fuel"]),
    ("Transport", "Public transport", [r"\btfl\b", r"transport for london", r"trainline",
                                       r"\bgwr\b", r"avanti", r"northern rail", r"national rail",
                                       r"\bmetrobus\b", r"gautrain", r"\bmyciti\b"]),
    ("Transport", "Taxi & rideshare", [r"\buber\b(?! ?eats)", r"\bbolt\b", r"\blyft\b",
                                       r"free ?now", r"\btaxi\b", r"\bola\b"]),
    ("Transport", "Car insurance", [r"car ins", r"motor ins", r"admiral", r"direct line",
                                    r"aviva.*motor", r"\bhollard\b", r"\boutsurance\b",
                                    r"\bmiway\b", r"\bnaked\b", r"king price"]),
    ("Transport", "Servicing & MOT", [r"\bmot\b", r"kwik ?fit", r"halfords", r"\bservice\b.*car",
                                      r"\bmidas\b", r"\btiger wheel\b", r"supa ?quick"]),
    ("Transport", "Parking & tolls", [r"parking", r"\bringgo\b", r"\bjustpark\b", r"dart charge",
                                      r"congestion", r"\bsanral\b", r"\betoll\b", r"\btoll\b"]),
    ("Transport", "Road tax / licence", [r"\bdvla\b", r"vehicle tax", r"road tax",
                                         r"licence renewal", r"natis"]),
    ("Transport", "Car finance", [r"car finance", r"\bpcp\b", r"vehicle finance", r"wesbank",
                                  r"\bmfc\b", r"vehicle asset"]),

    # ---------------------------------------------------------------- health
    ("Health", "Medical aid / health insurance", [r"\bbupa\b", r"\bvitality\b", r"\baxa health\b",
                                                  r"\bdiscovery health\b", r"\bmomentum health\b",
                                                  r"\bbonitas\b", r"\bmedihelp\b", r"medical aid",
                                                  r"\bdiscovery\b.*(health|medical)"]),
    ("Health", "Pharmacy", [r"\bboots\b", r"superdrug", r"pharmacy", r"\bclicks\b",
                            r"\bdis-?chem\b", r"lloyds pharmacy"]),
    ("Health", "Dentist & optician", [r"dentist", r"dental", r"specsavers", r"vision express",
                                      r"optician", r"\bmellins\b"]),
    ("Health", "Gym & fitness", [r"puregym", r"\bthe gym\b", r"david lloyd", r"nuffield health",
                                 r"\banytime fitness\b", r"\bvirgin active\b", r"\bplanet fitness\b",
                                 r"\bgym\b", r"\bpeloton\b", r"\bstrava\b"]),

    # ---------------------------------------------------------------- family
    ("Family", "Childcare", [r"nursery", r"childminder", r"childcare", r"after ?school",
                             r"\bcreche\b", r"\bcrèche\b"]),
    ("Family", "School fees", [r"school fee", r"\bschool\b", r"tuition", r"university",
                               r"\bpta\b"]),
    ("Family", "Pets", [r"pets ?at ?home", r"\bvet\b", r"veterinary", r"\bpetstop\b",
                        r"\bwestern shoppe\b", r"pet ?shop"]),

    # ---------------------------------------------------------------- shopping
    ("Shopping", "Clothing", [r"\bzara\b", r"\bh&m\b", r"\bnext\b(?=\s*(retail|plc|stores?|$))",
                              r"primark", r"uniqlo",
                              r"asos", r"\bjd sports\b", r"sports ?direct", r"\bmr price\b",
                              r"\btruworths\b", r"\bfoschini\b", r"\bcotton on\b", r"\bmarkham\b"]),
    ("Shopping", "Electronics", [r"currys", r"\bapple store\b", r"\bapple\.com\b",
                                 r"incredible connect", r"\bistore\b", r"\bgame\b\s*stores"]),
    # A shop that sells everything cannot be guessed at from the name of the shop.
    # Amazon used to land in Electronics for want of anywhere better, which made a
    # nappy order and a kettle both read as gadgets; the honest answer is "general
    # shopping", and split the ones that matter.
    ("Shopping", "Household & general", [r"\bamazon\b(?! ?prime)", r"\bamzn(?! ?prime)", r"\bebay\b",
                                         r"\btakealot\b", r"argos", r"john lewis",
                                         r"\bwilko\b", r"home bargains", r"\bb ?& ?m\b",
                                         r"poundland", r"\bpound ?stretcher\b",
                                         r"robert dyas", r"\bsavers\b", r"\btk ?maxx\b",
                                         r"\bhobbycraft\b", r"\bpep\b\s*stores"]),
    ("Shopping", "Books & media", [r"waterstones", r"\bkindle\b", r"audible", r"\bexclusive books\b",
                                   r"\bbargain books\b"]),
    # "gift" alone swallowed GIFT AID, which is a charitable donation and belongs
    # nowhere near a presents budget.
    ("Shopping", "Gifts given", [r"\bgift(?!\s*aid)\b", r"\bpresent\b", r"moonpig",
                                 r"netflorist"]),

    # ---------------------------------------------------------------- travel
    ("Travel", "Flights", [r"\bba\.com\b", r"british airways", r"easyjet", r"ryanair",
                           r"wizz ?air", r"\bjet2\b", r"emirates", r"\bklm\b", r"lufthansa",
                           r"\bflysafair\b", r"\bkulula\b", r"\bairlink\b", r"\bcemair\b",
                           r"\bairline\b", r"\bskyscanner\b"]),
    ("Travel", "Accommodation", [r"booking\.com", r"airbnb", r"\bhotel\b", r"premier inn",
                                 r"travelodge", r"\bexpedia\b", r"\bhotels\.com\b", r"\blekkeslaap\b"]),
    ("Travel", "Travel insurance", [r"travel ins"]),

    # ---------------------------------------------------------------- subscriptions
    ("Subscriptions", "Software", [r"\badobe\b", r"microsoft ?365", r"\bgoogle one\b",
                                   r"\bicloud\b", r"\bdropbox\b", r"\bnotion\b", r"\bcanva\b",
                                   r"\bopenai\b", r"\banthropic\b", r"\bgithub\b", r"\b1password\b",
                                   r"\bnordvpn\b", r"\bxero\b", r"\bquickbooks\b"]),
    ("Subscriptions", "Music & video", [r"amazon ?prime", r"\bprime video\b",
                                        r"spotify", r"apple ?music", r"\byoutube premium\b",
                                        r"\bdeezer\b", r"\btidal\b"]),
    ("Subscriptions", "News & magazines", [r"\bguardian\b", r"\btimes\b\s*sub", r"telegraph",
                                           r"\bnews24\b", r"\bdaily maverick\b", r"\bft\.com\b",
                                           r"\beconomist\b", r"\bnew york times\b"]),
    ("Subscriptions", "Memberships", [r"\bnational trust\b", r"\bgmc\b", r"\bbma\b", r"\brcp\b",
                                      r"\brcoa\b", r"\bhpcsa\b", r"\bsasa\b", r"\bmps\b",
                                      r"\bmdu\b", r"medical protection", r"membership",
                                      r"subscription"]),

    # ---------------------------------------------------------------- financial
    ("Financial", "Bank charges", [r"service charge", r"account fee", r"monthly fee",
                                   r"maintenance fee", r"bank charge", r"\boverdraft\b"]),
    ("Financial", "Interest & fees", [r"interest charged", r"late fee", r"\bapr\b"]),
    ("Financial", "Foreign exchange fees", [r"\bfx fee\b", r"non-?sterling", r"currency conversion",
                                            r"international fee"]),
    ("Financial", "Credit card payment", [r"credit card payment", r"card payment thank",
                                          r"\bcc payment\b"]),
    ("Financial", "Loan repayment", [r"loan repay", r"\bloan\b", r"personal loan"]),
    ("Financial", "Tax", [r"\bhmrc\b", r"\bsars\b", r"\bincome tax\b", r"self assessment",
                          r"\bpaye\b", r"\bvat\b"]),

    # ---------------------------------------------------------------- saving & investing
    ("Saving & Investing", "ISA contribution", [r"\bisa\b", r"stocks ?& ?shares"]),
    ("Saving & Investing", "SIPP / pension contribution", [r"\bsipp\b", r"pension contrib",
                                                           r"\bpensionbee\b", r"\bnest\b"]),
    ("Saving & Investing", "GIA contribution", [r"\bgia\b", r"general invest"]),
    ("Saving & Investing", "TFSA contribution (SA)", [r"\btfsa\b", r"tax-?free savings"]),
    ("Saving & Investing", "Retirement annuity (SA)", [r"retirement annuity", r"\bra contrib"]),
    ("Saving & Investing", "Other savings", [r"trading ?212", r"\bfreetrade\b", r"\bvanguard\b",
                                             r"hargreaves", r"\baj bell\b", r"interactive investor",
                                             r"\beasyequities\b", r"\ballan gray\b", r"\bsygnia\b",
                                             r"\b10x\b", r"\bcoronation\b", r"\bmoneybox\b",
                                             r"\bnutmeg\b", r"\binvestengine\b"]),

    # ---------------------------------------------------------------- giving
    ("Giving", "Charity", [r"\bgift ?aid\b", r"\bcharity\b", r"\bjustgiving\b", r"\bmacmillan\b", r"\boxfam\b",
                           r"\bunicef\b", r"\bdonation\b", r"\bgofundme\b"]),
    ("Giving", "Tithing", [r"\btithe\b", r"\bchurch\b", r"\bparish\b"]),

    # ---------------------------------------------------------------- transfers
    ("Transfers", "Cash withdrawal", [r"\batm\b", r"cash withdrawal", r"\bcashpoint\b",
                                      r"\blink\b\s*atm", r"cash @", r"\bsaswitch\b"]),
    ("Transfers", "Between own accounts", [r"transfer to", r"transfer from", r"\bsavings pot\b",
                                           r"\bpot transfer\b", r"own account"]),
    ("Transfers", "Cross-border transfer", [r"\bwise\b", r"transferwise", r"\bremitly\b",
                                            r"western union", r"\bxe\.com\b", r"currencyfair",
                                            r"\bmukuru\b", r"\bmama money\b"]),
]


def seed_builtin_rules():
    """Rules live in the database so the household can edit or delete any of them."""
    if db.scalar("SELECT COUNT(*) FROM rules WHERE is_builtin=1", (), 0):
        return
    with db.tx() as c:
        for parent, child, patterns in BUILTIN:
            row = c.execute("SELECT id FROM categories WHERE parent=? AND name=?",
                            (parent, child)).fetchone()
            if not row:
                continue
            for p in patterns:
                if not isinstance(p, str):
                    continue
                c.execute(
                    "INSERT INTO rules(match_type,pattern,field,category_id,priority,is_builtin)"
                    " VALUES('regex',?,'description',?,50,1)", (p, row["id"]))


#: Built-in rules added or corrected after the first release.
#:
#: `seed_builtin_rules()` does nothing once a database has any built-in rule, and it
#: is right not to: re-running it would resurrect rules the household deleted. So a
#: correction made later needs its own recorded step, or it only ever reaches new
#: installs while every existing copy keeps the bug.
#:
#: (key, old pattern or None, new pattern, parent, child)
#:
#: A correction is applied ONLY if the rule is still exactly as it shipped. Once
#: somebody has edited it, it is theirs, and quietly rewriting it would be worse than
#: leaving the bug alone.
RULE_UPDATES = [
    # "NEXT DAY DELIVERY CHARGE" filed as clothing. `next` is one of the commonest
    # words in English and had no business being matched bare.
    ("next-needs-context", r"\bnext\b", r"\bnext\b(?=\s*(retail|plc|stores?|$))",
     "Shopping", "Clothing"),
    # "GIFT AID DONATION" filed as a present.
    ("gift-not-gift-aid", r"\bgift\b", r"\bgift(?!\s*aid)\b", "Shopping", "Gifts given"),
    ("gift-aid-is-charity", None, r"\bgift ?aid\b", "Giving", "Charity"),
    # The old lookahead only looked forward, so "PETROL STATION GAS" — station before
    # gas — was filed as a utility bill.
    ("gas-not-a-forecourt", r"\bgas\b(?!.*station)",
     r"^(?!.*(petrol|fuel|station|garage|forecourt)).*\bgas\b", "Utilities", "Gas"),
    # Amazon and eBay move out of Electronics into the new general category.
    ("amazon-is-general", r"\bamazon\b", r"\bamazon\b(?! ?prime)",
     "Shopping", "Household & general"),
    ("ebay-is-general", r"\bebay\b", r"\bebay\b", "Shopping", "Household & general"),
    ("takealot-is-general", r"\btakealot\b", r"\btakealot\b", "Shopping", "Household & general"),
    ("argos-is-general", r"argos", r"argos", "Shopping", "Household & general"),
    ("amzn-general", None, r"\bamzn(?! ?prime)", "Shopping", "Household & general"),
    ("prime-is-a-subscription", None, r"amazon ?prime", "Subscriptions", "Music & video"),
    ("prime-video", None, r"\bprime video\b", "Subscriptions", "Music & video"),
    # Dual-fuel suppliers move out of Electricity into Energy. Their rules keep their
    # ids, which is what keeps them ahead of the bare \bgas\b pattern for "BRITISH GAS".
    ("octopus-is-energy", r"octopus energy", r"octopus energy", "Utilities", "Energy"),
    ("british-gas-is-energy", r"british gas", r"british gas", "Utilities", "Energy"),
    ("edf-is-energy", r"edf energy", r"edf energy", "Utilities", "Energy"),
    ("eon-is-energy", r"e\.?on", r"e\.?on", "Utilities", "Energy"),
    ("ovo-is-energy", r"ovo energy", r"ovo energy", "Utilities", "Energy"),
    ("scottish-power-is-energy", r"scottish power", r"scottish power", "Utilities", "Energy"),
    ("sse-is-energy", r"sse\b", r"sse\b", "Utilities", "Energy"),
    ("utilita-is-energy", r"utilita", r"utilita", "Utilities", "Energy"),
    ("shell-energy", None, r"shell energy", "Utilities", "Energy"),
    ("so-energy", None, r"so energy", "Utilities", "Energy"),
    ("utility-warehouse", None, r"utility warehouse", "Utilities", "Energy"),
    # A supermarket forecourt is fuel, not the weekly shop.
    ("supermarket-forecourt", None, r"(tesco|sainsbury\w*|asda|morrisons|waitrose|costco|pick n pay|engen|sasol)\b.{0,20}\b(petrol|fuel|filling|forecourt|pfs)\b", "Transport", "Fuel"),
    # The general shops that had nowhere to go at all.
    ("general-shops", None, r"john lewis", "Shopping", "Household & general"),
    ("general-shops-2", None, r"\bwilko\b", "Shopping", "Household & general"),
    ("general-shops-3", None, r"home bargains", "Shopping", "Household & general"),
    ("general-shops-4", None, r"\bb ?& ?m\b", "Shopping", "Household & general"),
    ("general-shops-5", None, r"poundland", "Shopping", "Household & general"),
    ("general-shops-6", None, r"\bpound ?stretcher\b", "Shopping", "Household & general"),
    ("general-shops-7", None, r"robert dyas", "Shopping", "Household & general"),
    ("general-shops-8", None, r"\btk ?maxx\b", "Shopping", "Household & general"),
    ("general-shops-9", None, r"\bhobbycraft\b", "Shopping", "Household & general"),
]


def apply_rule_updates() -> int:
    """Bring an existing database's built-in rules up to date, once each."""
    changed = 0
    for key, old, new, parent, child in RULE_UPDATES:
        if db.meta_done("rule:" + key):
            continue
        cid = db.category_id(parent, child)
        if cid is None:
            # The section was renamed or removed by the household. Their call; skip it
            # for good rather than recreating something they got rid of.
            db.mark_done("rule:" + key)
            continue
        with db.tx() as c:
            if old is not None:
                row = c.execute("SELECT id FROM rules WHERE is_builtin=1 AND pattern=?",
                                (old,)).fetchone()
                if row:
                    c.execute("UPDATE rules SET pattern=?, category_id=? WHERE id=?",
                              (new, cid, row["id"]))
                    changed += 1
            else:
                existing = c.execute("SELECT id FROM rules WHERE pattern=?", (new,)).fetchone()
                if not existing:
                    c.execute("INSERT INTO rules(match_type,pattern,field,category_id,"
                              "priority,is_builtin) VALUES('regex',?,'description',?,50,1)",
                              (new, cid))
                    changed += 1
        db.mark_done("rule:" + key)
    return changed


def _rules() -> list[dict]:
    return db.rows("SELECT r.*, c.parent, c.name FROM rules r "
                   "JOIN categories c ON c.id = r.category_id "
                   "ORDER BY r.priority DESC, r.is_builtin ASC, r.id ASC")


def _match(rule: dict, text: str, amount: float) -> bool:
    pat = rule["pattern"]
    mt = rule["match_type"]
    try:
        if mt == "regex":
            return re.search(pat, text, re.I) is not None
        if mt == "startswith":
            return text.lower().startswith(pat.lower())
        if mt == "exact":
            return text.strip().lower() == pat.strip().lower()
        if mt == "amount":
            return abs(abs(amount) - abs(float(pat))) < 0.005
        return pat.lower() in text.lower()
    except re.error:
        return False


def categorise_account(account_id: int, only_uncategorised: bool = True) -> int:
    where = "account_id=?"
    params = [account_id]
    if only_uncategorised:
        where += " AND (category_id IS NULL OR category_source='auto')"
    txs = db.rows(f"SELECT id, account_id, description, merchant, amount, category_source, "
                  f"is_split FROM transactions WHERE {where} AND IFNULL(is_split,0)=0",
                  tuple(params))
    return _apply(txs)


def categorise_all(only_uncategorised: bool = True) -> int:
    where = "1=1"
    if only_uncategorised:
        where = "(category_id IS NULL OR category_source='auto')"
    txs = db.rows(f"SELECT id, account_id, description, merchant, amount, category_source, "
                  f"is_split FROM transactions WHERE {where} AND IFNULL(is_split,0)=0")
    return _apply(txs)


def _apply(txs: list[dict]) -> int:
    rules = _rules()
    if not rules:
        return 0
    uncat = db.uncategorised_id()
    n = 0
    hits: dict[int, int] = {}
    with db.tx() as c:
        for t in txs:
            if t.get("category_source") == "manual":
                continue
            # A split parent is excluded from every total, so categorising it changes
            # nothing and only makes the screen disagree with itself. Its parts were
            # categorised by hand and are already skipped by the line above.
            if t.get("is_split"):
                continue
            text = f"{t['description'] or ''} {t.get('merchant') or ''}"
            chosen = None
            for r in rules:
                if r["account_id"] and r["account_id"] != t.get("account_id"):
                    continue
                if _match(r, text, t["amount"] or 0):
                    chosen = r
                    break
            cid = chosen["category_id"] if chosen else uncat
            src = "rule" if chosen and not chosen["is_builtin"] else ("auto" if chosen else "auto")
            is_transfer = 1 if (chosen and chosen["parent"] == "Transfers") else 0
            c.execute("UPDATE transactions SET category_id=?, category_source=?, is_transfer=? "
                      "WHERE id=?", (cid, src, is_transfer, t["id"]))
            if chosen:
                hits[chosen["id"]] = hits.get(chosen["id"], 0) + 1
            n += 1
        for rid, k in hits.items():
            c.execute("UPDATE rules SET hits = hits + ? WHERE id=?", (k, rid))
    return n


def set_category(transaction_id: int, category_id: int, make_rule: bool = False) -> dict:
    tx = db.one("SELECT * FROM transactions WHERE id=?", (transaction_id,))
    if not tx:
        raise ValueError("transaction not found")
    with db.tx() as c:
        cat = c.execute("SELECT parent FROM categories WHERE id=?", (category_id,)).fetchone()
        c.execute("UPDATE transactions SET category_id=?, category_source='manual', is_transfer=? "
                  "WHERE id=?",
                  (category_id, 1 if cat and cat["parent"] == "Transfers" else 0, transaction_id))
    applied = 0
    if make_rule:
        key = _rule_key(tx["merchant"] or tx["description"])
        with db.tx() as c:
            c.execute("INSERT INTO rules(match_type,pattern,field,category_id,priority,is_builtin)"
                      " VALUES('contains',?,'description',?,200,0)", (key, category_id))
        applied = backfill(key, category_id)
    return {"ok": True, "backfilled": applied}


def _rule_key(text: str) -> str:
    """Strip the noise banks add — card numbers, dates, reference codes."""
    t = re.sub(r"\b\d{2}[/-]\d{2}([/-]\d{2,4})?\b", " ", text or "")
    t = re.sub(r"\b\d{4,}\b", " ", t)
    t = re.sub(r"\b(card|ref|txn|pos|purchase|payment|dd|so|bp|ft)\b", " ", t, flags=re.I)
    t = re.sub(r"[^\w &'-]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    words = t.split()
    return " ".join(words[:3]) if words else (text or "")[:24]


def backfill(pattern: str, category_id: int) -> int:
    rows = db.rows("SELECT id, description, merchant FROM transactions "
                   "WHERE category_source != 'manual'")
    n = 0
    with db.tx() as c:
        cat = c.execute("SELECT parent FROM categories WHERE id=?", (category_id,)).fetchone()
        tflag = 1 if cat and cat["parent"] == "Transfers" else 0
        for r in rows:
            hay = f"{r['description'] or ''} {r['merchant'] or ''}".lower()
            if pattern.lower() in hay:
                c.execute("UPDATE transactions SET category_id=?, category_source='rule', "
                          "is_transfer=? WHERE id=?", (category_id, tflag, r["id"]))
                n += 1
    return n


def detect_internal_transfers(window_days: int = 4) -> int:
    """Pair up 'out of account A' with 'into account B' so budgets don't double-count."""
    # Neither half of a split may be paired: the parent because it is not counted at
    # all, and a part because a £50 fragment of a £100 shop matching some other £50 is a
    # false positive that silently removes real spending from the budget.
    rows = db.rows("""SELECT id, account_id, posted_on, amount FROM transactions
                      WHERE transfer_pair IS NULL AND ABS(amount) > 0.009
                        AND IFNULL(is_split, 0) = 0 AND split_of IS NULL
                      ORDER BY posted_on""")
    by_amount: dict[float, list[dict]] = {}
    for r in rows:
        by_amount.setdefault(round(abs(r["amount"]), 2), []).append(r)
    paired = 0
    cat = db.category_id("Transfers", "Between own accounts")
    with db.tx() as c:
        for amount, group in by_amount.items():
            outs = [g for g in group if g["amount"] < 0]
            ins = [g for g in group if g["amount"] > 0]
            used = set()
            for o in outs:
                for i in ins:
                    if i["id"] in used or i["account_id"] == o["account_id"]:
                        continue
                    d1 = o["posted_on"]
                    d2 = i["posted_on"]
                    if abs((_d(d1) - _d(d2)).days) <= window_days:
                        c.execute("UPDATE transactions SET transfer_pair=?, is_transfer=1, "
                                  "category_id=COALESCE(?, category_id) WHERE id=?",
                                  (i["id"], cat, o["id"]))
                        c.execute("UPDATE transactions SET transfer_pair=?, is_transfer=1, "
                                  "category_id=COALESCE(?, category_id) WHERE id=?",
                                  (o["id"], cat, i["id"]))
                        used.add(i["id"])
                        paired += 1
                        break
    return paired


def _d(s: str):
    import datetime as _dt
    return _dt.date.fromisoformat(s[:10])
