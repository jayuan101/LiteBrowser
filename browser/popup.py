"""A small borderless pop-up window (Shields panel, menu, downloads) drawn by ui/panel.html.

One WebView2 stays alive for the whole session so panels open instantly. Windows 11
rounds its corners; it hides as soon as you click anywhere else.
"""
import ctypes
import json
import os
import time

from .webview2 import Drawing, WebView2, WinForms

UI_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'ui')
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
SWP_NOACTIVATE, SWP_NOOWNERZORDER, SW_SHOWNOACTIVATE = 0x0010, 0x0200, 4


class Popup:
    def __init__(self, owner, engine, on_message, activate: bool = True):
        """activate=False: a list that opens without taking focus (address bar suggestions), so
        typing carries on in the toolbar; it is closed by its owner, not by clicking elsewhere."""
        self.owner = owner
        self.activate = activate
        self.shown = False  # our own flag: a window shown with SetWindowPos may not update Form.Visible
        self.on_message = on_message  # gets every {panel: ...} message except size/ready/close
        self.view = None
        self.data = None
        self.width = 0
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
        if activate:
            f.Deactivate += lambda s, e: self.hide()

        self.webview = WebView2()
        self.webview.DefaultBackgroundColor = f.BackColor
        self.webview.Dock = WinForms.DockStyle.Fill
        f.Controls.Add(self.webview)
        self.webview.CoreWebView2InitializationCompleted += self._on_ready
        self.webview.WebMessageReceived += self._on_message

        _ = f.Handle, self.webview.Handle  # create the (hidden) window now so WebView2 can start
        corner = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(f.Handle.ToInt64()), DWMWA_WINDOW_CORNER_PREFERENCE,
                                                   ctypes.byref(corner), ctypes.sizeof(corner))
        if not activate:
            hwnd = int(f.Handle.ToInt64())
            u32 = ctypes.windll.user32
            u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u32.SetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
            u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            u32.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
            u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
        engine.attach(self.webview)

    @property
    def visible(self) -> bool:
        return self.shown if not self.activate else bool(self.form.Visible)

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

    def show(self, view: str, data: dict, anchor, align: str = 'right', width: int = 0) -> None:
        """Open (or refresh) a panel under the button at `anchor`; it appears once it has a size.
        align='left' for buttons at the left end of the toolbar (Back, Forward)."""
        self.view, self.data, self.anchor, self.align = view, data, anchor, align
        self.width = width  # device pixels; 0 = as wide as the panel draws itself
        self._post()

    def update(self, view: str, data: dict) -> None:
        """Refresh the panel if it is open on this view (e.g. live counters)."""
        if self.visible and self.view == view:
            self.data = data
            self._post()

    def hide(self) -> None:
        if self.visible:
            self.shown = False
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
        if getattr(self, 'width', 0):
            w = self.width
        area = WinForms.Screen.FromControl(self.owner).WorkingArea
        x = self.anchor.Left if self.align == 'left' else self.anchor.Right - w
        x = min(max(x, area.Left + 8), area.Right - w - 8)
        y = min(self.anchor.Bottom + int(6 * scale), area.Bottom - h - 8)
        if not self.activate:  # placed and shown without ever taking focus from the toolbar
            self.shown = True
            hwnd = int(self.form.Handle.ToInt64())
            ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOACTIVATE | SWP_NOOWNERZORDER)
            if not self.form.Visible:
                ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)  # WinForms sees it: the WebView2 paints
            return
        self.form.Bounds = Drawing.Rectangle(x, y, w, h)
        if not self.form.Visible:
            self.form.Show()
            self.form.Activate()
            self.webview.Focus()
