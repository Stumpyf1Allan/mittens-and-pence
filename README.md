# Mittens & Pence

**A budget and investment app for your household.**

Your accounts, spending, budgets and investments in one place, with 275 UK and South
African banks and brokers built in. It keeps itself up to date and rebuilds two Excel
workbooks whenever you ask.

It runs on your own computer. There is no sign-in and no account — nothing goes anywhere
except to the banks and price feeds you connect yourself.

---

## Getting started

### The quick way — no building required

1. Install **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/).
   On Windows, tick **"Add python.exe to PATH"** on the first screen of the installer.
2. Double-click **`Start Mittens & Pence.bat`** (Windows) or **`Start Mittens & Pence.command`** (Mac).

The first run takes about a minute while it sets itself up. After that it opens
straight away. Mittens & Pence appears in its own window.

### The proper app (a real .exe)

Double-click **`build\build_windows.bat`** (Windows) or run
**`bash build/build_macos.sh`** (Mac). It installs what it needs into its own private
environment, runs the tests, and stops if any of them fail — so you can't ship something
broken. Five minutes or so. You need Python 3.10+ on the machine you build on; nobody
you send it to needs anything at all.

It makes two things:

| | | |
|---|---|---|
| `dist\Mittens & Pence\Mittens & Pence.exe` | a folder, starts in about a second | **keep this one** for your own machine |
| `dist\send\Mittens & Pence.exe` | one self-contained 40 MB file | **send this one**, with the `Read me first.txt` beside it |

The single file unpacks itself to a temporary folder each time it runs — slower to start,
but it's one thing you can put on a Drive link and describe over the phone.

**PyInstaller cannot cross-compile:** a Windows `.exe` has to be built on Windows and a
Mac app on a Mac — and a Mac is really two machines, because an Apple Silicon build will
not start at all on an Intel one. You don't need three computers, though. Pushing a tag
runs **[.github/workflows/release.yml](.github/workflows/release.yml)**, which borrows a
Windows box and both kinds of Mac from GitHub, runs the whole test suite on each, and
leaves a draft release with all three downloads and the update manifest on it. See
**[docs/PUBLISHING.md](docs/PUBLISHING.md)**.

**Sending it:** a link (OneDrive, Google Drive, Dropbox), never an email attachment —
every mail provider blocks `.exe` outright. Full instructions, including what your family
will see on first run and what to tell them, are in **[docs/SHARING.md](docs/SHARING.md)**.

> Windows says **"Windows protected your PC"** the first time — normal for anything not
> code-signed. **More info → Run anyway**. On a Mac it refuses outright, and you allow it
> in **System Settings → Privacy & Security → Open Anyway**. Both are on the read-me that
> goes out with the file, along with how to tell an Apple-chip Mac from an Intel one.

### Just having a look

Press **"Have a look with sample data first"** on the welcome screen. It builds a
made-up household — two ISAs, a current account in each country, a year of spending,
some budgets — so you can click around before connecting anything real. Settings →
**Clear everything** wipes it when you're done.

---

## How it works

### 1. Pick your bank or broker

Every UK and South African bank, building society, broker, platform, pension and
crypto exchange worth naming is in the list — **275 institutions**. Type a few
letters; abbreviations and old names work too ("FNB", "T212", "HL", "Clydesdale").

### 2. Mittens & Pence tells you what's actually possible

This is the part that matters, and it's honest rather than optimistic:

| What you'll see | What it means |
|---|---|
| **Connect automatically (their API)** | **Monzo** and **Starling** for UK current accounts; **Trading 212** and **Interactive Brokers** for investments; **Investec** in South Africa; and **Kraken, Binance, Coinbase, Luno and VALR** for crypto. All free, all self-serve: make a read-only key once, paste it in, done. With Trading 212, tick the **read** permissions only — it never needs order-execute rights. |
| **Download a statement and upload it** | Every other bank, and every investment platform bar the ones above. A minute a month. Mittens & Pence works out the columns the first time and remembers them. |
| **Type the balance in yourself** | A house, a pension statement, Premium Bonds. Mittens & Pence keeps the monthly history. |

**Where did Open Banking go?** Every UK bank here supports it — but a bank only shares
data with a registered provider, and there is no provider a household can register with
for free. TrueLayer's free console is sandbox only (it warns you not to enter real bank
details). Enable Banking is genuinely free but covers the EEA only — no United Kingdom
on its form at all. GoCardless's free tier is closed to new sign-ups. So Mittens & Pence lists
Open Banking as something your bank supports, and offers a one-click switch if you
already pay for an aggregator, but it does not *recommend* a route you can't take.
Monzo and Starling are the two UK banks that let you in directly, and Mittens & Pence uses that.

South Africa gets the same treatment: there is no mandated open banking there yet.
Aggregators (Stitch, Mono) reach the big banks but onboard businesses, not households —
so the practical route is a monthly statement upload, with Investec the one exception.

*First time you open it, Mittens & Pence walks you round the tabs once — nine steps, skippable,
and replayable any time from Settings → Show me round again.*

### 3. Drop the file in

Drag any statement onto the Import screen. CSV, Excel, OFX/QFX, QIF or PDF. Mittens & Pence finds
the header row (banks love putting three lines of junk above it), works out which
column is the date and which is the amount, copes with `1,234.56` and `1.234,56` and
`41 000,00` and `(45.00)`, and shows you what it found before saving anything.

Re-uploading a file that overlaps last month's is safe. Duplicates are ignored.

Drop as many files as you like at once. Statements in a layout Mittens & Pence already knows,
from a file that names its own account, go straight in; the rest stop and ask, one at a
time, while the others carry on.

Formats it recognises outright: Monzo, Starling, Barclays, Lloyds/Halifax/Bank of
Scotland, NatWest/RBS, HSBC/first direct, Santander, Nationwide, Revolut, Amex, Wise,
PayPal, Capitec, FNB, Absa, Standard Bank, Nedbank, Investec, Freetrade, Trading 212,
EasyEquities, Hargreaves Lansdown, AJ Bell, interactive investor, Vanguard, eToro and
Interactive Brokers Flex. Anything else goes through the auto-mapper.

### 3a. Or type one in

Not everything arrives in a statement. Cash, a payment before the statement catches up,
an account no bank will export at all. **Transactions → Add a transaction.**

You never type a minus sign — *money out* and *money in* are two buttons, because a
stray minus is the commonest way to enter an amount backwards. Type "Tesco" and
Groceries is already selected by the time you reach the amount. "Save and add another"
keeps the account and the date, so a handful of cash entries goes in one after another.

Tick **transfer between my own accounts** and both halves get written, so the money
leaves one account and arrives in the other. If the two accounts are in different
currencies Mittens & Pence asks what actually arrived rather than inventing an exchange rate.

Typed entries are marked *typed in* and are the only ones you can edit or delete —
anything imported is kept exactly as the bank sent it, or re-uploading a file would stop
being safe.

**And they get out of the way.** Note a £40 cash withdrawal on the day; import the
statement three weeks later; the bank's version is kept, your note is removed, and the
import tells you it happened. One payment, one row, budgets that add up.

### 3b. Split a payment across categories

£100 at Tesco is rarely £100 of groceries. **Transactions → Split** on any row lets you
break one payment into as many parts as it really was — £50 groceries, £20 electronics,
£30 liquor — each with its own category.

The original row is never touched. It keeps the bank's figure and its fingerprint, sits
underneath the parts, and is excluded from every total — so nothing is counted twice and
re-importing that statement still changes nothing. Undo the split and it's one row again.

The parts have to add up to the penny. The screen shows what's left to allocate and won't
save until it reaches zero, because splitting £100 into £50 + £20 + £25 loses £5 of
spending that nobody would ever notice was missing.

### 4. It sorts the spending

Around 200 built-in rules cover both countries — Tesco and Checkers, Shell and Engen,
Octopus and Eskom, TfL and Gautrain, Discovery Health and Bupa. Anything it gets wrong
takes one click to fix, and it offers to remember the correction and apply it to
everything else that matches.

**Shopping or Food & Drink?** The line is *things you consume* against *things you buy
and keep*, and it's drawn by the **shop**, not by what was in the basket.

| | |
|---|---|
| **Food & Drink** | Groceries (the supermarket shop), Restaurants & takeaway, Coffee & snacks, Alcohol |
| **Shopping** | Clothing, Electronics, Household & general, Hobbies, Books & media, Gifts given |

So one Tesco payment is **Groceries** in full, even if a third of it was a frying pan
and a birthday card — the bank sends one amount and has no idea what was in the trolley.
When a shop like that matters, **split the payment**: open it in Transactions and divide
it across as many categories as you like. The parts have to add up to the penny, and the
original row is left exactly as the bank sent it.

*Household & general* is for the shop that sells everything — Amazon, eBay, John Lewis,
Wilko, Argos, Poundland. Guessing at a category from the name of a shop that sells
literally everything is how a nappy order ends up filed as electronics.

**Energy, Electricity or Gas?** Almost every British supplier sells gas and electricity
on one direct debit, and "BRITISH GAS DD 11223" tells you who sent it, not which fuel it
paid for. Those go to **Utilities › Energy**. *Electricity* and *Gas* are kept for the
bills that really are only one thing — Eskom, City Power, a prepaid meter, a Calor
delivery. And a supermarket forecourt is a petrol station that happens to share a name
with a shop: **Tesco Petrol is Fuel**, not groceries.

### 5. Set budgets and watch them

A budget per section (Food & Drink, Transport, Home…) or per line inside one
(Groceries). Mittens & Pence can propose figures from what you've actually been spending.
Each budget shows spent, remaining, whether you're ahead of pace for the month, and
what's left per day.

Sections aren't fixed. **Settings → Budget sections** adds, renames, merges and removes
them, and renaming one carries its budget across with it. Income, Transfers and Saving
can be renamed but not removed — the engine finds your salary and your internal
transfers through them, so deleting one would quietly turn your pay into spending.

**Already keep a budget in a spreadsheet?** Drop it on the Import screen with everything
else. Mittens & Pence looks through the workbook for a column of category names beside a column
of monthly amounts — usually under a heading with the word *budget* in it — and shows
you what it found: every category, what it's set to, and which section it will land in.
Nothing is written until you say so, and every line is editable on that screen.

It takes the **plan** only, never the spending. Spending is recomputed from your
statements; importing a sheet's own arithmetic alongside them would double-count every
pound. Totals and subtotals are left out — by name, and by arithmetic, so a total row
called something else is still caught. Negative figures come in as amounts to stay
under. Categories budgeted at zero, and anything that looks like income, arrive
unticked, with the reason next to them. A workbook with a tab per year offers all of
them and defaults to the latest.

Transfers between your own accounts never count as spending, and money into an ISA or
a TFSA is counted as *saving*, on its own line — otherwise moving £500 into an ISA
would look like both income and an expense.

### 5a. Add your mortgage

**Accounts → Add a mortgage.** A mortgage entered as an ordinary account is a number
with a minus sign: it makes net worth right and answers nothing else. Give Mittens & Pence the
balance, the rate and the payment and it works out the things that actually matter.

**What each payment is really doing.** £1,180 leaves the current account, but perhaps
£672 of that is interest and £508 comes off what you owe. Only the interest leaves the
household — the rest moved from your current account into the house, exactly like paying
into an ISA. Mittens & Pence counts it as saving rather than spending, says so on the Budgets
screen, and lets you switch that off if you'd rather see the whole payment as an outgoing.

**When the cheap rate ends.** Put in the fixed-until date and the rate it reverts to, and
Mittens & Pence tells you how many days are left and what the payment becomes — for most people
the single most consequential number in their finances, and one that appears on no bank
statement.

**What an overpayment is worth.** Not "the balance goes down by £200" — the months it
takes off the end and the interest it saves. £200 a month on a typical UK mortgage saves
four years and twenty thousand pounds. It also reminds you that lenders cap penalty-free
overpayments, because Mittens & Pence can't know your limit.

**Your equity.** Link the property (or type its value) and you get equity and loan-to-
value beside the debt.

**Balances are projected, and say so.** A mortgage statement arrives once a year. Between
statements Mittens & Pence projects from the last figure you actually saw and labels it, with the
date it was confirmed — rather than showing a year-old number as though it were today's.
Update it in one click when the statement arrives. Net worth uses the same projection, so
two screens never report two different debts.

If the payment and the rate on file don't add up — the payment doesn't cover the
interest, or it won't clear the debt by the end of the term — Mittens & Pence says so instead of
quietly showing whichever number you asked for.

### 6. Out come the spreadsheets

**Spreadsheets → Build both.** Two `.xlsx` files, with real formulas, not pasted
numbers — edit a cost by hand and everything recalculates.

* **Investments** — an Overall tab, one per wrapper
  (ISA, GIA, SIPP, TFSA…), one per platform, Sold, Sectors, and a Monthly history.
* **Banking & Budgets** — accounts, every transaction with its category, spending by
  month, and a budget tracker whose month selector actually recalculates.

Both open directly in Google Sheets — drag them into Drive. Or link your own Google
account once and Mittens & Pence publishes into Drive itself, replacing the same sheet each
time so anyone you've shared it with keeps their link.

### 7. A copy for your phone

**Export a Spreadsheet → A copy for your phone.** One HTML file with everything inside
it: net worth, this month's budgets, your accounts, and what you've spent lately, laid
out for a small screen. Put it in iCloud, Google Drive or Dropbox — or email it to
yourself — and open it on your phone. No internet needed, nothing sent anywhere. Add it
to the home screen and it behaves like an app.

It's a snapshot, and it says so at the top, in words. A stale balance that looks current
is worse than no balance at all.

There is no native iPhone or Android app, and there isn't going to be: it would mean
writing the whole thing again in another language, paying two app stores for the
privilege, and keeping three versions in step — a great deal of work for a screen you
glance at. This does the useful part in one file.

---

## Keeping everyone's copy up to date

If you're the person who builds and sends this out, `docs/PUBLISHING.md` walks through
it click by click. The short version: a free GitHub account, and every release is one
page where you drop the new `.exe` and a small `latest.json` that `tools/publish.py`
writes for you. Every copy reads that file about once a day, at an address that never
changes.

When a copy finds a newer version it downloads it in the background, checks the download
against the SHA-256 fingerprint you published, and then puts a strip at the top of the
window saying it's ready. The person closes the app and runs the new file. Their data is
untouched — it lives in a separate folder that every version reads.

**It never replaces itself.** The executable isn't code-signed, and an unsigned program
that reaches out to the internet, downloads another program and silently overwrites
itself is behaving exactly like malware — every antivirus on the family's machines would
agree, and rightly. So it goes as far as it safely can and then asks a person.

A file that fails its checksum is deleted rather than kept, and nothing downloaded is
ever executed by the app. **Settings → Updates** shows the current state, checks on
demand, and turns the whole thing off.

---

## Why not just use a spreadsheet?

Mittens & Pence grew out of one, and inherited the lessons. Five things a spreadsheet makes hard
that stop being your problem here:

* **Row ceilings.** A formula like `SUM(L11:L41)` silently caps a platform at thirty
  holdings — the thirty-first is simply not counted, and nothing tells you. Mittens & Pence has
  no cap.
* **Quarterly rebuild rituals.** Delete the activity tab, export a fresh file, import it,
  rename it, repair the references. Mittens & Pence keeps the history in its own database, so an
  import adds to what is already there.
* **Live-price formulas that break offline** and leave `#N/A` scattered through the
  file. Prices are fetched when you press Refresh and written in as numbers, so the
  workbook opens with real figures even with no internet.
* **Wiring up cross-sheet references by hand** for every new holding. Type a symbol and
  the row fills itself in.
* **A wrong column in one `SUM`** that nobody spots for a year. Every figure Mittens & Pence
  produces is covered by a test.

You still get the workbooks — Mittens & Pence builds them for you, with real formulas rather
than pasted numbers, so anything you want to poke at by hand still recalculates.

---

## Who's in the household

**Settings → Who's in the household.** These names are labels on accounts — whose ISA is
whose, whose salary that is — so you can filter by person and set a budget per person.

**It is not a login.** Mittens & Pence has no sign-in, no user accounts and no passwords; the
whole thing runs on your computer against a file on your own disk. If someone else
installs it, they get their own empty copy and are asked their own name on the first run.

Names can be renamed, recoloured, made the main person, or removed. Removing someone asks
where their accounts should go first, so the household doesn't quietly lose track of whose
things they are, and it won't remove the last person — a household needs someone in it.

---

## Where things are kept

| | Windows | Mac |
|---|---|---|
| Database, logs, backups | `%LOCALAPPDATA%\Mittens & Pence` | `~/Library/Application Support/Mittens & Pence` |
| Built spreadsheets | `…\Mittens & Pence\exports` | `…/Mittens & Pence/exports` |

One SQLite file holds everything. Settings → **Back up now** copies it with a
timestamp; twenty backups are kept, and one is taken automatically before every
import. To move to a new computer, copy that folder across.

**Credentials** (API keys, OAuth tokens) are encrypted with AES-GCM, using a key held
in Windows Credential Manager or the macOS Keychain. If no keyring is available it
falls back to a protected file, and Settings says which is in use. Open Banking never
involves a password at all — you log in on the bank's own site.

---

## Running it from the command line

```
python run_kestrel.py                 # open the app
python run_kestrel.py --no-window     # just the server, at 127.0.0.1:8765
python run_kestrel.py --demo          # load the sample household first
python run_kestrel.py --sync          # sync every connection, then exit
python run_kestrel.py --export both   # rebuild both workbooks, then exit
```

The last two make a scheduled monthly refresh easy — Task Scheduler on Windows, cron
on a Mac:

```
0 7 1 * *  cd /path/to/kestrel && .venv/bin/python run_kestrel.py --sync --export both
```

---

## If something goes wrong

**It won't start.** Look in `…\Mittens & Pence\logs\kestrel.log`. Most often Python is missing
or wasn't added to PATH.

**Light or dark.** The button top-right cycles Auto → Light → Dark. On Auto it follows
this computer's clock — light through the day, dark from 7pm to 7am — and turns over on
its own without needing a restart.

**Prices are all blank.** The app says *Offline* on the dashboard when it can't reach a
price feed. Press **Refresh prices** once you're back online. Individual instruments
can be given a price by hand: Investments → *edit* on the row.

**A holding shows the wrong price.** The symbol is probably ambiguous. Investments →
*edit* → set the **quote symbol** explicitly (`DGE.L`, `NPN.JO`, `BRK-B`).

**Cash on a broker account is negative.** The statement doesn't reach back far enough
to include the early deposits, so the sum comes out short. Download a longer history,
or type the platform's cash balance in on the Accounts screen. Mittens & Pence says so on the
account itself rather than hiding it.

**A statement imported into the wrong account.** Settings → restore the backup taken
just before it (`…\Mittens & Pence\backups`), or delete the account and import again.

**Everything looks wrong after an import.** Transactions → **Re-sort into categories**
rebuilds the categorisation from the rules.

---

## If you want to look under the bonnet

`python -m pytest tests -q` — 222 tests covering the file parsers, the column mapper, the
institution registry, the portfolio and mortgage maths, the budget logic, the credential
vault, the splitting rules and both workbook builders.

`docs/ARCHITECTURE.md` explains how the pieces fit together and why.
`docs/CONNECTING.md` has the setup detail for each bank and broker.
`docs/SPREADSHEETS.md` explains what every tab and column in the two workbooks means.
`docs/SHARING.md` covers building the app and sending it to someone else.

---

## Honest limitations

* **Automatic UK bank sync is Monzo and Starling only.** Both publish an API for your
  own account and cost nothing. Every other UK bank supports Open Banking on paper, but
  reaching it needs a registered provider and none of them are free to a household:
  TrueLayer's free tier is a sandbox of fake banks, Enable Banking's application form
  has no United Kingdom on it, and GoCardless has closed its free tier to new sign-ups.
  If you already pay for one, the setup screen will switch a connection over to it.
  Otherwise: monthly upload, about a minute.
* **South African automatic sync is not realistically available to a household.**
  Investec is the exception. For everyone else, plan on a monthly upload.
* **Freetrade has no API**, and neither does any other mainstream UK investment
  platform. Their activity export is excellent, though, and Mittens & Pence reads all of it —
  orders, dividends, interest, top-ups. IG, Saxo and eToro do publish free self-serve
  APIs, but IG's cannot see an ISA at all and the other two are unverified for UK ISAs,
  so Mittens & Pence names them and explains rather than promising anything.
* **A crypto exchange reports what you hold, never what you paid.** A freshly connected
  exchange shows quantities and values but a dash where the return should be. Mittens & Pence
  will not assume a zero cost and call the whole holding profit; upload the trade history
  once and the figures fill in.
* **Past market values can't be recovered perfectly.** When Mittens & Pence reconstructs the
  months before you installed it, it fetches month-end prices where the feed has them
  and falls back to what you paid where it doesn't. Reconstructed points are drawn
  hollow on the chart and labelled, so you always know which is which.
* **The app is not code-signed**, so Windows and macOS both warn the first time, and
  updates stop at "here is the verified file" rather than installing themselves. A
  certificate costs a few hundred pounds a year, which is hard to justify for something
  shared between a few people.
* **The phone copy is a snapshot, not an app.** It shows the figures as they were when
  you made it and never refreshes on its own.
* **Nothing here is financial advice**, and the figures are only as good as the
  statements you feed it. Check anything that matters against your actual accounts.
