# Getting Mittens & Pence to the family

Two questions, answered in order: **how to build the single file**, and **how to get it
to somebody**.

---

## The short version

1. On a **Windows** PC, double-click `build\build_windows.bat`.
   On a **Mac**, run `bash build/build_macos.sh`.
2. Wait about five minutes.
3. Send the two files from **`dist\send\`** — `Mittens & Pence.exe` and `Read me first.txt` —
   as a **link** (OneDrive, Google Drive, Dropbox, WeTransfer). Not as an email
   attachment; every mail provider blocks `.exe`.

That's it. The `.exe` is self-contained: about 40 MB, no folder, no installer, no Python
needed on their machine.

---

## Building it

**You need Python 3.10 or newer** on the machine you build on, from python.org, with
*Add Python to PATH* ticked during install. Nothing else — the script sets up its own
private environment and installs the rest itself.

**You cannot cross-compile.** A Windows `.exe` must be built on Windows and a Mac app on
a Mac. There is no way round this; PyInstaller bundles a real Python interpreter for the
platform it is running on.

And a Mac counts as two machines. Apple changed processor in 2020: a build made on an
Apple Silicon Mac will not start at all on an Intel one, and an Intel build only runs on
Apple Silicon if the person installs Rosetta first. So "the Mac version" is two files,
and the only way to make both by hand is to sit at both kinds of Mac.

**You don't have to.** GitHub lends out a Windows box and both kinds of Mac, free and
unlimited, to public repositories, and `.github/workflows/release.yml` uses all three:
push a tag and it builds everything, runs the full test suite on each platform, and
leaves a draft release carrying all three downloads plus `latest.json`. Building by hand
is now the fallback, not the normal route — **[PUBLISHING.md](PUBLISHING.md)** has both.

### If it stops at step 3

**"No module named pytest"** — a bug in the script, fixed. It installed
`requirements.txt` and then ran pytest, which lives in `requirements-dev.txt`. Pull the
latest source and run it again; you don't need to delete `.venv`, the install step will
top it up.

**Anything else at step 3** means a test genuinely failed. Don't work around it — that
step exists so a broken build never reaches anyone. Send the output on.

### If it stops at step 4 or 5 with a wall of PyInstaller output

Look for the last line. **`Target module "distutils" already imported as
ExcludedModule`** was a bug in the spec, fixed — pull the latest source. It only
appeared on Python 3.12 and newer, which is why it survived so long.

Anything else: the last few lines are the real message; everything above is PyInstaller
narrating what it found.

### If it stops at step 2

Almost always no internet, or a work laptop blocking `pip`. Nothing else to it.

### If the build fails part-way with "permission denied" or "file in use"

Your copy is inside OneDrive (the script now warns about this on startup). The sync
client grabs files while PyInstaller is still writing them. Copy the whole folder to your
Desktop, build there, and move the result back.

---

The script does five things, and stops if any of them fail:

1. makes a private Python environment in `.venv`
2. installs what Mittens & Pence needs
3. **runs the tests** — if they fail the build stops, so you can't ship something broken
4. builds the folder version → `dist\Mittens & Pence\Mittens & Pence.exe`
5. builds the single file → `dist\send\Mittens & Pence.exe`, and copies the read-me beside it

### The two shapes, and which to use

|  | **Folder** (`dist\Mittens & Pence\`) | **Single file** (`dist\send\Mittens & Pence.exe`) |
|---|---|---|
| What it is | ~85 MB folder, exe inside | one 40 MB file |
| Starts in | about a second | 10–20 seconds the first time, quicker after |
| Sending it | zip the whole folder, and they must keep it together | just send the file |
| Antivirus | rarely bothered | occasionally grumbles |

Keep the **folder** for your own machine — it starts instantly. Send the **single file**,
because "download this and double-click it" is an instruction that survives being read
over the phone. A folder where the .exe only works if everything stays next to it does
not.

The single file is self-extracting in the sense you remember: it unpacks itself into a
temporary folder each time it runs, then cleans up. That is why it is slower to start,
and why nothing is left behind on their machine except their own data.

---

## What to send

Exactly two files, both already sitting in `dist\send\` when the build finishes:

```
Mittens & Pence.exe          the whole app
Read me first.txt    what to expect, and where their data lives
```

The read-me matters more than it looks. Without it, the first thing your family sees is
a full-screen blue **"Windows protected your PC"** warning, and most people stop there.
With it, they know it's coming and what to click.

**Don't send:** the source zip, the `dist\Mittens & Pence\` folder, `requirements.txt`, or
anything else in this repository. They need none of it.

---

## How to send it

**Email will not work.** Gmail, Outlook and every other provider block `.exe`
attachments outright — not "warn about", block. Renaming it to `.txt` or zipping it to
get past that is exactly the behaviour real malware uses, and their antivirus will treat
it accordingly. Don't.

**Use a link:**

* **OneDrive / Google Drive / Dropbox** — upload both files (or a zip of the two), get a
  share link, send the link. Best option: the link keeps working, and you can replace
  the file with a newer build without sending anything again.
* **WeTransfer** — no account needed, but links expire after a week or so.
* **A USB stick** — for anyone you'll see in person, and the only option that skips the
  download warnings entirely.

Google Drive will show its own "Google Drive can't scan this file for viruses" notice
before downloading. That is a size notice, not a verdict; tell them to download anyway.

---

## What they will see, and what to tell them

**Windows, first run:** a blue full-screen box, *"Windows protected your PC"*.
→ **More info** → **Run anyway**.

This is SmartScreen, and it appears because the app is **not code-signed**. A signing
certificate runs to a few hundred pounds a year and needs renewing, which is hard to
justify for an app shared between a few people. Any unsigned app gets the same treatment, so the
warning is about the absence of a receipt, not about the app.

**Mac, which file:** there are two, `...-macos-arm64.app.zip` and
`...-macos-x86_64.app.zip`, and only one of them will start. **Apple menu → About This
Mac**: "Chip: Apple M…" means arm64, "Processor: … Intel …" means x86_64. Somebody who
picks the wrong one gets a file that does nothing when they double-click it and no
explanation — so say which is which when you send the link, not just in the read-me.
They unzip it and drag the app into Applications.

**Mac, first run:** an unsigned app refuses to open, saying macOS "cannot check it for
malicious software". The route is **System Settings → Privacy & Security**, scroll down,
**Open Anyway** — which is what Apple documents, and the only one that still works on
current macOS. Right-click → Open was the old way and has been withdrawn; do not tell
anyone to use it, because on a recent Mac it simply fails and they will conclude the app
is broken.

**Antivirus:** occasionally quarantines a self-unpacking exe on sight. Same cause. If it
happens, they allow it in their antivirus, or you send them the folder version instead.

**Where their data goes:**

* Windows — `C:\Users\<them>\AppData\Roaming\Mittens & Pence\`
* Mac — `~/Library/Application Support/Mittens & Pence/`

One database file plus their exported spreadsheets. Back up that folder and everything is
backed up; delete it and the app starts fresh. Settings shows the exact path with a
button to open it. Nothing is uploaded and there is no sign-in — each person's copy is
entirely their own.

---

## Sending a new version later

Build again and replace the file on the Drive link. They download it and double-click it
as before.

**Their data survives.** It lives in that AppData folder, not next to the .exe, so a
newer build picks up exactly where the old one left off. They can delete the old
download. Any database changes migrate themselves on first open — Mittens & Pence adds columns
rather than rebuilding tables, precisely so an upgrade never asks anyone to start again.
