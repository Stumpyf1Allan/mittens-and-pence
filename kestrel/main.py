"""Mittens & Pence entry point.

Starts the local server, then opens the window. Three ways to show the UI, tried in
order: a native window (pywebview), an app-mode Chrome/Edge window, and finally the
default browser. Whichever works, it is the same app.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from . import config, db
from .engine import snapshots
from .web import server


def _setup_logging(verbose: bool = False):
    logfile = config.logs_dir() / "mittens.log"
    handlers = [logging.FileHandler(logfile, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers)
    return logfile


def _startup_jobs():
    """Housekeeping the user should never have to think about."""
    try:
        db.init()
    except Exception:
        logging.exception("database init failed")
        return
    try:
        from .engine import sync as sync_engine
        fixed = sync_engine.repair_connections()
        if fixed["moved"] or fixed["downgraded"]:
            logging.info("repaired connections: %s", fixed)
    except Exception:
        logging.exception("connection repair failed")
    try:
        snap = snapshots.maybe_take_monthly()
        if snap:
            logging.info("monthly snapshot taken for %s", snap.get("period"))
    except Exception:
        logging.exception("monthly snapshot failed")
    if config.settings.get("auto_sync_on_open"):
        def _bg():
            time.sleep(1.5)
            try:
                from .market import prices as market
                market.refresh_prices()
            except Exception:
                logging.exception("price refresh failed")
        threading.Thread(target=_bg, daemon=True).start()
    try:
        from . import updates
        updates.start_up()
    except Exception:
        logging.exception("update check failed to start")
    try:
        # Makes the folder if it isn't there yet, so it exists before anybody is told
        # about it, and sends the monthly email only if somebody set up a mail server.
        from . import inbox, reminders
        inbox.folder()
        threading.Thread(target=reminders.maybe_send, daemon=True).start()
    except Exception:
        logging.exception("statement folder / reminder start-up failed")


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

def _try_pywebview(url: str) -> bool:
    try:
        import webview
    except Exception:
        return False
    try:
        # Maximised, not `fullscreen=True`: fullscreen hides the title bar and its close
        # button, which on Windows leaves no obvious way out of a finance app.
        webview.create_window(config.APP_NAME, url, width=1360, height=880,
                              min_size=(1024, 680), confirm_close=False,
                              maximized=True)
        webview.start()
        return True
    except Exception:
        logging.exception("pywebview failed")
        return False


_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def _try_app_window(url: str) -> subprocess.Popen | None:
    exes = [p for p in _CHROME_CANDIDATES if pathlib.Path(p).exists()]
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        found = shutil.which(name)
        if found:
            exes.append(found)
    if not exes:
        return None
    profile = config.data_dir() / "window"
    try:
        return subprocess.Popen(
            [exes[0], f"--app={url}", f"--user-data-dir={profile}",
             "--window-size=1360,880", "--no-first-run", "--no-default-browser-check"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        logging.exception("app-mode window failed")
        return None


def run(open_ui: bool = True, port: int | None = None, prefer: str = "auto"):
    logfile = _setup_logging(bool(os.environ.get("KESTREL_VERBOSE")))
    logging.info("%s %s starting", config.APP_NAME, config.APP_VERSION)

    httpd, port = server.serve(port=port, block=False)
    url = f"http://127.0.0.1:{port}/"
    _startup_jobs()

    print(f"\n  {config.APP_NAME} {config.APP_VERSION} — {config.APP_TAGLINE}")
    print(f"  Open:  {url}")
    print(f"  Data:  {config.data_dir()}")
    print(f"  Log:   {logfile}")
    print("  Close this window to stop.\n")

    if not open_ui:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    if prefer in ("auto", "native") and _try_pywebview(url):
        return                                     # blocks until the window closes

    proc = _try_app_window(url) if prefer in ("auto", "app") else None
    if proc is None:
        webbrowser.open(url)
        print("  Opened in your browser. Leave this window running.\n")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
    httpd.shutdown()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mittens", description=config.APP_TAGLINE)
    ap.add_argument("--port", type=int, help="port to listen on (default 8765)")
    ap.add_argument("--no-window", action="store_true", help="just run the server")
    ap.add_argument("--window", choices=["auto", "native", "app", "browser"], default="auto")
    ap.add_argument("--demo", action="store_true", help="load the sample household first")
    ap.add_argument("--export", choices=["investments", "banking", "both"],
                    help="build the workbooks and exit")
    ap.add_argument("--sync", action="store_true", help="sync every connection and exit")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print(f"{config.APP_NAME} {config.APP_VERSION}")
        return 0

    db.init()

    if args.demo:
        from . import demo
        rep = demo.load()
        print(f"Sample data loaded: {len(rep['connections'])} connections, "
              f"{rep['budgets']} budgets.")

    if args.sync:
        from .engine import sync as sync_engine
        from .market import prices as market
        out = sync_engine.sync_all()
        market.refresh_prices()
        for r in out["results"]:
            print(f"  connection {r['connection_id']}: {r['message']}")
        print(f"{out['synced']} connection(s) synced.")
        if not args.export:
            return 0

    if args.export:
        from .export import banking_xlsx, investments_xlsx
        made = []
        if args.export in ("both", "investments"):
            made.append(investments_xlsx.build())
        if args.export in ("both", "banking"):
            made.append(banking_xlsx.build())
        for p in made:
            print(f"  {p}")
        return 0

    run(open_ui=not args.no_window, port=args.port,
        prefer="browser" if args.window == "browser" else args.window)
    return 0


if __name__ == "__main__":
    sys.exit(main())
