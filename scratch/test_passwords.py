"""Offline checks for browser/passwords.py - no network, no files left behind.

    python scratch/test_passwords.py
"""
import os
import sys
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import store  # noqa: E402
from browser.passwords import Passwords, matches  # noqa: E402

failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      {detail}'))


tmp = tempfile.mkdtemp(prefix='lite-passwords-')
path = os.path.join(tmp, 'passwords.json')

# add()/remember() encrypt with Windows DPAPI, which needs an interactive user profile; this
# account (running the test headless) has none, so stand in with a no-op "encryption" for it -
# hosts() and matching, which this file is about, never look at the encrypted bytes anyway.
patcher = mock.patch('browser.passwords._protect', lambda text: text)
patcher.start()

# ---- the vault -------------------------------------------------------------
pw = Passwords(path)
check('empty vault has no hosts', pw.hosts() == set())
check('add saves a row', pw.add('https://www.twitch.tv/', 'me', 's3cret'))
check('count after add', pw.count() == 1)
check('duplicate add is refused', not pw.add('https://www.twitch.tv/', 'me', 's3cret'))
pw.add('https://vault.bitwarden.com/', 'me@example.com', 'hunter2')

# ---- hosts(): what keeps a site's cookies alive (app.py's cookie sweep) ----
check('hosts() has one entry per site, www dropped', pw.hosts() == {'twitch.tv', 'vault.bitwarden.com'})
check('a saved site keeps its cookie', store.host_keeps_cookie('www.twitch.tv', pw.hosts(), set()))
check('a saved site keeps its cookie (bare host)', store.host_keeps_cookie('twitch.tv', pw.hosts(), set()))
check('a site with no saved login does not', not store.host_keeps_cookie('tracker.io', pw.hosts(), set()))
check('combines with starred hosts like app.py does',
      store.host_keeps_cookie('example.com', {'example.com'} | pw.hosts(), set()))

# ---- persistence across restarts -------------------------------------------
pw2 = Passwords(path)
check('passwords survive restart', pw2.count() == 2)
check('hosts() survives restart too', pw2.hosts() == {'twitch.tv', 'vault.bitwarden.com'})

# ---- site matching for autofill --------------------------------------------
check('same host matches', matches('https://www.twitch.tv/login', 'https://www.twitch.tv/'))
check('a stranger does not match', not matches('https://www.twitch.tv/', 'https://evil.example/'))

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
