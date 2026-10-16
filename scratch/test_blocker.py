"""Scratch checks for browser/blocker.py, including per-site Shields.  python scratch/test_blocker.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from browser.blocker import Blocker  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'   got={got!r} want={want!r}'))


tmp = tempfile.mkdtemp(prefix='lite-blocker-')
shields = os.path.join(tmp, 'shields.json')
listfile = os.path.join(tmp, 'blocklist.txt')
with open(listfile, 'w', encoding='utf-8') as f:
    f.write('# comment line\n0.0.0.0 ads.example.com  # hosts-file style\ntracker.test\n')

# --- existing behaviour must not change ---------------------------------
b = Blocker(listfile, shields)
check('listed domain blocked', b.is_blocked('doubleclick.net', 'www.cnn.com'), True)
check('subdomain of listed domain blocked', b.is_blocked('securepubads.g.doubleclick.net', 'www.cnn.com'), True)
check('first-party exempt (facebook.net on facebook.com)', b.is_blocked('connect.facebook.net', 'www.facebook.com'), False)
check('same tracker blocked on another site', b.is_blocked('connect.facebook.net', 'www.cnn.com'), True)
check('unlisted host allowed', b.is_blocked('media.cnn.com', 'www.cnn.com'), False)
check('empty request host allowed', b.is_blocked('', 'www.cnn.com'), False)
check('no page host still blocks', b.is_blocked('doubleclick.net', None), True)
check('hosts-file line parsed', b.is_blocked('ads.example.com', 'www.cnn.com'), True)
check('plain list line parsed', b.is_blocked('x.tracker.test', 'www.cnn.com'), True)
check('case-insensitive request host', b.is_blocked('DoubleClick.NET', 'www.cnn.com'), True)

# --- Shields: per-site toggle -------------------------------------------
check('shields up by default', b.shields_up('www.cnn.com'), True)
check('no shields file before first toggle', os.path.exists(shields), False)
b.set_shields('www.cnn.com', up=False)
check('shields down for that site', b.shields_up('www.cnn.com'), False)
check('tracker allowed on shields-down site', b.is_blocked('doubleclick.net', 'www.cnn.com'), False)
check('other subdomain keeps shields (exact host)', b.is_blocked('doubleclick.net', 'edition.cnn.com'), True)
check('other site keeps shields', b.is_blocked('doubleclick.net', 'www.bbc.co.uk'), True)
with open(shields, encoding='utf-8') as f:
    check('choice written to shields.json', json.load(f), {'off': ['www.cnn.com']})
check('no temp file left behind', os.path.exists(shields + '.tmp'), False)

b2 = Blocker(listfile, shields)
check('choice survives restart', b2.shields_up('www.cnn.com'), False)
check('restart: other sites unaffected', b2.shields_up('www.bbc.co.uk'), True)

b2.set_shields('WWW.Example.COM', up=False)
check('hostnames are case-insensitive', b2.shields_up('www.example.com'), False)
b2.set_shields('www.cnn.com', up=True)
check('shields back up', b2.shields_up('www.cnn.com'), True)
check('tracker blocked again', b2.is_blocked('doubleclick.net', 'www.cnn.com'), True)
with open(shields, encoding='utf-8') as f:
    check('file updated on shields up', json.load(f), {'off': ['www.example.com']})

# --- bad or missing state never breaks blocking --------------------------
for label, content in (('corrupt json', '{not json'), ('json list instead of object', '["www.cnn.com"]')):
    with open(shields, 'w', encoding='utf-8') as f:
        f.write(content)
    b3 = Blocker(listfile, shields)
    check(f'{label}: loads as all-shields-up', (b3.shields_down, b3.is_blocked('doubleclick.net', 'www.cnn.com')),
          (set(), True))

mem = Blocker(listfile, None)
mem.set_shields('www.cnn.com', up=False)
check('no path: works in memory only', mem.shields_up('www.cnn.com'), False)

bad = Blocker(listfile, os.path.join(tmp, 'missing-dir', 'shields.json'))
try:
    bad.set_shields('www.cnn.com', up=False)
    check('unwritable path: no exception, still applies', bad.shields_up('www.cnn.com'), False)
except OSError as e:
    check('unwritable path: no exception', repr(e), None)

# --- private tabs: choices last for the session only ------------------------
priv_file = os.path.join(tmp, 'private-shields.json')
p = Blocker(listfile, priv_file)
p.set_shields('secret.example', up=False, remember=False)
check('private-tab choice applies', p.shields_up('secret.example'), False)
check('private-tab choice is never written to disk', os.path.exists(priv_file), False)
p.set_shields('www.cnn.com', up=False)
with open(priv_file, encoding='utf-8') as f:
    check('a later normal-tab choice does not leak it', json.load(f), {'off': ['www.cnn.com']})
check('after restart the private choice is gone', Blocker(listfile, priv_file).shields_up('secret.example'), True)

# --- request decisions before the filter lists have loaded -------------------
check('check(): built-in list until the engine is ready',
      Blocker(listfile, None).check('https://securepubads.g.doubleclick.net/gpt.js', 'https://www.bbc.com/', 'script'),
      (True, ''))
check('check(): nothing blocked where Shields are down',
      p.check('https://securepubads.g.doubleclick.net/gpt.js', 'https://www.cnn.com/', 'script'), (False, ''))
check('custom blocklist.txt becomes engine rules', Blocker(listfile, None).custom_rules(),
      ['||ads.example.com^', '||tracker.test^'])


class FakeResult:
    def __init__(self, matched):
        self.matched, self.redirect = matched, None


class FakeFilters:
    """Blocks whole sites on the list; records what it was asked."""
    def __init__(self):
        self.asked = []

    def check(self, url, source, kind):
        self.asked.append((url, kind))
        return FakeResult('ads.example' in url)


e = Blocker(None, None)
e.filters = FakeFilters()
check('strict blocking: whole ad site', e.is_ad_page('https://ads.example/click?id=1&u=x'), True)
check('strict blocking checks only the site root', e.filters.asked[-1], ('https://ads.example/', 'document'))
check('strict blocking: ordinary page', e.is_ad_page('https://news.example/ads/story-about-ads'), False)

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
