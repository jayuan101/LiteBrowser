"""Running totals for the new tab page, like Brave's "trackers & ads blocked" counter.

Only counts are stored - never which sites they came from.
"""
import json
import os
import time

KINDS = ('blocked', 'popups', 'links', 'dangerous')  # requests, pop-ups, tracking links, malware pages


class Stats:
    def __init__(self, path: str | None):
        self.path = path
        self.counts = dict.fromkeys(KINDS, 0)
        self.since = time.strftime('%Y-%m-%d')
        self._dirty = False
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            self.counts.update({k: int(data.get(k, 0)) for k in KINDS})
            self.since = str(data.get('since', self.since))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def add(self, kind: str, n: int = 1) -> None:
        self.counts[kind] += n
        self._dirty = True

    def save(self) -> None:
        if not self._dirty or not self.path:
            return
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({**self.counts, 'since': self.since}, f)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass
