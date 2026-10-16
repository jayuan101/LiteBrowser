"""Brave-style Shields: what gets blocked, and where the user turned blocking off.

Every network request the page makes goes through Blocker.check(). Normally
that asks the filter-list engine in filters.py (EasyList, uBlock Origin...).
Until the lists have been downloaded once - or if the `adblock` package is
missing - a built-in list of the big ad networks is used instead: a host is
blocked when it, or any parent domain of it, is on that list, and first-party
requests (same site as the page) are never blocked, so logging in to e.g.
facebook.com keeps working.

Like Brave's Shields, all of it can be turned off for one site (exact
hostname) when it breaks a page. Those choices are remembered in
shields.json - except ones made in private tabs, which last for the session.
"""
import json
import os
from urllib.parse import urlparse

# Common ad networks and trackers. Extend with blocklist.txt (one domain per line).
BUILTIN = """
doubleclick.net googlesyndication.com googleadservices.com google-analytics.com
googletagmanager.com googletagservices.com adservice.google.com
adnxs.com adsrvr.org scorecardresearch.com quantserve.com outbrain.com taboola.com
criteo.com criteo.net rubiconproject.com pubmatic.com openx.net amazon-adsystem.com
moatads.com hotjar.com mixpanel.com segment.io fullstory.com adform.net
casalemedia.com bluekai.com demdex.net krxd.net chartbeat.com mouseflow.com
crazyegg.com yieldmo.com sharethrough.com media.net 33across.com smartadserver.com
teads.tv zedo.com advertising.com doubleverify.com adsafeprotected.com bidswitch.net
rlcdn.com agkn.com exelator.com tapad.com liveintent.com id5-sync.com adcolony.com
connect.facebook.net pixel.facebook.com ads.yahoo.com analytics.yahoo.com
ads.linkedin.com px.ads.linkedin.com ads-twitter.com analytics.twitter.com
bat.bing.com clarity.ms adroll.com mathtag.com turn.com spotxchange.com
indexww.com lijit.com sovrn.com conversantmedia.com contextweb.com
popads.net popcash.net propellerads.com exoclick.com juicyads.com
""".split()


def site_label(host: str) -> str:
    """The brand label of a host: 'facebook' for www.facebook.com and connect.facebook.net.

    Comparing this (not the full domain) is what lets facebook.com load its own
    facebook.net scripts while still blocking them on every other site.
    """
    parts = host.lower().strip('.').split('.')
    return parts[-2] if len(parts) >= 2 else parts[0]


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


class Blocker:
    def __init__(self, list_path: str | None = None, shields_path: str | None = None):
        self.custom: set[str] = set()
        if list_path and os.path.exists(list_path):
            with open(list_path, encoding='utf-8') as f:
                for line in f:
                    line = line.split('#', 1)[0].strip().lower()
                    if line:
                        self.custom.add(line.split()[-1])  # tolerate hosts-file "0.0.0.0 host" lines
        self.domains: set[str] = set(BUILTIN) | self.custom
        self.filters = None  # filters.Filters once the lists are loaded
        self.shields_path = shields_path
        self.shields_down: set[str] = self._load_shields()
        self._session: dict[str, bool] = {}  # choices made in private tabs: never written to disk

    def _load_shields(self) -> set[str]:
        if not self.shields_path:
            return set()
        try:
            with open(self.shields_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return set()
        return {str(h).lower() for h in data.get('off', [])} if isinstance(data, dict) else set()

    def custom_rules(self) -> list[str]:
        """blocklist.txt as filter-list rules, so the engine blocks those domains too."""
        return [f'||{d}^' for d in sorted(self.custom)]

    def shields_up(self, page_host: str) -> bool:
        page_host = page_host.lower()
        return self._session.get(page_host, page_host not in self.shields_down)

    def set_shields(self, page_host: str, up: bool, remember: bool = True) -> None:
        """Turn blocking on (up) or off (down) for one site; remember=False keeps it off disk."""
        page_host = page_host.lower()
        if not remember:
            self._session[page_host] = up
            return
        self._session.pop(page_host, None)
        if up:
            self.shields_down.discard(page_host)
        else:
            self.shields_down.add(page_host)
        if not self.shields_path:
            return
        try:
            tmp = self.shields_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'off': sorted(self.shields_down)}, f, indent=1)
            os.replace(tmp, self.shields_path)
        except OSError:
            pass  # still applies for this session

    def check(self, url: str, page_url: str, request_type: str) -> tuple[bool, str]:
        """Should a request from the page at page_url be blocked?

        Returns (blocked, stub): stub is a data: URL to answer with instead of an error
        (a do-nothing script, a 1x1 image...) so the page keeps working, or ''.
        """
        page_host = _host(page_url)
        if page_host and not self.shields_up(page_host):
            return False, ''
        if self.filters is None:
            return self.is_blocked(_host(url), page_host), ''
        r = self.filters.check(url, page_url or url, request_type)
        return r.matched, (r.redirect or '') if r.matched else ''

    def blocks_popup(self, url: str, page_url: str) -> bool:
        """A new window that goes straight to an ad or tracking server (a pop-under)."""
        if self.filters is None:
            return self.is_blocked(_host(url), _host(page_url))
        return self.filters.check(url, page_url or url, 'document').matched

    def is_dangerous(self, url: str) -> bool:
        return self.filters is not None and self.filters.is_dangerous(url)

    def is_ad_page(self, url: str) -> bool:
        """uBlock Origin's "strict blocking": the whole site is an ad or tracking server.

        Only the site's root is checked, so rules for whole hostnames (||ads.example^)
        match but rules aimed at single files never stop an ordinary page."""
        host = _host(url)
        if self.filters is None or not host:
            return False
        root = f'{urlparse(url).scheme}://{host}/'
        return self.filters.check(root, root, 'document').matched

    def is_blocked(self, request_host: str, page_host: str | None) -> bool:
        """The built-in list: used until the filter lists are ready."""
        if not request_host:
            return False
        request_host = request_host.lower()
        if page_host and not self.shields_up(page_host):
            return False  # the user turned Shields down for this site
        if page_host and site_label(request_host) == site_label(page_host):
            return False  # first-party
        labels = request_host.split('.')
        for i in range(len(labels) - 1):
            if '.'.join(labels[i:]) in self.domains:
                return True
        return False
