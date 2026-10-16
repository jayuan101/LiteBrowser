"""Update check: compares the running version with the newest GitHub release.

Runs on a worker thread once a start-up, at most once a day (the last answer
is cached). A check failure is silent - the browser works offline.
"""
import json
import os
import re
import time
import urllib.request

API = 'https://api.github.com/repos/jayuan101/LiteBrowser/releases/latest'
RELEASES_URL = 'https://github.com/jayuan101/LiteBrowser/releases'
DAY = 24 * 3600


def _parts(version: str) -> tuple[int, ...]:
    """'0.2.0' -> (0, 2, 0); anything without digits compares as (0,)."""
    m = re.search(r'\d[\d.]*', version or '')
    return tuple(int(p) for p in m.group(0).rstrip('.').split('.')) if m else (0,)


def is_newer(latest: str, running: str) -> bool:
    a, b = _parts(latest), _parts(running)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def check(cache_path: str, running: str, timeout: float = 8.0) -> dict | None:
    """{'tag', 'url', 'name'} when a newer release exists, else None."""
    try:
        with open(cache_path, encoding='utf-8') as f:
            cached = json.load(f)
        if time.time() - cached.get('checked', 0) < DAY:
            return cached.get('update')
    except (OSError, ValueError):
        cached = None

    update = None
    try:
        req = urllib.request.Request(API, headers={'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rel = json.load(r)
        tag = str(rel.get('tag_name') or '')
        if tag and is_newer(tag, running):
            update = {'tag': tag, 'name': str(rel.get('name') or tag),
                      'url': str(rel.get('html_url') or 'https://github.com/jayuan101/LiteBrowser/releases')}
    except Exception:
        update = None

    try:
        tmp = cache_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'checked': time.time(), 'update': update}, f)
        os.replace(tmp, cache_path)
    except OSError:
        pass
    return update
