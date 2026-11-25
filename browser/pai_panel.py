"""The PAI side panel: a WebView docked to the right of the page, under the toolbar.

It draws ui/pai.html and does the talking through pai.py on a worker thread, handing each
event back to the UI thread. Toggle it with the PAI button in the toolbar or Ctrl+Shift+A.
Private tabs and Lite's own pages never send their page.
"""
import json
import os
import threading
import traceback

from . import pai
from .webview2 import Action, Drawing, String, Task, WebView2, WinForms

UI_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'ui')
WIDTH = 380  # CSS pixels; scaled by the window's DPI


class PaiPanel:
    def __init__(self, app):
        self.app = app
        self.client = pai.PaiClient(app.settings.get('pai_url', pai.DEFAULT_URL))
        self.session = ''
        self.cancel: threading.Event | None = None
        self.busy = False
        self.ready = False
        self._page_sig = None

        v = self.webview = WebView2()
        v.Dock = WinForms.DockStyle.Right
        v.Visible = False
        v.DefaultBackgroundColor = Drawing.Color.FromArgb(255, 22, 24, 29)
        v.CoreWebView2InitializationCompleted += self._on_ready
        v.WebMessageReceived += self._on_message
        form = app.form
        form.Controls.Add(v)
        # Docking runs from the back of the list: the toolbar first, then this panel, then the
        # page area fills what is left - so the panel sits under the toolbar, not beside it.
        form.Controls.SetChildIndex(v, 1)
        self.resize()
        form.DpiChanged += lambda s, e: self.resize()
        self._attached = False

    # ---- showing --------------------------------------------------------------------------
    @property
    def shown(self) -> bool:
        return bool(self.webview.Visible)

    def resize(self):
        self.webview.Width = round(WIDTH * self.app._scale())

    def toggle(self):
        if self.shown:
            self.hide()
        else:
            self.show()

    def show(self):
        if not self._attached:  # the WebView starts the first time the panel is wanted
            self._attached = True
            self.app.engine.attach(self.webview)
        self.webview.Visible = True
        self._page_sig = None
        self.refresh_page()
        self._check_health()
        self.webview.Focus()
        self._post({'focus': True})

    def hide(self):
        self.webview.Visible = False
        tab = self.app.current
        if tab:
            tab.webview.Focus()

    # ---- the WebView ----------------------------------------------------------------------
    def _on_ready(self, sender, args):
        if not args.IsSuccess:
            return
        core = self.webview.CoreWebView2
        s = core.Settings
        s.AreDevToolsEnabled = False
        s.AreDefaultContextMenusEnabled = True
        s.IsStatusBarEnabled = False
        s.IsZoomControlEnabled = False
        core.NavigationStarting += self._lock
        core.NewWindowRequested += lambda s2, a: setattr(a, 'Handled', True)
        with open(os.path.join(UI_DIR, 'pai.html'), encoding='utf-8') as fh:
            core.NavigateToString(fh.read())

    @staticmethod
    def _lock(sender, args):
        if not str(args.Uri).startswith('data:'):
            args.Cancel = True  # the panel never navigates; links in replies stay text

    def _post(self, obj: dict):
        if self.ready:
            try:
                self.webview.CoreWebView2.PostWebMessageAsJson(json.dumps(obj))
            except Exception:
                pass

    def _on_message(self, sender, args):
        try:
            msg = json.loads(args.WebMessageAsJson)
        except ValueError:
            return
        what = msg.get('pai')
        if what == 'ready':
            self.ready = True
            self._page_sig = None
            self.refresh_page()
            self._check_health()
            if self.shown:
                self._post({'focus': True})
        elif what == 'send':
            self._send(str(msg.get('text', '')), bool(msg.get('page')))
        elif what == 'stop':
            if self.cancel:
                self.cancel.set()
            self._finish()
            self._post({'note': 'Stopped. PAI finishes the answer on its own; it is in PAI\'s history.'})
        elif what == 'new':
            if self.cancel:
                self.cancel.set()
            self._finish()
            self.session = ''
        elif what == 'close':
            self.hide()

    # ---- the page ---------------------------------------------------------------------------
    def _page_state(self) -> dict:
        tab = self.app.current
        if tab is None:
            return {'ok': False, 'why': 'No page to share'}
        if tab.private:
            return {'ok': False, 'why': 'Private tabs never share their page with PAI'}
        if tab.builtin or not tab.url.startswith(('http://', 'https://')):
            return {'ok': False, 'why': 'No page to share (open a website)'}
        return {'ok': True, 'title': tab.title or tab.url}

    def refresh_page(self):
        """Keep the panel's "This page" line and buttons in step with the tab in front."""
        if not self.shown:
            return
        state = self._page_state()
        sig = json.dumps(state, sort_keys=True)
        if sig != self._page_sig:
            self._page_sig = sig
            self._post({'page': state})

    def _check_health(self):
        def work():
            ok, text = self.client.health()
            self.app.later(lambda: self._post({'health': {'ok': ok, 'text': text}}))
        threading.Thread(target=work, daemon=True, name='lite-pai-health').start()

    # ---- a turn -------------------------------------------------------------------------------
    def _send(self, text: str, with_page: bool):
        if self.busy:
            return
        if not with_page:
            if text.strip():
                self._post({'you': {'text': text}})
                self._start(text, None)
            return
        state = self._page_state()
        if not state['ok']:
            self._post({'error': state['why']})
            return
        tab = self.app.current
        self.busy = True  # until the page is read

        def collected(task):
            try:
                page = json.loads(json.loads(str(task.Result))) if not task.IsFaulted else None
            except (ValueError, TypeError):
                page = None
            if not isinstance(page, dict) or tab is not self.app.current or tab.private:
                self.busy = False
                self._post({'error': "Couldn't read this page."})
                return
            self.busy = False
            question = text.strip() or 'Summarize this page for me.'
            self._post({'you': {'text': question, 'about': page.get('title') or page.get('url', '')}})
            self._start(pai.page_message(question, page), page)
        try:
            tab.webview.ExecuteScriptAsync(pai.PAGE_JS).ContinueWith(
                Action[Task[String]](collected), tab.scheduler)
        except Exception:
            self.busy = False
            self._post({'error': "Couldn't read this page."})

    def _start(self, message: str, page: dict | None):
        self.session = self.session or pai.new_session_id()
        self.busy = True
        cancel = self.cancel = threading.Event()
        self._post({'start': True})
        session, client, later = self.session, self.client, self.app.later

        def on_event(kind, content):
            if cancel.is_set():
                return
            if kind == 'token':
                later(lambda c=str(content): self._post({'token': c}))
            elif kind == 'status':
                later(lambda c=str(content): self._post({'status': c}))
            elif kind == 'approval':
                later(lambda: self._post({'note': 'PAI wants your OK for something it is about to do. '
                                                  'Approve or deny it in the PAI app.'}))
            elif kind == 'error':
                later(lambda c=str(content): self._post({'error': f'PAI: {c}'[:400]}))

        def work():
            try:
                client.stream(session, message, on_event, cancel)
            except pai.PaiError as e:
                later(lambda m=str(e): self._post({'error': m}))
            except Exception:
                traceback.print_exc()
                later(lambda: self._post({'error': 'Something went wrong talking to PAI.'}))
            finally:
                if not cancel.is_set():
                    later(self._finish)

        threading.Thread(target=work, daemon=True, name='lite-pai-chat').start()

    def _finish(self):
        self.busy = False
        self.cancel = None
        self._post({'done': True})
