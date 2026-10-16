"""Offline checks for browser/store.py and browser/updater.py - no network, no files left behind.

    python scratch/test_store.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import store, updater  # noqa: E402

failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      {detail}'))


tmp = tempfile.mkdtemp(prefix='lite-store-')

# ---- bookmarks -----------------------------------------------------------
bm = store.Bookmarks(os.path.join(tmp, 'bm.json'))
check('star adds and reports', bm.toggle('https://example.com/a?q=1#frag', 'Example') is True)
check('fragment ignored for matching', bm.is_starred('https://example.com/a?q=1#other'))
check('not starred: another page', not bm.is_starred('https://example.com/b'))
check('toggle again removes', bm.toggle('https://example.com/a?q=1') is False)
bm.toggle('https://www.shop.example/item', 'Shop')
check('hosts() drops scheme/path', bm.hosts() == {'www.shop.example'})
bm.retitle('https://www.shop.example/item', 'Renamed')
check('retitle updates bookmark', bm.items()[0]['title'] == 'Renamed')
check('retitle unknown is quiet', bm.retitle('https://x.example/', 'x') is None)

# persistence across restarts
bm.toggle('https://keep.example/', 'Keep')
bm.save()
bm2 = store.Bookmarks(os.path.join(tmp, 'bm.json'))
check('bookmarks survive restart', bm2.is_starred('https://keep.example/'))

# ---- history -------------------------------------------------------------
h = store.History(os.path.join(tmp, 'h.json'))
h.record('https://a.example/one', 'One')
h.record('https://a.example/two', 'Two')
h.record('https://a.example/two', 'Two again')  # same page: one entry, moved to top
check('same url recorded once', len(h.recent()) == 2)
check('newest first', h.recent()[0]['url'] == 'https://a.example/two')
check('non-http skipped', h.record('about:blank', '') is None and len(h.recent()) == 2)
h.retitle('https://a.example/two', 'The Real Title')
check('retitle updates history', h.recent()[0]['title'] == 'The Real Title')
h.delete('https://a.example/one')
check('delete removes the page', all(e['url'] != 'https://a.example/one' for e in h.recent()))
for i in range(store.History.LIMIT + 50):
    h.record(f'https://s.example/{i}', str(i))
check('history is capped', len(h.recent()) <= store.History.LIMIT)
h.clear()
check('clear empties history', h.recent() == [])

# ---- settings ------------------------------------------------------------
s = store.Settings(os.path.join(tmp, 's.json'))
check('setting default', s.get('nope', 7) == 7)
s.set('tab_sleep', False)
s2 = store.Settings(os.path.join(tmp, 's.json'))
check('setting survives restart', s2.get('tab_sleep', True) is False)

# ---- cookie rule ---------------------------------------------------------
starred = {'example.com'}
open_hosts = {'news.site.org'}
check('cookie: starred site kept', store.host_keeps_cookie('example.com', starred, open_hosts))
check('cookie: subdomain of starred kept', store.host_keeps_cookie('www.example.com', starred, open_hosts))
check('cookie: parent match kept', store.host_keeps_cookie('example.com', {'www.example.com'}, set()))
check('cookie: open site kept', store.host_keeps_cookie('news.site.org', set(), open_hosts))
check('cookie: stranger swept', not store.host_keeps_cookie('tracker.io', starred, open_hosts))
check('cookie: tracker under starred host swept', not store.host_keeps_cookie('track.example.com.evil.io', starred, set()))
check('cookie: leading dot handled', store.host_keeps_cookie('.example.com', starred, set()))
check('cookie: empty domain swept', not store.host_keeps_cookie('', starred, set()))

# ---- updater -------------------------------------------------------------
check('version equal is not newer', not updater.is_newer('v0.2.0', '0.2.0'))
check('version newer', updater.is_newer('v0.3.0', '0.2.0'))
check('version older is not newer', not updater.is_newer('v0.1.9', '0.2.0'))
check('version longer wins', updater.is_newer('0.2.0.1', '0.2.0'))
check('garbage version is not newer', not updater.is_newer('', '0.2.0'))

import json as _json  # noqa: E402
import pathlib  # noqa: E402
import time as _time  # noqa: E402
import urllib.error  # noqa: E402
import zipfile  # noqa: E402

real_api = updater.API
cache = os.path.join(tmp, 'updates.json')
updater.API = 'http://127.0.0.1:9/releases/latest'  # nothing listens there: an offline check
check('offline check: no crash, no update', updater.check(cache, '0.0.1', timeout=2) is None)
check('offline check: not cached, the next start asks again', not os.path.exists(cache))
try:
    updater.check(cache, '0.0.1', timeout=2, force=True)
    forced = None
except Exception as e:  # noqa: BLE001
    forced = updater.describe(e)
check('offline forced check: raises a plain reason', forced == 'no connection to GitHub', forced)

release = os.path.join(tmp, 'latest.json')
with open(release, 'w', encoding='utf-8') as f:
    _json.dump({'tag_name': 'v0.9.0', 'name': 'Lite 0.9.0', 'html_url': 'https://example.com/r'}, f)
updater.API = pathlib.Path(release).as_uri()
found = updater.check(cache, '0.2.0', force=True)
check('successful check finds the newer release', bool(found) and found['tag'] == 'v0.9.0', found)
updater_json = None
try:
    with open(cache, encoding='utf-8') as f:
        updater_json = _json.load(f)
except OSError:
    pass
check('cache written after a successful check', updater_json is not None and 'checked' in updater_json)
updater.API = real_api
os.remove(cache)

check('reason: rate limited', 'limiting' in updater.describe(urllib.error.HTTPError('u', 403, 'x', None, None)))
check('reason: damaged zip', updater.describe(zipfile.BadZipFile('x')) == 'the download was damaged')
check('reason: our own message', updater.describe(FileNotFoundError('the release has no x.zip')) ==
      'the release has no x.zip')

wd = updater.work_dir()
check('work folder is per process and inside %TEMP%, never %TEMP% itself',
      os.path.dirname(wd) == tempfile.gettempdir() and wd.endswith(f'Lite-update-{os.getpid()}'), wd)


def release_zip(folder, wrapped):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'source.zip')
    with zipfile.ZipFile(path, 'w') as z:
        prefix = 'Lite/' if wrapped else ''
        z.writestr(prefix + 'Lite.exe', b'MZ fake')
        z.writestr(prefix + '_internal/base_library.zip', b'x')
    return path


for wrapped in (True, False):
    label = 'wrapped in a Lite folder' if wrapped else 'no wrapping folder'
    work = os.path.join(tmp, f'Lite-update-{int(wrapped)}')
    src = release_zip(os.path.join(tmp, f'src-{int(wrapped)}'), wrapped)
    got = updater.download(pathlib.Path(src).as_uri(), folder=work)
    check(f'download ({label}): lands in the work folder', got == os.path.join(work, updater.ASSET), got)
    app_dir = updater.unpack(got)
    check(f'unpack ({label}): finds Lite.exe', os.path.isfile(os.path.join(app_dir, 'Lite.exe')))
    check(f'unpack ({label}): strictly inside the work folder', app_dir.startswith(work + os.sep), app_dir)
    check(f'unpack ({label}): the zip is deleted', not os.path.exists(got))
    check(f'unpack ({label}): a retry starts clean', updater.unpack(updater.download(
        pathlib.Path(src).as_uri(), folder=work)) == app_dir)

empty = os.path.join(tmp, 'Lite-update-empty')
os.makedirs(empty)
with zipfile.ZipFile(os.path.join(empty, updater.ASSET), 'w') as z:
    z.writestr('readme.txt', b'no app here')
try:
    updater.unpack(os.path.join(empty, updater.ASSET))
    no_exe = None
except FileNotFoundError as e:
    no_exe = updater.describe(e)
check('a zip without Lite.exe is refused with a reason', no_exe == 'the downloaded update has no Lite.exe', no_exe)

# leftovers in %TEMP% from other Lite processes: old ones go, fresh ones and look-alikes stay
fake_temp = os.path.join(tmp, 'temp')
os.makedirs(fake_temp)
old = _time.time() - 2 * 3600
names = {'Lite-update-111': 'dir', 'Lite-update-222.zip': 'file', 'Lite-update-333.cmd': 'file',
         'Lite-update-444': 'fresh', f'Lite-update-{os.getpid()}': 'mine', 'Lite-update-notes': 'file',
         'Lite-updater-555': 'dir'}
for name, kind in names.items():
    path = os.path.join(fake_temp, name)
    if kind == 'file':
        open(path, 'w').close()
    else:
        os.makedirs(os.path.join(path, 'app'))
    if kind != 'fresh':
        os.utime(path, (old, old))
saved_tempdir, tempfile.tempdir = tempfile.tempdir, fake_temp
try:
    updater.clean_leftovers()
finally:
    tempfile.tempdir = saved_tempdir
left = set(os.listdir(fake_temp))
check('leftovers: old update folders, zips and scripts removed',
      not left & {'Lite-update-111', 'Lite-update-222.zip', 'Lite-update-333.cmd'}, left)
check('leftovers: a fresh one (maybe mid-update) is kept', 'Lite-update-444' in left, left)
check("leftovers: this process's own folder is kept", f'Lite-update-{os.getpid()}' in left, left)
check('leftovers: look-alike names are not touched', {'Lite-update-notes', 'Lite-updater-555'} <= left, left)

# ---- default browser registration (throwaway keys, cleaned up) -------------
import winreg
from browser import default_browser as db

test_client = r'Software\Clients\StartMenuInternet\LiteTestBrowser'
test_progid = 'LiteTestBrowser.URL'
db.register(r'C:\Program Files\Lite\Lite.exe', client_key=test_client, progid=test_progid,
            registered_as='LiteTestBrowser')
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_client + r'\Capabilities\URLAssociations') as k:
    http_val, _ = winreg.QueryValueEx(k, 'http')
    https_val, _ = winreg.QueryValueEx(k, 'https')
check('default browser: URL associations written', http_val == test_progid and https_val == test_progid)
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\RegisteredApplications') as k:
    target, _ = winreg.QueryValueEx(k, 'LiteTestBrowser')
check('default browser: registered as an application', target.endswith(r'\Capabilities'))
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\{test_progid}\shell\open\command') as k:
    cmd_val, _ = winreg.QueryValueEx(k, None)
check('default browser: the open command hands over the URL', r'"%1"' in cmd_val)
check('default browser: is_default answers a bool', isinstance(db.is_default(), bool))
for key in (test_client + r'\shell\open\command', test_client + r'\shell\open', test_client + r'\shell',
            test_client + r'\DefaultIcon', test_client + r'\Capabilities\URLAssociations',
            test_client + r'\Capabilities', test_client,
            rf'Software\Classes\{test_progid}\shell\open\command', rf'Software\Classes\{test_progid}\shell\open',
            rf'Software\Classes\{test_progid}\shell', rf'Software\Classes\{test_progid}\DefaultIcon',
            rf'Software\Classes\{test_progid}'):
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\RegisteredApplications', 0, winreg.KEY_SET_VALUE) as k:
    winreg.DeleteValue(k, 'LiteTestBrowser')
# cleanup ran to the end without an OSError: the throwaway keys are gone

# a fresh cache entry for a version the app has already become must not bring the banner back
import json as _json
with open(cache, 'w', encoding='utf-8') as f:
    _json.dump({'checked': __import__('time').time(),
                'update': {'tag': 'v9.9.9', 'name': 'Lite 9.9.9', 'url': 'u'}}, f)
check('cached update equal to running: not shown', updater.check(cache, '9.9.9') is None)
check('cached update older than running: not shown', updater.check(cache, '9.9.10') is None)
check('cached update newer than running: shown', updater.check(cache, '9.9.8') is not None)

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
