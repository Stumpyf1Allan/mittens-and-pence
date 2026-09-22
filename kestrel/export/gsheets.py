"""Push the workbooks straight into Google Sheets.

Optional. The manual route — drag the .xlsx into Drive — needs nothing at all, and is
what most people will do. This is for anyone who wants the sheet to refresh itself in
Drive so the rest of the family sees the current figures without being sent a file.

No extra Python packages: it speaks to Google's REST API over urllib, using the same
loopback OAuth flow the bank connections use. You supply a Client ID and secret from
your own Google Cloud project (free), so the data goes from this computer to your own
Drive and nowhere else.

Set-up, once:
  1. console.cloud.google.com → create a project (any name).
  2. APIs & Services → Library → enable "Google Drive API".
  3. APIs & Services → OAuth consent screen → External → add yourself as a test user.
  4. Credentials → Create credentials → OAuth client ID → Desktop app.
  5. Paste the Client ID and Client secret into Mittens & Pence's Settings.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import pathlib
import urllib.error
import urllib.parse
import urllib.request

from .. import config, db, security

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

VAULT_REF = "google:drive"


class SheetsError(RuntimeError):
    pass


def _creds() -> dict:
    return security.get(VAULT_REF) or {}


def save_client(client_id: str, client_secret: str):
    security.put(VAULT_REF, {**_creds(), "client_id": client_id.strip(),
                             "client_secret": client_secret.strip()})


def configured() -> bool:
    c = _creds()
    return bool(c.get("client_id") and c.get("client_secret"))


def linked() -> bool:
    return bool(_creds().get("refresh_token"))


def status() -> dict:
    c = _creds()
    return {
        "configured": configured(),
        "linked": linked(),
        "client_id": (c.get("client_id") or "")[:18] + "…" if c.get("client_id") else "",
        "files": {k: v for k, v in (c.get("files") or {}).items()},
    }


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def begin_link(redirect_uri: str) -> str:
    c = _creds()
    if not configured():
        raise SheetsError("Add your Google Client ID and secret in Settings first.")
    security.put(VAULT_REF, {**c, "redirect_uri": redirect_uri})
    q = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": "mithapp-google",
    })
    return f"{AUTH_URL}?{q}"


def complete_link(code: str) -> dict:
    c = _creds()
    payload = _post_form(TOKEN_URL, {
        "code": code,
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "redirect_uri": c.get("redirect_uri"),
        "grant_type": "authorization_code",
    })
    if "refresh_token" not in payload:
        raise SheetsError("Google didn't return a refresh token — remove Mittens & Pence from "
                          "your Google account's third-party access and link again.")
    security.put(VAULT_REF, {**c, "refresh_token": payload["refresh_token"],
                             "access_token": payload.get("access_token"),
                             "expires_at": _expiry(payload)})
    return {"ok": True}


def _expiry(payload: dict) -> str:
    secs = int(payload.get("expires_in") or 3500)
    return (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=secs - 60)).isoformat()


def _token() -> str:
    c = _creds()
    if not c.get("refresh_token"):
        raise SheetsError("Google Drive isn't linked yet.")
    if c.get("access_token") and c.get("expires_at", "") > dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat():
        return c["access_token"]
    payload = _post_form(TOKEN_URL, {
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"],
        "grant_type": "refresh_token",
    })
    security.put(VAULT_REF, {**c, "access_token": payload.get("access_token"),
                             "expires_at": _expiry(payload)})
    return payload["access_token"]


def unlink():
    c = _creds()
    security.put(VAULT_REF, {"client_id": c.get("client_id"),
                             "client_secret": c.get("client_secret")})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode({k: v for k, v in fields.items() if v}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    return _send(req)


def _send(req) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
            return json.loads(raw or b"{}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:500]
        except Exception:
            pass
        raise SheetsError(f"Google returned {e.code}: {body or e.reason}") from e
    except urllib.error.URLError as e:
        raise SheetsError(f"Couldn't reach Google: {e.reason}") from e


def _multipart(metadata: dict, blob: bytes, mime: str) -> tuple[bytes, str]:
    boundary = "mithapp-" + dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
    parts = [
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(metadata).encode(),
        f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode(),
        blob,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/related; boundary={boundary}"


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def publish(path: pathlib.Path, name: str | None = None, key: str | None = None) -> dict:
    """Upload an .xlsx as a Google Sheet, replacing the previous one if there is one.

    Keeping the same Drive file id matters: anyone you've shared the sheet with keeps
    their link, and their tabs and comments survive the refresh.
    """
    path = pathlib.Path(path)
    if not path.exists():
        raise SheetsError(f"{path.name} isn't there — build it first.")
    blob = path.read_bytes()
    name = name or path.stem
    key = key or name
    c = _creds()
    files = dict(c.get("files") or {})
    token = _token()

    existing = files.get(key)
    if existing:
        body, ctype = _multipart({"name": name}, blob, XLSX_MIME)
        req = urllib.request.Request(
            f"{UPLOAD_URL}/{existing}?uploadType=multipart&supportsAllDrives=true",
            data=body, method="PATCH",
            headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
        try:
            out = _send(req)
        except SheetsError as e:
            if " 404" not in str(e):
                raise
            existing = None          # someone deleted it in Drive; make a new one
    if not existing:
        body, ctype = _multipart({"name": name, "mimeType": SHEET_MIME}, blob, XLSX_MIME)
        req = urllib.request.Request(
            f"{UPLOAD_URL}?uploadType=multipart&supportsAllDrives=true",
            data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
        out = _send(req)
        files[key] = out["id"]
        security.put(VAULT_REF, {**c, "files": files})

    file_id = files.get(key) or out.get("id")
    db.log("export.gsheets", {"key": key, "file_id": file_id})
    return {
        "ok": True,
        "name": name,
        "file_id": file_id,
        "url": f"https://docs.google.com/spreadsheets/d/{file_id}/edit",
        "replaced": bool(existing),
    }


def publish_both(investments: pathlib.Path, banking: pathlib.Path) -> dict:
    out = []
    if investments:
        out.append(publish(investments, "Investments (Mittens & Pence)", "investments"))
    if banking:
        out.append(publish(banking, "Banking & Budgets (Mittens & Pence)", "banking"))
    return {"sheets": out}


def share(file_id: str, email: str, role: str = "reader") -> dict:
    """Give someone in the family access to the sheet."""
    token = _token()
    req = urllib.request.Request(
        f"{FILES_URL}/{file_id}/permissions?sendNotificationEmail=false",
        data=json.dumps({"type": "user", "role": role,
                         "emailAddress": email}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    return _send(req)
