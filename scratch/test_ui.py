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
from browser import guard, pages  # noqa: E402

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

html = pages.new_tab_html(counts, '2026-10-15', 'lists')
check('new tab page shows the totals', all(s in html for s in ('12,345', '>87<', '>3<', 'Oct 15, 2026')), html[:200])
check('new tab page promises no sponsored content', 'never shows ads, sponsored' in html)
warn = pages.blocked_html('https://evil.example/"<x>', 'r', 'warn')
check('warning page escapes the URL', '"<x>' not in warn.split('<script>')[0] and 'Continue anyway' in warn)
check('hard block has no way through', 'Continue anyway' not in pages.blocked_html('javascript:x', 'r', 'block'))
check('punycode is a warning (can continue), not a hard block',
      guard.check_navigation('https://xn--80ak6aa92e.com/').level == 'warn')
check('user@host is a warning, not a hard block', guard.check_navigation('https://google.com@evil.example/').level == 'warn')
check('javascript: stays blocked', guard.check_navigation('javascript:alert(1)').level == 'block')

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
