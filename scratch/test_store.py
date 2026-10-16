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

cache = os.path.join(tmp, 'updates.json')
check('check with no network file: no crash', updater.check(cache, '0.0.1', timeout=0.001) is None)
updater_json = None
try:
    with open(cache, encoding='utf-8') as f:
        updater_json = __import__('json').load(f)
except OSError:
    pass
check('cache written after a check', updater_json is not None and 'checked' in updater_json)

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
