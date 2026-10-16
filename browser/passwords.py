"""Saved passwords, locked to this Windows account.

Lite never sends a password anywhere and never shows one: they sit in
passwords.json encrypted by Windows DPAPI (only this Windows account can
decrypt them), and the only way out is the Copy button, which puts the
password on the clipboard. Imported from the CSV export file of another
browser - Chrome, Edge and Firefox all have "Export passwords" in their
password managers.
"""
import base64
import csv
import ctypes
import json
import os
from ctypes import wintypes

_COLS = {  # the export headers of Chrome, Edge and Firefox, and their common aliases
    'url': {'url', 'website', 'web site', 'site', 'address', 'login_uri', 'origin'},
    'user': {'username', 'user name', 'user', 'login', 'login_username', 'email', 'account'},
    'pw': {'password', 'pass', 'login_password'},
}


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(wintypes.BYTE))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(wintypes.BYTE)))


def _protect(text: str) -> str:
    """Encrypt with Windows DPAPI (this user only); returns base64."""
    data = text.encode('utf-8')
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(_blob(data)), None, None, None, None,
                                                  0, ctypes.byref(out)):
        raise OSError('Windows refused to encrypt the password')
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(b64: str) -> str:
    blob = _blob(base64.b64decode(b64))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob), None, None, None, None,
                                                   0, ctypes.byref(out)):
        raise OSError('This Windows account cannot unlock the saved passwords')
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class Passwords:
    """The vault: a JSON file of (url, username, encrypted password) rows."""

    def __init__(self, path: str):
        self.path = path
        self.rows: list[dict] = []
        try:
            with open(path, encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                self.rows = [r for r in loaded
                             if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ('url', 'user', 'pw'))]
        except (OSError, ValueError):
            self.rows = []

    def _save(self) -> None:
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.rows, f)
        os.replace(tmp, self.path)

    def items(self) -> list[dict]:
        """Rows without their secrets: what the passwords page shows."""
        return [{'url': r['url'], 'user': r['user']} for r in self.rows]

    def count(self) -> int:
        return len(self.rows)

    def add(self, url: str, user: str, pw: str) -> bool:
        """One login row; the password is encrypted the moment it arrives. False if already saved."""
        url, user = url.strip(), user.strip()
        if not url.startswith(('http://', 'https://')) or not pw:
            return False
        if any(r['url'] == url and r['user'] == user for r in self.rows):
            return False
        self.rows.append({'url': url, 'user': user, 'pw': _protect(pw)})
        self._save()
        return True

    def reveal(self, url: str, user: str) -> str:
        for r in self.rows:
            if r['url'] == url and r['user'] == user:
                return _unprotect(r['pw'])
        return ''

    def remove(self, url: str, user: str) -> None:
        self.rows = [r for r in self.rows if not (r['url'] == url and r['user'] == user)]
        self._save()

    def import_csv(self, path: str) -> tuple[int, int]:
        """Merge a browser's CSV password export; returns (added, skipped)."""
        added = skipped = 0
        with open(path, encoding='utf-8-sig', errors='replace', newline='') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return 0, 0
            cols = {k: next((h for h in reader.fieldnames if h.strip().lower() in v), None)
                    for k, v in _COLS.items()}
            if not cols['url'] or not cols['pw']:
                return 0, 0
            for row in reader:
                url = (row.get(cols['url']) or '').strip()
                pw = (row.get(cols['pw']) or '').strip()
                user = (row.get(cols['user']) or '').strip() if cols['user'] else ''
                if not url.startswith(('http://', 'https://')) or not pw:
                    skipped += 1
                    continue
                if self.add(url, user, pw):
                    added += 1
                else:
                    skipped += 1
        if added:
            self._save()
        return added, skipped
