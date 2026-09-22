# Connecting each institution

Mittens & Pence shows this in the app when you pick an institution. It's written out here too,
so you can read it before you start and know what you're in for.

---

## The short version

| | UK | South Africa |
|---|---|---|
| **Monzo, Starling** | **Their own free APIs** — connect once, syncs by itself | — |
| Other current accounts, credit cards | **Statement upload.** They support Open Banking, but every provider that can reach them charges a business subscription | **Statement upload** — no mandated open banking yet |
| Savings, building societies | Statement upload | Statement upload |
| Investment platforms | Statement upload — **except Trading 212 and Interactive Brokers**, which have their own APIs | Statement upload — **except Investec**, which does |
| Pensions | Usually a value typed in once a year | Same |

**If you only do one thing:** connect Monzo or Starling if you bank there, wire up
Trading 212, and upload the rest once a month. That's ninety per cent of the value for
about a minute of work.

---

## Why most UK banks are a statement upload

This is the honest version, and it took a round of beta testing to establish.

Every UK bank on the list supports Open Banking. That is not the obstacle. The obstacle
is that a bank will only hand data to an FCA-registered provider, and **there is no
provider a household can register with for free**:

* **TrueLayer** — a free console account is *sandbox only*. It presents a list of
  fake banks, and shows an orange **"Testing mode active"** banner. Reaching a real
  bank needs a paid plan. If you ever see that banner, do not enter real banking
  details: that flow does not go to your bank.
* **Enable Banking** — genuinely free for your own accounts, and would have been
  perfect, but its application form covers the **EEA only**. There is no United Kingdom
  on the list and no South Africa. Verified against the live form, August 2026.
* **GoCardless (ex-Nordigen)** — the free tier is closed to new sign-ups.
* **Plaid, Yapily, Tink, Salt Edge** — commercial, business onboarding.

So Mittens & Pence does not recommend an automatic route for those banks. It still *lists*
Open Banking as something the bank supports, and if you already pay for an aggregator
there is a one-line link on the setup screen to switch a connection over to it. But the
default is the statement upload, because that is the thing that actually works.

The rule the app enforces, and that the test suite checks: **Mittens & Pence never recommends a
route you can't take.** A provider has to be self-serve, have code behind it, *and* be
free to a household before it can be the suggestion.

---

## Monzo — its own API, free

Monzo publishes a developer API meant for connecting to your own account. No aggregator,
no plan, no sandbox banner.

1. Go to **developers.monzo.com** and sign in with the email on your Monzo account.
   You approve the sign-in from the Monzo app.
2. **Clients → New OAuth Client.**
3. **Name:** anything — "Mittens & Pence" is fine. It is only ever shown to you.
4. **Redirect URL:** `http://127.0.0.1:8765/oauth/callback` — the setup screen shows
   the exact address with a Copy button, and tells you if Mittens & Pence is on another port.
5. **Confidentiality: CONFIDENTIAL.** This is what lets Mittens & Pence refresh its token by
   itself instead of asking you to log in every few hours.
6. Copy the **Client ID** and **Client Secret** into Mittens & Pence and press **Save & link**.
7. Approve in the browser — **and then approve the second prompt in your Monzo app.**
   Until you do, Monzo answers every request with 403. Mittens & Pence says so in plain English
   rather than showing you the bare error.

**One quirk worth knowing:** Monzo serves your whole history for five minutes after you
authorise, and only the last 90 days after that. So the first sync pulls everything and
later syncs top up. Mittens & Pence keeps its own history, so this works out — but it means the
first sync is the one that matters. Do it straight after linking.

---

## Starling — a personal access token, free

The simplest connection in the app. No OAuth, no redirect URL, no 90-day re-approval.

1. Register at **developer.starlingbank.com** and sign in.
2. Open **Personal Access** (it may be under your account menu).
3. Create a token. **Name:** anything — "Mittens & Pence" is fine.
4. Tick these read scopes and no others: `account:read`, `balance:read`,
   `transaction:read`, `space:read`.
5. Copy the token into Mittens & Pence. That's it.

---

## TrueLayer — only if you already pay for it

Kept in the app for completeness. A free console account will not reach your bank; see
above.

**What "paid" means, as far as anyone can tell from outside.** TrueLayer publishes no
prices. Their help centre's entire answer to "I want to go live and disable the testing
banner" is *"please contact our Sales team to explore the best subscription options for
your needs"* — it is a sales-gated B2B subscription with a base monthly fee plus usage,
sold after commercial qualification and business (KYB) checks. The free sandbox is
unlimited and permanent, and the banner disappears only "once you become a paid customer
and move to live access". For scale: Plaid, the nearest comparable, has no self-serve
tier in the UK at all, with monthly minimums commonly quoted around $1,000–$3,000. This
is priced for a fintech, not a household.

If you do hold a paid plan:

1. Sign in at `console.truelayer.com`.
2. **Create application.** Name: **Mittens & Pence** — it appears on your own bank's consent
   screen and nowhere else, so any name works.
3. **Settings → Redirect URIs → Add** → paste `http://127.0.0.1:8765/oauth/callback`
   exactly. (Mittens & Pence's setup screen has a Copy button, and names the port if it differs.)
4. Copy the **Client ID** and **Client Secret** — the secret is shown once.
5. In Mittens & Pence: Connections → your bank → paste both → **Save & link**.

Consent lasts up to 90 days. Mittens & Pence warns you ten days before it lapses.

**Is `http://127.0.0.1:8765/oauth/callback` safe without https?** Yes, and it is what
the standard asks for. `127.0.0.1` is your own machine talking to itself — the request
never reaches a network, so there is nothing in between to encrypt against. HTTPS there
would also need a certificate no authority will issue for an address every computer in
the world shares. RFC 8252, the OAuth spec for native apps, specifies exactly this
pattern, which is why banks and providers accept it. Your actual bank login happens on
your bank's own **https** page; only the final "here is the code" hop comes back to the
loopback address.

---

## Enable Banking — EEA only, not usable from here

Left in the code for anyone with an EEA account, but no UK or South African institution
routes to it. Its application form has no United Kingdom and no South Africa, so it
cannot reach a bank Mittens & Pence covers, however good the free tier is.

If you are setting one up for an EEA account, two things that confuse everyone:

* **Type of authorisation.** Choose the option that says you *rely on an authorised
  third party* — that is Enable Banking's own licence. Do **not** choose "own
  authorisation"; that means you personally hold a regulator's licence.
* **AISP vs PISP.** AIS/AISP is *account information* — read-only, which is all
  Mittens & Pence wants. PIS/PISP is *payment initiation* — moving money. Mittens & Pence never
  initiates a payment, so leave PISP unticked.

---

## Crypto exchanges — mostly free APIs, and Mittens & Pence missed this at first

Mittens & Pence originally marked all thirteen exchanges "download a CSV". That was wrong. Most
major exchanges hand any individual a **read-only API key** from the account settings
page, free, no business account, no contract — the same deal Monzo and Starling offer.
All verified against each exchange's own documentation in September 2026.

| Exchange | Route | Where the key lives | Notes |
|---|---|---|---|
| **Kraken** | Automatic | Settings → API | Best scoping of the lot: tick *Query Funds* only |
| **Binance** | Automatic | Account → API Management | Read-only is the default; needs 2FA + KYC first |
| **Coinbase** | Automatic | portal.cdp.coinbase.com | ECDSA key, not Ed25519. API access is **off by default** — switch it on |
| **Luno** | Automatic | Settings → API keys | Has a single "Read-only access" preset |
| **VALR** | Automatic | Account → API Keys | Tick *View access*; 2FA must be on or it refuses |
| Gemini | Listed | Settings → API | Free, self-serve — pick the **Auditor** role. No Mittens & Pence client yet |
| Bitstamp | Listed | Settings → API Access | Free, self-serve. No Mittens & Pence client yet |
| Bitpanda | Listed | Web app → API Key | Free, self-serve, web only. No Mittens & Pence client yet |
| Uphold | Upload | — | Production API needs a **business account** |
| Altify | Upload | — | Retail app, no developer surface at all |
| AltCoinTrader | Upload | — | An API exists; read-only scoping unverified |
| Ovex | Upload | — | Appears to have pivoted to institutional clients |
| ~~Ziglu~~ | — | — | **Entered special administration July 2025.** Listed only so an old balance can be recorded by hand |

**Always tick read-only.** Every set of steps in the app says so. A key that cannot
trade or withdraw cannot cost you anything if it leaks, and Mittens & Pence never needs more.

**What you get, and what you don't.** An exchange tells Mittens & Pence what you *hold*, never
what you *paid*. So a freshly connected exchange shows quantities and current values but
a dash in the return column — Mittens & Pence will not invent a cost basis and report your whole
holding as profit. Upload the trade history once and the returns fill in properly.

---

## Brokers with an API that doesn't help

Three UK brokers publish genuinely free, self-serve APIs that turn out to be no use for
an ISA. Mittens & Pence lists them and explains why rather than pretending they don't exist:

* **IG** — keys are issued instantly at labs.ig.com, and reach **spread betting and CFD
  accounts only**. A share dealing account or ISA returns `stockbroking-not-supported`.
* **Saxo Markets** — genuinely self-serve (free simulation account, then a live app
  request that is usually auto-approved), but it needs a funded live account, and
  whether a UK Stocks & Shares ISA appears through it is undocumented.
* **eToro** — API Key Management under Settings → Trading, free for eligible users;
  whether UK-entity accounts are eligible isn't published.

Everything else — Freetrade, Hargreaves Lansdown, AJ Bell, interactive investor,
Vanguard, InvestEngine, Lightyear, CMC, Nutmeg, Moneybox, Plum, Chip, Wealthify,
Moneyfarm, Bestinvest, Charles Stanley, Fidelity, Fineco — has either no API at all or a
partner programme a household will not be accepted onto. Statement upload it is.

---

## Trading 212

The one broker in this list with a proper self-serve API, and the best connection in
the whole app — holdings, dividends, cash and orders, all automatic.

1. Trading 212 app or web app → **Settings → API (Beta)** → **Generate API key**.
2. **Tick the READ permissions only:** Account, Portfolio, History, Metadata.
   **Leave "Orders - Execute" and "Pies - Write" unticked.** Mittens & Pence only ever reads. A
   key that cannot trade cannot cost you anything if it leaks, and there is no reason to
   hand a program that looks at numbers the ability to buy and sell.
3. Leave the **IP restriction** box empty unless you have a fixed IP. A restricted key
   stops working the moment your address changes.
4. Copy **both values** — newer keys come as an API **key** and a **secret**, and both are
   needed. They are shown once. Older keys have a single value; paste it as the key and
   leave the secret blank.
5. Paste them in and press Save & test.

One key per account. If you hold both an ISA and an Invest account, generate a key for
each and add a connection per account. SIPPs aren't supported by their API.

### A correction, and what a 401 really means

Earlier versions of this guide told you to tick **every** permission, on the basis that
Trading 212 answers 401 unless they are all ticked. **That was wrong, and I'm sorry — it
meant telling you to grant trade-execution rights to an app that only reads.**

The claim traces back to AlgoCloud's setup page. AlgoCloud is an automated *trading*
platform, so its own account-connection step genuinely needs order permissions; that
instruction was never a statement about the Trading 212 API. Trading 212 added the
ability to untick "Orders - Execute" and "Pies - Write" in October 2025 precisely so
read-only keys are possible.

The two codes mean different things:

* **403** — the credentials were accepted, but the key lacks a permission that endpoint
  needs. *This* is the permission error. Mittens & Pence needs Account, Portfolio and History.
* **401** — the credentials themselves were rejected. Not a permission problem.

The real cause of the 401 in beta was that the API had moved underneath Mittens & Pence: it now
uses HTTP Basic with a key **and** a secret, and several paths changed
(`/equity/account/info` and `/equity/account/cash` merged into `/equity/account/summary`,
`/equity/portfolio` became `/equity/positions`, and the `/history/*` paths moved under
`/equity/`). Mittens & Pence now speaks the current API, still recognises older keys, and tries
every authentication style before it says anything about permissions.

**If you still get a 401:** check you pasted both the key and the secret in full, that
the key matches the account type you're pointing at (live vs practice), and that you
haven't since regenerated it. If several old keys are lying around in the app, delete
them all and make one.

---

## Investec (South Africa)

The exception to everything above: Investec Private Banking clients can enable
Programmable Banking and get their own credentials, free.

1. Log in to Investec Online.
2. **Programmable Banking** → enable it for your account.
3. Create API credentials. You get three values: **Client ID**, **Client Secret** and
   an **API key** (`x-api-key`).
4. Paste all three into Mittens & Pence.

Mittens & Pence only reads — accounts, balances, transactions. It never initiates a payment.

---

## Interactive Brokers

Not a live API in the usual sense; you build a report and Mittens & Pence fetches it.

1. Client Portal → **Performance & Reports → Flex Queries**.
2. Create an **Activity Flex Query** including Open Positions, Trades, Cash Report and
   Change in Dividend Accruals. Set the format to **XML**.
3. Note the **Query ID**.
4. Under **Flex Web Service**, generate a token (valid a year). Note it.
5. Paste the token and query ID into Mittens & Pence.

---

## South Africa — why it's uploads

South Africa has no regulatory equivalent of PSD2, so there's no obligation on banks
to open their data and no consumer-facing consent flow. Aggregators do exist and do
reach the big banks:

* **Stitch** — the widest South African coverage.
* **Mono** — pan-African, now part of Flutterwave.
* **Salt Edge** — some South African reach.

All three onboard businesses. None sells to individuals. If you have credentials
through work, Mittens & Pence will use them; otherwise the honest answer is the monthly
upload, and it genuinely takes under a minute.

**And no, this isn't about to change.** The SARB's 2020 consultation on open banking led
to no rules. The FSCA's 2024 Open Finance recommendations propose a **voluntary** regime
first, with anything mandatory "over the longer term" — its own roadmap runs consent and
risk frameworks to September 2026, and nothing has been enacted. Every South African bank
with an API programme (Standard Bank, Absa, Nedbank, FNB) built it for fintechs and
corporates: you onboard as a business, or through a relationship manager. Investec is the
sole exception and looks likely to stay that way.

**One Investec trap:** only the **main account holder** can create API credentials. If
you are a joint or secondary holder, the developer section will not offer them to you.

**Where to get each file**

| Bank | Where |
|---|---|
| Capitec | App or online → Statements → Download → CSV |
| FNB | Online → Statements → Download → CSV / OFX / QIF |
| Absa | Absa Online → Statements → Download → CSV / OFX / QIF |
| Standard Bank | Internet Banking → Statements → Download → CSV / OFX |
| Nedbank | Nedbank Online → Statements → Download → CSV / OFX |
| Discovery Bank | App → Statements → Download → CSV |
| TymeBank | App → Statements → email or download |
| Investec | Investec Online → Statements → Download (or use the API above) |
| EasyEquities | My Investments → Transactions / Holdings → Download CSV, per wallet |

OFX is the best format where it's offered — it's unambiguous, so nothing needs mapping.

### If the only option is a PDF

Plenty of banks — Nedbank, Discovery, several of the building societies — hand you a PDF
and nothing else. Mittens & Pence reads those too. Drop the PDF on the Import screen exactly like
a CSV.

It works by rebuilding the grid from where the words sit on the page, not by reading the
text top-to-bottom: a statement's meaning is in its columns, and "extract the text" throws
that away. Ruled tables are read from their own lines; unruled ones have their columns
inferred from the header and then re-fitted to where the numbers actually are, because
headers are usually left-aligned and money right-aligned. Descriptions that wrap onto a
second line are rejoined to their row, and a header repeated at the top of page 2 is
ignored.

**What it can't do: a scanned PDF.** If the statement is a photograph or a scan of paper,
there is no text in the file at all — only a picture — and no amount of cleverness will
find any. Mittens & Pence says so plainly rather than showing you an empty table. Two ways
forward: ask the bank for a CSV, OFX or QIF (almost all of them offer one, even when the
PDF is what they push at you), or run the file through an OCR tool first.

**Password-protected PDFs** — common for statements emailed to you — need opening once in
a PDF reader and saving as an unprotected copy. Banks usually set the password to your
date of birth or postcode.

Always glance at the preview before pressing Import. The column mapping is shown, and
nothing is saved until you confirm it.

---

## UK statement downloads

| Bank | Where |
|---|---|
| Monzo | App → Account → Export statement (CSV), or web.monzo.com |
| Starling | App → Account → Statements → Export CSV or OFX |
| Barclays | Online Banking → Statements → Export → CSV (Excel) or QIF |
| Lloyds / Halifax / BoS | Internet Banking → Statements → Export (CSV) |
| NatWest / RBS | Online Banking → Statements → Download → CSV / OFX / QIF |
| HSBC / first direct | Online Banking → Statements → Download → CSV / OFX / QIF |
| Santander | Online Banking → Statements → Download → CSV / Excel / QIF |
| Nationwide | Internet Bank → Statements → Download (CSV) |
| Revolut | App → Account → Statement → Excel / CSV |
| Amex | Statements & Activity → Download → CSV / QIF / OFX |

**Brokers**

| Platform | Where | What it gives |
|---|---|---|
| Freetrade | App → Profile → Activity feed → Export (emailed to you) | Everything: orders, dividends, interest, top-ups, splits |
| Trading 212 | App → Settings → History → Export CSV | Everything (but the API is better) |
| Hargreaves Lansdown | Account → Investments → Download, and History → Download | Holdings and transactions, separately |
| AJ Bell | Portfolio → Export, and Transaction history → Export | Holdings and transactions |
| interactive investor | Portfolio → Export CSV, Transactions → Download | Holdings and transactions |
| Vanguard | Transaction history / Holdings → Download CSV | Both |
| InvestEngine | Reports → Download portfolio / transactions | Both |
| eToro | Portfolio → History → Account statement → XLSX | Everything |

---

## Anything not listed

Pick **"Other / not listed"** at the bottom of the dropdown and upload whatever file
you have. The auto-mapper handles unknown layouts, and once you confirm the columns it
remembers them — the second month is a single click.

---

## Doing it once a month

Set a reminder for the 1st. Ten minutes, tops:

1. Open Mittens & Pence. Automatic connections sync themselves.
2. Download the two or three files that need it. Drop them in.
3. Dashboard → **Take snapshot** (or let it happen by itself).
4. Spreadsheets → **Build both**.

Or automate the whole thing — see the scheduled-task section of the README.
