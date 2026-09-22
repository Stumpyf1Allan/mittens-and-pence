"""Finding, fetching and verifying a new version of the app.

The shape of this is decided by one fact: the executable is not code-signed. An
unsigned program that silently overwrites itself is indistinguishable, from the
outside, from malware — and if anything ever went wrong with the download, the
family would be left with a broken file where the working app used to be. So this
module goes exactly as far as it safely can and then stops:

    check  →  tell them a new version exists
    fetch  →  download it and verify its SHA-256 against the manifest
    stop   →  hand them the verified file and let them run it

Nothing here ever replaces a running program, and nothing downloaded is ever
executed by the app. The checksum is the whole point of the exercise: it is what
makes "this 40 MB file came off the internet" into "this is byte-for-byte the file
that was published".
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

#: How long between automatic checks. A household app does not need to ask hourly.
CHECK_INTERVAL = 60 * 60 * 20        # 20 hours
#: After a failed check. A laptop opened on a train is offline for minutes, not a
#: day, and making it wait out the full interval means it never notices the update.
RETRY_INTERVAL = 60 * 30             # 30 minutes
#: Give up quickly. A check that hangs must never hold anything else up.
TIMEOUT = 12.0
DOWNLOAD_TIMEOUT = 300.0
#: A manifest is a few hundred bytes. Anything vastly larger is not our manifest.
MAX_MANIFEST_BYTES = 256 * 1024
#: Sanity ceiling on a download, so a wrong URL cannot fill somebody's disk.
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024

USER_AGENT = f"{config.APP_FILE_NAME}/{config.APP_VERSION} (update check)"

#: Hosts a download may come from, beyond the host serving the manifest itself.
#: The manifest is the trust root — if it is tampered with, a host list saves nobody.
#: What this does catch is the ordinary mistake: a typo'd or copy-pasted URL that
#: points somewhere nobody intended. Suffix match on the hostname.
ALLOWED_DOWNLOAD_HOSTS = (
    "github.com", "githubusercontent.com", "githubassets.com",
    "gitlab.com", "gitlab.io",
    "sourceforge.net",
    "dropboxusercontent.com",
    "1drv.ms", "sharepoint.com", "onedrive.live.com",
    "googleusercontent.com", "drive.google.com",
    "backblazeb2.com", "b-cdn.net", "r2.dev", "cloudfront.net",
)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

_VERSION_RX = re.compile(r"^(\d+(?:\.\d+)*)(?:[-_+.]?([A-Za-z][A-Za-z0-9.+_-]*))?$")


def parse_version(text: str) -> tuple[tuple[int, ...], int, str] | None:
    """'1.2.3' → ((1,2,3), 1, ''), '1.2.3-beta2' → ((1,2,3), 0, 'beta2').

    Returns None for anything that is not a version, which the callers treat as
    "cannot compare" — and therefore as "do not offer an update". A malformed
    manifest must never be able to push somebody to a download.
    """
    m = _VERSION_RX.match((text or "").strip().lstrip("vV"))
    if not m:
        return None
    numbers = tuple(int(p) for p in m.group(1).split("."))
    suffix = (m.group(2) or "").lower()
    # A pre-release of 1.2.3 is older than 1.2.3 itself, so it sorts below it.
    return numbers, (0 if suffix else 1), suffix


def _padded(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple, tuple]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def is_newer(candidate: str, current: str) -> bool:
    """Is `candidate` a later version than `current`? False if either is unreadable."""
    c, k = parse_version(candidate), parse_version(current)
    if c is None or k is None:
        return False
    cn, kn = _padded(c[0], k[0])
    if cn != kn:
        return cn > kn
    if c[1] != k[1]:
        return c[1] > k[1]
    return c[2] > k[2]


# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------

def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


#: What a machine calls its own processor, mapped onto the two names a manifest uses.
#: Deliberately not a catch-all: an architecture nobody builds for must come out as
#: "I don't know", so the plain platform entry is used, rather than be guessed into a
#: name that happens to have a download behind it.
_ARCH_NAMES = {
    "x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64",
    "arm64": "arm64", "aarch64": "arm64",
}


def arch_key() -> str | None:
    """'arm64', 'x86_64', or None for anything we do not publish builds for.

    On an Apple Silicon Mac running an Intel build under Rosetta this reports
    x86_64. That is the right answer rather than a bug: that copy should be
    offered the Intel update, because the Intel one is the one it can run.
    """
    return _ARCH_NAMES.get(platform.machine().strip().lower())


def download_keys() -> tuple[str, ...]:
    """Which manifest entries this machine may use, best first.

    A Mac is two machines wearing one name. An Apple Silicon build will not start
    at all on an Intel Mac, and an Intel build only runs on Apple Silicon if the
    person installs Rosetta first — so "macos" on its own cannot answer the
    question, and a release carrying both builds needs a way to say which is which.

    Manifests that only have the plain key keep working untouched: the specific
    name is tried first and the plain one is the fallback, so an old release stays
    installable and a new one can be precise.
    """
    plain = platform_key()
    arch = arch_key()
    return (f"{plain}-{arch}", plain) if arch else (plain,)


def downloads_dir() -> pathlib.Path:
    p = config.data_dir() / "updates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_file() -> pathlib.Path:
    return config.data_dir() / "update-state.json"


def manifest_url() -> str:
    """Where to look. A setting wins, then the environment, then the built-in default."""
    return (str(config.settings.get("update_url") or "").strip()
            or os.environ.get("MITTENS_UPDATE_URL", "").strip()
            or config.UPDATE_MANIFEST_URL).strip()


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------------------
# The live state the UI reads
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_STATE: dict = {
    "status": "idle",        # idle | checking | current | available | downloading
                             # | ready | error | off
    "current": config.APP_VERSION,
    "latest": None,
    "notes": "",
    "page": "",
    "published": "",
    "checked_at": None,      # the last check that actually got an answer
    "attempted_at": None,    # the last time we tried, answer or not
    "error": "",
    "download": None,        # {url, filename, sha256, bytes}
    "progress": 0.0,
    "file": None,            # verified file on disk, once downloaded
    "dismissed_version": None,
}


def _load_persisted():
    try:
        saved = json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return
    with _LOCK:
        for k in ("checked_at", "attempted_at", "dismissed_version", "latest",
                  "notes", "page", "published"):
            if k in saved:
                _STATE[k] = saved[k]
        # A file recorded as downloaded is only believed if it is still there.
        f = saved.get("file")
        if f and pathlib.Path(f).exists():
            _STATE["file"] = f


def _persist():
    with _LOCK:
        blob = {k: _STATE[k] for k in
                ("checked_at", "attempted_at", "dismissed_version", "latest", "notes",
                 "page", "published", "file")}
    try:
        tmp = _state_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        tmp.replace(_state_file())
    except Exception:
        pass                 # A state file that will not write is not worth a crash.


def state() -> dict:
    with _LOCK:
        out = dict(_STATE)
    # Deliberately NOT called "error". Every API reply in this app is read by one
    # helper that treats a top-level "error" as "the request failed" and throws —
    # so a state whose whole job is to carry an explanation would have been
    # swallowed as a broken request, and the user would have been told the update
    # status could not be read instead of being told why the check failed.
    out["last_error"] = out.pop("error", "")
    out["enabled"] = bool(config.settings.get("check_for_updates", True))
    out["configured"] = bool(manifest_url())
    out["frozen"] = _is_frozen()
    if not out["configured"]:
        out["status"] = "off"
    # The banner is the one thing the front end should not have to reason about.
    latest = out.get("latest")
    out["show_banner"] = bool(
        out["configured"] and out["enabled"] and latest
        and is_newer(latest, config.APP_VERSION)
        and out.get("dismissed_version") != latest)
    return out


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

class UpdateError(Exception):
    pass


def _open(url: str, timeout: float):
    if not url.lower().startswith("https://"):
        # Plain HTTP would let anyone on the same café wi-fi choose what "the latest
        # version" is. There is no version of that worth supporting.
        raise UpdateError("Updates are only fetched over https.")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/octet-stream, */*",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def _host_allowed(download_url: str, manifest: str) -> bool:
    try:
        host = (urllib.parse.urlparse(download_url).hostname or "").lower()
        same = (urllib.parse.urlparse(manifest).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == same:
        return True
    return any(host == h or host.endswith("." + h) for h in ALLOWED_DOWNLOAD_HOSTS)


def _read_manifest(url: str) -> dict:
    try:
        with _open(url, TIMEOUT) as r:
            raw = r.read(MAX_MANIFEST_BYTES + 1)
    except urllib.error.HTTPError as e:
        # Only 404 means "there is nothing there". 403 used to be lumped in with it,
        # and 403 is also exactly what a workplace proxy or a captive portal returns —
        # so somebody on a blocked network was told no version had ever been published,
        # which is a statement about the publisher rather than about their wifi.
        if e.code == 404:
            raise UpdateError("Nothing has been published at that address yet.") from e
        if e.code in (401, 403, 407):
            raise UpdateError("The update server refused the request — some networks "
                              "block it. Try again on another connection.") from e
        raise UpdateError(f"The update server answered {e.code}.") from e
    except urllib.error.URLError as e:
        raise UpdateError(f"Couldn't reach the update server: {e.reason}") from e
    except UpdateError:
        raise
    except Exception as e:                                   # timeouts, DNS, TLS
        raise UpdateError(f"Couldn't reach the update server: {e}") from e
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("That address didn't return an update file.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise UpdateError("That address didn't return an update file.") from e
    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError("The update file is missing a version number.")
    return data


def _download_entry(manifest: dict, url: str) -> dict | None:
    """The entry for this platform, once it has been checked over."""
    downloads = manifest.get("downloads")
    if not isinstance(downloads, dict):
        return None
    entry = next((e for e in map(downloads.get, download_keys()) if isinstance(e, dict)),
                 None)
    if not isinstance(entry, dict):
        return None
    href, digest = str(entry.get("url") or ""), str(entry.get("sha256") or "").lower()
    if not href.lower().startswith("https://"):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        # No checksum means no way to know what arrived, which means no download.
        return None
    if not _host_allowed(href, url):
        return None
    name = pathlib.PurePosixPath(urllib.parse.urlparse(href).path).name
    filename = str(entry.get("filename") or name or "update.bin")
    # The manifest chooses a name that lands on somebody's disk, so it gets no say
    # in *where*: strip it back to a plain filename.
    filename = pathlib.PurePath(filename.replace("\\", "/")).name
    if not filename or filename in (".", ".."):
        filename = "update.bin"
    size = entry.get("bytes")
    return {"url": href, "filename": filename, "sha256": digest,
            "bytes": int(size) if isinstance(size, (int, float)) and size > 0 else None}


def check(force: bool = False) -> dict:
    """Ask the manifest what the latest version is. Safe to call from any thread."""
    url = manifest_url()
    if not url:
        _set(status="off")
        return state()
    if not force and not config.settings.get("check_for_updates", True):
        return state()
    if not force:
        last = _STATE.get("attempted_at") or _STATE.get("checked_at") or 0
        wait = RETRY_INTERVAL if _STATE.get("status") == "error" else CHECK_INTERVAL
        if last and (time.time() - last) < wait:
            return state()

    _set(status="checking", error="")
    try:
        manifest = _read_manifest(url)
    except UpdateError as e:
        # A failed attempt is still an attempt. Leaving this unset meant the Settings
        # screen showed no "last looked" at all after a failure, which reads as an app
        # that has never tried — and left nothing to throttle the retry against, so a
        # machine with no network re-checked on every render.
        _set(status="error", error=str(e), attempted_at=time.time())
        _persist()
        return state()

    latest = str(manifest.get("version") or "").strip()
    entry = _download_entry(manifest, url)
    newer = is_newer(latest, config.APP_VERSION)
    _set(latest=latest,
         notes=str(manifest.get("notes") or "")[:4000],
         page=str(manifest.get("page") or "") if
              str(manifest.get("page") or "").lower().startswith("https://") else "",
         published=str(manifest.get("published") or "")[:40],
         checked_at=time.time(),
         attempted_at=time.time(),
         download=entry if newer else None,
         status="available" if newer else "current",
         error="")
    if not newer:
        _set(file=None, progress=0.0)
    _persist()

    if newer and entry and _is_frozen() and config.settings.get("auto_download_updates", True):
        start_download()
    return state()


def _sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def download() -> dict:
    """Fetch the new version and verify it. Blocks; run it on a thread."""
    with _LOCK:
        entry = _STATE.get("download")
        latest = _STATE.get("latest")
    if not entry:
        _set(status="error", error="There's nothing to download.")
        return state()

    target = downloads_dir() / entry["filename"]
    part = target.with_name(target.name + ".part")

    # Already have it, verified? Then there is nothing to do.
    if target.exists():
        try:
            if _sha256_of(target) == entry["sha256"]:
                _set(status="ready", file=str(target), progress=1.0, error="")
                _persist()
                return state()
        except OSError:
            pass
        try:
            target.unlink()
        except OSError:
            pass

    _set(status="downloading", progress=0.0, error="", file=None)
    total = entry.get("bytes") or 0
    got = 0
    try:
        with _open(entry["url"], DOWNLOAD_TIMEOUT) as r:
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit():
                total = int(declared)
            if total and total > MAX_DOWNLOAD_BYTES:
                raise UpdateError("That download is far bigger than it should be.")
            with open(part, "wb") as fh:
                while True:
                    chunk = r.read(1024 * 128)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_DOWNLOAD_BYTES:
                        raise UpdateError("That download is far bigger than it should be.")
                    fh.write(chunk)
                    if total:
                        _set(progress=min(0.999, got / total))
    except UpdateError as e:
        part.unlink(missing_ok=True)
        _set(status="error", error=str(e), progress=0.0)
        return state()
    except Exception as e:
        part.unlink(missing_ok=True)
        _set(status="error", progress=0.0,
             error=f"The download didn't finish: {e}")
        return state()

    # The part that matters. Anything that is not byte-for-byte what was published
    # gets deleted rather than left lying about looking like an update.
    actual = _sha256_of(part)
    if actual != entry["sha256"]:
        part.unlink(missing_ok=True)
        _set(status="error", progress=0.0,
             error="The downloaded file didn't match its checksum, so it has been "
                   "deleted. Nothing was installed. Try again later.")
        return state()

    part.replace(target)
    try:
        # Not executed by the app, but a downloaded program the person is being asked
        # to run should at least be runnable without a chmod on macOS and Linux.
        if platform_key() != "windows":
            os.chmod(target, 0o755)
    except OSError:
        pass
    _set(status="ready", file=str(target), progress=1.0, error="")
    _persist()
    _prune(keep=target, version=latest)
    return state()


def _prune(keep: pathlib.Path, version: str | None):
    """Don't let the updates folder collect every version ever downloaded."""
    try:
        for p in downloads_dir().iterdir():
            if p == keep or not p.is_file():
                continue
            if p.suffix == ".part" or p.stat().st_mtime < time.time() - 60 * 60 * 24 * 30:
                p.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

#: One slot per job, not one slot for all of them. With a single shared slot the
#: automatic download never happened: the startup check is itself running on that
#: thread when it decides to fetch, so the download was refused as "already busy"
#: and the update sat there waiting for somebody to press a button — which is the
#: one thing automatic downloading is meant to avoid.
_threads: dict[str, threading.Thread] = {}


def _run(key: str, fn, *a, **kw) -> bool:
    running = _threads.get(key)
    if running and running.is_alive():
        return False
    t = threading.Thread(target=fn, args=a, kwargs=kw,
                         name=f"mittens-updates-{key}", daemon=True)
    _threads[key] = t
    t.start()
    return True


def start_check(force: bool = False) -> bool:
    return _run("check", check, force)


def start_download() -> bool:
    return _run("download", download)


def dismiss(version: str | None = None):
    with _LOCK:
        _STATE["dismissed_version"] = version or _STATE.get("latest")
    _persist()


def start_up(delay: float = 4.0):
    """Called once from main. Quiet, late and entirely optional."""
    _load_persisted()
    if not manifest_url() or not config.settings.get("check_for_updates", True):
        _set(status="off" if not manifest_url() else "idle")
        return

    def later():
        # Late enough that it is never the reason the window took a moment to open.
        time.sleep(delay)
        try:
            check()
        except Exception:
            pass

    _run("startup", later)
