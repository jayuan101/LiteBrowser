"""One browser tab: a WebView2 and everything Lite checks around it.

Before a page opens (NavigationStarting): the phishing guard, the malware lists,
uBlock-style stopping of whole ad/tracking pages, HTTPS-only, tracking-link
cleaning and - the first time a site with scriptlet rules is opened in this tab -
registering that site's scriptlets. WebView2 fixes a navigation's start-up scripts
when it begins, so Lite cancels that first navigation, registers, and re-opens it.
While it loads (WebResourceRequested): every request goes through Shields and the
rest carry the Global Privacy Control header. After it loads: the Claude Fable check.
"""
import base64
import json
import threading
import time
import traceback
from collections import Counter
from urllib.parse import quote_plus, unquote, urlparse

from . import filters, guard, pages, privacy
from .scanner import COLLECT_JS
from .webview2 import (
    Action, CoreWebView2CreationProperties, CoreWebView2FaviconImageFormat, CoreWebView2WebResourceContext,
    Drawing, Keys, MemoryStream, Stream, String, System, Task, TaskScheduler, WebView2, WinForms,
)

SEARCH_URL = 'https://search.brave.com/search?q='
MAX_SITE_SCRIPTS = 16  # scriptlet scripts kept registered per tab, least recently used dropped first
DANGEROUS = ('This site is on the malware and scam lists Lite uses (URLhaus and uBlock Origin\'s "badware '
             'risks"). It may try to install malware, steal passwords or trick you into paying.')
AD_PAGE = ('{host} is an ad or tracking server, not a normal website. Ads and tracked links often pass through '
           'servers like this before sending you on to the real page.')


def to_url(text: str) -> str:
    text = text.strip()
    if not text:
        return 'about:blank'
    p = urlparse(text)
    if p.scheme in ('http', 'https', 'about', 'edge', 'file', 'javascript', 'data'):
        return text
    looks_like_host = ' ' not in text and ('.' in text or text.startswith('localhost'))
    if looks_like_host:
        return 'https://' + text
    return SEARCH_URL + quote_plus(text)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


def _origin(url: str) -> str:
    p = urlparse(url)
    return f'{p.scheme}://{p.netloc}'


def _trim_user_agent(ua: str) -> str:
    # Drop the "Edg/..." token so sites see a plain Chromium browser, like Brave does.
    return ' '.join(t for t in ua.split() if not t.startswith('Edg/'))


def _stub_response(env, data_url: str):
    """Answer a blocked request with a harmless stand-in (an empty script, a 1x1 gif...)."""
    head, _, payload = data_url.partition(',')
    mime = head[5:].split(';')[0] or 'text/plain'
    body = base64.b64decode(payload) if head.endswith(';base64') else unquote(payload).encode()
    return env.CreateWebResourceResponse(MemoryStream(body), 200, 'OK',
                                         f'Content-Type: {mime}\nAccess-Control-Allow-Origin: *')


class Tab:
    _ids = 0

    def __init__(self, app, url: str | None, private: bool = False):
        Tab._ids += 1
        self.id = Tab._ids
        self.app = app
        self.private = private
        self.webview = WebView2()
        props = CoreWebView2CreationProperties()
        props.UserDataFolder = app.profile_dir
        props.AdditionalBrowserArguments = app.browser_args
        props.IsInPrivateModeEnabled = private  # nothing from private tabs is kept once the last one closes
        self.webview.CreationProperties = props
        self.webview.Dock = WinForms.DockStyle.Fill
        self.webview.Visible = False
        self.webview.DefaultBackgroundColor = Drawing.Color.White  # what pages without a background expect
        app.content.Controls.Add(self.webview)

        self.pending_url = url
        self.url = ''
        self.nav_url = ''  # the document being loaded or shown; source of its requests
        self.nav_id = 0
        self.host = ''
        self.title = 'Private tab' if private else 'New tab'
        self.favicon = ''
        self.loading = bool(url)
        self.audible = self.muted = False
        self.builtin = True  # showing one of Lite's own pages (new tab, warning)
        self.showing_block_page = False
        self.blocked = self.popups = self.links = self.fingerprints = self._links_pending = 0
        self.blocked_hosts: Counter = Counter()
        self.scan_state = 'local'
        self.scan_reason = ''
        self.scan_token = 0
        self.site_scripts: dict[str, str | None] = {}  # host -> registered script id (None while registering)
        self.fp_js = privacy.fingerprint_js(app.fp_keys[private])
        self.fp_script: str | None = None  # id of the registered fingerprinting script; None while Shields are down
        self.denied: dict[str, float] = {}  # pages Lite cancelled -> when; their request is answered locally
        self.cosmetic = None  # (token, exceptions) while generic cosmetic rules apply to the page
        self.scheduler = TaskScheduler.FromCurrentSynchronizationContext()

        self.webview.CoreWebView2InitializationCompleted += self._on_ready
        self.webview.NavigationStarting += self._on_navigation_starting
        self.webview.NavigationCompleted += self._on_navigation_completed
        self.webview.SourceChanged += self._on_source_changed
        self.webview.WebMessageReceived += self._on_web_message
        self.webview.KeyDown += self._on_key_down  # WinForms WebView2 forwards accelerator keys here
        self.webview.EnsureCoreWebView2Async(None)

    # ---- lifecycle -------------------------------------------------------
    def _on_ready(self, sender, args):
        if not args.IsSuccess:
            WinForms.MessageBox.Show(f'WebView2 failed to start:\n{args.InitializationException}', self.app.name)
            return
        core = self.webview.CoreWebView2
        s = core.Settings
        s.AreDevToolsEnabled = True
        s.AreDefaultContextMenusEnabled = True
        s.AreBrowserAcceleratorKeysEnabled = True
        s.IsStatusBarEnabled = True
        s.IsPasswordAutosaveEnabled = False
        s.IsGeneralAutofillEnabled = False
        s.UserAgent = _trim_user_agent(s.UserAgent)

        core.DocumentTitleChanged += self._on_title_changed
        core.NewWindowRequested += self._on_new_window
        core.DownloadStarting += lambda s2, a: self.app.download_starting(self, a)
        core.FaviconChanged += self._on_favicon_changed
        core.IsDocumentPlayingAudioChanged += self._on_audio_changed
        core.IsMutedChanged += self._on_audio_changed
        core.FrameNavigationStarting += self._on_frame_navigation_starting
        core.AddWebResourceRequestedFilter('*', CoreWebView2WebResourceContext.All)
        core.WebResourceRequested += self._on_resource_requested

        core.AddScriptToExecuteOnDocumentCreatedAsync(privacy.GPC_JS)

        def start(task):  # first navigation only once the start-up scripts are in place
            if self.pending_url:
                self.navigate(self.pending_url)
            else:
                self.show_new_tab()
        core.AddScriptToExecuteOnDocumentCreatedAsync(filters.COSMETIC_JS).ContinueWith(
            Action[Task[String]](lambda task: self._set_fingerprinting(True, then=lambda: start(task))),
            self.scheduler)

    def dispose(self):
        try:
            self.app.content.Controls.Remove(self.webview)
            self.webview.Dispose()
        except Exception:
            pass

    # ---- navigation ------------------------------------------------------
    def navigate(self, url: str):
        self.showing_block_page = False
        try:
            self.webview.CoreWebView2.Navigate(url)
        except Exception as e:  # malformed URL etc.
            self.show_block_page(url, f'Cannot open this address: {e}', 'block')

    def _show_builtin(self, html: str):
        self.builtin = True
        self.loading = False
        self.host = ''
        self.blocked = self.popups = self.links = self.fingerprints = 0
        self.blocked_hosts.clear()
        self.webview.CoreWebView2.NavigateToString(html)

    def show_new_tab(self):
        self.app.stats.save()
        self.url = ''
        self.showing_block_page = False
        self.scan_state = 'local'
        self.title = 'Private tab' if self.private else 'New tab'
        self.favicon = ''
        self._show_builtin(pages.new_tab_html(self.app.stats.counts, self.app.stats.since,
                                              self.app.filters_text(short=True), self.private))
        self.app.refresh_chrome(self)
        if self is self.app.current:
            self.app.focus_address()

    def show_block_page(self, url: str, reason: str, level: str, kind: str | None = None):
        self.showing_block_page = True
        self.url = url
        self.favicon = ''
        self.scan_state = 'dangerous' if level == 'block' or kind == 'danger' else 'warn'
        self.scan_reason = reason
        self._show_builtin(pages.blocked_html(url, reason, level, kind))
        self.app.refresh_chrome(self)

    def _on_navigation_starting(self, sender, args):
        url = str(args.Uri)
        self.scan_token += 1
        if url.startswith('data:text/html') or url == 'about:blank':
            return  # our own built-in pages
        app = self.app
        host = _host(url)
        token = self.scan_token

        def stop(then):
            args.Cancel = True
            self._deny(url)
            app.later(then)

        if url not in app.allowed_once:
            verdict = guard.check_navigation(url)
            if verdict.level == 'block':
                return stop(lambda: self.show_block_page(url, verdict.reason, 'block'))
            if host not in app.allowed_hosts:
                if verdict.level == 'warn':
                    return stop(lambda: self.show_block_page(url, verdict.reason, 'warn'))
                if app.blocker.is_dangerous(url):
                    app.stats.add('dangerous')
                    return stop(lambda: self.show_block_page(url, DANGEROUS, 'warn', 'danger'))
                if app.blocker.shields_up(host) and app.blocker.is_ad_page(url):
                    return stop(lambda: self.show_block_page(url, AD_PAGE.format(host=host), 'warn', 'ads'))
            if (url.startswith('http://') and app.https_only
                    and host not in guard.LOCAL_HOSTS and host not in app.http_fallback):
                app.https_attempted[host] = url
                return stop(lambda: self.navigate('https://' + url[len('http://'):]))

        up = app.blocker.shields_up(host)
        if not args.RequestHeaders.Contains('Content-Type'):  # not a form POST, so it can be re-opened
            clean = privacy.strip_tracking(url) if up else url
            if clean != url:
                self._links_pending += 1
                app.stats.add('links')
                if url in app.allowed_once:
                    app.allowed_once.add(clean)
                return stop(lambda: self.navigate(clean))
            # Start-up scripts (this site's scriptlets, fingerprinting protection on or off) must
            # be in place before the navigation begins: register them, then open the page again.
            if str(args.NavigationKind) != 'BackOrForward' and (
                    up != (self.fp_script is not None) or (up and self._needs_site_script(host))):
                args.Cancel = True
                self._deny(url)

                def reopen():
                    if token == self.scan_token:
                        self.navigate(url)
                if up:
                    self._set_fingerprinting(True, then=lambda: self._add_site_script(host, then=reopen))
                else:
                    self._set_fingerprinting(False, then=reopen)
                return
        app.allowed_once.discard(url)
        self.denied.pop(url.split('#', 1)[0], None)  # allowed now, even if it was stopped a moment ago

        self.showing_block_page = False
        self.builtin = False
        self.blocked = self.popups = self.fingerprints = 0
        self.links, self._links_pending = self._links_pending, 0
        self.blocked_hosts.clear()
        self.cosmetic = None
        self.nav_url = url
        self.nav_id = args.NavigationId
        self.host = host
        self.loading = True
        if self.private:
            self.scan_state, self.scan_reason = 'off', 'Off in private tabs'
        else:
            self.scan_state = 'scanning' if app.scanner.enabled else 'off'
            self.scan_reason = ''
        if host in self.site_scripts:
            self.site_scripts[host] = self.site_scripts.pop(host)  # most recently used
        app.refresh_chrome(self)

    def _on_navigation_completed(self, sender, args):
        if args.NavigationId != self.nav_id:
            return  # a navigation Lite cancelled, or one of its built-in pages
        self.loading = False
        if self.showing_block_page:
            self.app.refresh_chrome(self)
            return
        if not args.IsSuccess:
            original = self.app.https_attempted.pop(self.host, None)
            if original and str(args.WebErrorStatus) != 'OperationCanceled':
                # HTTPS upgrade failed; fall back to plain HTTP for this host and say so.
                self.app.http_fallback.add(self.host)
                self.scan_state = 'warn'
                self.scan_reason = 'Site does not support HTTPS - connection is not encrypted'
                self.navigate(original)
            self.app.refresh_chrome(self)
            return
        self.app.https_attempted.pop(self.host, None)
        if self.app.scanner.enabled and not self.private and self.host and self.host not in guard.LOCAL_HOSTS:
            self._start_scan()
        self.app.refresh_chrome(self)

    def _on_source_changed(self, sender, args):
        src = str(self.webview.Source) if self.webview.Source else ''
        if self.showing_block_page or src.startswith('data:') or src == 'about:blank':
            return  # built-in pages keep showing the real address
        self.url = src
        self.app.refresh_chrome(self)

    def _on_title_changed(self, sender, args):
        self.title = str(sender.DocumentTitle or '') or self.url or self.title
        self.app.refresh_chrome(self)

    def _on_favicon_changed(self, sender, args):
        core = self.webview.CoreWebView2
        if not str(core.FaviconUri or ''):
            self.favicon = ''
            self.app.refresh_chrome(self)
            return

        def done(task):
            if task.IsFaulted or task.Result is None:
                return
            buf = MemoryStream()
            task.Result.CopyTo(buf)
            data = buf.ToArray()
            self.favicon = 'data:image/png;base64,' + System.Convert.ToBase64String(data) if data.Length else ''
            self.app.refresh_chrome(self)
        core.GetFaviconAsync(CoreWebView2FaviconImageFormat.Png).ContinueWith(Action[Task[Stream]](done), self.scheduler)

    def _on_audio_changed(self, sender, args):
        core = self.webview.CoreWebView2
        self.audible, self.muted = bool(core.IsDocumentPlayingAudio), bool(core.IsMuted)
        self.app.refresh_chrome(self)

    def toggle_mute(self):
        core = self.webview.CoreWebView2
        core.IsMuted = not core.IsMuted

    def _on_new_window(self, sender, args):
        args.Handled = True
        if self.host and self.app.blocker.shields_up(self.host) and not args.IsUserInitiated:
            self.popups += 1  # opened without a click: what Chrome's pop-up blocker stops too
            self.app.stats.add('popups')
            self.app.refresh_chrome(self)
            return
        self.app.new_tab(str(args.Uri), private=self.private)

    def _deny(self, url: str):
        """Cancelling in NavigationStarting does not stop WebView2 from sending the page request,
        so Lite also answers that request itself (in _on_resource_requested): the site never sees it."""
        now = time.time()
        self.denied = {u: t for u, t in self.denied.items() if now - t < 10}
        self.denied[url.split('#', 1)[0]] = now

    # ---- site scriptlets -------------------------------------------------
    def _needs_site_script(self, host: str) -> bool:
        f = self.app.blocker.filters
        return bool(host and f) and host not in self.site_scripts and bool(f.scriptlet_script(host))

    def _add_site_script(self, host: str, then=None):
        f = self.app.blocker.filters
        script = f.scriptlet_script(host) if f else ''
        if not script or host in self.site_scripts:
            if then:
                then()
            return
        self.site_scripts[host] = None

        def done(task):
            if not task.IsFaulted:
                if host in self.site_scripts:
                    self.site_scripts[host] = task.Result
                else:  # dropped while registering (Shields were turned off)
                    self.webview.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(task.Result)
            while len(self.site_scripts) > MAX_SITE_SCRIPTS:
                old = next(h for h in self.site_scripts if h != self.host)
                old_id = self.site_scripts.pop(old)
                if old_id:
                    self.webview.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(old_id)
            if then:
                then()
        self.webview.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(script).ContinueWith(
            Action[Task[String]](done), self.scheduler)

    def _set_fingerprinting(self, on: bool, then=None):
        """Register (Shields up) or remove (Shields down) the fingerprinting script; `then` runs once done."""
        core = self.webview.CoreWebView2
        if on == (self.fp_script is not None):
            if then:
                then()
            return
        if not on:
            core.RemoveScriptToExecuteOnDocumentCreated(self.fp_script)
            self.fp_script = None
            if then:
                then()
            return

        def done(task):
            if not task.IsFaulted:
                self.fp_script = task.Result
            if then:
                then()
        core.AddScriptToExecuteOnDocumentCreatedAsync(self.fp_js).ContinueWith(Action[Task[String]](done), self.scheduler)

    def drop_site_scripts(self):
        """Forget every registered scriptlet script (Shields toggled); takes effect on the next navigation."""
        for script_id in self.site_scripts.values():
            if script_id:
                self.webview.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(script_id)
        self.site_scripts.clear()

    def _on_frame_navigation_starting(self, sender, args):
        host = _host(str(args.Uri))
        if host and self.host and self.app.blocker.shields_up(self.host) and self._needs_site_script(host):
            self._add_site_script(host)  # frames start late enough that this applies right away

    # ---- requests --------------------------------------------------------
    def _on_resource_requested(self, sender, args):
        try:
            request = args.Request
            url = str(request.Uri)
            context = str(args.ResourceContext)
            if context == 'Document' and self.denied.pop(url.split('#', 1)[0], None) is not None:
                args.Response = sender.Environment.CreateWebResourceResponse(None, 403, 'Blocked', '')
                return  # a page Lite stopped (warning page, tracking link, re-opened for scriptlets)
            if not (context == 'Document' and url.split('#', 1)[0] == self.nav_url.split('#', 1)[0]):
                blocked, stub = self.app.blocker.check(url, self.nav_url, filters.REQUEST_TYPES.get(context, 'other'))
                if blocked:
                    env = sender.Environment
                    args.Response = (_stub_response(env, stub) if stub
                                     else env.CreateWebResourceResponse(None, 403, 'Blocked', ''))
                    self.blocked += 1
                    self.blocked_hosts[_host(url)] += 1
                    self.app.stats.add('blocked')
                    self.app.refresh_shield(self)
                    return
            request.Headers.SetHeader('Sec-GPC', '1')
        except Exception:
            traceback.print_exc()

    # ---- messages from pages ---------------------------------------------
    def _on_web_message(self, sender, args):
        try:
            msg = json.loads(args.WebMessageAsJson)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get('lite') == 'fingerprint':  # a page (or a frame in it) read canvas/audio/WebGL data
            if not self.builtin and self.fp_script is not None:
                self.fingerprints += 1
                self.app.refresh_shield(self)
            return
        if 'lite' in msg:
            self._on_cosmetic_message(msg)
            return
        source = str(args.Source)
        if not self.builtin or not (source == 'about:blank' or source.startswith('data:')):
            return  # commands only come from Lite's own pages - never from a website
        cmd = msg.get('cmd')
        if cmd == 'go':
            self.navigate(to_url(str(msg.get('q', ''))))
        elif cmd == 'back':
            if self.webview.CanGoBack:
                self.webview.GoBack()
            else:
                self.show_new_tab()
        elif cmd == 'proceed' and self.showing_block_page and msg.get('url') == self.url:
            url = self.url
            self.app.allowed_once.add(url)
            self.app.allowed_hosts.add(_host(url))
            self.navigate(url)

    def _on_cosmetic_message(self, msg: dict):
        """The page's cosmetic script asks for its hide rules, then for rules matching new class names/ids."""
        f = self.app.blocker.filters
        href, token = str(msg.get('href', '')), str(msg.get('token', ''))[:32]
        if not f or not self.host or _host(href) != self.host or not self.app.blocker.shields_up(self.host):
            return
        if msg['lite'] == 'hello':
            css, generic, exceptions = f.cosmetics(href)
            self.cosmetic = (token, exceptions) if generic else None
            reply = {'lite': 'css', 'token': token, 'css': css, 'generic': generic}
        elif msg['lite'] == 'cosmetic' and self.cosmetic and self.cosmetic[0] == token:
            classes = [c for c in msg.get('classes', [])[:4000] if isinstance(c, str) and len(c) < 200]
            ids = [i for i in msg.get('ids', [])[:4000] if isinstance(i, str) and len(i) < 200]
            rules = f.generic_selectors(classes, ids, self.cosmetic[1])
            if not rules:
                return
            reply = {'lite': 'css', 'token': token, 'rules': rules}
        else:
            return
        self.webview.CoreWebView2.PostWebMessageAsJson(json.dumps(reply))

    def _on_key_down(self, sender, args):
        key, ctrl, shift, app = int(args.KeyCode), args.Control, args.Shift, self.app
        if ctrl and shift and key == ord('N'):
            app.new_tab(None, private=True)
        elif ctrl and key == ord('T'):
            app.new_tab(None)
        elif ctrl and key in (ord('W'), int(Keys.F4)):
            app.close_tab(self)
        elif (ctrl and key == ord('L')) or key == int(Keys.F6) or (args.Alt and key == ord('D')):
            app.focus_address()
        elif ctrl and key == int(Keys.Tab):
            app.cycle_tab(-1 if shift else 1)
        elif ctrl and ord('1') <= key <= ord('9'):
            app.select_nth(key - ord('0'))
        elif ctrl and key == ord('J'):
            app.show_downloads()
        else:
            return
        args.Handled = True
        args.SuppressKeyPress = True

    # ---- Fable scan ------------------------------------------------------
    def _start_scan(self):
        token = self.scan_token
        cached = self.app.scanner.cached(_origin(self.url))
        if cached:
            self._apply_scan(cached, token)
            return
        self.scan_state = 'scanning'

        def on_collected(task):
            if task.IsFaulted or token != self.scan_token:
                return
            try:
                page = json.loads(json.loads(task.Result))
            except (ValueError, TypeError):
                return
            threading.Thread(target=self._scan_worker, args=(page, token), daemon=True).start()

        self.webview.ExecuteScriptAsync(COLLECT_JS).ContinueWith(
            Action[Task[String]](on_collected), self.scheduler)

    def _scan_worker(self, page: dict, token: int):
        result = self.app.scanner.scan(page)
        self.app.later(lambda: self._apply_scan(result, token))

    def _apply_scan(self, result: dict, token: int):
        if token != self.scan_token:
            return  # user already navigated elsewhere
        self.scan_state = result.get('risk', 'unknown')
        self.scan_reason = result.get('reason', '')
        if self.scan_state == 'dangerous' and self.host not in self.app.allowed_hosts:
            self.show_block_page(self.url, 'Claude Fable: ' + self.scan_reason, 'warn', 'danger')
        self.app.refresh_chrome(self)
