"""One browser tab: a WebView2 and everything Lite checks around it.

Before a page opens (NavigationStarting): the phishing guard, the malware lists,
uBlock-style stopping of whole ad/tracking pages, HTTPS-only, tracking-link
cleaning and - the first time a site with scriptlet rules is opened in this tab -
registering that site's scriptlets. WebView2 fixes a navigation's start-up scripts
when it begins, so Lite cancels that first navigation, registers, and re-opens it.
While it loads (WebResourceRequested): every request goes through Shields and the
rest carry the Global Privacy Control header. After it loads: the optional
page check, when one is installed.
"""
import base64
import json
import threading
import time
import traceback
from collections import Counter
from urllib.parse import quote_plus, unquote, unquote_plus, urlparse

from . import autofill, default_browser, filters, guard, pages, privacy, search
from .scanner import COLLECT_JS
from .webview2 import (
    Action, CoreWebView2CreationProperties, CoreWebView2FaviconImageFormat, CoreWebView2MemoryUsageTargetLevel,
    CoreWebView2WebResourceContext,
    Drawing, Keys, MemoryStream, Stream, String, System, Task, TaskScheduler, WebView2, WinForms,
)

SEARCH_URL = 'https://search.brave.com/search?q='
MAX_SITE_SCRIPTS = 16  # scriptlet scripts kept registered per tab, least recently used dropped first
DANGEROUS = ('This site is on the malware and scam lists Lite uses (URLhaus and uBlock Origin\'s "badware '
             'risks"). It may try to install malware, steal passwords or trick you into paying.')
AD_PAGE = ('{host} is an ad or tracking server, not a normal website. Ads and tracked links often pass through '
           'servers like this before sending you on to the real page.')
SAVE_OFFER_SECONDS = 300  # how long the key button keeps offering to save a password you signed in with


def to_url(text: str, search_url: str = SEARCH_URL) -> str:
    text = text.strip()
    if not text:
        return 'about:blank'
    p = urlparse(text)
    if p.scheme in ('http', 'https', 'about', 'edge', 'file', 'javascript', 'data'):
        return text
    looks_like_host = ' ' not in text and ('.' in text or text.startswith('localhost'))
    if looks_like_host:
        return 'https://' + text
    if search_url == search.LITE_ENGINE:  # our own engine: a pseudo-URL the tab intercepts
        return search.LITE_PREFIX + quote_plus(text)
    return search_url + quote_plus(text)


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

    def __init__(self, app, url: str | None, private: bool = False, asleep: bool = False, title: str = ''):
        """asleep: a tab brought back from the last session - it waits with its title and address
        and only loads the page when you look at it."""
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
        self.pending_builtin = None  # a library/settings render waiting for the core to initialize
        self.pinned = False  # pinned tabs are exempt from sleeping
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
        self.search_query = ''  # the query this tab's Lite Search page is showing, '' when it is not
        self.site_scripts: dict[str, str | None] = {}  # host -> registered script id (None while registering)
        self.fp_js = privacy.fingerprint_js(app.fp_keys[private])
        self.fp_script: str | None = None  # id of the registered fingerprinting script; None while Shields are down
        self.denied: dict[str, float] = {}  # pages Lite cancelled -> when; their request is answered locally
        self.cosmetic = None  # (token, exceptions) while generic cosmetic rules apply to the page
        self.sleep_url: str | None = None  # set while this tab shows its "asleep" placeholder
        self.last_active = time.time()  # background idle time, for tab sleeping
        self.reader_source: str | None = None  # the real page's URL while reader view is shown
        self.library_view = ''  # which section of the bookmarks/history page is shown
        self.login_script: str | None = None  # id of the registered LOGIN_JS; None while passwords are off
        self.login_fields = ''  # 'password' / 'username' while the page shows sign-in fields
        self.login_user: tuple[str, str] | None = None  # (origin, username) typed on an earlier sign-in step
        self.login_candidate: dict | None = None  # what you just signed in with, until the sign-in form goes
        self.save_pending: dict | None = None  # the key button's offer to save it
        self.request_hosts: set[str] = set()  # sites this page loads from: their cookies stay while it is open
        self.twitch_player = False  # twitch.py wrapped this page's video player
        self.twitch_ads = 0  # Twitch ad breaks skipped on this page
        self.scheduler = TaskScheduler.FromCurrentSynchronizationContext()
        self.restore_asleep = bool(asleep and url)  # until the core is ready: then rest() or load
        if self.restore_asleep:
            self.url, self.title, self.loading = url, title or url, False

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
        if self.app.settings.get('performance_mode', False):
            core.MemoryUsageTargetLevel = CoreWebView2MemoryUsageTargetLevel.Low

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
        self.set_login_script(self.app.settings.get('password_manager', True))

        def start(task):  # first navigation only once the start-up scripts are in place
            asleep, self.restore_asleep = self.restore_asleep, False
            if asleep and self is not self.app.active:
                self.rest(self.pending_url)
            elif self.pending_url:
                self.navigate(self.pending_url)
            else:
                self.show_new_tab()
                if self.pending_builtin:  # library/settings asked for before the core existed
                    fn, self.pending_builtin = self.pending_builtin, None
                    fn()
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
        self.sleep_url = None
        self.reader_source = None
        try:
            self.webview.CoreWebView2.Navigate(url)
        except Exception as e:  # malformed URL etc.
            self.show_block_page(url, f'Cannot open this address: {e}', 'block')

    def _show_builtin(self, html: str):
        self.builtin = True
        self.loading = False
        self.host = ''
        self.search_query = ''  # this is no longer a Lite Search page (show_search_results sets it back)
        self.blocked = self.popups = self.links = self.fingerprints = 0
        self.blocked_hosts.clear()
        self.webview.CoreWebView2.NavigateToString(html)

    def show_new_tab(self):
        self.app.stats.save()
        self.url = ''
        self.showing_block_page = False
        self.sleep_url = None
        self.reader_source = None
        self.scan_state = 'local'
        self.title = 'Private tab' if self.private else 'New tab'
        self.favicon = ''
        self._show_builtin(pages.new_tab_html(self.app.stats.counts, self.app.stats.since,
                                              self.app.filters_text(short=True), self.private,
                                              banner=self.app.update_banner,
                                              update=self.app.update_state,
                                              version=self.app.version,
                                              engine=self.app.SEARCH_ENGINES.get(
                                                  self.app.settings.get('search_engine', 'brave'),
                                                  ('Brave', ''))[0]))
        self.app.refresh_chrome(self)
        if self is self.app.current:
            self.app.focus_address()

    def show_library(self, view: str = 'history'):
        """Bookmarks and history in the current tab (a new tab if this page is a real site)."""
        if self.webview.CoreWebView2 is None:  # a just-created tab: render when the core is ready
            self.pending_builtin = lambda: self.show_library(view)
            return
        self.library_view = view
        self.sleep_url = None
        self.reader_source = None
        self.title = {'bookmarks': 'Bookmarks', 'history': 'History', 'passwords': 'Passwords'}.get(view, 'History')
        self.favicon = ''
        note = self.app.import_note if view in ('bookmarks', 'passwords') else ''
        self.app.import_note = ''  # an import note is shown once
        self._show_builtin(pages.library_html(self.app.bookmarks.items(), self.app.history.recent(), view,
                                              passwords=self.app.passwords.items(), note=note))
        self.app.refresh_chrome(self)

    def show_settings(self):
        if self.webview.CoreWebView2 is None:  # a just-created tab: render when the core is ready
            self.pending_builtin = lambda: self.show_settings()
            return
        self.sleep_url = None
        self.reader_source = None
        self.title = 'Settings'
        self.favicon = ''
        s = self.app.settings
        self._show_builtin(pages.settings_html(s.get('auto_delete_cookies', True), s.get('tab_sleep', True),
                                               int(s.get('sleep_after_minutes', 30)), s.get('update_check', True),
                                               self.app.version,
                                               [(eid, name) for eid, (name, _) in self.app.SEARCH_ENGINES.items()],
                                               s.get('search_engine', 'brave'),
                                               s.get('bookmarks_bar', True),
                                               self.app.update_note,
                                               s.get('performance_mode', False),
                                               default_browser.is_default(),
                                               s.get('restore_tabs', True),
                                               s.get('password_manager', True),
                                               len(s.get('password_never', []))))
        self.app.refresh_chrome(self)

    def show_search_results(self, query: str, results: list[dict] | None):
        """Lite's own search results page; results=None shows the Searching... state."""
        self.url = ''
        self.showing_block_page = False
        self.sleep_url = None
        self.reader_source = None
        self.scan_state = 'local'
        self.title = query
        self.favicon = ''
        self._show_builtin(pages.search_results_html(query, results))
        self.search_query = query  # keep: the fetch callback checks it before rendering
        self.app.refresh_chrome(self)

    def show_reader(self, article: dict):
        self.reader_source = self.url
        self.sleep_url = None
        self.title = article.get('title') or self.title
        self._show_builtin(pages.reader_html(article))
        self.app.refresh_chrome(self)

    def toggle_reader(self):
        if self.reader_source:  # already reading: go back to the real page
            url, self.reader_source = self.reader_source, None
            self.navigate(url)
        elif self.host and self.url.startswith(('http://', 'https://')):
            self._collect_reader()

    def _collect_reader(self):
        self.webview.ExecuteScriptAsync(pages.READER_JS).ContinueWith(
            Action[Task[String]](lambda task: self._reader_collected(task)), self.scheduler)

    def _reader_collected(self, task):
        if task.IsFaulted or self.reader_source or self.builtin:
            return  # navigated away meanwhile, or reader already shown
        try:
            article = json.loads(json.loads(task.Result))
        except (ValueError, TypeError):
            return
        if isinstance(article, dict) and article.get('blocks'):
            self.show_reader(article)

    # ---- tab sleeping ------------------------------------------------------
    def sleepable(self) -> bool:
        return bool(self.sleep_url is None and not self.builtin and not self.private and not self.pinned
                    and not self.loading and not self.audible
                    and self.host and self.webview.CoreWebView2 is not None)

    def toggle_pin(self):
        """Pinned tabs keep their live renderer: they never sleep."""
        self.pinned = not self.pinned
        self.app.refresh_chrome(self)

    def sleep(self):
        """Replace the page with a placeholder, freeing the site's renderer."""
        if not self.sleepable():
            return
        self.sleep_url = self.url
        self._show_builtin(pages.sleeping_html(self.url))
        self.app.refresh_chrome(self)

    def rest(self, url: str):
        """A tab from the last session: asleep from the start, so it costs nothing until you open it."""
        self.sleep_url = self.url = url
        self._show_builtin(pages.sleeping_html(url))
        self.app.refresh_chrome(self)

    def wake(self):
        if not self.sleep_url:
            return
        url, self.sleep_url = self.sleep_url, None
        self.navigate(url)

    def session_url(self) -> str:
        """The address to bring back after a restart; '' for private tabs, warnings and Lite's own pages."""
        if self.private or self.showing_block_page:
            return ''
        url = self.url or self.pending_url or ''
        return url if url.startswith(('http://', 'https://')) else ''

    def open_host(self) -> str:
        """The site this tab keeps signed in: the page it shows, or the one it reloads when it wakes."""
        if self.private:
            return ''
        if self.host and not self.builtin:
            return self.host
        if self.sleep_url or self.reader_source or self.restore_asleep:
            return _host(self.sleep_url or self.reader_source or self.pending_url or '')
        return ''

    def cookie_hosts(self) -> set[str]:
        """Sites whose cookies this tab needs: its own, and the ones its page loads from - a sign-in
        check such as Twitch's keeps its cookie on another site, and loses its mind when it goes."""
        if self.private:
            return set()  # private tabs keep their cookies in a profile of their own
        own = self.open_host()
        return ({own} | self.request_hosts) if own else set()

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

        if url.startswith(search.LITE_PREFIX):  # our own search engine, rendered locally
            query = unquote_plus(url[len(search.LITE_PREFIX):]).strip()
            return stop(lambda: app.search_results(self, query))

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
        self.login_fields = ''  # the new page reports its own sign-in fields

        self.showing_block_page = False
        self.builtin = False
        self.blocked = self.popups = self.fingerprints = self.twitch_ads = 0
        self.twitch_player = False
        self.links, self._links_pending = self._links_pending, 0
        self.blocked_hosts.clear()
        self.request_hosts = set()
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
        if not self.private and self.host and not self.app.blocker.is_ad_page(self.url):
            self.app.history.record(self.url, self.title)
        if self.host and not self.private:  # sites come back at the zoom you left them at
            z = self.app.zoom_for(self.host)
            if abs(z - 1.0) > 0.001 and abs(self.webview.ZoomFactor - z) > 0.001:
                self.webview.ZoomFactor = z
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
        if self.sleep_url:
            return  # asleep: the tab keeps the title of its page, not "Tab asleep"
        self.title = str(sender.DocumentTitle or '') or self.url or self.title
        if not self.private and not self.builtin and self.host:
            self.app.history.retitle(self.url, self.title)
            self.app.bookmarks.retitle(self.url, self.title)
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

    def set_login_script(self, on: bool):
        """Passwords on: pages get LOGIN_JS from their next load. Off: it goes, and so does what Lite saw."""
        core = self.webview.CoreWebView2
        if core is None:
            return  # _on_ready registers it
        if not on:
            self.login_fields, self.login_user, self.login_candidate, self.save_pending = '', None, None, None
            if self.login_script:
                core.RemoveScriptToExecuteOnDocumentCreated(self.login_script)
            self.login_script = None
            return
        if self.login_script is not None:
            return  # registered, or registering ('')
        self.login_script = ''

        def done(task):
            if task.IsFaulted:
                self.login_script = None if self.login_script == '' else self.login_script
            elif self.login_script == '':
                self.login_script = task.Result
            else:  # switched off (or on again) while this one was registering
                core.RemoveScriptToExecuteOnDocumentCreated(task.Result)
        core.AddScriptToExecuteOnDocumentCreatedAsync(autofill.LOGIN_JS).ContinueWith(
            Action[Task[String]](done), self.scheduler)

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
            if len(self.request_hosts) < 500:
                self.request_hosts.add(_host(url))
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
        if msg.get('lite') in ('loginform', 'login', 'username'):
            self._on_login_message(msg, str(args.Source))
            return
        if msg.get('lite') == 'twitchad':  # twitch.py: the player's worker is wrapped / an ad break was skipped
            if msg.get('worker'):
                self.twitch_player = True
            elif msg.get('debug'):
                self.twitch_log = (getattr(self, 'twitch_log', []) + [str(msg['debug'])])[-50:]
            elif self.host and self.app.blocker.shields_up(self.host):
                self.blocked += 1
                self.twitch_ads += 1
                self.blocked_hosts['twitch.tv video ad'] += 1
                self.app.stats.add('blocked')
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
            self.navigate(to_url(str(msg.get('q', '')), self.app.search_url()))
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
        elif cmd == 'wake':
            self.wake()
        elif cmd == 'leave' and self.reader_source:
            url, self.reader_source = self.reader_source, None
            self.navigate(url)
        elif cmd == 'library':
            self.show_library(str(msg.get('v', 'history')))
        elif cmd == 'unstar':
            self.app.bookmarks.remove(str(msg.get('url', '')))
            self.show_library('bookmarks')
        elif cmd == 'importbookmarks':
            self.app.import_bookmarks()
        elif cmd == 'importpasswords':
            self.app.import_passwords()
        elif cmd in ('copyuser', 'copypass'):
            self.app.copy_login(str(msg.get('url', '')), str(msg.get('user', '')), cmd == 'copypass')
        elif cmd == 'delpass':
            self.app.passwords.remove(str(msg.get('url', '')), str(msg.get('user', '')))
            self.show_library('passwords')
        elif cmd == 'delhist':
            self.app.history.delete(str(msg.get('url', '')))
            self.show_library('history')
        elif cmd == 'clearhist':
            self.app.history.clear()
            self.show_library('history')
        elif cmd == 'settings':
            self.app.set_setting(str(msg.get('key', '')), msg.get('value'))
        elif cmd == 'clearcookies':
            self.app.clear_cookies_now()
        elif cmd == 'updates':
            self.app.update_now()  # over the air: check, download, restart - never a releases page
        elif cmd == 'setdefault':
            self.app.set_default_browser()
        elif cmd == 'update':
            self.app.update_now()

    def _on_login_message(self, msg: dict, source: str):
        """LOGIN_JS: the page's sign-in fields, and what you signed in with."""
        if self.builtin or self.login_script is None or not self.app.settings.get('password_manager', True):
            return
        page = autofill.origin(source)
        if not page or page != autofill.origin(self.url):
            return  # only the page this tab shows: never a frame, never a page that has moved on
        kind = msg['lite']
        if kind == 'loginform':
            fields = msg.get('fields')
            self.login_fields = fields if fields in ('password', 'username') else ''
            if self.login_fields != 'password':
                # The sign-in form is gone - the page moved on, or a new one loaded: you are signed in.
                # A failed sign-in shows the form again, so a mistyped password is not offered.
                self._offer_save()
            self.app.refresh_chrome(self)
        elif kind == 'username':
            value = str(msg.get('value', '')).strip()[:200]
            if value:
                self.login_user = (page, value)
        elif kind == 'login' and not self.private:  # private tabs keep nothing, passwords included
            pw, user = msg.get('pw'), str(msg.get('user', '')).strip()[:200]
            if not isinstance(pw, str) or not pw or len(pw) > 1000:
                return
            if not user and self.login_user and self.login_user[0] == page:
                user = self.login_user[1]  # the username step came first
            self.login_candidate = {'origin': page, 'user': user, 'pw': pw, 'at': time.time()}

    def _offer_save(self):
        login, self.login_candidate = self.login_candidate, None
        if login and time.time() - login['at'] < SAVE_OFFER_SECONDS:
            self.app.offer_save(self, login)

    def save_offer(self) -> dict | None:
        """The password the key button offers to save, while that offer lasts."""
        if self.save_pending and time.time() - self.save_pending['at'] > SAVE_OFFER_SECONDS:
            self.save_pending = None  # forget it: an unsaved password does not stay in memory
        return self.save_pending

    def expire_logins(self):
        if self.login_candidate and time.time() - self.login_candidate['at'] > SAVE_OFFER_SECONDS:
            self.login_candidate = None
        self.save_offer()

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
        elif ctrl and shift and key == ord('T'):
            app.reopen_closed()
        elif ctrl and key == ord('T'):
            app.new_tab(None)
        elif ctrl and key in (ord('W'), int(Keys.F4)):
            app.close_tab(self)
        elif (ctrl and key == ord('L')) or key == int(Keys.F6) or (args.Alt and key == ord('D')):
            app.focus_address()
        elif ctrl and key == int(Keys.Oemcomma):
            app.show_settings()
        elif ctrl and key in (ord('+'), int(Keys.Add), int(Keys.Oemplus)):
            app.zoom(1)
        elif ctrl and key in (ord('-'), int(Keys.Subtract), int(Keys.OemMinus)):
            app.zoom(-1)
        elif ctrl and key == ord('0'):
            app.zoom(0)
        elif ctrl and shift and key == ord('P'):
            app.toggle_pin(self)
        elif ctrl and key == int(Keys.Tab):
            app.cycle_tab(-1 if shift else 1)
        elif ctrl and ord('1') <= key <= ord('9'):
            app.select_nth(key - ord('0'))
        elif ctrl and key == ord('J'):
            app.show_downloads()
        elif ctrl and key == ord('F'):
            self.open_find()
        elif ctrl and key == ord('D'):
            app.toggle_bookmark(self)
        elif ctrl and key == ord('H'):
            app.show_library()
        else:
            return
        args.Handled = True
        args.SuppressKeyPress = True

    # ---- find in page ------------------------------------------------------
    def open_find(self):
        if self.webview.CoreWebView2 is None:
            return
        self.webview.ExecuteScriptAsync(pages.FIND_JS)

    # ---- Page check ------------------------------------------------------
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
            self.show_block_page(self.url, 'Page check: ' + self.scan_reason, 'warn', 'danger')
        self.app.refresh_chrome(self)
