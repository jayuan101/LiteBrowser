"""Lite Search: our own results page, powered by DuckDuckGo's plain-HTML lite index.

One daemon thread per search fetches a ~100 KB page and parses it with the
stdlib HTML parser; nothing stays resident. No cookies, no identifiers - just
the query in the URL.
"""
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

LITE_ENGINE = 'lite:'  # search_url marker: to_url builds a lite.search URL the tab intercepts
LITE_PREFIX = 'https://lite.search/?q='
FETCH_URL = 'https://lite.duckduckgo.com/lite/?q='
TIMEOUT = 8.0


class _Parser(HTMLParser):
    """Collects (title, href) pairs and snippets from the lite results table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._href = ''
        self._title: list[str] = []
        self._snippet: list[str] | None = None
        self._pending: int | None = None  # index of the last result still missing its snippet

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and a.get('class') == 'result-link' and a.get('href'):
            self._href = a['href']
            self._title = []
        elif tag == 'td' and a.get('class') == 'result-snippet':
            self._snippet = []

    def handle_endtag(self, tag):
        if tag == 'a' and self._href:
            if self._emit():
                self._pending = len(self.results) - 1
        elif tag == 'td':
            if self._snippet is not None and self._pending is not None:
                self.results[self._pending]['snippet'] = ' '.join(''.join(self._snippet).split())
                self._pending = None
            self._snippet = None

    def handle_data(self, data):
        if self._href:
            self._title.append(data)
        elif self._snippet is not None:
            self._snippet.append(data)

    def _emit(self) -> bool:
        """Add the finished result; returns False for dropped rows (ads), whose snippet must be dropped too."""
        href, title = self._href, ' '.join(''.join(self._title).split())
        self._href, self._title = '', []
        if href.startswith('//duckduckgo.com/l/?') or href.startswith('/l/?'):  # redirector link
            qs = urllib.parse.urlsplit('https:' + href if href.startswith('//') else 'https://duckduckgo.com' + href).query
            url = urllib.parse.parse_qs(qs).get('uddg', [''])[0]
        else:
            url = href
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith(('http://', 'https://')) or not title:
            return False
        if 'duckduckgo.com/y.js' in url or '/duckduckgo-help-pages/' in url:
            return False  # the index's own ads: Lite does not show ads, not even in its search
        self.results.append({'title': title, 'url': url, 'snippet': ''})
        return True


def fetch(query: str, timeout: float = TIMEOUT) -> list[dict]:
    """The first 30 results for the query, or [] when the index cannot be reached."""
    req = urllib.request.Request(FETCH_URL + urllib.parse.quote_plus(query), headers={
        'Accept-Language': 'en-US,en;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Lite Search',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode('utf-8', 'replace')
    p = _Parser()
    p.feed(html)
    seen, out = set(), []
    for e in p.results[:30]:
        if e['url'] in seen:
            continue
        seen.add(e['url'])
        out.append(e)
    return out
