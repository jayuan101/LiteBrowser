"""Import bookmarks from other browsers on this PC (Chrome, Edge, Brave, Firefox).

Every browser keeps bookmarks unencrypted in its own profile folder, so Lite
reads them straight from there - read-only: nothing in the other browser is
touched, changed or deleted. A Netscape bookmarks.html export file works too.
"""
import json
import os
import shutil
import sqlite3
import tempfile
from html.parser import HTMLParser

CHROMIUM = [  # label, profile root under %LOCALAPPDATA%
    ('Chrome', os.path.join('Google', 'Chrome')),
    ('Edge', os.path.join('Microsoft', 'Edge')),
    ('Brave', os.path.join('BraveSoftware', 'Brave-Browser')),
    ('Opera', os.path.join('Opera Software')),  # bookmarks sit directly under this folder
]


def _local(*parts: str) -> str:
    return os.path.join(os.environ.get('LOCALAPPDATA', ''), *parts)


def _roaming(*parts: str) -> str:
    return os.path.join(os.environ.get('APPDATA', ''), *parts)


def find_bookmarks() -> list[tuple[str, list[tuple[str, str]]]]:
    """(label, [(title, url)]) for every browser on this PC that has bookmarks."""
    out = []
    for label, rows in _chromium() + _firefox():
        if rows:
            out.append((label, rows))
    return out


def import_all(bookmarks) -> list[str]:
    """Merge every found browser's bookmarks; returns one 'Chrome: 42 new' note per source."""
    notes = []
    for label, rows in find_bookmarks():
        added = sum(1 for title, url in rows if bookmarks.add(url, title))
        if added:
            notes.append(f'{label}: {added} new')
    return notes


def import_html_file(bookmarks, path: str) -> int:
    """Merge a bookmarks.html export file (what every browser's bookmark manager exports)."""
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            html = f.read()
    except OSError:
        return 0
    p = _Netscape()
    try:
        p.feed(html)
    except Exception:
        return 0
    return sum(1 for title, url in p.out if bookmarks.add(url, title))


def _read_chromium(path: str) -> list[tuple[str, str]]:
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out: list[tuple[str, str]] = []

    def walk(node):
        if not isinstance(node, dict):
            return
        url = node.get('url') or ''
        if node.get('type') == 'url' and url.startswith(('http://', 'https://')):
            out.append((node.get('name') or url, url))
        for child in node.get('children') or []:
            walk(child)

    for root in (data.get('roots') or {}).values():
        walk(root)
    return out


def _chromium() -> list[tuple[str, list]]:
    out = []
    for label, root_name in CHROMIUM:
        root = _local(root_name)
        if not os.path.isdir(root):
            continue
        rows: list[tuple[str, str]] = []
        for base in (root, os.path.join(root, 'User Data')):  # real installs keep profiles under User Data
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.listdir(base)):
                path = os.path.join(base, entry, 'Bookmarks')
                if os.path.isfile(path):
                    rows += _read_chromium(path)
        if rows:
            out.append((label, rows))
    return out


def _firefox() -> list[tuple[str, list]]:
    root = _roaming('Mozilla', 'Firefox', 'Profiles')
    if not os.path.isdir(root):
        return []
    rows: list[tuple[str, str]] = []
    for profile in os.listdir(root):
        places = os.path.join(root, profile, 'places.sqlite')
        if not os.path.isfile(places):
            continue
        rows += _read_firefox(places)
    return [('Firefox', rows)] if rows else []


def _read_firefox(places: str) -> list[tuple[str, str]]:
    # Firefox keeps the database locked while it runs, so Lite reads a copy.
    tmp = os.path.join(tempfile.mkdtemp(prefix='lite-places-'), 'places.sqlite')
    try:
        shutil.copy2(places, tmp)
        con = sqlite3.connect(tmp)
        try:
            rows = con.execute(
                "SELECT p.title, p.url FROM moz_bookmarks b JOIN moz_places p ON b.fk = p.id "
                "WHERE b.type = 1 AND p.url LIKE 'http%'").fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return []
    finally:
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    return [(title or url, url) for title, url in rows]


class _Netscape(HTMLParser):
    """Collects (title, href) pairs from a Netscape bookmarks.html export file."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[tuple[str, str]] = []
        self._href = ''
        self._title: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            href = dict(attrs).get('href', '')
            if href.startswith(('http://', 'https://')):
                self._href = href
                self._title = []

    def handle_data(self, data):
        if self._href:
            self._title.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self._href:
            self.out.append((' '.join(''.join(self._title).split()) or self._href, self._href))
            self._href = ''
