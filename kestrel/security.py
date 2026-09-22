"""Credential vault.

API keys and OAuth tokens are encrypted with a key held in the operating system's
own credential store (Windows Credential Manager / macOS Keychain / Secret Service).
If no keyring is available the key falls back to a file in the app data directory
with owner-only permissions — weaker, and the app says so in Settings.

Nothing here ever touches a banking password: Open Banking connections are
redirect-based, so the password is typed on the bank's own site.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import secrets
import stat

from . import config, db

#: The name the vault key is stored under in Windows Credential Manager / the macOS
#: Keychain. Renaming the app must NOT rename this: the key is what decrypts every saved
#: bank credential, and a new service name simply cannot see the old entry — the app
#: would silently generate a fresh key and every stored credential would become
#: unreadable. New installs write under the current name; older ones keep working.
_KEYRING_SERVICE = "Mittens and Pence Finance"
_LEGACY_KEYRING_SERVICES = ("Mithapp Finance", "Kestrel Finance")
_KEYRING_USER = "vault-key"


def _try_keyring():
    try:
        import keyring  # noqa
        return keyring
    except Exception:
        return None


def _key_file() -> pathlib.Path:
    return config.data_dir() / ".vaultkey"


def _load_or_create_key() -> bytes:
    kr = _try_keyring()
    if kr is not None:
        try:
            val = kr.get_password(_KEYRING_SERVICE, _KEYRING_USER)
            if not val:
                # An install that predates a rename stored the key elsewhere. Read it
                # from there rather than minting a new one and orphaning the vault.
                for older in _LEGACY_KEYRING_SERVICES:
                    val = kr.get_password(older, _KEYRING_USER)
                    if val:
                        try:              # copy it forward, but never lose the original
                            kr.set_password(_KEYRING_SERVICE, _KEYRING_USER, val)
                        except Exception:
                            pass
                        break
            if val:
                return base64.urlsafe_b64decode(val.encode())
            raw = secrets.token_bytes(32)
            kr.set_password(_KEYRING_SERVICE, _KEYRING_USER,
                            base64.urlsafe_b64encode(raw).decode())
            return raw
        except Exception:
            pass
    f = _key_file()
    if f.exists():
        return base64.urlsafe_b64decode(f.read_bytes())
    raw = secrets.token_bytes(32)
    f.write_bytes(base64.urlsafe_b64encode(raw))
    try:
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return raw


def storage_backend() -> str:
    return "os-keychain" if _try_keyring() else "local-file"


class _Cipher:
    """AES-GCM if `cryptography` is present, otherwise an HMAC-authenticated
    XOR-with-SHA256-keystream. The fallback is not a substitute for a real AEAD;
    it exists so the app still runs on a bare Python install, and Settings shows
    which one is active."""

    def __init__(self, key: bytes):
        self.key = key
        self.aead = None
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            self.aead = AESGCM(key)
        except Exception:
            self.aead = None

    def encrypt(self, data: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        if self.aead is not None:
            return b"A1" + nonce + self.aead.encrypt(nonce, data, None)
        import hashlib
        import hmac
        stream = b""
        counter = 0
        while len(stream) < len(data):
            stream += hashlib.sha256(self.key + nonce + counter.to_bytes(4, "big")).digest()
            counter += 1
        ct = bytes(a ^ b for a, b in zip(data, stream))
        mac = hmac.new(self.key, nonce + ct, hashlib.sha256).digest()
        return b"F1" + nonce + mac + ct

    def decrypt(self, blob: bytes) -> bytes:
        tag, rest = blob[:2], blob[2:]
        if tag == b"A1":
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce, ct = rest[:12], rest[12:]
            return AESGCM(self.key).decrypt(nonce, ct, None)
        if tag == b"F1":
            import hashlib
            import hmac
            nonce, mac, ct = rest[:12], rest[12:44], rest[44:]
            if not hmac.compare_digest(mac, hmac.new(self.key, nonce + ct, hashlib.sha256).digest()):
                raise ValueError("credential store integrity check failed")
            stream = b""
            counter = 0
            while len(stream) < len(ct):
                stream += hashlib.sha256(self.key + nonce + counter.to_bytes(4, "big")).digest()
                counter += 1
            return bytes(a ^ b for a, b in zip(ct, stream))
        raise ValueError("unknown credential format")


_cipher: _Cipher | None = None


def cipher() -> _Cipher:
    global _cipher
    if _cipher is None:
        _cipher = _Cipher(_load_or_create_key())
    return _cipher


def put(ref: str, payload: dict) -> str:
    blob = cipher().encrypt(json.dumps(payload).encode("utf-8"))
    with db.tx() as c:
        c.execute("INSERT INTO vault(ref, blob) VALUES(?,?) "
                  "ON CONFLICT(ref) DO UPDATE SET blob=excluded.blob", (ref, blob))
    return ref


def get(ref: str) -> dict | None:
    row = db.one("SELECT blob FROM vault WHERE ref=?", (ref,))
    if not row:
        return None
    try:
        return json.loads(cipher().decrypt(row["blob"]).decode("utf-8"))
    except Exception:
        return None


def drop(ref: str):
    with db.tx() as c:
        c.execute("DELETE FROM vault WHERE ref=?", (ref,))


def redact(payload: dict) -> dict:
    """Safe-for-display version of a credential set."""
    out = {}
    secretish = ("secret", "key", "token", "password", "pass")
    for k, v in (payload or {}).items():
        if k.startswith("_"):                       # internal tokens, never shown
            continue
        if not isinstance(v, str):
            out[k] = v
        elif any(s in k.lower() for s in secretish):
            out[k] = (v[:3] + "•" * 8 + v[-3:]) if len(v) >= 8 else "•" * 8
        else:
            out[k] = v                              # not a secret — show it
    return out
