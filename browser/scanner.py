"""Optional page-check hook.

The open-source build ships this disabled placeholder: it collects nothing,
never runs, and reports every page as unchecked. A private build can drop a
`fable_local.py` next to this module (kept out of the repository) that
provides:

- ``COLLECT_JS``: JavaScript string run after a page loads, returning a JSON
  description of the page.
- ``FableScanner``: class with ``__init__(config, cache_path)``, an
  ``enabled`` property, ``cached(origin)``, ``scan(page)`` and ``clear()``.
"""
try:  # private build only - never present in the repository
    from .fable_local import COLLECT_JS, FableScanner  # type: ignore
except ImportError:
    COLLECT_JS = ''

    class FableScanner:
        enabled = False

        def __init__(self, config: dict | None = None, cache_path: str | None = None):
            self.enabled = False

        def cached(self, origin: str) -> dict | None:
            return None

        def scan(self, page: dict) -> dict:
            return {'risk': 'unknown', 'reason': 'Page check is not installed'}

        def clear(self) -> None:
            pass
