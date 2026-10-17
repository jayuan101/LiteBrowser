"""Lite - a lightweight, privacy-first browser shell on top of WebView2.

Layout: a tab strip and toolbar drawn in HTML (ui/chrome.html, in its own small
WebView2) over a content area where every tab is one WebView2 (tab.py). Pop-up
panels - Shields, the menu, downloads - live in popup.Popup. All of them share one
browser process tree, which is what keeps memory far below a full Chromium bundle.
"""
import ctypes
import json
import os
import sys
import subprocess
import tempfile
import threading
import time
import traceback
from urllib.parse import urlparse

from . import (
    autofill, default_browser, extensions, filters, guard, importer, instance, privacy, search as search_mod, updater,
)
from .blocker import Blocker
from .downloads import Scanner as AntivirusScanner, fallback_path
from .engine import Engine
from .passwords import Passwords
from .popup import Popup
from .scanner import FableScanner
from .stats import Stats
from .store import Bookmarks, History, Session, Settings, host_keeps_cookie
from .tab import Tab, to_url
from .webview2 import (
    Action, ApartmentState, CoreWebView2MemoryUsageTargetLevel,
    CoreWebView2PrintDialogKind, Drawing, Drawing2D,
    String, System, Task, TaskScheduler, Thread, ThreadStart, WebView2, WinForms,
)

APP_NAME = 'Lite'
APP_VERSION = '0.2.11'

SEARCH_ENGINES = {  # id -> (name, query URL); Brave is the default, the rest are one click away in Settings
    'brave': ('Brave', 'https://search.brave.com/search?q='),
    'duckduckgo': ('DuckDuckGo', 'https://duckduckgo.com/?q='),
    'google': ('Google', 'https://www.google.com/search?q='),
    'bing': ('Bing', 'https://www.bing.com/search?q='),
    'startpage': ('Startpage', 'https://www.startpage.com/sp/search?query='),
    'ecosia': ('Ecosia', 'https://www.ecosia.org/search?q='),
    'lite': ('Lite', 'lite:'),  # our own results page, fetched from a clean index - no ads, no engine cookies
}
# Project folder, or the bundle folder when packaged with PyInstaller (packaging/lite.spec).
ROOT = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA', ROOT), 'LiteBrowser')
UI_DIR = os.path.join(ROOT, 'browser', 'ui')
ICON_FILE = os.path.join(ROOT, 'packaging', 'lite.ico')
CHROME_HEIGHT = 85  # CSS pixels: 38 tab strip + 46 toolbar + 1 border
STRIP = (22, 24, 29)  # tab strip colour, also used for the Windows title bar

SCAN_TEXT = {  # page-check verdict -> (label, colour); empty label = nothing to show
    'off': ('', '#9aa0ab'),
    'local': ('', '#9aa0ab'),
    'scanning': ('Checking', '#f6b74a'),
    'safe': ('Safe', '#3fd18f'),
    'suspicious': ('Suspicious', '#f6b74a'),
    'dangerous': ('Dangerous', '#ff6b6b'),
    'unknown': ('Check failed', '#9aa0ab'),
    'warn': ('Warning', '#f6b74a'),
}

# DwmSetWindowAttribute: dark title bar in the tab strip's colour (Windows 11; ignored elsewhere).
DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 20, 34, 35, 36


def load_config() -> dict:
    path = os.path.join(ROOT, 'config.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _colorref(rgb) -> int:
    return rgb[0] | rgb[1] << 8 | rgb[2] << 16


def _dwm(hwnd: int, attr: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


def _app_icon():
    """An orange shield with a check mark: packaging/lite.ico when present, else drawn at start-up."""
    if os.path.exists(ICON_FILE):
        try:
            return Drawing.Icon(ICON_FILE)
        except Exception:
            pass
    bmp = Drawing.Bitmap(64, 64)
    g = Drawing.Graphics.FromImage(bmp)
    g.SmoothingMode = Drawing2D.SmoothingMode.AntiAlias
    shield = Drawing2D.GraphicsPath()
    shield.AddClosedCurve(System.Array[Drawing.PointF]([Drawing.PointF(x, y) for x, y in (
        (32, 3), (57, 12), (57, 30), (51, 45), (32, 61), (13, 45), (7, 30), (7, 12))]), 0.12)
    brush = Drawing2D.LinearGradientBrush(Drawing.Point(0, 0), Drawing.Point(0, 64),
                                          Drawing.Color.FromArgb(255, 255, 143, 63), Drawing.Color.FromArgb(255, 251, 84, 43))
    g.FillPath(brush, shield)
    pen = Drawing.Pen(Drawing.Color.White, 7.0)
    pen.StartCap = pen.EndCap = Drawing2D.LineCap.Round
    pen.LineJoin = Drawing2D.LineJoin.Round
    g.DrawLines(pen, System.Array[Drawing.PointF]([Drawing.PointF(20, 33), Drawing.PointF(29, 42), Drawing.PointF(45, 23)]))
    g.Dispose()
    return Drawing.Icon.FromHandle(bmp.GetHicon())


def _size(n) -> str:
    n = float(n or 0)
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1024


def _words(name: str) -> str:
    """'NetworkTimeout' -> 'network timeout'."""
    return ''.join(' ' + c.lower() if c.isupper() else c for c in name).strip()


class App:
    version = APP_VERSION
    SEARCH_ENGINES = SEARCH_ENGINES  # settings page reads it off the instance

    def __init__(self, start_urls: list[str], restore: bool = True):
        self.name = APP_NAME
        self.closing = False  # the window is closing: links from a later start are not taken any more
        self.closed: list[tuple[str, str]] = []  # (url, title) of the last ten closed tabs
        self.config = load_config()
        self.data_dir = DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        self.profile_dir = os.path.join(self.data_dir, 'profile')
        self.browser_args = '--disable-features=ElasticOverscroll'
        self.blocker = Blocker(os.path.join(ROOT, 'blocklist.txt'), os.path.join(self.data_dir, 'shields.json'))
        self.scanner = FableScanner(self.config, os.path.join(self.data_dir, 'scan_cache.json'))
        self.stats = Stats(os.path.join(self.data_dir, 'stats.json'))
        self.bookmarks = Bookmarks(os.path.join(self.data_dir, 'bookmarks.json'))
        self.history = History(os.path.join(self.data_dir, 'history.json'))
        self.settings = Settings(os.path.join(self.data_dir, 'settings.json'))
        self.passwords = Passwords(os.path.join(self.data_dir, 'passwords.json'))
        self.session = Session(os.path.join(self.data_dir, 'session.json'))
        self.extensions = extensions.Extensions(os.path.join(self.data_dir, 'extensions'))
        self.extensions.empty_trash()  # removed extensions and replaced versions, before WebView2 runs
        self.ext_note = ''  # the last extension add/update/remove result, shown on the settings page
        self.import_note = ''  # result of the last bookmark/password import, shown on the library page
        self.antivirus = AntivirusScanner()
        self.update_banner: dict | None = None  # a newer release exists -> banner on the new tab page
        self.update_state: dict = {'phase': 'idle'}  # idle -> downloading -> ready (restart to install)
        self.update_note = ''  # the settings Check button's answer, e.g. "You're up to date"
        self.https_only = self.config.get('https_only', True)
        # Fingerprinting keys: new every start, and private tabs use their own so a site cannot
        # match a private visit to a normal one.
        self.fp_keys = {False: privacy.new_session_key(), True: privacy.new_session_key()}

        self.allowed_once: set[str] = set()
        self.allowed_hosts: set[str] = set()
        self.https_attempted: dict[str, str] = {}
        self.http_fallback: set[str] = set()
        self.tabs: list[Tab] = []
        self.active: Tab | None = None
        self.downloads: list[dict] = []
        self._download_ids = 0
        self.filter_errors: list[str] = []
        self._pending_download = None
        self._details_open = False
        self._ui_ready = self._ui_dirty = self._panel_dirty = False
        self._panel_due = 0.0

        self._build_form()
        self.scheduler = TaskScheduler.FromCurrentSynchronizationContext()  # cookie sweep continuations
        active = self._restore_session() if restore and self.settings.get('restore_tabs', True) else None
        for url in start_urls:  # links opened from another app come after the restored tabs, in front
            self.new_tab(url)
        if not start_urls:
            if active is not None:
                self.select(active)
            else:
                self.new_tab(None)
        threading.Thread(target=self._load_filters, daemon=True).start()
        threading.Thread(target=self._check_updates, daemon=True).start()

        self.slow_timer = WinForms.Timer()
        self.slow_timer.Interval = 30_000
        self.slow_timer.Tick += lambda s, e: (self._sleep_check(), self._cookie_sweep(), self._save_state())
        self.slow_timer.Start()

    # ---- window ----------------------------------------------------------
    def _build_form(self):
        f = self.form = WinForms.Form()
        f.Text = APP_NAME
        f.Width, f.Height = 1280, 820
        f.StartPosition = WinForms.FormStartPosition.CenterScreen
        f.BackColor = Drawing.Color.FromArgb(255, *STRIP)
        f.Icon = _app_icon()
        f.FormClosed += self._on_closed
        # The form exists, so this thread has its WinForms context: the shared WebView2 environment
        # can be created now, and every WebView below joins it once it is there.
        self.engine = Engine(self.profile_dir, self.browser_args)

        self.content = WinForms.Panel()
        self.content.Dock = WinForms.DockStyle.Fill
        self.content.BackColor = Drawing.Color.FromArgb(255, 16, 18, 22)

        self.chrome = WebView2()
        self.chrome.Dock = WinForms.DockStyle.Top
        self.chrome.DefaultBackgroundColor = f.BackColor
        self.chrome.CoreWebView2InitializationCompleted += self._on_chrome_ready
        self.chrome.WebMessageReceived += self._on_ui_message

        f.Controls.Add(self.content)
        f.Controls.Add(self.chrome)
        hwnd = int(f.Handle.ToInt64())  # creating the handle now also lets worker threads use later()
        _dwm(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        _dwm(hwnd, DWMWA_CAPTION_COLOR, _colorref(STRIP))
        _dwm(hwnd, DWMWA_BORDER_COLOR, _colorref(STRIP))
        _dwm(hwnd, DWMWA_TEXT_COLOR, _colorref((236, 238, 242)))
        self._size_chrome()
        f.DpiChanged += lambda s, e: self._size_chrome()
        self.engine.attach(self.chrome)

        self.popup = Popup(f, self.engine, self._on_panel_message)
        f.Activated += lambda s, e: self.popup.hide()  # back in the browser window: close any panel
        self.ui_timer = WinForms.Timer()
        self.ui_timer.Interval = 50  # UI updates are batched: pages can block hundreds of requests a second
        self.ui_timer.Tick += lambda s, e: self._flush_ui()
        self.ui_timer.Start()

    def _scale(self) -> float:
        return ctypes.windll.user32.GetDpiForWindow(int(self.form.Handle.ToInt64())) / 96 or 1.0

    def _size_chrome(self):
        bar = 34 if self.settings.get('bookmarks_bar', True) else 0
        self.chrome.Height = round((CHROME_HEIGHT + bar) * self._scale())

    def _on_chrome_ready(self, sender, args):
        if not args.IsSuccess:
            WinForms.MessageBox.Show(f'WebView2 failed to start:\n{args.InitializationException}', APP_NAME)
            return
        core = self.chrome.CoreWebView2
        s = core.Settings
        s.AreDevToolsEnabled = False
        s.AreBrowserAcceleratorKeysEnabled = False  # Ctrl+R, F5... are handled by chrome.html
        s.IsZoomControlEnabled = False
        s.IsPinchZoomEnabled = False
        s.IsStatusBarEnabled = False
        s.IsSwipeNavigationEnabled = False
        core.NavigationStarting += self._lock_chrome
        core.NewWindowRequested += lambda s2, a: setattr(a, 'Handled', True)
        core.ContextMenuRequested += self._chrome_menu
        with open(os.path.join(UI_DIR, 'chrome.html'), encoding='utf-8') as fh:
            core.NavigateToString(fh.read())
        self._sync_extensions()

    @staticmethod
    def _lock_chrome(sender, args):
        if not str(args.Uri).startswith('data:'):
            args.Cancel = True  # the toolbar never navigates anywhere

    @staticmethod
    def _chrome_menu(sender, args):
        if not args.ContextMenuTarget.IsEditable:
            args.Handled = True  # keep cut/copy/paste in the address bar, nothing else

    def later(self, fn):
        """Run fn on the UI thread (safe from worker threads and from inside WebView2 events)."""
        try:
            self.form.BeginInvoke(Action(fn))
        except Exception:
            pass  # the form is gone (shutting down); there is nothing left to run fn on

    def take_links(self, urls: list[str]) -> bool:
        """Links from a later start of Lite (another app opened them): new tabs in this window.

        Runs on the instance listener thread and waits for the UI thread, so the answer is
        true: False once the window is closing, and that start then waits to take over.
        """
        taken = []

        def open_links():
            if self.closing:
                return
            taken.append(True)  # even if a tab fails below: a retry would open the others twice
            try:
                for url in urls or [None]:  # started without a link (Start menu): a new tab
                    self.new_tab(url)
                hwnd = int(self.form.Handle.ToInt64())
                user32 = ctypes.windll.user32
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE: back to normal or maximized, as it was
                user32.SetForegroundWindow(hwnd)  # the start allowed it (AllowSetForegroundWindow)
            except Exception:
                traceback.print_exc()
        try:
            self.form.Invoke(Action(open_links))
        except Exception:
            return False  # the window is gone
        return bool(taken)

    def _on_closed(self, sender, args):
        self.closing = True
        self.ui_timer.Stop()
        self.slow_timer.Stop()
        self._save_session()  # first: the tabs to bring back, whatever happens below
        self._final_cookie_sweep()  # best effort: a final wipe of cookies you would not keep
        self.stats.save()
        self.bookmarks.save()
        self.history.save()
        for t in self.tabs:
            t.dispose()
        self.popup.form.Dispose()

    # ---- toolbar (chrome.html) -------------------------------------------
    def _on_ui_message(self, sender, args):
        try:
            msg = json.loads(args.WebMessageAsJson)
        except ValueError:
            return
        ui, tab = msg.get('ui'), self.current
        if ui == 'ready':
            self._ui_ready = True
            self.refresh_chrome(tab)
            if tab and tab.builtin and not tab.url and not tab.pending_url:
                self.focus_address()
        elif tab is None:
            return
        elif ui == 'go':
            tab.navigate(to_url(str(msg.get('text', '')), self.search_url()))
            tab.webview.Focus()
        elif ui == 'escape':
            tab.webview.Focus()
        elif ui in ('back', 'forward', 'reload', 'stop'):
            self._nav(ui)
        elif ui == 'newtab':
            self.new_tab(None, private=bool(msg.get('private')))
        elif ui == 'select':
            self.select(self._tab(msg.get('id')))
        elif ui == 'close':
            self.close_tab(self._tab(msg['id']) if 'id' in msg else tab)
        elif ui == 'mute':
            target = self._tab(msg.get('id'))
            if target:
                target.toggle_mute()
        elif ui == 'cycle':
            self.cycle_tab(-1 if msg.get('step') == -1 else 1)
        elif ui == 'nth':
            self.select_nth(int(msg.get('n', 1)))
        elif ui == 'shields':
            self.show_shields(self._anchor(msg.get('rect')))
        elif ui == 'menu':
            self.show_menu(self._anchor(msg.get('rect')))
        elif ui == 'downloads':
            self.show_downloads(self._anchor(msg.get('rect')))
        elif ui == 'find':
            tab.open_find()
        elif ui == 'reader':
            tab.toggle_reader()
            tab.webview.Focus()
        elif ui == 'star':
            self.toggle_bookmark(tab)
        elif ui == 'library':
            self.show_library()
        elif ui == 'open':
            tab.navigate(str(msg.get('url', '')))
            tab.webview.Focus()
        elif ui == 'opennew':
            self.new_tab(str(msg.get('url', '')) or None)
        elif ui == 'settings':
            self.show_settings()
        elif ui == 'reopen':
            self.reopen_closed()
        elif ui == 'zoom':
            self.zoom(int(msg.get('step', 0)))
        elif ui == 'pin' and tab:
            self.toggle_pin(tab)
        elif ui == 'key':
            self.show_key(self._anchor(msg.get('rect')))
        elif ui == 'navlist':
            self.show_nav_list('forward' if msg.get('dir') == 'forward' else 'back', self._anchor(msg.get('rect')))

    def _anchor(self, rect: dict | None = None):
        """Screen rectangle (device pixels) of a toolbar button, from its CSS-pixel rect in chrome.html."""
        scale = self._scale()
        origin = self.chrome.PointToScreen(Drawing.Point(0, 0))
        if not rect:  # opened from the keyboard: under the right end of the toolbar
            return Drawing.Rectangle(origin.X + self.chrome.Width - int(10 * scale), origin.Y + int(44 * scale),
                                     1, int(36 * scale))
        return Drawing.Rectangle(origin.X + int(rect['x'] * scale), origin.Y + int(rect['y'] * scale),
                                 int(rect['w'] * scale), int(rect['h'] * scale))

    def focus_address(self):
        self.chrome.Focus()
        if self._ui_ready:
            self.chrome.CoreWebView2.PostWebMessageAsJson('{"focus": "address"}')

    def refresh_chrome(self, tab: Tab | None = None):
        """Something visible changed; the toolbar is redrawn on the next timer tick."""
        self._ui_dirty = True

    refresh_shield = refresh_chrome

    def _ui_state(self) -> dict:
        tab = self.current
        on_site = bool(tab and tab.host and not tab.builtin and not tab.url.startswith('chrome-extension://'))
        return {
            'tabs': [{'id': t.id, 'title': t.title, 'favicon': t.favicon, 'loading': t.loading, 'private': t.private,
                      'audible': t.audible, 'muted': t.muted, 'builtin': t.builtin, 'pinned': t.pinned} for t in self.tabs],
            'active': tab.id if tab else None,
            'url': tab.url if tab else '',
            'private': bool(tab and tab.private),
            'canBack': bool(tab and tab.webview.CanGoBack),
            'canForward': bool(tab and tab.webview.CanGoForward),
            'loading': bool(tab and tab.loading),
            'starred': bool(tab and on_site and self.bookmarks.is_starred(tab.url)),
            'engine': SEARCH_ENGINES.get(self.settings.get('search_engine', 'brave'), SEARCH_ENGINES['brave'])[0],
            'reader': bool(tab and (on_site or tab.reader_source)),
            'reading': bool(tab and tab.reader_source),
            'shields': {'show': on_site, 'up': on_site and self.blocker.shields_up(tab.host),
                        'count': tab.blocked if tab else 0},
            'scan': self._scan_info(tab) if on_site else {'show': False},
            'downloads': self._downloads_badge(),
            'key': self._key_state(tab),
            'bar': {'show': bool(self.settings.get('bookmarks_bar', True)),
                    'items': [{'url': b['url'], 'title': (b['title'] or b['url'])[:24]} for b in self.bookmarks.items()[:40]]},
        }

    def _flush_ui(self):
        if self.popup.view == 'downloads' and any(d['state'] == 'downloading' for d in self.downloads):
            self._panel_dirty = True  # "waiting for you" appears without a new download event
        if self._ui_dirty:
            self._ui_dirty = False
            self._panel_dirty = True
            tab = self.current
            self.form.Text = f'{tab.title} - {APP_NAME}' if tab and tab.title else APP_NAME
            if self._ui_ready:
                self.chrome.CoreWebView2.PostWebMessageAsJson(json.dumps(self._ui_state()))
        if self._panel_dirty and self.popup.visible and time.time() >= self._panel_due:
            self._panel_dirty = False
            self._panel_due = time.time() + 0.4  # live counters, without re-rendering on every request
            tab = self.current
            if self.popup.view == 'shields' and tab and tab.host:
                self.popup.update('shields', self._shields_data(tab))
            elif self.popup.view == 'downloads':
                self.popup.update('downloads', self._downloads_data())

    # ---- Shields ---------------------------------------------------------
    def _scan_info(self, tab: Tab) -> dict:
        if tab.private:
            return {'show': False, 'label': 'Off in private tabs', 'color': '#9aa0ab', 'reason': ''}
        if not self.scanner.enabled:
            return {'show': False, 'label': 'Off', 'color': '#9aa0ab',
                    'reason': 'The optional page check is not installed.'}
        label, color = SCAN_TEXT.get(tab.scan_state, SCAN_TEXT['unknown'])
        return {'show': bool(label), 'label': label or 'Not checked', 'color': color, 'reason': tab.scan_reason}

    def _shields_data(self, tab: Tab) -> dict:
        up = self.blocker.shields_up(tab.host)
        f = self.blocker.filters
        return {'host': tab.host.removeprefix('www.'), 'up': up, 'private': tab.private, 'blocked': tab.blocked,
                'popups': tab.popups, 'links': tab.links, 'fingerprints': tab.fingerprints,
                'fixes': f.fixes(tab.host) if f and up else 0,
                'https': tab.url.startswith('https://'), 'hosts': tab.blocked_hosts.most_common(40),
                'open': self._details_open, 'scan': self._scan_info(tab), 'lists': self.filters_text()}

    def _toggle_popup(self, view: str) -> bool:
        """Clicking the button of an open panel (which also closed it) only closes it."""
        if self.popup.visible and self.popup.view == view:
            self.popup.hide()
            return True
        return self.popup.just_closed(view)

    def show_shields(self, anchor=None):
        tab = self.current
        if tab and tab.host and not tab.builtin and not self._toggle_popup('shields'):
            self.popup.show('shields', self._shields_data(tab), anchor or self._anchor())

    def toggle_shields(self):
        tab = self.current
        if tab is None or not tab.host or tab.builtin:
            return
        up = not self.blocker.shields_up(tab.host)
        self.blocker.set_shields(tab.host, up, remember=not tab.private)
        tab.drop_site_scripts()
        if up:
            tab._add_site_script(tab.host, then=tab.webview.Reload)
        else:
            tab.webview.Reload()  # requests already made were decided under the old setting
        self.refresh_chrome(tab)
        self.popup.update('shields', self._shields_data(tab))

    def filters_text(self, short: bool = False) -> str:
        f = self.blocker.filters
        if f:
            return f.describe(short)
        if short:
            return ''  # the new tab page simply leaves it out until the lists are loaded
        if filters.adblock is None:
            return 'Built-in blocklist only (pip install adblock for full Shields)'
        if self.filter_errors:
            return 'Filter lists could not be downloaded - using the built-in blocklist'
        return 'Downloading filter lists...'

    def _load_filters(self):
        """Worker thread: cached engine first (fast), then download whatever has expired."""
        folder = os.path.join(self.data_dir, 'filters')
        extra = self.blocker.custom_rules()
        try:
            cached = filters.load(folder, extra)
            if cached:
                self.later(lambda: self._use_filters(cached, None))
            fresh, errors = filters.update(folder, extra, cached)
            self.later(lambda: self._use_filters(fresh or cached, errors))
        except Exception:
            traceback.print_exc()
            try:
                self.later(lambda: self._use_filters(None, ['the filter lists could not be loaded']))
            except Exception:
                pass  # window already closed

    def _use_filters(self, f, errors):
        if f is not None:
            self.blocker.filters = f
        if errors is not None:
            self.filter_errors = errors
            for e in errors:
                print('filter lists:', e)
        self.refresh_chrome()

    # ---- menu ------------------------------------------------------------
    def show_menu(self, anchor=None):
        if not self._toggle_popup('menu'):
            tab = self.current
            data = {'cookies': bool(self.settings.get('auto_delete_cookies', True)),
                    'reader': bool(tab and (tab.reader_source or (tab.host and not tab.builtin))),
                    'pinned': bool(tab and not tab.builtin and tab.pinned),
                    'store': bool(tab and not tab.private and self._store_page(tab.url))}
            self.popup.show('menu', data, anchor or self._anchor())

    def _menu(self, item: str):
        tab = self.current
        if item == 'clear':
            self.popup.show('menu', {'confirm': True}, self.popup.anchor)
            return
        self.popup.hide()
        if item == 'newtab':
            self.new_tab(None)
        elif item == 'private':
            self.new_tab(None, private=True)
        elif item == 'downloads':
            self.show_downloads(force=True)
        elif item == 'bookmarks':
            self.show_library('bookmarks')
        elif item == 'history':
            self.show_library('history')
        elif item == 'passwords':
            self.show_library('passwords')
        elif item == 'settings':
            self.show_settings()
        elif item == 'pin' and tab:
            self.toggle_pin(tab)
        elif item == 'reader' and tab:
            tab.toggle_reader()
        elif item == 'cookies':
            self.settings.set('auto_delete_cookies',
                              not self.settings.get('auto_delete_cookies', True))
            self._cookie_sweep()
        elif item == 'print' and tab:
            tab.webview.CoreWebView2.ShowPrintUI(CoreWebView2PrintDialogKind.Browser)
        elif item == 'devtools' and tab:
            tab.webview.CoreWebView2.OpenDevToolsWindow()
        elif item == 'clear-yes':
            self._clear_browsing_data()
        elif item == 'extensions':
            self.show_settings('extensions')
        elif item == 'addext' and tab and self._store_page(tab.url):
            self.add_extension(tab.url)  # the store's own "Add to Chrome" cannot reach Lite
            self.show_settings('extensions')

    @staticmethod
    def _store_page(url: str) -> bool:
        """An extension's page in the Chrome Web Store."""
        try:
            p = urlparse(url or '')
        except ValueError:
            return False
        return (p.hostname in ('chromewebstore.google.com', 'chrome.google.com') and '/detail/' in p.path
                and bool(extensions.parse_id(p.path)))

    def _clear_browsing_data(self):
        """Cookies, site data, cache and history of normal tabs, and Lite's own memory of visited sites."""
        self.chrome.CoreWebView2.Profile.ClearBrowsingDataAsync()
        self.scanner.clear()
        self.history.clear()
        self.allowed_once.clear()
        self.allowed_hosts.clear()
        self.http_fallback.clear()
        for t in self.tabs:
            if not t.private and not t.builtin:
                t.webview.Reload()

    def _on_panel_message(self, msg: dict):
        kind = msg.get('panel')
        if kind == 'toggle':
            self.toggle_shields()
        elif kind == 'details':
            self._details_open = bool(msg.get('open'))
        elif kind == 'menu':
            self._menu(str(msg.get('item')))
        elif kind == 'download':
            self._download_action(str(msg.get('act')), msg.get('id'))
        elif kind == 'confirm':
            self._finish_confirm(bool(msg.get('keep')))
        elif kind == 'hidden' and msg.get('view') == 'confirm':
            self._finish_confirm(False)  # closed without an answer: don't download
        elif kind == 'fill':
            self.fill_login(str(msg.get('user', '')))
        elif kind == 'savepw':
            self._answer_save(str(msg.get('act', '')))
        elif kind == 'navgo':
            self._go_to_entry(msg.get('id'))

    # ---- downloads -------------------------------------------------------
    def download_starting(self, tab: Tab, args):
        """WebView2's own download box shows progress (and asks before keeping files Windows
        SmartScreen does not know). Lite adds the checks before and the antivirus scan after."""
        op = args.DownloadOperation
        url, path = str(op.Uri), fallback_path(str(args.ResultFilePath))
        if path != str(args.ResultFilePath):  # Windows' Downloads folder is on a drive that is not there
            args.ResultFilePath = path
        self._download_ids += 1
        item = {'id': self._download_ids, 'name': os.path.basename(path), 'path': path, 'url': url, 'tab': tab,
                'op': op, 'state': 'downloading', 'status': 'Starting...', 'progress': 0.0, 'moved': time.time()}
        if self.blocker.is_dangerous(url):
            args.Cancel = True
            item.update(state='blocked', status='Blocked - this link is on the malware lists (URLhaus, uBlock badware)')
            self.stats.add('dangerous')
            self.downloads.insert(0, item)
            self.later(lambda: self.show_downloads(force=True, auto=True))
            return
        op.BytesReceivedChanged += lambda s, e: self._download_progress(item)
        op.StateChanged += lambda s, e: self._download_state(item)
        if guard.is_dangerous_download(item['name']):
            self._pending_download = (args, args.GetDeferral(), item)
            data = {'name': item['name'], 'host': urlparse(url).hostname or url, 'av': self.antivirus.name}
            self.later(lambda: self.popup.show('confirm', data, self._anchor()))
            return
        self.downloads.insert(0, item)
        self.refresh_chrome()

    def _finish_confirm(self, keep: bool):
        if not self._pending_download:
            return
        args, deferral, item = self._pending_download
        self._pending_download = None
        args.Cancel = not keep
        deferral.Complete()
        self.popup.hide()
        if keep:
            self.downloads.insert(0, item)
            self.refresh_chrome()

    def _download_progress(self, item: dict):
        op = item['op']
        total, got = op.TotalBytesToReceive, op.BytesReceived
        item['progress'] = got / total if total else 0.0
        item['status'] = f'{_size(got)} of {_size(total)}' if total else f'{_size(got)} so far'
        if got != item.get('got'):
            item['got'], item['moved'] = got, time.time()
            self.refresh_chrome()

    def _download_state(self, item: dict):
        op = item['op']
        state = str(op.State)
        if state == 'Completed':
            item.update(state='scanning', progress=1.0,
                        status=f'Scanning with {self.antivirus.name or "your antivirus"}...')
            try:
                item['tab'].webview.CoreWebView2.CloseDefaultDownloadDialog()  # Lite's panel takes over
            except Exception:
                pass  # tab already closed
            self.show_downloads(force=True, auto=True)
            threading.Thread(target=self._scan_download, args=(item,), daemon=True).start()
        elif state == 'Interrupted':
            reason = str(op.InterruptReason)
            if reason == 'UserCanceled':
                item.update(state='cancelled', status='Cancelled')
            elif reason in ('FileMalicious', 'FileSecurityCheckFailed', 'FileBlockedByPolicy'):
                item.update(state='threat', status='Windows blocked this file as unsafe (SmartScreen / antivirus)')
                self.stats.add('dangerous')
            else:
                item.update(state='failed', status=f'Failed - {_words(reason)}')
        self.refresh_chrome()

    def _scan_download(self, item: dict):
        verdict, text = self.antivirus.scan(item['path'])

        def apply():
            item.update(state=verdict, status=text)
            if verdict == 'threat':
                self.stats.add('dangerous')
                self.show_downloads(force=True, auto=True)
            self.refresh_chrome()
        self.later(apply)

    def _download_action(self, act: str, item_id):
        item = next((d for d in self.downloads if d['id'] == item_id), None)
        if not item:
            return
        if act == 'open' and item['state'] in ('clean', 'unscanned') and os.path.exists(item['path']):
            self.popup.hide()
            os.startfile(item['path'])  # Windows still shows its own warning for downloaded programs
        elif act == 'folder' and os.path.exists(item['path']):
            self.popup.hide()
            subprocess.Popen(['explorer', '/select,', item['path']])
        elif act == 'cancel' and item['state'] == 'downloading':
            item['op'].Cancel()
        elif act == 'review' and item['state'] == 'downloading':
            self.popup.hide()
            try:
                item['tab'].webview.CoreWebView2.OpenDefaultDownloadDialog()
            except Exception:
                pass

    def _downloads_data(self) -> dict:
        items = []
        for d in self.downloads[:12]:
            if d['state'] == 'downloading':
                self._download_progress(d)  # read it fresh: WebView2 does not report every chunk
            row = {k: d[k] for k in ('id', 'name', 'path', 'state', 'status', 'progress')}
            if d['state'] == 'downloading' and d['progress'] >= 1 and time.time() - d['moved'] > 1.5:
                row['state'] = 'waiting'  # all bytes are in: WebView2's download box wants an answer
                row['status'] = 'Waiting for you to keep or delete it in the download box'
            items.append(row)
        return {'items': items, 'av': self.antivirus.name}

    def _downloads_badge(self) -> dict:
        if not self.downloads:
            return {'show': False}
        states = {d['state'] for d in self.downloads}
        latest = self.downloads[0]['state']
        state = ('active' if states & {'downloading', 'scanning'} else 'threat' if latest in ('threat', 'blocked')
                 else 'clean' if latest == 'clean' else 'idle')
        return {'show': True, 'state': state}

    def show_downloads(self, anchor=None, force: bool = False, auto: bool = False):
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        if auto and user32.GetForegroundWindow() not in (self.form.Handle.ToInt64(), self.popup.form.Handle.ToInt64()):
            return  # you are in another app: the toolbar's download dot shows the result instead
        if force or not self._toggle_popup('downloads'):
            self.popup.show('downloads', self._downloads_data(), anchor or self._anchor())

    # ---- bookmarks, library, updates, sleeping tabs ----------------------
    def toggle_bookmark(self, tab: Tab | None):
        if tab is None or not tab.host or tab.builtin:
            return
        self.bookmarks.toggle(tab.url, tab.title)
        self.refresh_chrome(tab)

    def show_library(self, view: str = 'history'):
        tab = self.current
        if tab is None:
            return
        if not tab.builtin:  # keep the page you were on: the library opens in its own tab
            self.new_tab(None)
            tab = self.current
        tab.show_library(view)
        tab.webview.Focus()

    def import_bookmarks(self):
        """Pull bookmarks from installed browsers (read-only); the result appears on the library page."""
        notes = importer.import_all(self.bookmarks)
        self.import_note = ', '.join(notes) or 'No bookmarks found in Chrome, Edge, Brave or Firefox'
        self.show_library('bookmarks')

    def import_passwords(self):
        """Ask for a CSV exported from another browser and add its rows to the vault."""
        dialog = WinForms.OpenFileDialog()
        dialog.Title = 'Import passwords from CSV'
        dialog.Filter = 'Password CSV (*.csv)|*.csv|All files (*.*)|*.*'
        if dialog.ShowDialog(self.form) != WinForms.DialogResult.OK:
            return
        try:
            added, skipped = self.passwords.import_csv(dialog.FileName)
        except Exception as exc:
            self.import_note = f'Could not read that file: {exc}'
            self.show_library('passwords')
            return
        self.import_note = f'Imported {added} passwords' + (f', skipped {skipped} already saved' if skipped else '')
        if not added:
            self.import_note = 'No passwords imported' + (f' ({skipped} already saved)' if skipped else '')
        self.show_library('passwords')

    def copy_login(self, url: str, user: str, password: bool):
        """Put the username or the revealed password on the clipboard; the vault never displays it."""
        try:
            WinForms.Clipboard.SetText(self.passwords.reveal(url, user) if password else user)
        except Exception:
            pass

    def show_settings(self, focus: str = ''):
        """focus: a section to scroll to, e.g. 'extensions'."""
        tab = self.current
        if tab is None:
            return
        if not tab.builtin:  # keep the page you were on: settings open in their own tab
            self.new_tab(None)
            tab = self.current
        tab.show_settings(focus)
        tab.webview.Focus()

    # ---- passwords: offered after you sign in, filled from the key button ---
    def _key_state(self, tab: Tab | None) -> dict:
        if not tab or tab.builtin or not self.settings.get('password_manager', True):
            return {'show': False}
        if tab.save_offer():
            return {'show': True, 'save': True}
        return {'show': bool(tab.login_fields) and self.passwords.has_logins(tab.url), 'save': False}

    def _in_front(self) -> bool:
        """Lite's window or its panel is the one you are using - not another app."""
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        return user32.GetForegroundWindow() in (self.form.Handle.ToInt64(), self.popup.form.Handle.ToInt64())

    def offer_save(self, tab: Tab, login: dict):
        """You signed in on `tab`: offer to save the password, or the new one of a login already saved."""
        host = autofill.site_host(login['origin'])
        if not host or host in self.settings.get('password_never', []):
            return
        try:
            status = self.passwords.status(login['origin'], login['user'], login['pw'])
        except OSError:
            return
        if status == 'same':
            return
        tab.save_pending = dict(login, update=status == 'changed', at=time.time())
        self.refresh_chrome(tab)
        if tab is self.current and not self.popup.visible and self._in_front():
            self.popup.show('savepw', self._save_data(tab), self._anchor())  # closed unanswered, the key reopens it

    @staticmethod
    def _save_data(tab: Tab) -> dict:
        offer = tab.save_pending
        return {'host': autofill.site_host(offer['origin']), 'user': offer['user'], 'update': offer['update']}

    def show_key(self, anchor=None):
        """The key button: the offer to save what you signed in with, else the site's logins to fill."""
        tab = self.current
        if tab is None or tab.builtin or self._toggle_popup('savepw') or self._toggle_popup('logins'):
            return
        if tab.save_offer():
            self.popup.show('savepw', self._save_data(tab), anchor or self._anchor())
        else:
            users = [r['user'] for r in self.passwords.logins_for(tab.url)]
            self.popup.show('logins', {'host': autofill.site_host(tab.url), 'users': users}, anchor or self._anchor())

    def _answer_save(self, act: str):
        tab = self.current
        offer = tab.save_offer() if tab else None
        self.popup.hide()
        if not offer or act not in ('save', 'never'):
            return
        tab.save_pending = None
        if act == 'save':
            try:
                self.passwords.remember(offer['origin'], offer['user'], offer['pw'])
            except OSError:
                traceback.print_exc()
        else:
            never = list(self.settings.get('password_never', []))
            host = autofill.site_host(offer['origin'])
            if host not in never:
                self.settings.set('password_never', never + [host])
        self.refresh_chrome(tab)

    def fill_login(self, user: str):
        """Type the chosen login into the page - and only if the page is the site it belongs to."""
        self.popup.hide()
        tab = self.current
        core = tab.webview.CoreWebView2 if tab else None
        if core is None or tab.builtin:
            return
        page = autofill.origin(str(core.Source))
        if not page or page != autofill.origin(tab.url):
            return
        row = next((r for r in self.passwords.logins_for(page) if r['user'] == user), None)
        try:
            password = self.passwords.reveal(row['url'], row['user']) if row else ''
        except OSError:
            return
        if password:
            core.ExecuteScriptAsync(autofill.fill_js(page, row['user'], password))
        tab.webview.Focus()

    # ---- back / forward lists ----------------------------------------------
    def show_nav_list(self, direction: str, anchor=None):
        """Right-click or hold Back or Forward: the pages that way, to jump several at once."""
        tab = self.current
        if tab is None or tab.webview.CoreWebView2 is None or self._toggle_popup('navlist'):
            return

        def got(task):
            if task.IsFaulted or tab is not self.current:
                return
            try:
                history = json.loads(str(task.Result))
                entries, here = history.get('entries', []), int(history.get('currentIndex', 0))
            except (ValueError, TypeError, AttributeError):
                return
            items = []
            for e in (entries[:here][::-1] if direction == 'back' else entries[here + 1:]):
                url, title = str(e.get('url', '')), str(e.get('title', ''))
                if url.startswith('data:'):  # one of Lite's own pages
                    if title in ('', 'Tab asleep'):
                        continue  # the sleeping placeholder is not a page you visited
                    where = APP_NAME
                else:
                    where = (urlparse(url).hostname or url).removeprefix('www.')
                items.append({'id': e.get('id'), 'title': title or url, 'host': where})
                if len(items) == 15:
                    break
            if items:
                self.popup.show('navlist', {'dir': direction, 'items': items}, anchor or self._anchor(), align='left')
        tab.webview.CoreWebView2.CallDevToolsProtocolMethodAsync('Page.getNavigationHistory', '{}').ContinueWith(
            Action[Task[String]](got), self.scheduler)

    def _go_to_entry(self, entry_id):
        self.popup.hide()
        tab = self.current
        if tab is None or tab.webview.CoreWebView2 is None or not isinstance(entry_id, int) or isinstance(entry_id, bool):
            return
        tab.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
            'Page.navigateToHistoryEntry', json.dumps({'entryId': entry_id}))
        tab.webview.Focus()

    # ---- extensions (extensions.py fetches and verifies, WebView2 runs them) --
    def _then(self, task, fn):
        """fn(task) on the UI thread once a WebView2 call has finished (result or error read off task)."""
        task.ContinueWith(Action[Task](lambda _t: fn(task)), self.scheduler)

    def _browser_version(self) -> str:
        try:
            return str(self.engine.env.BrowserVersionString).split()[0]
        except Exception:
            return '140.0.0.0'

    def _profile(self):
        core = self.chrome.CoreWebView2  # the toolbar uses the normal profile: the one extensions run in
        return core.Profile if core is not None and self.engine.extensions else None

    def _sync_extensions(self):
        """Start-up: WebView2 keeps the extensions it was given, but drops one whose files changed -
        bring those back, adopt the on/off state it remembers, then check for new versions."""
        profile = self._profile()
        if profile is None:
            return
        task = profile.GetBrowserExtensionsAsync()

        def done(t):
            loaded = {} if t.IsFaulted else {str(e.Id): e for e in t.Result}
            for ext_id, entry in list(self.extensions.items.items()):
                if ext_id in loaded:
                    entry['enabled'] = bool(loaded[ext_id].IsEnabled)
                elif os.path.isdir(entry['folder']):
                    self._load_extension(entry, quiet=True)
            if self.extensions.due_for_update_check():
                threading.Thread(target=self._update_extensions, args=(self._browser_version(),), daemon=True).start()
        self._then(task, done)

    def _with_extension(self, ext_id: str, fn):
        """fn(WebView2's extension object, or None) for one installed extension."""
        profile = self._profile()
        if profile is None:
            fn(None)
            return
        task = profile.GetBrowserExtensionsAsync()
        self._then(task, lambda t: fn(None if t.IsFaulted else next((e for e in t.Result if str(e.Id) == ext_id), None)))

    def _extension_note(self, note: str):
        self.ext_note = note
        self._refresh_settings_pages(focus='extensions')

    def _load_extension(self, entry: dict, quiet: bool = False, updated: bool = False):
        """Hand an unpacked extension to WebView2; on success it is remembered (and runs from now on)."""
        profile = self._profile()
        if profile is None:
            return
        task = profile.AddBrowserExtensionAsync(entry['folder'])

        def done(t):
            if t.IsFaulted:
                reason = str(t.Exception.InnerException.Message if t.Exception.InnerException else t.Exception)
                print('extension failed to load:', entry['folder'], reason)
                if not any(e['folder'] == entry['folder'] for e in self.extensions.items.values()):
                    self.extensions.discard(entry['folder'])  # never used; an update's old version stays installed
                if not quiet:
                    self._extension_note(f"Couldn't add {entry['name']}: {reason.strip()[:160]}")
                return
            ext = t.Result
            ext_id = str(ext.Id)
            old = self.extensions.items.get(ext_id)
            self.extensions.items[ext_id] = dict(entry, id=ext_id)
            self.extensions.keep(entry['folder'])
            self.extensions.save()
            if old and old['folder'] != entry['folder']:
                self.extensions.throw_away(old['folder'])  # the version this one replaced in place
            if bool(ext.IsEnabled) != entry.get('enabled', True):
                ext.EnableAsync(entry.get('enabled', True))  # e.g. added back after a removal (which left it off)
            if not quiet:
                verb = 'Updated' if updated else 'Added'
                self._extension_note(f"{verb} {entry['name']} {entry['version']} - reload a page to see it there.")
        self._then(task, done)

    def add_extension(self, text: str):
        """A Chrome Web Store link (or ID) from the settings page or the menu."""
        ext_id = extensions.parse_id(text)
        if not self.engine.extensions:
            self._extension_note('This WebView2 cannot run extensions - update the Microsoft Edge WebView2 '
                                 'Runtime and restart Lite.')
        elif not ext_id:
            self._extension_note('Paste the link of an extension in the Chrome Web Store '
                                 '(chromewebstore.google.com/detail/...).')
        elif ext_id in self.extensions.items:
            self._extension_note(f"{self.extensions.items[ext_id]['name']} is added already.")
        else:
            self._extension_note('Adding the extension...')
            threading.Thread(target=self._fetch_extension, args=(ext_id, self._browser_version()), daemon=True).start()

    def _fetch_extension(self, ext_id: str, version: str):
        """Worker thread: download, check the signature, unpack - then WebView2 loads it."""
        try:
            entry = self.extensions.fetch(ext_id, version)
        except Exception as e:
            traceback.print_exc()
            why = extensions.explain(e)
            self.later(lambda: self._extension_note(f"Couldn't add it: {why}."))
            return
        self.later(lambda: self._load_extension(entry))

    def add_extension_folder(self):
        """An unpacked extension (a folder with manifest.json), e.g. a developer build."""
        if not self.engine.extensions:
            self.add_extension('')  # says why not
            return
        dialog = WinForms.FolderBrowserDialog()
        dialog.Description = "Pick the extension's folder - the one with manifest.json in it"
        if dialog.ShowDialog(self.form) != WinForms.DialogResult.OK:
            return
        try:
            entry = self.extensions.copy_folder(str(dialog.SelectedPath))
        except (OSError, ValueError) as e:
            self._extension_note(f"Couldn't add that folder: {extensions.explain(e)}.")
            return
        self._load_extension(entry)

    def toggle_extension(self, ext_id: str, on: bool):
        def toggle(ext):
            entry = self.extensions.items.get(ext_id)
            if ext is None or entry is None:
                return
            def done(t):
                if not t.IsFaulted:
                    entry['enabled'] = bool(on)
                    self.extensions.save()
                self._extension_note(f"{entry['name']} is {'on' if on else 'off'}"
                                     + (' - reload a page to see it there.' if on else ' - reload open pages to remove it there.'))
            self._then(ext.EnableAsync(bool(on)), done)
        self._with_extension(ext_id, toggle)

    def remove_extension(self, ext_id: str):
        """Off right away; its files go at the next start, and WebView2 then drops it. Not RemoveAsync:
        after that call the WebView2 browser process keeps running once Lite has closed."""
        def remove(ext):
            entry = self.extensions.forget(ext_id)
            note = f"Removed {entry['name']} - reload open pages to take it off them." if entry else ''
            if ext is None:
                self._extension_note(note)
            else:
                self._then(ext.EnableAsync(False), lambda _t: self._extension_note(note))
        self._with_extension(ext_id, remove)

    def open_extension_page(self, ext_id: str, which: str):
        """An extension's pop-up or options page, in a tab of its own (WebView2 has no toolbar for them)."""
        entry = self.extensions.items.get(ext_id)
        page = entry.get(which) if entry and which in ('popup', 'options') else ''
        if page:
            self.new_tab(f'chrome-extension://{ext_id}/{page.lstrip("/")}')

    def _update_extensions(self, version: str):
        """Worker thread, once a day: a newer version from the store replaces the installed one."""
        for ext_id, entry in list(self.extensions.items.items()):
            if entry.get('source') != 'store':
                continue
            try:
                if extensions.latest_version(entry.get('store_id', ext_id), entry['version'], version):
                    new = self.extensions.fetch(entry.get('store_id', ext_id), version)
                    self.later(lambda i=ext_id, e=new, old=dict(entry): self._replace_extension(i, e, old))
            except Exception:
                traceback.print_exc()  # offline or the store is slow: tomorrow's check tries again

        def checked():
            self.extensions.checked = time.time()
            self.extensions.save()
        self.later(checked)

    def _replace_extension(self, ext_id: str, entry: dict, old: dict):
        """Adding the new version's folder replaces the installed one in place (same ID)."""
        if ext_id not in self.extensions.items:
            self.extensions.discard(entry['folder'])  # removed while the update downloaded
            return
        entry['enabled'] = old.get('enabled', True)
        self._load_extension(entry, updated=True)

    def _extensions_data(self) -> list[dict]:
        return [{'id': ext_id, 'name': e['name'], 'version': e['version'], 'enabled': e.get('enabled', True),
                 'popup': bool(e.get('popup')), 'options': bool(e.get('options')), 'source': e.get('source', 'store')}
                for ext_id, e in sorted(self.extensions.items.items(), key=lambda kv: kv[1]['name'].lower())]

    # ---- zoom, closed tabs, pinned tabs ------------------------------------
    def zoom_for(self, host: str) -> float:
        try:
            return min(5.0, max(0.25, float(self.settings.get('site_zoom', {}).get(host, 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def remember_zoom(self, host: str, factor: float) -> None:
        if not host or factor <= 0:
            return
        zooms = dict(self.settings.get('site_zoom', {}))
        if abs(factor - 1.0) < 0.001:  # back to 100%: forget the site
            if host in zooms:
                zooms.pop(host)
                self.settings.set('site_zoom', zooms)
            return
        if abs(zooms.get(host, 1.0) - factor) > 0.001:
            zooms[host] = round(factor, 3)
            self.settings.set('site_zoom', zooms)

    def zoom(self, step: int):
        """Zoom the current page in (+1), out (-1) or back to 100% (0); keys are remembered per site."""
        tab = self.current
        if tab is None or tab.builtin or tab.webview.CoreWebView2 is None:
            return
        if step == 0:
            tab.webview.ZoomFactor = 1.0
        else:
            f = tab.webview.ZoomFactor * (1.1 if step > 0 else 1 / 1.1)
            tab.webview.ZoomFactor = min(5.0, max(0.25, f))
        if tab.host and not tab.private:  # Ctrl+scroll zoom stays live-only; keys are remembered
            self.remember_zoom(tab.host, tab.webview.ZoomFactor)

    def reopen_closed(self):
        if not self.closed:
            return
        url, _title = self.closed.pop()
        self.new_tab(url)

    def search_url(self) -> str:
        return SEARCH_ENGINES.get(self.settings.get('search_engine', 'brave'), SEARCH_ENGINES['brave'])[1]

    def search_results(self, tab: Tab, query: str):
        """Lite's own search: fetch results from a clean index in the background, render them locally."""
        tab.show_search_results(query, None)  # the "Searching..." page while the fetch runs
        def worker():
            try:
                results = search_mod.fetch(query)
            except Exception:
                traceback.print_exc()
                results = []
            def render():
                if tab.builtin and tab.search_query == query:  # the tab moved on to something else meanwhile
                    tab.show_search_results(query, results)
            self.later(render)
        threading.Thread(target=worker, daemon=True, name='lite-search').start()

    def toggle_pin(self, tab: Tab | None):
        if tab is not None and not tab.builtin:
            tab.toggle_pin()

    def set_setting(self, key: str, value) -> None:
        """Apply a change from the settings page right away."""
        if key == 'auto_delete_cookies':
            self.settings.set(key, bool(value))
            if value:
                self._cookie_sweep()
        elif key == 'tab_sleep':
            self.settings.set(key, bool(value))
        elif key == 'sleep_after_minutes':
            self.settings.set(key, max(1, min(1440, int(value or 30))))
        elif key == 'update_check':
            self.settings.set(key, bool(value))
        elif key == 'search_engine' and value in SEARCH_ENGINES:
            self.settings.set(key, str(value))
            self.refresh_chrome()  # the address bar placeholder names the engine
        elif key == 'bookmarks_bar':
            self.settings.set(key, bool(value))
            self._size_chrome()  # the tab area grows or shrinks with the bar
            self.refresh_chrome()
        elif key == 'performance_mode':
            self.settings.set(key, bool(value))
            self.apply_performance_mode()
        elif key == 'restore_tabs':
            self.settings.set(key, bool(value))
            self._save_session()  # off: the saved tabs are deleted right away
        elif key == 'password_manager':
            self.settings.set(key, bool(value))
            for t in list(self.tabs):
                t.set_login_script(bool(value))
            self.refresh_chrome()
        elif key == 'password_never' and not value:  # the page can only clear the list, never fill it
            self.settings.set(key, [])
            self._refresh_settings_pages()

    def set_default_browser(self):
        """Register Lite with Windows, then the Default apps page asks the user to confirm."""
        if not getattr(sys, 'frozen', False):
            return  # a source checkout cannot be a default browser: there is no Lite.exe
        try:
            default_browser.register(os.path.abspath(sys.executable))
            default_browser.open_settings()
        except OSError:
            traceback.print_exc()  # registration is a convenience: the browser works either way

    def apply_performance_mode(self):
        """Set the renderer memory target of every live tab right away."""
        low = self.settings.get('performance_mode', False)
        for t in list(self.tabs):
            core = t.webview.CoreWebView2
            if core is not None:
                core.MemoryUsageTargetLevel = (CoreWebView2MemoryUsageTargetLevel.Low if low
                                               else CoreWebView2MemoryUsageTargetLevel.Normal)

    def clear_cookies_now(self):
        """Wipe every cookie, starred sites included - the settings page's explicit button."""
        core = self.chrome.CoreWebView2
        if core is None:
            return
        try:
            task = core.CookieManager.GetCookiesAsync(None)
        except Exception:
            return

        def worker():
            for _ in range(100):
                if task.IsCompleted:
                    break
                time.sleep(0.1)
            if task.IsCompleted and not task.IsFaulted:
                self.later(lambda: self._delete_cookies(task.Result, set(), everything=True))
        threading.Thread(target=worker, daemon=True).start()

    def _check_updates(self):
        """Worker thread: once a day, does this release exist on GitHub that is newer?"""
        if not self.settings.get('update_check', True):
            return
        update = updater.check(os.path.join(self.data_dir, 'update_check.json'), APP_VERSION)
        if update:
            self.later(lambda: self._use_update_banner(update))

    def _use_update_banner(self, banner: dict):
        self.update_banner = banner
        self.update_note = ''  # the answer on the settings page is about to change
        self._refresh_update_pages()
        # over the air: a known update downloads in the background without being asked
        if getattr(sys, 'frozen', False) and self.update_state['phase'] == 'idle':
            self._start_download()

    def _refresh_update_pages(self):
        """The banner on every open new tab page reflects the update's phase."""
        for t in list(self.tabs):
            if t.builtin and not t.url and not t.pending_url:
                t.show_new_tab()

    def _refresh_settings_pages(self, focus: str = ''):
        """Open settings pages pick up the Check button's answer (or an extension's news)."""
        for t in list(self.tabs):
            if t.builtin and t.title == 'Settings':
                t.show_settings(focus)

    def _start_download(self):
        self.update_state = {'phase': 'downloading'}
        self._refresh_update_pages()
        threading.Thread(target=self._download_update, daemon=True).start()

    def update_now(self):
        """The restart button, or the settings Check: keep the over-the-air flow moving."""
        if not getattr(sys, 'frozen', False):
            return  # running from source: a checkout updates with git, not over the air
        if self.update_state['phase'] == 'downloading':
            return
        if self.update_state['phase'] == 'ready':
            self._install_update()
            return
        if not self.update_banner:  # pressed before the daily check: look right now
            try:
                update = updater.check(os.path.join(self.data_dir, 'update_check.json'), APP_VERSION, force=True)
            except Exception as e:
                self._log_update_error()
                self.update_note = f"Couldn't check for updates - {updater.describe(e)}."
                self._refresh_settings_pages()
                return
            if not update:
                self.update_note = f'You are up to date - Lite {APP_VERSION} is the newest release.'
                self._refresh_settings_pages()
                return
            self._use_update_banner(update)  # starts the background download
        else:
            self._start_download()  # a failed download: the buttons try again

    def _download_update(self):
        """Worker thread: fetch the release zip into this process's work folder and unpack it there."""
        updater.clean_leftovers()
        try:
            url = updater.asset_url(self.update_banner['tag'])
            if not url:
                raise FileNotFoundError(f'the release has no {updater.ASSET}')
            folder = updater.unpack(updater.download(url))
            state = {'phase': 'ready', 'folder': folder}
        except Exception as e:
            self._log_update_error()
            state = {'phase': 'idle', 'error': updater.describe(e)}  # the banner says why and invites a retry
        self.update_state = state
        self.later(self._refresh_update_pages)

    def _log_update_error(self):
        """The packaged app has no console: why an update failed goes to update.log in the profile."""
        traceback.print_exc()
        try:
            path = os.path.join(self.data_dir, 'update.log')
            mode = 'a' if os.path.isfile(path) and os.path.getsize(path) < 64 * 1024 else 'w'
            with open(path, mode, encoding='utf-8') as f:
                f.write(f'--- {time.strftime("%Y-%m-%d %H:%M:%S")} Lite {APP_VERSION}\n{traceback.format_exc()}\n')
        except OSError:
            pass

    def _install_update(self):
        """Exit, then a detached script swaps the extracted release over this folder and restarts Lite."""
        folder = self.update_state.get('folder')
        if not folder or not os.path.isfile(os.path.join(folder, 'Lite.exe')):
            return
        pid, target = os.getpid(), os.path.dirname(os.path.abspath(sys.executable))
        script = os.path.join(tempfile.gettempdir(), f'Lite-update-{pid}.cmd')
        download_dir = updater.work_dir()  # only ever this folder: never the folder that holds it
        if not os.path.abspath(folder).startswith(download_dir + os.sep):
            return
        with open(script, 'w', encoding='ascii') as f:
            # ping is the delay because "timeout" aborts a batch that has no console to read.
            # The copy is the wait: this folder is locked while Lite runs, so xcopy only
            # succeeds once the app has exited. Retry for up to 5 minutes, then give up.
            f.write(f'''@echo off\r
setlocal EnableDelayedExpansion\r
set /a tries=0\r
:wait\r
xcopy /E /Y /I "{folder}" "{target}" >nul 2>&1\r
if errorlevel 1 (\r
  ping -n 2 127.0.0.1 >nul\r
  set /a tries+=1\r
  if !tries! LSS 300 goto wait\r
  exit\r
)\r
start "" "{target}\\Lite.exe"\r
rd /s /q "{download_dir}"\r
del "%~f0"\r''')
        # A hidden console that xcopy and ping share: under DETACHED_PROCESS each of them would
        # open a console window of its own. Breakaway leaves any job that would kill children.
        NO_WINDOW, BREAKAWAY = 0x08000000, 0x01000000
        try:
            subprocess.Popen(['cmd', '/c', script], creationflags=NO_WINDOW | BREAKAWAY, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            subprocess.Popen(['cmd', '/c', script], creationflags=NO_WINDOW, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.form.Close()

    def _sleep_check(self):
        if not self.settings.get('tab_sleep', True):
            return
        minutes = int(self.settings.get('sleep_after_minutes', 30))
        if self.settings.get('performance_mode', False):  # high performance: background tabs let go in 5 minutes
            minutes = min(minutes, 5)
        limit = minutes * 60
        now = time.time()
        for t in self.tabs:
            if t is not self.active and t.sleepable() and now - t.last_active > limit:
                t.sleep()

    # ---- the tabs of the last session ---------------------------------------
    def _restore_session(self) -> Tab | None:
        """Bring back the tabs Lite had open: asleep until you look at one, so the start stays fast.
        Returns the tab you were on, or None when that was one of Lite's own pages."""
        chosen, active = None, self.session.active()
        for i, entry in enumerate(self.session.tabs()):
            tab = Tab(self, entry['url'], asleep=True, title=entry['title'])
            tab.pinned = entry['pinned']
            self.tabs.append(tab)
            if i == active:
                chosen = tab
        return chosen

    def _save_session(self):
        if not self.settings.get('restore_tabs', True):
            self.session.forget()  # switched off: nothing about your tabs stays on disk
        else:
            entries = [(t, t.session_url()) for t in self.tabs]
            saved = [(t, url) for t, url in entries if url]
            active = next((i for i, (t, _) in enumerate(saved) if t is self.active), -1)
            self.session.remember([{'url': url, 'title': t.title, 'pinned': t.pinned} for t, url in saved], active)
        self.session.save()

    def _save_state(self):
        """Every 30 seconds: what a crash or a power cut would otherwise lose (only what changed is written)."""
        self._save_session()
        self.bookmarks.save()
        self.history.save()
        for t in self.tabs:
            t.expire_logins()  # a password you did not save does not wait in memory past its offer

    def _open_hosts(self) -> set[str]:
        """Sites open in normal tabs (sleeping ones included: they come back signed in), and the
        sites their pages load from while they are open."""
        return {h for t in self.tabs for h in t.cookie_hosts() if h}

    def _final_cookie_sweep(self):
        """The close-time sweep must run synchronously: there is no message loop left to call it back."""
        if not self.settings.get('auto_delete_cookies', True):
            return
        core = self.chrome.CoreWebView2
        if core is None:
            return
        open_hosts = self._open_hosts()
        try:
            task = core.CookieManager.GetCookiesAsync(None)
            for _ in range(100):  # up to ~2 s; pumping lets the COM result arrive on this STA thread
                if task.IsCompleted:
                    break
                WinForms.Application.DoEvents()
                time.sleep(0.02)
            if task.IsCompleted and not task.IsFaulted:
                self._delete_cookies(task.Result, open_hosts)
        except Exception:
            pass  # best effort: a failed final wipe just leaves the cookies for the next start

    def _cookie_sweep(self):
        """Delete the cookies of sites you have not starred and are not open (auto-delete mode)."""
        if not self.settings.get('auto_delete_cookies', True):
            return
        core = self.chrome.CoreWebView2
        if core is None:
            return
        open_hosts = self._open_hosts()
        try:
            task = core.CookieManager.GetCookiesAsync(None)
        except Exception:
            return

        def worker():
            for _ in range(100):  # the wait must happen off the UI thread; the cookies themselves are
                if task.IsCompleted:  # COM-STA bound and may only be touched on it, so hand back the task
                    break
                time.sleep(0.1)
            if task.IsCompleted and not task.IsFaulted:
                self.later(lambda: self._delete_cookies(task.Result, open_hosts))
        threading.Thread(target=worker, daemon=True).start()

    def _delete_cookies(self, cookies, open_hosts: set[str], everything: bool = False):
        try:
            for c in cookies:
                uri = f'https://{str(c.Domain).lstrip(".").lower()}{c.Path}'
                if not everything and host_keeps_cookie(str(c.Domain), self.bookmarks.hosts(), open_hosts):
                    continue
                self.chrome.CoreWebView2.CookieManager.DeleteCookies(str(c.Name), uri)
        except Exception:
            pass  # best effort: a failed sweep just leaves the cookies for the next one

    # ---- tabs ------------------------------------------------------------
    @property
    def current(self) -> Tab | None:
        return self.active

    def _tab(self, tab_id) -> Tab | None:
        return next((t for t in self.tabs if t.id == tab_id), None)

    def new_tab(self, url: str | None, private: bool = False):
        tab = Tab(self, to_url(url, self.search_url()) if url else None, private)
        self.tabs.append(tab)
        self.select(tab)

    def select(self, tab: Tab | None):
        if tab is None or tab not in self.tabs:
            return
        previous, self.active = self.active, tab
        tab.last_active = time.time()
        tab.webview.Visible = True
        tab.webview.BringToFront()
        if previous is not None and previous is not tab:
            previous.last_active = min(previous.last_active, time.time())  # sleeping counts from when you left
            previous.webview.Visible = False
        if tab.sleep_url:
            tab.wake()  # a sleeping tab reloads when you come back to it
        self.popup.hide()
        self.refresh_chrome(tab)
        if tab.builtin and not tab.url and not tab.pending_url:
            self.focus_address()
        else:
            tab.webview.Focus()

    def close_tab(self, tab: Tab | None):
        if tab is None or tab not in self.tabs:
            return
        if len(self.tabs) == 1:
            self.form.Close()
            return
        i = self.tabs.index(tab)
        self.tabs.remove(tab)
        if tab.url and tab.url.startswith(('http://', 'https://')) and not tab.private:
            self.closed.append((tab.url, tab.title))  # Ctrl+Shift+T brings it back
            del self.closed[:-10]  # keep the last ten
        if tab is self.active:
            self.active = None
            self.select(self.tabs[min(i, len(self.tabs) - 1)])
        tab.dispose()
        self.refresh_chrome()

    def cycle_tab(self, step: int):
        if self.tabs and self.active in self.tabs:
            self.select(self.tabs[(self.tabs.index(self.active) + step) % len(self.tabs)])

    def select_nth(self, n: int):
        if self.tabs:
            self.select(self.tabs[-1] if n == 9 else self.tabs[n - 1] if n <= len(self.tabs) else None)

    def _nav(self, what: str):
        tab = self.current
        if not tab or tab.webview.CoreWebView2 is None:
            return
        wv = tab.webview
        if what == 'back' and wv.CanGoBack:
            wv.GoBack()
        elif what == 'forward' and wv.CanGoForward:
            wv.GoForward()
        elif what == 'reload':
            if tab.builtin and not tab.showing_block_page:
                tab.show_new_tab()  # fresh counters
            else:
                wv.Reload()
        elif what == 'stop':
            wv.Stop()

    def run(self):
        WinForms.Application.Run(self.form)


def run(argv: list[str]):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    # One Lite per profile: when one is open already it takes the links, and this start is done.
    # The main thread holds the profile's mutex until the process ends.
    owner = instance.claim(DATA_DIR, argv)
    if owner is None:
        return
    WinForms.Application.EnableVisualStyles()

    # WebView2 (COM) requires a single-threaded apartment; Python's main thread is not one.
    def ui_main():
        try:
            app = App(argv)
            owner.serve(app.take_links)
            app.run()
        except Exception:
            traceback.print_exc()
        finally:
            owner.close()

    thread = Thread(ThreadStart(ui_main))
    thread.SetApartmentState(ApartmentState.STA)
    thread.Start()
    while thread.IsAlive:
        thread.Join(200)  # short joins keep Ctrl+C in the console working
