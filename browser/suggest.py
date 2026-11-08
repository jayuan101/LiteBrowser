"""Address bar suggestions: what you are likely to want while you type.

Two sources. Local ones - open tabs, bookmarks and history - are matched here in
a fraction of a millisecond and never leave the machine. Remote ones are the search
engine's own suggestion list (one small JSON request), sent only when the text looks
like a search: never in private tabs, never for addresses, file paths or anything
that looks like a pasted password or key.
"""
import json
import math
import re
import time
import urllib.parse
import urllib.request

LIMIT = 8  # rows in the list, the "search for" row included
LOCAL_LIMIT = 5
REMOTE_LIMIT = 5
TIMEOUT = 2.5
DELAY = 0.12  # seconds of no typing before the engine is asked

# Engine id -> suggestion URL (OpenSearch JSON: ["query", ["s1", "s2", ...]]).
# Brave's endpoint answers scripts with a bot check, so Brave and Lite's own engine use
# DuckDuckGo's, which keeps no identifiers either.
_DDG = 'https://duckduckgo.com/ac/?type=list&q='
SUGGEST_URLS = {
    'brave': _DDG,
    'duckduckgo': _DDG,
    'lite': _DDG,
    'google': 'https://suggestqueries.google.com/complete/search?client=firefox&q=',
    'bing': 'https://api.bing.com/osjson.aspx?query=',
    'startpage': 'https://www.startpage.com/osuggestions?q=',
    'ecosia': 'https://ac.ecosia.org/autocomplete?type=list&q=',
}

_PATH = re.compile(r'^([a-z]:[\\/]|\\\\|/|~[\\/])', re.I)


def _bare(url: str) -> str:
    """'https://www.twitch.tv/cohh' -> 'twitch.tv/cohh' (what people type)."""
    u = re.sub(r'^https?://', '', url, flags=re.I)
    u = re.sub(r'^www\.', '', u, flags=re.I)
    return u.rstrip('/') if u.count('/') == 1 else u


def _host(url: str) -> str:
    return _bare(url).split('/', 1)[0].split('?', 1)[0].lower()


def looks_like_address(text: str) -> bool:
    t = text.strip()
    if '://' in t or t.lower().startswith(('about:', 'edge:', 'file:', 'javascript:', 'data:')):
        return True
    if _PATH.match(t):
        return True
    if ' ' in t:
        return False
    return '.' in t or '/' in t or t.lower().startswith('localhost')


def looks_secret(text: str) -> bool:
    """A long unbroken run of mixed letters/digits/symbols: a password, token or key, not a search."""
    for word in text.split():
        if len(word) < 16:
            continue
        kinds = sum(bool(re.search(p, word)) for p in (r'[a-z]', r'[A-Z]', r'\d', r'[^A-Za-z0-9]'))
        if kinds >= 3 or (kinds == 2 and re.search(r'\d', word) and len(word) >= 24):
            return True
    return False


def may_send(text: str, private: bool, enabled: bool) -> bool:
    """Whether this text may go to the search engine for suggestions."""
    t = text.strip()
    return (enabled and not private and 0 < len(t) <= 80
            and not looks_like_address(t) and not looks_secret(t))


def _score_text(q: str, title: str, url: str) -> float:
    """How well the typed words match a page; 0 = not at all. Every word must match somewhere."""
    bare = _bare(url).lower()
    host = _host(url)
    title = (title or '').lower()
    words = _norm(q).split()  # 'www.tw' and 'https://tw' match like 'tw'
    if not words:
        return 0.0
    score = 0.0
    for i, w in enumerate(words):
        if i == 0 and host.startswith(w):
            score += 6  # "twi" -> twitch.tv
        elif i == 0 and bare.startswith(w):
            score += 5
        elif any(part.startswith(w) for part in re.split(r'[./?=&_-]', host)):
            score += 4  # "tv" in twitch.tv, "mail" in mail.google.com
        elif re.search(r'(^|[^a-z0-9])' + re.escape(w), title):
            score += 3  # start of a word in the title
        elif w in bare:
            score += 1.5
        elif w in title:
            score += 1
        else:
            return 0.0
    return score


def _frecency(visits: int, ts: float, now: float) -> float:
    """Often and recent beats once and long ago: visits, halved every two weeks."""
    age_days = max(0.0, (now - (ts or now)) / 86400)
    return math.log2(1 + max(1, visits)) * 0.5 ** (age_days / 14)


def local(text: str, tabs: list[dict], bookmarks: list[dict], history: list[dict], now: float | None = None,
          skip: tuple[str, ...] = ()) -> list[dict]:
    """Matching open tabs, bookmarks and history pages, best first; one row per URL.

    tabs: [{'id', 'url', 'title'}]; bookmarks: [{'url', 'title'}]; history: [{'url', 'title', 'ts', 'visits'}]
    skip: URL prefixes left out of history - search results pages, which the search rows already cover
    """
    q = text.strip()
    if not q:
        return []
    now = now or time.time()
    stats = {}
    for e in history:
        u = e.get('url', '')
        if u and u not in stats:
            stats[u] = (int(e.get('visits', 1) or 1), float(e.get('ts', 0) or 0), e.get('title', ''))
    starred = {b['url'] for b in bookmarks}
    rows: dict[str, dict] = {}

    def add(kind, url, title, bonus, extra=None):
        if not url.startswith(('http://', 'https://')):
            return
        m = _score_text(q, title, url)
        if not m:
            return
        visits, ts, htitle = stats.get(url, (0, 0.0, ''))
        score = m + bonus + 2 * _frecency(visits, ts, now) + (1.5 if url in starred else 0)
        key = url.rstrip('/')
        old = rows.get(key)
        if old and (old['kind'] == 'tab' or old['score'] >= score):
            return
        row = {'kind': kind, 'url': url, 'title': title or htitle or _bare(url), 'score': score}
        if extra:
            row.update(extra)
        rows[key] = row

    for e in history[:4000]:
        if not (skip and e.get('url', '').startswith(skip)):
            add('history', e.get('url', ''), e.get('title', ''), 0)
    for b in bookmarks:
        add('bookmark', b['url'], b.get('title', ''), 1)
    for t in tabs:
        add('tab', t.get('url', ''), t.get('title', ''), 1.5, {'id': t.get('id')})
    best = sorted(rows.values(), key=lambda r: -r['score'])[:LOCAL_LIMIT]
    for r in best:
        r.pop('score', None)
    return best


def _norm(text: str) -> str:
    """Typed text the way history is compared: lower case, no scheme, no leading 'www.'."""
    q = re.sub(r'^https?://', '', text.strip().lower())
    return re.sub(r'^www\.', '', q)


def rest(text: str, completion: str) -> str:
    """The part of `completion` that comes after what was typed ('twi', 'twitch.tv' -> 'tch.tv')."""
    q = _norm(text)
    if q and completion.lower().startswith(q) and len(completion) > len(q) and text == text.rstrip():
        return completion[len(q):]
    return ''


def inline(text: str, history: list[dict], bookmarks: list[dict]) -> str:
    """What to complete the typed text to, as typed form ('twi' -> 'twitch.tv'), or ''.

    Only on a prefix of a site you have been to: the host while you are typing the host,
    the full address once you have typed a '/'.
    """
    q = _norm(text)
    if not q or ' ' in q or len(q) < 2:
        return ''
    now = time.time()
    best, best_score = '', 0.0
    pages = [(b['url'], 3, now) for b in bookmarks]
    pages += [(e.get('url', ''), int(e.get('visits', 1) or 1), float(e.get('ts', 0) or 0)) for e in history[:4000]]
    hosts: dict[str, float] = {}
    for url, visits, ts in pages:
        if not url.startswith(('http://', 'https://')):
            continue
        f = _frecency(visits, ts, now)
        if '/' in q:
            bare = _bare(url)
            if bare.lower().startswith(q) and len(bare) > len(q) and f > best_score:
                best, best_score = bare, f
        else:
            h = _host(url)
            if h.startswith(q) and len(h) > len(q):
                hosts[h] = hosts.get(h, 0.0) + f
    if hosts:
        best = max(hosts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
    return best


def remote(text: str, engine: str, timeout: float = TIMEOUT) -> list[str]:
    """The engine's suggestions for the text, or [] when it cannot be reached."""
    base = SUGGEST_URLS.get(engine, _DDG)
    req = urllib.request.Request(base + urllib.parse.quote(text.strip()), headers={
        'Accept': 'application/x-suggestions+json, application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Lite',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except (OSError, ValueError):
        return []
    if not (isinstance(data, list) and len(data) > 1 and isinstance(data[1], list)):
        return []
    q = text.strip().lower()
    out = []
    for s in data[1]:
        if isinstance(s, str) and s.strip() and s.strip().lower() != q and s not in out:
            out.append(s.strip())
    return out[:REMOTE_LIMIT]


def rows(text: str, local_rows: list[dict], remote_rows: list[str], engine_name: str) -> list[dict]:
    """The list shown under the address bar: the typed text first, then pages, then searches."""
    t = text.strip()
    if not t:
        return []
    first = ({'kind': 'go', 'text': t, 'title': _bare(t) if '://' in t else t} if looks_like_address(t)
             else {'kind': 'search', 'text': t, 'title': t, 'engine': engine_name})
    out = [first]
    room = LIMIT - 1
    pages = local_rows[:max(2, room - len(remote_rows[:3]))]
    out += [dict(r, sub=_bare(r['url'])) for r in pages]
    room = LIMIT - len(out)
    out += [{'kind': 'search', 'text': s, 'title': s, 'engine': engine_name, 'suggested': True} for s in remote_rows[:room]]
    return out[:LIMIT]
