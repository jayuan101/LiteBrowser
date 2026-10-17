"""Saved passwords, locked to this Windows account.

Lite never sends a password anywhere and never shows one: they sit in
passwords.json encrypted by Windows DPAPI (only this Windows account can
decrypt them). The ways out are the Copy button, which puts the password on
the clipboard, and filling it into its own site when you pick it from the key
button (autofill.py). They come from signing in - Lite offers to save what you
typed - or from the CSV export file of another browser: Chrome, Edge and
Firefox all have "Export passwords" in their password managers.
"""
import base64
import csv
import ctypes
import json
import os
from ctypes import wintypes
from urllib.parse import urlsplit

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


def _site(url: str) -> tuple[str, str, int] | None:
    """(scheme, host without 'www.', port): the site a login belongs to; None for anything but http(s)."""
    try:
        p = urlsplit((url or '').strip())
        scheme, host = p.scheme.lower(), (p.hostname or '').lower().rstrip('.')
        port = p.port or {'http': 80, 'https': 443}.get(scheme, 0)
    except ValueError:
        return None
    if scheme not in ('http', 'https') or not host:
        return None
    try:
        host = host.encode('idna').decode('ascii')  # exports may spell international names in Unicode
    except UnicodeError:
        pass
    return scheme, host.removeprefix('www.'), port


def _same_site(saved, page) -> bool:
    if not saved or not page or saved[1] != page[1]:
        return False
    if saved[0] == page[0]:
        return saved[2] == page[2]
    # a site that moved to https keeps its logins; an https login never goes to an unencrypted page
    return saved[0] == 'http' and page[0] == 'https' and saved[2] == 80 and page[2] == 443


def matches(saved_url: str, page_url: str) -> bool:
    """Does a login saved for saved_url belong on page_url? Same host (with or without www.), same port."""
    return _same_site(_site(saved_url), _site(page_url))


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
        self._sites = None  # the site of every row, worked out once: the toolbar asks on every redraw

    def _save(self) -> None:
        self._sites = None
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.rows, f)
        os.replace(tmp, self.path)

    def _rows_for(self, page_url: str) -> list[dict]:
        if self._sites is None:
            self._sites = [_site(r['url']) for r in self.rows]
        page = _site(page_url)
        return [r for r, s in zip(self.rows, self._sites) if _same_site(s, page)] if page else []

    def logins_for(self, page_url: str) -> list[dict]:
        """The saved logins of the page's site, one per username: [{'url', 'user'}], without secrets."""
        seen, out = set(), []
        for r in self._rows_for(page_url):
            if r['user'] not in seen:
                seen.add(r['user'])
                out.append({'url': r['url'], 'user': r['user']})
        return out

    def has_logins(self, page_url: str) -> bool:
        return bool(self._rows_for(page_url))

    def status(self, page_url: str, user: str, pw: str) -> str:
        """'same' when this login is saved already, 'changed' when its password is not the saved one, else 'new'."""
        for r in self._rows_for(page_url):
            if r['user'] == user:
                try:
                    return 'same' if _unprotect(r['pw']) == pw else 'changed'
                except (OSError, ValueError):
                    return 'changed'
        return 'new'

    def remember(self, page_url: str, user: str, pw: str) -> bool:
        """Save a login typed on a page: the new password of a saved one, or a new row for its site."""
        site = _site(page_url)
        if not site or not pw:
            return False
        secret = _protect(pw)
        rows = [r for r in self._rows_for(page_url) if r['user'] == user]
        for r in rows:
            r['pw'] = secret
        if not rows:
            scheme, _, port = site
            host = (urlsplit(page_url).hostname or '').lower()
            host = f'[{host}]' if ':' in host else host  # IPv6
            default = {'http': 80, 'https': 443}[scheme]
            self.rows.append({'url': f'{scheme}://{host}' + (f':{port}' if port != default else '') + '/',
                              'user': user.strip(), 'pw': secret})
        self._save()
        return True

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
        self._save()  # also forgets the cached sites
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
