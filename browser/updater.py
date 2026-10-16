"""Update check: compares the running version with the newest GitHub release.

Runs on a worker thread once a start-up, at most once a day (the last answer
is cached). A failed check is silent and not cached - the browser works offline.
Downloads and the unpacked release live in one work folder per process in %TEMP%
(work_dir), which the install script removes once the new version is in place.
"""
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

API = 'https://api.github.com/repos/jayuan101/LiteBrowser/releases/latest'
ASSET_API = 'https://api.github.com/repos/jayuan101/LiteBrowser/releases/tags/{}'
ASSET = 'Lite-windows-x64.zip'
DAY = 24 * 3600
LEFTOVER = re.compile(r'^Lite-update-(\d+)(\.zip|\.cmd)?$')  # work folders, and zips/scripts of older versions


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
    """{'tag', 'url', 'name'} when a newer release exists, else None.

    A failed lookup is not cached. With force it raises, so the caller can say what went wrong.
    """
    if not force:
        try:
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            if time.time() - cached.get('checked', 0) < DAY:
                update = cached.get('update')
                # the cache can outlive an install: the banner must not pop for the running version
                if update and is_newer(str(update.get('tag') or ''), running):
                    return update
                return None
        except (OSError, ValueError):
            cached = None
    else:
        cached = None

    update = None
    try:
        req = urllib.request.Request(API, headers={'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rel = json.load(r)
    except Exception:
        if force:
            raise  # the Check button says why instead of "you are up to date"
        return None  # offline: not cached, so the next start asks again
    tag = str(rel.get('tag_name') or '')
    if tag and is_newer(tag, running):
        update = {'tag': tag, 'name': str(rel.get('name') or tag),
                  'url': str(rel.get('html_url') or 'https://github.com/jayuan101/LiteBrowser/releases')}

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


def work_dir() -> str:
    """This process's own update folder in %TEMP%: the downloaded zip and the unpacked release."""
    return os.path.join(tempfile.gettempdir(), f'Lite-update-{os.getpid()}')


def clean_leftovers(max_age: float = 3600) -> None:
    """Remove update folders, zips and scripts that other Lite processes left in %TEMP%."""
    tmp, now = tempfile.gettempdir(), time.time()
    try:
        names = os.listdir(tmp)
    except OSError:
        return
    for name in names:
        m = LEFTOVER.match(name)
        if not m or int(m.group(1)) == os.getpid():
            continue
        path = os.path.join(tmp, name)
        try:
            if now - os.path.getmtime(path) < max_age:
                continue  # another Lite may be mid-update
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass


def download(url: str, progress=lambda done, total: None, timeout: float = 60.0, folder: str | None = None) -> str:
    """Download the release zip into the work folder; returns its path."""
    folder = folder or work_dir()
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, ASSET)
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
    if total and done != total:
        raise OSError(f'the download stopped at {done:,} of {total:,} bytes')
    return dest


def unpack(zip_path: str) -> str:
    """Extract the zip next to itself, delete the zip and return the extracted folder that contains Lite.exe."""
    out = os.path.join(os.path.dirname(zip_path), 'app')
    shutil.rmtree(out, ignore_errors=True)  # a retry starts clean
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out)
    os.remove(zip_path)  # unpacked: the zip is no longer needed
    if os.path.isfile(os.path.join(out, 'Lite.exe')):  # zipped without a wrapping folder
        return out
    for name in sorted(os.listdir(out)):
        folder = os.path.join(out, name)
        if os.path.isdir(folder) and os.path.isfile(os.path.join(folder, 'Lite.exe')):
            return folder
    raise FileNotFoundError('the downloaded update has no Lite.exe')


def describe(error: BaseException) -> str:
    """A short, plain reason for the update banner and the settings page."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (403, 429):
            return 'GitHub is limiting requests right now, try again in an hour'
        return f'GitHub answered with error {error.code}'
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return 'no connection to GitHub'
    if isinstance(error, zipfile.BadZipFile):
        return 'the download was damaged'
    if isinstance(error, FileNotFoundError) and error.filename is None:
        return str(error)  # our own messages, e.g. the release has no zip
    if isinstance(error, OSError):
        return error.strerror or str(error) or 'a disk error'
    return str(error) or type(error).__name__
