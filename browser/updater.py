"""Update check: compares the running version with the newest GitHub release.

Runs on a worker thread once a start-up, at most once a day (the last answer
is cached). A check failure is silent - the browser works offline.
"""
import json
import os
import re
import tempfile
import time
import urllib.request
import zipfile

API = 'https://api.github.com/repos/jayuan101/LiteBrowser/releases/latest'
ASSET_API = 'https://api.github.com/repos/jayuan101/LiteBrowser/releases/tags/{}'
RELEASES_URL = 'https://github.com/jayuan101/LiteBrowser/releases'
ASSET = 'Lite-windows-x64.zip'
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


def check(cache_path: str, running: str, timeout: float = 8.0, force: bool = False) -> dict | None:
    """{'tag', 'url', 'name'} when a newer release exists, else None."""
    if not force:
        try:
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            if time.time() - cached.get('checked', 0) < DAY:
                return cached.get('update')
        except (OSError, ValueError):
            cached = None
    else:
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


def asset_url(tag: str, timeout: float = 8.0) -> str:
    """Browser-download URL of the release's Lite-windows-x64.zip asset; '' when it has none."""
    req = urllib.request.Request(ASSET_API.format(tag),
                                 headers={'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rel = json.load(r)
    for asset in rel.get('assets') or []:
        if asset.get('name') == ASSET:
            return str(asset.get('browser_download_url') or '')
    return ''


def download(url: str, progress=lambda done, total: None, timeout: float = 60.0) -> str:
    """Download the release zip to a temp file; returns its path."""
    dest = os.path.join(tempfile.gettempdir(), f'Lite-update-{os.getpid()}.zip')
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            progress(done, total)
    return dest


def unpack(zip_path: str) -> str:
    """Extract the zip and return the extracted folder that contains Lite.exe."""
    out = os.path.join(tempfile.gettempdir(), f'Lite-update-{os.getpid()}')
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out)
    if os.path.isfile(os.path.join(out, 'Lite.exe')):  # zipped without a wrapping folder
        return out
    for name in sorted(os.listdir(out)):
        folder = os.path.join(out, name)
        if os.path.isdir(folder) and os.path.isfile(os.path.join(folder, 'Lite.exe')):
            return folder
    raise FileNotFoundError('the downloaded update has no Lite.exe')
