"""A small borderless pop-up window (Shields panel, menu, downloads) drawn by ui/panel.html.

One WebView2 stays alive for the whole session so panels open instantly. Windows 11
rounds its corners; it hides as soon as you click anywhere else.
"""
import ctypes
import json
import os
import time

from .webview2 import CoreWebView2CreationProperties, Drawing, WebView2, WinForms

UI_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'ui')
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2


class Popup:
    def __init__(self, owner, profile_dir: str, browser_args: str, on_message):
        self.owner = owner
        self.on_message = on_message  # gets every {panel: ...} message except size/ready/close
        self.view = None
        self.data = None
        self.anchor = None  # screen rectangle (device pixels) of the button that opened it
        self.align = 'right'  # which edge of the button the panel lines up with
        self.hidden_at = 0.0
        self.ready = False
        self._last_view = None

        f = self.form = WinForms.Form()
        f.FormBorderStyle = getattr(WinForms.FormBorderStyle, 'None')
        f.ShowInTaskbar = False
        f.StartPosition = WinForms.FormStartPosition.Manual
        f.BackColor = Drawing.Color.FromArgb(255, 32, 35, 41)
        f.Owner = owner  # always above the browser window, minimizes with it
        f.Deactivate += lambda s, e: self.hide()

        self.webview = WebView2()
        props = CoreWebView2CreationProperties()
        props.UserDataFolder = profile_dir
        props.AdditionalBrowserArguments = browser_args
        self.webview.CreationProperties = props
        self.webview.DefaultBackgroundColor = f.BackColor
        self.webview.Dock = WinForms.DockStyle.Fill
        f.Controls.Add(self.webview)
        self.webview.CoreWebView2InitializationCompleted += self._on_ready
        self.webview.WebMessageReceived += self._on_message

        _ = f.Handle, self.webview.Handle  # create the (hidden) window now so WebView2 can start
        corner = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(f.Handle.ToInt64()), DWMWA_WINDOW_CORNER_PREFERENCE,
                                                   ctypes.byref(corner), ctypes.sizeof(corner))
        self.webview.EnsureCoreWebView2Async(None)

    @property
    def visible(self) -> bool:
        return bool(self.form.Visible)

    def _on_ready(self, sender, args):
        if not args.IsSuccess:
            return
        core = self.webview.CoreWebView2
        s = core.Settings
        s.AreDefaultContextMenusEnabled = False
        s.AreDevToolsEnabled = False
        s.AreBrowserAcceleratorKeysEnabled = False
        s.IsZoomControlEnabled = False
        s.IsStatusBarEnabled = False
        core.NavigationStarting += self._lock
        with open(os.path.join(UI_DIR, 'panel.html'), encoding='utf-8') as fh:
            core.NavigateToString(fh.read())

    @staticmethod
    def _lock(sender, args):
        if not str(args.Uri).startswith('data:'):
            args.Cancel = True  # this window only ever shows Lite's own panel page

    def show(self, view: str, data: dict, anchor, align: str = 'right') -> None:
        """Open (or refresh) a panel under the button at `anchor`; it appears once it has a size.
        align='left' for buttons at the left end of the toolbar (Back, Forward)."""
        self.view, self.data, self.anchor, self.align = view, data, anchor, align
        self._post()

    def update(self, view: str, data: dict) -> None:
        """Refresh the panel if it is open on this view (e.g. live counters)."""
        if self.visible and self.view == view:
            self.data = data
            self._post()

    def hide(self) -> None:
        if self.form.Visible:
            self.form.Hide()
            self.hidden_at = time.time()
            view, self.view = self.view, None
            self.on_message({'panel': 'hidden', 'view': view})

    def just_closed(self, view: str) -> bool:
        """True right after a click outside closed `view` - so clicking its button again only closes it."""
        return time.time() - self.hidden_at < 0.3 and self._last_view == view

    def _post(self):
        self._last_view = self.view
        if self.ready:
            self.webview.CoreWebView2.PostWebMessageAsJson(json.dumps({'view': self.view, 'data': self.data}))

    def _on_message(self, sender, args):
        try:
            msg = json.loads(args.WebMessageAsJson)
        except ValueError:
            return
        kind = msg.get('panel')
        if kind == 'ready':
            self.ready = True
            if self.view:
                self._post()
        elif kind == 'size':
            self._place(int(msg.get('w', 300)), int(msg.get('h', 200)))
        elif kind == 'close':
            self.hide()
            self.owner.Activate()
        else:
            self.on_message(msg)

    def _place(self, css_w: int, css_h: int):
        if not self.view or self.anchor is None:
            return
        scale = ctypes.windll.user32.GetDpiForWindow(int(self.owner.Handle.ToInt64())) / 96 or 1
        w, h = int(css_w * scale), int(css_h * scale)
        area = WinForms.Screen.FromControl(self.owner).WorkingArea
        x = self.anchor.Left if self.align == 'left' else self.anchor.Right - w
        x = min(max(x, area.Left + 8), area.Right - w - 8)
        y = min(self.anchor.Bottom + int(6 * scale), area.Bottom - h - 8)
        self.form.Bounds = Drawing.Rectangle(x, y, w, h)
        if not self.form.Visible:
            self.form.Show()
            self.form.Activate()
            self.webview.Focus()
