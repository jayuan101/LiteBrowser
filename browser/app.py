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
import threading
import time
import traceback
from urllib.parse import urlparse

from . import filters, guard, privacy, updater
from .blocker import Blocker
from .downloads import Scanner as AntivirusScanner
from .popup import Popup
from .scanner import FableScanner
from .stats import Stats
from .store import Bookmarks, History, Settings, host_keeps_cookie
from .tab import Tab, to_url
from .webview2 import (
    Action, ApartmentState, CoreWebView2CreationProperties, CoreWebView2PrintDialogKind, Drawing, Drawing2D,
    System, Task, TaskScheduler, Thread, ThreadStart, WebView2, WinForms,
)

APP_NAME = 'Lite'
APP_VERSION = '0.2.0'
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
    def __init__(self, start_urls: list[str]):
        self.name = APP_NAME
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
        self.antivirus = AntivirusScanner()
        self.update_banner: dict | None = None  # a newer release exists -> banner on the new tab page
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
        for url in start_urls or [None]:
            self.new_tab(url)
        threading.Thread(target=self._load_filters, daemon=True).start()
        threading.Thread(target=self._check_updates, daemon=True).start()

        self.slow_timer = WinForms.Timer()
        self.slow_timer.Interval = 30_000
        self.slow_timer.Tick += lambda s, e: (self._sleep_check(), self._cookie_sweep())
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

        self.content = WinForms.Panel()
        self.content.Dock = WinForms.DockStyle.Fill
        self.content.BackColor = Drawing.Color.FromArgb(255, 16, 18, 22)

        self.chrome = WebView2()
        props = CoreWebView2CreationProperties()
        props.UserDataFolder = self.profile_dir
        props.AdditionalBrowserArguments = self.browser_args
        self.chrome.CreationProperties = props
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
        self.chrome.EnsureCoreWebView2Async(None)

        self.popup = Popup(f, self.profile_dir, self.browser_args, self._on_panel_message)
        f.Activated += lambda s, e: self.popup.hide()  # back in the browser window: close any panel
        self.ui_timer = WinForms.Timer()
        self.ui_timer.Interval = 50  # UI updates are batched: pages can block hundreds of requests a second
        self.ui_timer.Tick += lambda s, e: self._flush_ui()
        self.ui_timer.Start()

    def _scale(self) -> float:
        return ctypes.windll.user32.GetDpiForWindow(int(self.form.Handle.ToInt64())) / 96 or 1.0

    def _size_chrome(self):
        self.chrome.Height = round(CHROME_HEIGHT * self._scale())

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

    def _on_closed(self, sender, args):
        self.ui_timer.Stop()
        self.slow_timer.Stop()
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
            tab.navigate(to_url(str(msg.get('text', ''))))
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
        on_site = bool(tab and tab.host and not tab.builtin)
        return {
            'tabs': [{'id': t.id, 'title': t.title, 'favicon': t.favicon, 'loading': t.loading, 'private': t.private,
                      'audible': t.audible, 'muted': t.muted, 'builtin': t.builtin} for t in self.tabs],
            'active': tab.id if tab else None,
            'url': tab.url if tab else '',
            'private': bool(tab and tab.private),
            'canBack': bool(tab and tab.webview.CanGoBack),
            'canForward': bool(tab and tab.webview.CanGoForward),
            'loading': bool(tab and tab.loading),
            'starred': bool(tab and on_site and self.bookmarks.is_starred(tab.url)),
            'reader': bool(tab and (on_site or tab.reader_source)),
            'reading': bool(tab and tab.reader_source),
            'shields': {'show': on_site, 'up': on_site and self.blocker.shields_up(tab.host),
                        'count': tab.blocked if tab else 0},
            'scan': self._scan_info(tab) if on_site else {'show': False},
            'downloads': self._downloads_badge(),
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
                    'reader': bool(tab and (tab.reader_source or (tab.host and not tab.builtin)))}
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

    # ---- downloads -------------------------------------------------------
    def download_starting(self, tab: Tab, args):
        """WebView2's own download box shows progress (and asks before keeping files Windows
        SmartScreen does not know). Lite adds the checks before and the antivirus scan after."""
        op = args.DownloadOperation
        url, path = str(op.Uri), str(args.ResultFilePath)
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

    def _check_updates(self):
        """Worker thread: once a day, does this release exist on GitHub that is newer?"""
        update = updater.check(os.path.join(self.data_dir, 'update_check.json'), APP_VERSION)
        if update:
            self.later(lambda: self._use_update_banner(update))

    def _use_update_banner(self, banner: dict):
        self.update_banner = banner
        for t in list(self.tabs):
            if t.builtin and not t.url and not t.pending_url:
                t.show_new_tab()  # open new tab pages pick up the banner

    def _sleep_check(self):
        if not self.settings.get('tab_sleep', True):
            return
        limit = int(self.settings.get('sleep_after_minutes', 30)) * 60
        now = time.time()
        for t in self.tabs:
            if t is not self.active and t.sleepable() and now - t.last_active > limit:
                t.sleep()

    def _final_cookie_sweep(self):
        """The close-time sweep must run synchronously: there is no message loop left to call it back."""
        if not self.settings.get('auto_delete_cookies', True):
            return
        core = self.chrome.CoreWebView2
        if core is None:
            return
        open_hosts = {t.host for t in self.tabs if t.host and not t.private and not t.builtin}
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
        open_hosts = {t.host for t in self.tabs if t.host and not t.private and not t.builtin}
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

    def _delete_cookies(self, cookies, open_hosts: set[str]):
        try:
            for c in cookies:
                uri = f'https://{str(c.Domain).lstrip(".").lower()}{c.Path}'
                keep = host_keeps_cookie(str(c.Domain), self.bookmarks.hosts(), open_hosts)
                if not keep:
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
        tab = Tab(self, to_url(url) if url else None, private)
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
    WinForms.Application.EnableVisualStyles()

    # WebView2 (COM) requires a single-threaded apartment; Python's main thread is not one.
    def ui_main():
        try:
            App(argv).run()
        except Exception:
            traceback.print_exc()

    thread = Thread(ThreadStart(ui_main))
    thread.SetApartmentState(ApartmentState.STA)
    thread.Start()
    while thread.IsAlive:
        thread.Join(200)  # short joins keep Ctrl+C in the console working
