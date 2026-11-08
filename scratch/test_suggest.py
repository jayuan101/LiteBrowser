"""Address bar suggestions (browser/suggest.py): matching, ranking, inline completion, what may be sent.

    python scratch/test_suggest.py          offline checks
    python scratch/test_suggest.py --live   also asks every engine for suggestions
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from browser import suggest as s  # noqa: E402
from browser.store import History  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(('PASS  ' if ok else 'FAIL  ') + name)
    fails += not ok


now = time.time()
H = [
    {'url': 'https://www.twitch.tv/cohhcarnage', 'title': 'CohhCarnage - Twitch', 'ts': now - 3600, 'visits': 6},
    {'url': 'https://www.twitch.tv/', 'title': 'Twitch', 'ts': now - 7200, 'visits': 9},
    {'url': 'https://www.youtube.com/', 'title': 'YouTube', 'ts': now - 60, 'visits': 12},
    {'url': 'https://twitter.com/home', 'title': 'Home / X', 'ts': now - 86400 * 90, 'visits': 1},
    {'url': 'https://duckduckgo.com/?q=twitch+drops', 'title': 'twitch drops at DuckDuckGo', 'ts': now, 'visits': 1},
]
B = [{'url': 'https://github.com/jayuan101/LiteBrowser', 'title': 'LiteBrowser'}]
T = [{'id': 7, 'url': 'https://www.youtube.com/watch?v=x', 'title': 'A video'}]

# inline completion
check("'twi' completes to twitch.tv", s.inline('twi', H, B) == 'twitch.tv' and s.rest('twi', 'twitch.tv') == 'tch.tv')
check("'www.tw' keeps the www the user typed", s.rest('www.tw', s.inline('www.tw', H, B)) == 'itch.tv')
check("'Tw' (capital) completes", s.rest('Tw', s.inline('Tw', H, B)) == 'itch.tv')
check("'twitch.tv/c' completes the page", s.inline('twitch.tv/c', H, B) == 'twitch.tv/cohhcarnage')
check('bookmarked site completes', s.inline('git', H, B) == 'github.com')
check('no completion for a phrase', s.inline('twitch drops', H, B) == '')
check('no completion for one letter', s.inline('t', H, B) == '')
check('no completion for an unknown site', s.inline('zzz', H, B) == '')
check('trailing space stops completion', s.rest('twi ', 'twitch.tv') == '')

# local matches
skip = ('https://duckduckgo.com/?q=',)
loc = s.local('twi', [], B, H, skip=skip)
check('host prefix ranks frequent twitch.tv first', [r['url'] for r in loc][:2] == ['https://www.twitch.tv/', 'https://www.twitch.tv/cohhcarnage'])
check('old twitter visit ranks below', loc[-1]['url'] == 'https://twitter.com/home')
check('title words match', [r['url'] for r in s.local('cohh', [], B, H)] == ['https://www.twitch.tv/cohhcarnage'])
check('every word must match', s.local('twitch zebra', [], B, H) == [])
yt = s.local('you', T, B, H)
check('open tab is offered as a tab row', any(r['kind'] == 'tab' and r['id'] == 7 for r in yt))
check('a page open in a tab is not repeated as history', [r['url'] for r in yt].count('https://www.youtube.com/watch?v=x') == 1)
check('bookmark row', s.local('lite browser', [], B, H)[0]['kind'] == 'bookmark')
check('search result pages are skipped', all('duckduckgo' not in r['url'] for r in s.local('twitch', [], B, H, skip=skip)))
check('nothing for empty text', s.local('  ', T, B, H) == [])
check('non-web URLs never suggested', s.local('file', [{'id': 1, 'url': 'file:///C:/x.txt', 'title': 'file'}], [], []) == [])

# what may go to the search engine
check('a search phrase may be sent', s.may_send('twitch drops', False, True))
check('never from a private tab', not s.may_send('twitch drops', True, True))
check('never with the setting off', not s.may_send('twitch drops', False, False))
for text in ('twitch.tv', 'https://example.com/a', 'localhost:8000', r'C:\Users\young', '/etc/hosts', 'example.com/path'):
    check(f'address not sent: {text}', not s.may_send(text, False, True))
for text in ('hunter2Xy9!pQ7zLm4#', 'ghp_abcdEFGH1234ijklMNOP5678qrst', 'sk-ant-api03-AbCdEf123456GhIjKl'):
    check(f'secret-looking text not sent: {text[:6]}...', not s.may_send(text, False, True))
check('long text not sent', not s.may_send('word ' * 30, False, True))

# the rows shown
rows = s.rows('twi', s.local('twi', [], B, H), ['twitch', 'twitchcon', 'twitter'], 'DuckDuckGo')
check('first row searches the typed text', rows[0] == {'kind': 'search', 'text': 'twi', 'title': 'twi', 'engine': 'DuckDuckGo'})
check('at most 8 rows', len(rows) <= s.LIMIT)
check('pages come before engine suggestions',
      [r['kind'] for r in rows].index('search', 1) > max(i for i, r in enumerate(rows) if r['kind'] == 'history'))
check('an address goes first as Go to', s.rows('twitch.tv', [], [], 'DuckDuckGo')[0]['kind'] == 'go')

# history counts visits
h = History('')
h.record('https://a.example/', 'A')
h.record('https://b.example/', 'B')
h.record('https://a.example/#x', 'A')
check('history counts visits per page', h.recent(1)[0] == {**h.recent(1)[0], 'url': 'https://a.example/', 'visits': 2})
check('history still one entry per page', len(h.recent()) == 2)

if '--live' in sys.argv:
    for engine in s.SUGGEST_URLS:
        got = s.remote('weather', engine)
        check(f'live: {engine} answers', bool(got) or engine == 'startpage')

print(f'\n{fails} failure(s)')
sys.exit(1 if fails else 0)
