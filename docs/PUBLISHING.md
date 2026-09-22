# Sending out an update

You've fixed something. The family are running last month's copy. This is how their
copies find out.

---

## The idea, in one paragraph

Every copy of Mittens & Pence reads one small text file on the internet, about once a
day. That file says *"the newest version is 1.1.0, here's where to get it, and here's
its fingerprint."* If the version in it is higher than the one they're running, their
copy downloads the new build in the background, checks it against the fingerprint, and
puts a strip at the top of the window saying it's ready. They close the app and run the
new file.

So publishing an update is: **put the new .exe somewhere, and put that little text file
next to it.** Everything below is the plumbing for those two sentences.

The little text file is called `latest.json`. A program called `tools/publish.py`
writes it for you — you never type it by hand.

---

## What you need

A free GitHub account. That's it.

GitHub is used here purely as a place to put two files where they'll stay put and be
downloadable. You will not be writing code on it, and nobody needs to see it. (Dropbox
and OneDrive *look* like they'd work, but their share links hand back a preview page
instead of the file, and they change the link when you replace a file — which breaks
the one thing that has to stay permanent.)

---

## Part 1 — Set it up. Once, about ten minutes.

### 1. Make the GitHub account

Go to <https://github.com> and sign up as **Stumpyf1Allan** — the app is already built
pointing at that name, so using a different one means editing `kestrel/config.py` to
match.

### 2. Make the repository

A "repository" is just a folder on GitHub. This one holds the app's source, and the
releases hang off it.

- Top right, the **+** menu → **New repository**
- **Repository name**: `mittens-and-pence` — exactly that, lower case with the hyphens
- **Public** — this matters twice. On a private repository the download links need a
  password, and the family's copies don't have one; and the Mac and Windows machines
  GitHub lends you for building (Part 2) are free and unlimited on public repositories
  only. Nobody is going to stumble across it, and there is nothing in it worth hiding:
  no keys, no passwords, and none of your figures — those never leave your own disk.
- **Leave "Add a README file" unticked.** Step 4 pushes your folder up, and that only
  works cleanly onto an empty repository — a README GitHub made for you is enough to
  make it refuse.
- **Create repository**

You now have a page at `https://github.com/Stumpyf1Allan/mittens-and-pence`. Keep it open.

### 3. Check the address — already filled in for you

This one is done. `kestrel/config.py` already says:

```python
UPDATE_MANIFEST_URL = (
    "https://github.com/Stumpyf1Allan/mittens-and-pence"
    "/releases/latest/download/latest.json")
```

Nothing to change, as long as you used the username and repository name above. If you
used something different, edit those two lines to match and save.

**That address never changes again.** GitHub keeps `/releases/latest/download/...`
permanently pointing at your newest release, so it's set once and forgotten.

> It's filled in already on purpose, even though the repository doesn't exist yet. A copy
> pointed at an address with nothing at it starts working the moment you publish
> something there. A copy built with it **blank** can never be told about updates at all,
> on anybody's machine, ever — and you can't fix that remotely. Of the two ways to be
> wrong, only one is recoverable.

### 4. Put the source on it

This is what lets GitHub build the app for you, on machines you don't own — including
the two kinds of Mac, which is the only way there will ever be a Mac version.

The easy route, if you've never used git:

1. Install **GitHub Desktop** from <https://desktop.github.com>, and sign in with the
   account from step 1.
2. **File → Add local repository**, and choose your Mittens and Pence folder. If it
   says the folder isn't a repository, click **create a repository** in that message
   and then **Create repository**.
3. Write anything in the summary box (`First version` will do) and click
   **Commit to main**.
4. Click **Publish repository**. Untick **Keep this code private**. If it offers a
   name, make it `mittens-and-pence`.

It will not upload your data. `.gitignore` already keeps out `*.db`, `.vaultkey`,
`dist/` and `.venv/` — the database with your accounts in it stays on your machine.
Worth a look at the file list in GitHub Desktop before you commit, all the same.

That's the setup finished. Everything from here is the routine you'll repeat.

---

## Part 2 — Publishing. Every time, about five minutes of your attention.

### Step 1. Change the version number

Open `kestrel/config.py`. Find:

```python
APP_VERSION = "1.1.0"
```

Change it to something higher — `1.2.0` for a normal update, `1.1.1` for a small fix.
Save.

**This is the step people forget.** Every copy compares the number in `latest.json`
against the number baked into the file it is running. Skip this and nothing happens:
either nobody is offered the update, or everybody is offered it over and over. The
build refuses to go ahead if the tag and this number disagree, which is the only reason
it can't quietly go wrong.

Commit and push it — in GitHub Desktop, a summary, **Commit to main**, then **Push
origin**.

### Step 2. Press the button

On your repository page: **Actions** → **Release** (down the left) → **Run workflow**
(a grey button on the right).

- **Tag for this release**: `v1.2.0` — the same number as step 1, with a `v` in front.
- **What changed**: a sentence in plain English. This is what the family see in the
  app, so write it for them, not for you. Leave it blank if you'd rather.
- **Run workflow**.

Now leave it alone for fifteen or twenty minutes. It is:

- checking your tag against `APP_VERSION`, and stopping immediately if they disagree;
- building the Windows `.exe` on a Windows machine;
- building the Mac app twice — once on an Apple Silicon Mac and once on an Intel one,
  because a build for one will not start at all on the other;
- running all the tests on every one of those machines, and stopping if any fail;
- writing `latest.json` with a fingerprint for each of the three downloads;
- checking every file named in `latest.json` is actually attached, and that its address
  matches what GitHub will really serve it as.

A green tick means all of that passed. A red cross means something is genuinely wrong —
click into it and the failing step says what.

### Step 3. Read it, then publish it

The workflow leaves the release as a **draft**. Nothing has reached anybody yet: the
address baked into every copy out there ignores drafts, so until you do this step the
family are still being pointed at the previous release.

**Releases** → open the draft → read the description → **Publish release**.

### Step 4. Check it worked, once

Paste your `UPDATE_MANIFEST_URL` into a browser. You should get a wall of text starting
`{ "version": "1.2.0"`, with three entries under `downloads`: `windows`,
`macos-arm64` and `macos-x86_64`.

If you want to be thorough, click each of the three download links on the release page
and check they actually start downloading.

---

## The long way, by hand

You do not need this. It is here for the day GitHub is down, or you want a build that
never goes on the internet at all.

1. **Windows:** double-click `build\build_windows.bat`. Out comes
   `dist\send\Mittens and Pence.exe`.
2. **Mac:** `bash build/build_macos.sh`, *on a Mac*. Out comes
   `dist/send/Mittens and Pence-macos-<arch>.app.zip`, where `<arch>` is whichever
   processor that Mac has. To get both you need both kinds of Mac. There is no way
   round it — PyInstaller bundles a real interpreter for the machine it is running on,
   and cannot cross-compile.
3. Open a Command Prompt in the Mittens and Pence folder (open the folder in File
   Explorer, click the address bar, type `cmd`, Enter) and run, all on one line:

```
python tools/publish.py --version 1.2.0 --notes "You can now add your mortgage." "dist/send/Mittens and Pence.exe" "macos-arm64=...app.zip" "macos-x86_64=...app.zip"
```

   It works out the repository from the address in `config.py`, hashes each file, and
   writes `latest.json` next to them. It will tell you if you have published only one
   of the two Mac builds — which is the quiet failure in all of this, because the other
   half of the family would be told, cheerfully and for ever, that they already have
   the newest version.

4. **Releases → Draft a new release**, tag `v1.2.0`, title `1.2.0`, drag every built
   file **and** `latest.json` onto the attach box, **Publish release**.

`latest.json` is an attachment like any other — it is not special, it is just small.

---

## That's it

Everyone's copy will notice within a day. Anyone impatient can go to
**Settings → Updates → Check now**.

Next time round it's steps 1–4 again: change a number, press a button, read the
draft, publish it.

---

## What it will and won't do

| | |
|---|---|
| Looks for a new version on its own | yes, about once a day, in the background |
| Downloads it on its own | yes — a setting they can turn off |
| Checks it's really your file | yes, against the fingerprint in `latest.json` |
| Installs itself | **no** |

That last row is deliberate and someone will ask about it. The .exe isn't code-signed —
a certificate costs a few hundred pounds a year, which is hard to justify for an app
shared between a few people. An unsigned program that reaches out to the internet,
downloads another program and silently overwrites itself is behaving exactly like
malware, and every antivirus on the family's machines would agree. So it goes as far as
it safely can — finds it, fetches it, proves it's the right file — and then asks a
person to run it.

Their data is never touched. It lives in a separate folder and every version reads the
same one.

---

## When something goes wrong

**"Nothing has been published at that address yet."**
Exactly what it says, and what every copy shows until your first release goes up. After
that, it means something is off: paste the address into a browser — whatever you see is
what the app sees. Usually the repository is set to Private, or the name doesn't match
`Stumpyf1Allan/mittens-and-pence`, or `latest.json` didn't get attached to the release.

**"The downloaded file didn't match its checksum."**
The .exe on GitHub isn't the .exe you hashed. Almost always: you rebuilt after running
`publish.py`, or you attached an older .exe by mistake. Re-run `publish.py` against the
exact file you uploaded, then edit the release and replace `latest.json`.

The app deletes the download and installs nothing when this happens — which is the
behaviour you want — but it does mean everyone is stuck until you fix it.

**Nobody is offered the update.**
Check the version really went up in *both* places: in `latest.json`, and in the build
itself. **Settings → Updates** shows what a copy thinks it is. A build whose
`APP_VERSION` you forgot to bump will read `latest.json`, see its own number, and
correctly decide there's nothing to do.

**Somebody on a Mac says nothing happens when they double-click it.**
They have the wrong one of the two Mac files. `-arm64` is for a Mac with an Apple chip
(2020 and later), `-x86_64` for an Intel one. **Apple menu → About This Mac** tells them
which they have, in the "Chip" or "Processor" line. This is the single most likely thing
to go wrong with a Mac release, and it produces no error message at all — so say which
is which when you send the link.

**Somebody on a Mac says it's blocked.**
Expected, once, on every Mac: **System Settings → Privacy & Security**, scroll down,
**Open Anyway** beside the message about Mittens & Pence. Do *not* tell them to
right-click and choose Open — Apple withdrew that route and on a current Mac it simply
fails, which leaves them certain the app is broken.

**The build went red.**
Click into it from the Actions tab; the step that failed is the one with the cross next
to it, and its log says what happened. Two common ones: the tag and `APP_VERSION`
disagree (fix `config.py`, push, run it again with the same tag), or a test failed on
Windows or a Mac that passes on your machine. The second is the build doing its job —
it is telling you something in this release does not work on somebody else's computer.

**You published something broken.**
Put the old build back out as a *higher* number — `1.1.1` containing 1.0.0's code —
rather than lowering the number in the manifest. Copies that already updated are on
1.1.0 and will never be offered a 1.0.0.

---

## Notes for later

- The download links inside `latest.json` are pinned to their own release tag, not to
  "latest". A manifest and the file it fingerprints stay locked together, so publishing
  a new release mid-download can't cause a checksum failure.
- GitHub turns spaces in an attached file's name into full stops — `Mittens and
  Pence.exe` is served as `Mittens.and.Pence.exe`. `publish.py` already accounts for
  this. If you ever hand-write a URL, that's the likely mistake.
- Downloads are only accepted from the manifest's own host or from
  `ALLOWED_DOWNLOAD_HOSTS` in `kestrel/updates.py`. It's a typo catcher, not a security
  boundary — the manifest is the trust root. Host somewhere else and you add it there.
- The daily check sends a plain request for one file, with a user-agent naming the app
  and its version. No account names, no balances, nothing about the household.
  **Settings → Updates → Look for new versions** turns it off entirely.
- A manifest may name a processor as well as a platform: `macos-arm64` and
  `macos-x86_64` are tried first, and plain `macos` is the fallback. That is what keeps
  the releases published before any of this existed installable — an old manifest with
  only `macos` in it still works on a machine running the newer code.
- The Mac app goes out as a `.app` bundle zipped with `ditto`, not as a bare
  executable. A Unix executable with no extension opens a Terminal window when it is
  double-clicked in Finder, and a `.app` zipped with plain `zip` loses the symlinks and
  permission bits inside the bundle — it unzips looking perfectly normal and then
  refuses to launch.
- GitHub's Intel Mac runner (`macos-15-intel`) is the last one they intend to offer,
  and it is due to go away in August 2027. When it does, the Intel build stops being
  buildable and `macos-x86_64` quietly drops out of the release. By then, Intel Macs
  will be seven years old.
