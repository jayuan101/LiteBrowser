"""Static checks for the UI: every Python module compiles and every page's JavaScript parses.

    python scratch/test_ui.py
"""
import glob
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import guard, pages, privacy  # noqa: E402

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

html = pages.new_tab_html(counts, '2026-10-15', 'lists')
check('new tab page shows the totals', all(s in html for s in ('12,345', '>87<', '>3<', 'Oct 15, 2026')), html[:200])
check('new tab page promises no sponsored content', 'never shows ads, sponsored' in html)
banner = pages.new_tab_html(counts, '2026-10-15', '', banner={'tag': 'v0.3.0', 'name': 'Lite 0.3.0', 'url': 'u'})
check('update banner renders and links', 'Lite 0.3.0' in banner and "cmd:'update'" in banner)
check('no banner by default', 'class=banner' not in html)
st = pages.settings_html(False, True, 45, False, '0.2.0')
check('settings page shows the stored values',
      'value=45' in st and '<button class="tog" data-k=auto_delete_cookies' in st
      and '<button class="tog on" data-k=tab_sleep' in st,
      st[:200])
check('settings page sends changes back', all(s in st for s in ("cmd:'settings'", "cmd:'clearcookies'", "cmd:'updates'")))
sr = pages.search_results_html('q "<x>', [{'title': 'T', 'url': 'https://x.example/', 'snippet': 's'}])
check('search page escapes the query and rows', '"<x>' not in sr and 'Searching' not in sr)
check('search page has a waiting state while fetching', 'Searching' in pages.search_results_html('q', None))
check('search page explains a failed fetch', 'Could not reach' in pages.search_results_html('q', []))
warn = pages.blocked_html('https://evil.example/"<x>', 'r', 'warn')
check('warning page escapes the URL', '"<x>' not in warn.split('<script>')[0] and 'Continue anyway' in warn)
check('hard block has no way through', 'Continue anyway' not in pages.blocked_html('javascript:x', 'r', 'block'))
check('punycode is a warning (can continue), not a hard block',
      guard.check_navigation('https://xn--80ak6aa92e.com/').level == 'warn')
check('user@host is a warning, not a hard block', guard.check_navigation('https://google.com@evil.example/').level == 'warn')
check('javascript: stays blocked', guard.check_navigation('javascript:alert(1)').level == 'block')

key = privacy.new_session_key()
scripts_ok('fingerprinting script', f'<script>{privacy.fingerprint_js(key)}</script>')
check('session keys differ between sessions (and private tabs)', key != privacy.new_session_key())
check('fingerprinting script covers canvas, WebGL, audio and cores',
      all(s in privacy.fingerprint_js(key) for s in ('getImageData', 'toDataURL', 'readPixels', 'getChannelData', 'hardwareConcurrency')))

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
