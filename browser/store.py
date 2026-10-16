"""Sites you starred, pages you visited, and Lite's own settings.

Three small JSON files in Lite's data folder. Bookmarks keep one entry per
page; history is capped and forgets itself; settings hold the toggles that
the menu shows. Nothing here ever leaves the machine.
"""
import json
import os
import time
from urllib.parse import urlsplit, urlunsplit


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or '').lower()
    except ValueError:
        return ''


def _no_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))


def host_keeps_cookie(cookie_domain: str, starred: set[str], open_hosts: set[str]) -> bool:
    """A cookie survives the sweep when its site is starred or open in a tab.

    Matching is on registered domain: a star for example.com keeps cookies of
    www.example.com, and the other way round.
    """
    d = (cookie_domain or '').lstrip('.').lower()
    if not d:
        return False
    for h in starred | open_hosts:
        if d == h or d.endswith('.' + h) or h.endswith('.' + d):
            return True
    return False


class _Store:
    """A JSON file loaded once and rewritten atomically when dirty."""

    def __init__(self, path: str, default):
        self.path = path
        self.data = default()
        try:
            with open(path, encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, type(self.data)):
                self.data = loaded
        except (OSError, ValueError):
            pass
        self._dirty = False

    def save(self) -> None:
        if not self._dirty or not self.path:
            return
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass


class Bookmarks(_Store):
    """Starred pages, oldest kept in the order they were starred."""

    def __init__(self, path: str):
        super().__init__(path, dict)

    def toggle(self, url: str, title: str = '') -> bool:
        url = _no_fragment(url)
        starred = self.is_starred(url)
        if starred:
            del self.data[url]
        else:
            self.data[url] = {'title': title or url, 'ts': time.time()}
        self._dirty = True
        return not starred

    def remove(self, url: str) -> None:
        if _no_fragment(url) in self.data:
            del self.data[_no_fragment(url)]
            self._dirty = True

    def retitle(self, url: str, title: str) -> None:
        url = _no_fragment(url)
        if url in self.data and title:
            self.data[url]['title'] = title
            self._dirty = True

    def is_starred(self, url: str) -> bool:
        return _no_fragment(url) in self.data

    def items(self) -> list[dict]:
        return [{'url': u, 'title': v.get('title', u)} for u, v in self.data.items()]

    def hosts(self) -> set[str]:
        return {h for h in (_host_of(u) for u in self.data) if h}


class History(_Store):
    """Pages opened in normal tabs, newest first. Capped so the file stays small."""

    LIMIT = 4000

    def __init__(self, path: str):
        super().__init__(path, list)

    def record(self, url: str, title: str) -> None:
        url = _no_fragment(url)
        if not url.startswith(('http://', 'https://')):
            return
        self.data = [e for e in self.data if e.get('url') != url]
        self.data.insert(0, {'url': url, 'title': title or url, 'ts': time.time()})
        del self.data[self.LIMIT:]
        self._dirty = True

    def retitle(self, url: str, title: str) -> None:
        url = _no_fragment(url)
        for e in self.data[:8]:  # only the newest entries: the page just told us its title
            if e.get('url') == url:
                if title and e['title'] != title:
                    e['title'] = title
                    self._dirty = True
                break

    def recent(self, n: int = 500) -> list[dict]:
        return self.data[:n]

    def delete(self, url: str) -> None:
        url = _no_fragment(url)
        self.data = [e for e in self.data if e.get('url') != url]
        self._dirty = True

    def clear(self) -> None:
        self.data = []
        self._dirty = True


class Settings(_Store):
    """Menu toggles: on-disk defaults with in-memory answers for private sessions."""

    def __init__(self, path: str):
        super().__init__(path, dict)

    def get(self, key: str, default):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self._dirty = True
        self.save()
