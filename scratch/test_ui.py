"""Static checks for the UI: every Python module compiles and every page's JavaScript parses.

    python scratch/test_ui.py
"""
import glob
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import autofill, guard, importer, pages, privacy  # noqa: E402
from browser.passwords import Passwords, matches  # noqa: E402
from browser.store import Bookmarks  # noqa: E402

failures = 0
NODE = shutil.which('node')


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      {detail}'))


for path in sorted(glob.glob(os.path.join(ROOT, 'browser', '*.py'))):
    try:
        py_compile.compile(path, doraise=True)
        check(f'compiles: {os.path.basename(path)}', True)
    except py_compile.PyCompileError as e:
        check(f'compiles: {os.path.basename(path)}', False, str(e)[:300])

# Settings.get(key, default) has no default of its own: a call without one raises inside a
# WebView2 or timer callback (it broke every tab's setup and the tab-sleep timer in 0.2.4-0.2.6)
no_default = []
for path in sorted(glob.glob(os.path.join(ROOT, 'browser', '*.py'))):
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            for m in re.finditer(r'settings\.get\(([^()]*)\)', line):
                if ',' not in m.group(1):
                    no_default.append(f'{os.path.basename(path)}:{n}: {line.strip()}')
check('every settings.get() passes a default', not no_default, '\n      '.join(no_default))


def scripts_ok(name, html):
    blocks = re.findall(r'<script>(.*?)</script>', html, re.S)
    if not NODE:
        print(f'SKIP  {name} (node not installed)')
        return
    path = os.path.join(tempfile.mkdtemp(prefix='lite-ui-'), 'page.js')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n;\n'.join(blocks))
    r = subprocess.run([NODE, '--check', path], capture_output=True, text=True)
    check(f'JavaScript parses: {name} ({len(blocks)} script block(s))', r.returncode == 0 and bool(blocks),
          r.stderr.strip()[:400])


for ui in ('chrome.html', 'panel.html'):
    with open(os.path.join(ROOT, 'browser', 'ui', ui), encoding='utf-8') as f:
        scripts_ok(ui, f.read())
counts = {'blocked': 12345, 'links': 87, 'popups': 2, 'dangerous': 1}
scripts_ok('new tab page', pages.new_tab_html(counts, '2026-10-15', '9 filter lists, 198,678 rules'))
scripts_ok('private new tab page', pages.new_tab_html(counts, '2026-10-15', '', private=True))
scripts_ok('warning page', pages.blocked_html('https://evil.example/"<x>', 'Reason with "quotes" & <tags>', 'warn', 'danger'))
scripts_ok('block page', pages.blocked_html('javascript:alert(1)', 'scripts', 'block'))
scripts_ok('library page', pages.library_html([{'url': 'https://a.example/', 'title': 'A'}],
                                              [{'url': 'https://b.example/', 'title': 'B', 'ts': 0}]))
scripts_ok('empty library page', pages.library_html([], []))
scripts_ok('passwords library page', pages.library_html([], [], 'passwords',
                                                        passwords=[{'url': 'https://s.example/', 'user': 'u', 'pw': 'p'}],
                                                        note='Imported 3 passwords'))
scripts_ok('reader page', pages.reader_html({'title': 'T" & t', 'url': 'https://x.example/a',
                                             'blocks': [{'t': 'p', 'x': 'Hi <there>'}, {'t': 'li', 'x': 'one'}]}))
scripts_ok('sleeping page', pages.sleeping_html('https://x.example/'))
scripts_ok('settings page', pages.settings_html(True, True, 30, True, '0.2.0'))
scripts_ok('search results page', pages.search_results_html('test query', [
    {'title': 'Example', 'url': 'https://x.example/a', 'snippet': 'A snippet with <html> in it'}]))

def js_ok(name, js):
    if not NODE:
        return
    path = os.path.join(tempfile.mkdtemp(prefix='lite-ui-'), 'injected.js')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(js)
    r = subprocess.run([NODE, '--check', path], capture_output=True, text=True)
    check(f'JavaScript parses: {name}', r.returncode == 0, r.stderr.strip()[:400])


js_ok('find bar script', pages.FIND_JS)
js_ok('reader collector script', pages.READER_JS)
js_ok('block page go()', 'function go(cmd){window.chrome.webview.postMessage({cmd:cmd})}')

html = pages.new_tab_html(counts, '2026-10-15', 'lists', version='0.2.1')
check('new tab page shows the totals', all(s in html for s in ('12,345', '>87<', '>3<', 'Oct 15, 2026')), html[:200])
check('new tab page shows the version', 'Lite 0.2.1' in html)
check('new tab page promises no sponsored content', 'never shows ads, sponsored' in html)
ready = pages.new_tab_html(counts, '2026-10-15', '', banner={'tag': 'v0.3.0', 'name': 'Lite 0.3.0', 'url': 'u'},
                           update={'phase': 'ready'})
check('ready banner asks for a restart and links', 'Restart to update to Lite 0.3.0' in ready and "cmd:'update'" in ready)
downloading = pages.new_tab_html(counts, '2026-10-15', '', banner={'tag': 'v0.3.0', 'name': 'Lite 0.3.0', 'url': 'u'},
                                 update={'phase': 'downloading'})
check('downloading banner is not clickable', 'Updating in the background' in downloading and 'id=banner' not in downloading)
failed = pages.new_tab_html(counts, '2026-10-15', '', banner={'tag': 'v0.3.0', 'name': 'Lite 0.3.0', 'url': 'u'})
check('failed download invites a retry', "Couldn't download the update" in failed and "cmd:'update'" in failed)
failed_why = pages.new_tab_html(counts, '2026-10-15', '', banner={'tag': 'v0.3.0', 'name': 'Lite 0.3.0', 'url': 'u'},
                                update={'phase': 'idle', 'error': 'no connection to GitHub'})
check('failed download says why', 'no connection to GitHub - click to try again' in failed_why
      and "cmd:'update'" in failed_why)
check('failed download reason is escaped', '&lt;b&gt;' in pages.new_tab_html(
    counts, '', '', banner={'tag': 'v1', 'url': 'u'}, update={'phase': 'idle', 'error': '<b>'}))
check('no banner by default', 'class=banner' not in html)
st = pages.settings_html(False, True, 45, False, '0.2.0')
check('settings page shows the stored values',
      'value=45' in st and '<button class="tog" data-k=auto_delete_cookies' in st
      and '<button class="tog on" data-k=tab_sleep' in st,
      st[:200])
check('settings page sends changes back', all(s in st for s in ("cmd:'settings'", "cmd:'clearcookies'", "cmd:'updates'")))
check('settings page shows the check answer',
      'You are up to date' in pages.settings_html(False, True, 45, False, '0.2.0', update_note='You are up to date - Lite 0.2.0 is the newest release.')
      and 'You are up to date' not in st)
sr = pages.search_results_html('q "<x>', [{'title': 'T', 'url': 'https://x.example/', 'snippet': 's'}])
check('search page escapes the query and rows', '"<x>' not in sr and 'Searching' not in sr)
check('search page has a waiting state while fetching', 'Searching' in pages.search_results_html('q', None))
check('search page explains a failed fetch', 'Could not reach' in pages.search_results_html('q', []))

# ---- bookmarks bar, password vault, imports --------------------------------
lib = pages.library_html([{'url': 'https://a.example/', 'title': 'A & B'}], [], 'bookmarks', note='Chrome: 2 new')
check('library page shows the import note once', 'Chrome: 2 new' in lib and lib.count('Chrome: 2 new') == 1)
pw = pages.library_html([], [], 'passwords',
                        passwords=[{'url': 'https://s.example/login', 'user': 'ann <x>', 'pw': 'S3cret!'}], note='')
check('passwords page shows hosts and users but never secrets',
      's.example' in pw and 'ann' in pw and 'S3cret!' not in pw and 'Copy password' in pw)
st = pages.settings_html(False, True, 45, False, '0.2.0', bookmarks_bar=True)
check('settings page has a bookmarks bar toggle', 'data-k=bookmarks_bar' in st)
check('settings page has the performance toggle', 'data-k=performance_mode' in st)
st_perf = pages.settings_html(False, True, 45, False, '0.2.0', performance=True)
check('performance toggle shows on', '<button class="tog on" data-k=performance_mode' in st_perf)
check('settings page offers to become the default browser',
      'Make Lite the default' in st and "cmd:'setdefault'" in st)
check('settings page knows when Lite is the default',
      'Lite opens links from every app' in pages.settings_html(False, True, 45, False, '0.2.0', default_ok=True))

with tempfile.TemporaryDirectory(prefix='lite-import-') as d:
    chrome_dir = os.path.join(d, 'Google', 'Chrome', 'Default')
    os.makedirs(chrome_dir)
    bm = {'roots': {'bookmark_bar': {'children': [
        {'type': 'url', 'name': 'Example', 'url': 'https://example.com/'},
        {'type': 'url', 'name': 'Sub', 'url': 'https://sub.example/deep?x=1#frag'},
        {'type': 'folder', 'name': 'f', 'children': [{'type': 'url', 'name': 'Bad', 'url': 'javascript:x'}]},
    ]}}, 'other': {'children': []}}
    with open(os.path.join(chrome_dir, 'Bookmarks'), 'w', encoding='utf-8') as f:
        json.dump(bm, f)
    edge_dir = os.path.join(d, 'Microsoft', 'Edge', 'User Data', 'Default')
    os.makedirs(edge_dir)
    with open(os.path.join(edge_dir, 'Bookmarks'), 'w', encoding='utf-8') as f:  # the real Chrome-family layout
        json.dump({'roots': {'bookmark_bar': {'children': [
            {'type': 'url', 'name': 'Edge Site', 'url': 'https://edge.example/'}]}}}, f)
    saved_local = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = d  # find_bookmarks() reads this at call time
    try:
        found = importer.find_bookmarks()
        check('importer reads both Chromium layouts (bare profile and User Data)',
              [(label, len(rows)) for label, rows in found] == [('Chrome', 2), ('Edge', 1)], str(found))
        store = Bookmarks(os.path.join(d, 'bm.json'))
        notes = importer.import_all(store)
        check('importer adds the found bookmarks once', len(store.items()) == 3 and notes == ['Chrome: 2 new', 'Edge: 1 new'],
              f'{len(store.items())} {notes}')
        notes2 = importer.import_all(store)
        check('importing again adds nothing and says so quietly', notes2 == [], str(notes2))
    finally:
        if saved_local is None:
            del os.environ['LOCALAPPDATA']
        else:
            os.environ['LOCALAPPDATA'] = saved_local
    html_path = os.path.join(d, 'bookmarks.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write('<!DOCTYPE NETSCAPE-Bookmark-file1><DL><DT><A HREF="https://html.example/">'
                'From HTML</A><DT><A HREF="ftp://skip.me/">Nope</A></DL>')
    n = importer.import_html_file(store, html_path)
    check('Netscape HTML import works', n == 1 and store.is_starred('https://html.example/'), str(n))

vault_path = os.path.join(tempfile.mkdtemp(prefix='lite-pw-'), 'pw.json')
v = Passwords(vault_path)
secret = 'Zq7#TrW!42Lm'
check('passwords add and dedupe', v.add('https://a.example/', 'ann', secret) and
      not v.add('https://a.example/', 'ann', 'other') and v.count() == 1)
check('passwords are never stored in the clear', secret not in open(vault_path, encoding='utf-8').read())
check('passwords roundtrip through DPAPI', Passwords(vault_path).reveal('https://a.example/', 'ann') == secret)
check('passwords reject junk rows', not v.add('notaurl', 'u', 'p') and not v.add('https://b.example/', 'u', ''))
csv_path = os.path.join(tempfile.mkdtemp(prefix='lite-csv-'), 'pw.csv')
with open(csv_path, 'w', encoding='utf-8-sig') as f:  # BOM, like Chrome's export
    f.write('name,url,username,password\nSite,https://c.example/,carol,Cs3cret,\n')
    f.write('Site,https://a.example/,ann,ignored-already-saved\n')
    f.write('Bad,notaurl,carol,also-ignored\n')
added, skipped = Passwords(vault_path).import_csv(csv_path)
check('CSV import maps Chrome columns and skips saved rows', added == 1 and skipped == 2, f'{added}/{skipped}')
fx_path = os.path.join(tempfile.mkdtemp(prefix='lite-csv-'), 'fx.csv')
with open(fx_path, 'w', encoding='utf-8') as f:
    f.write('"url","username","password"\n"https://d.example/","dave","Ds3cret"\n')
added, _ = Passwords(vault_path).import_csv(fx_path)
check('CSV import maps Firefox columns', added == 1)
warn = pages.blocked_html('https://evil.example/"<x>', 'r', 'warn')
check('warning page escapes the URL', '"<x>' not in warn.split('<script>')[0] and 'Continue anyway' in warn)
check('hard block has no way through', 'Continue anyway' not in pages.blocked_html('javascript:x', 'r', 'block'))
check('punycode is a warning (can continue), not a hard block',
      guard.check_navigation('https://xn--80ak6aa92e.com/').level == 'warn')
check('user@host is a warning, not a hard block', guard.check_navigation('https://google.com@evil.example/').level == 'warn')
check('javascript: stays blocked', guard.check_navigation('javascript:alert(1)').level == 'block')

# ---- password filling and saving (autofill.py + the vault's site matching) -----
js_ok('sign-in page script', autofill.LOGIN_JS)
tricky = 'p"a\'ss\\word</script>\n\u2028end'
fill = autofill.fill_js('https://login.example', 'ann "the" user', tricky)
js_ok('fill script (password with quotes, backslash, newline, </script>)', fill)
check('fill script refuses any other origin before it touches a field',
      fill.index('location.origin !== "https://login.example"') < fill.index('const user'))
check('fill script carries the password as one JSON string', json.dumps(tricky) in fill)
check('origin is what location.origin says', [autofill.origin(u) for u in (
    'https://Login.Example/path?q', 'https://a.example:443/', 'http://a.example:8080/x', 'file:///c:/x', 'data:text/html,x',
    'javascript:alert(1)', '')] == ['https://login.example', 'https://a.example', 'http://a.example:8080', '', '', '', ''])
check('site_host drops www.', autofill.site_host('https://www.shop.example/a') == 'shop.example')
check('login matches its own site, with or without www.',
      matches('https://www.a.example/login', 'https://a.example/') and matches('https://a.example/', 'https://www.a.example/x'))
check('login never matches another site or a lookalike',
      not any(matches('https://a.example/', u) for u in (
          'https://b.example/', 'https://a.example.evil.example/', 'https://evil-a.example/', 'https://sub.a.example/')))
check('an https login never goes to an http page', not matches('https://a.example/', 'http://a.example/'))
check('an http login works once the site moved to https', matches('http://a.example/', 'https://a.example/'))
check('ports must match', not matches('https://a.example:8443/', 'https://a.example/')
      and matches('http://localhost:8000/', 'http://localhost:8000/x'))
check('only http(s) logins match', not matches('ftp://a.example/', 'ftp://a.example/'))

v2 = Passwords(os.path.join(tempfile.mkdtemp(prefix='lite-pw2-'), 'pw.json'))
v2.add('https://www.site.example/signin', 'ann', 'one')
v2.add('https://site.example/', 'ann', 'dup-row')
v2.add('https://site.example/', 'bob', 'two')
v2.add('https://other.example/', 'eve', 'three')
check('logins_for: the site only, one row per username, no secrets',
      [r['user'] for r in v2.logins_for('https://site.example/login')] == ['ann', 'bob']
      and all('pw' not in r for r in v2.logins_for('https://site.example/')))
check('has_logins', v2.has_logins('https://www.site.example/') and not v2.has_logins('https://none.example/'))
check('status: same / changed / new', (v2.status('https://site.example', 'bob', 'two'),
      v2.status('https://site.example', 'bob', 'new-pw'), v2.status('https://site.example', 'carl', 'x'))
      == ('same', 'changed', 'new'))
v2.remember('https://site.example', 'bob', 'new-pw')
check('remember updates a changed password in place', v2.status('https://site.example', 'bob', 'new-pw') == 'same'
      and v2.count() == 4)
v2.remember('https://new.example:8443', 'zed', 'zz')
check('remember adds a new site as its origin', v2.reveal('https://new.example:8443/', 'zed') == 'zz'
      and v2.logins_for('https://new.example:8443/a') == [{'url': 'https://new.example:8443/', 'user': 'zed'}])
check('remember refuses an empty password or a non-web page',
      not v2.remember('https://new.example', 'x', '') and not v2.remember('file:///c:/', 'x', 'y'))
check('the vault survives a restart with the new rows', Passwords(v2.path).status('https://new.example:8443', 'zed', 'zz') == 'same')

st = pages.settings_html(False, True, 45, False, '0.2.8')
check('settings: restore tabs and passwords switches, on by default',
      '<button class="tog on" data-k=restore_tabs' in st and '<button class="tog on" data-k=password_manager' in st
      and 'id=never-reset' not in st)
st_off = pages.settings_html(False, True, 45, False, '0.2.8', restore_tabs=False, passwords=False, never_sites=2)
check('settings: switches off, and the "Never" list can be reset',
      '<button class="tog" data-k=restore_tabs' in st_off and '<button class="tog" data-k=password_manager' in st_off
      and '2 sites' in st_off and 'id=never-reset' in st_off)
scripts_ok('settings page with the "Never" reset', st_off)
with open(os.path.join(ROOT, 'browser', 'ui', 'panel.html'), encoding='utf-8') as f:
    panel = f.read()
check('panel has the save, fill and back/forward views',
      all(s in panel for s in ("savepw: savePassword", 'logins,', "navlist: navList", "panel: 'fill'", "panel: 'navgo'")))
with open(os.path.join(ROOT, 'browser', 'ui', 'chrome.html'), encoding='utf-8') as f:
    chrome = f.read()
check('toolbar has the key button and the back/forward lists',
      'id="key"' in chrome and "ui: 'key'" in chrome and "ui: 'navlist'" in chrome and 'contextmenu' in chrome)

key = privacy.new_session_key()
scripts_ok('fingerprinting script', f'<script>{privacy.fingerprint_js(key)}</script>')
check('session keys differ between sessions (and private tabs)', key != privacy.new_session_key())
check('fingerprinting script covers canvas, WebGL, audio and cores',
      all(s in privacy.fingerprint_js(key) for s in ('getImageData', 'toDataURL', 'readPixels', 'getChannelData', 'hardwareConcurrency')))

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
