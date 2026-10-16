"""Scratch driver for Lite: open a URL, run timed steps, report what Shields saw.

    python scratch/drive.py [URL] [--wait S] [--fresh] [--step T:ACTION[:ARG] ...]

ACTION is one of:
    click:TEXT    click the button or link whose text is exactly TEXT (e.g. a consent wall)
    toggle        flip Shields for the current site (same as the switch in the Shields panel)
    panel:NAME    open a pop-up panel: shields, menu or downloads
    hide          close the pop-up panel
    shot:PATH     screenshot of Lite's window plus its open panel (PrintWindow on their own HWNDs)
    pageshot:PATH the current page as Chromium renders it (DevTools capture; shows hardware video)
    report        print a REPORT json line
    newtab        open a new tab;  private  opens a new private tab
    nav:URL       navigate the current tab (goes through the guard like typed URLs)
    js:CODE       run JavaScript in the current tab and print the result (js:@file.js reads a file)
    tab:N         switch to tab N (1-based)
    tapel:SEL     a real mouse click on the element matching CSS selector SEL (counts as a user gesture)
    uiclick:ID    a real mouse click on the toolbar element with that id (shields, menu, downloads...)
    confirm:yes   answer the risky-download prompt (yes = download anyway, no = don't)
    mem           print the private memory of Lite's processes, by process type

Runs the real browser code on an isolated profile under %TEMP%, so the user's own
Lite profile is left alone. Every network request of the first tab is recorded
independently of the app's counter so the two can be cross-checked.
"""
import argparse
import base64
import ctypes
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from browser import app as lite  # noqa: E402
from browser import filters as fl  # noqa: E402
from browser.blocker import site_label  # noqa: E402
from browser.webview2 import (  # noqa: E402
    Action, ApartmentState, Drawing, String, Task, Thread, ThreadStart, WinForms,
)
from System.Drawing.Imaging import ImageFormat  # noqa: E402

NAVLOG = '--navlog' in sys.argv  # print every top-level navigation and whether Lite let it through
MSGLOG = '--msglog' in sys.argv  # print every message pages send to Lite
user32 = ctypes.windll.user32
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
PW_RENDERFULLCONTENT = 2  # includes the WebView2 (DirectComposition) content

CLICK_JS = r"""
(() => {
  const want = %s;
  const el = [...document.querySelectorAll('button, a, [role=button]')]
    .find(e => (e.innerText || e.textContent || '').trim() === want);
  if (!el) return 'not found';
  el.click();
  return 'clicked';
})()
"""


def capture(form):
    bmp = Drawing.Bitmap(form.Width, form.Height)
    g = Drawing.Graphics.FromImage(bmp)
    hdc = g.GetHdc()
    ok = user32.PrintWindow(form.Handle.ToInt64(), hdc.ToInt64(), PW_RENDERFULLCONTENT)
    g.ReleaseHdc(hdc)
    g.Dispose()
    return bmp, bool(ok)


def screenshot(app, path: str) -> bool:
    """Lite's window, with the pop-up panel drawn where it is on screen - never another window."""
    bmp, ok = capture(app.form)
    if app.popup.visible:
        pop, ok2 = capture(app.popup.form)
        g = Drawing.Graphics.FromImage(bmp)
        g.DrawImage(pop, app.popup.form.Left - app.form.Left, app.popup.form.Top - app.form.Top)
        g.Dispose()
        pop.Dispose()
        ok = ok and ok2
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bmp.Save(path, ImageFormat.Png)
    bmp.Dispose()
    return ok


def domain(host: str) -> str:
    parts = host.split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


class Recorder:
    """Second WebResourceRequested listener on the first tab; runs after the app's handler."""

    def __init__(self, app):
        self.app = app
        self.tab = app.tabs[0]
        self.requests: list[tuple[str, bool, bool]] = []  # (host, app blocked it, a filter list matches it)
        self.tab.webview.CoreWebView2InitializationCompleted += self._ready
        self.tab.webview.NavigationStarting += self._reset

    def _ready(self, sender, args):
        if args.IsSuccess:
            sender.CoreWebView2.WebResourceRequested += self._on_request
            if MSGLOG:
                sender.CoreWebView2.WebMessageReceived += lambda s, a: print(
                    f'MSG from {str(a.Source)[:60]}: {str(a.WebMessageAsJson)[:200]}', flush=True)

    def _reset(self, sender, args):
        url = str(args.Uri)
        if NAVLOG:
            print(f'NAV {"cancelled" if args.Cancel else "allowed  "} kind={args.NavigationKind} {url[:100]}', flush=True)
        if not args.Cancel and not url.startswith('data:') and url != 'about:blank':
            self.requests.clear()  # same rule as Tab._on_navigation_starting

    def _on_request(self, sender, args):
        url = str(args.Request.Uri)
        host = (urlparse(url).hostname or '').lower()
        f = self.app.blocker.filters
        kind = fl.REQUEST_TYPES.get(str(args.ResourceContext), 'other')
        listed = f.check(url, self.tab.nav_url or url, kind).matched if f else self.app.blocker.is_blocked(host, self.tab.host)
        self.requests.append((host, args.Response is not None, listed))


def build_report(app, rec: Recorder) -> dict:
    tab = rec.tab
    page = tab.host
    state = app._ui_state()
    blocked = [h for h, b, _ in rec.requests if b]
    listed_allowed = [h for h, b, listed in rec.requests if listed and not b]
    unlisted_3p = [h for h, b, listed in rec.requests
                   if not listed and h and page and site_label(h) != site_label(page)]
    return {
        'url': tab.url,
        'host': page,
        'toolbar_shields': state['shields'],
        'filters': app.filters_text(),
        'tab_blocked_counter': tab.blocked,
        'popups_blocked': tab.popups,
        'links_cleaned': tab.links,
        'site_scripts': sorted(tab.site_scripts),
        'tabs': [[t.title, t.url] for t in app.tabs],
        'downloads': [[d['name'], d['state'], d['status']] for d in app.downloads],
        'requests_seen': len(rec.requests),
        'blocked_by_app': len(blocked),
        'listed_requests': sum(1 for *_, listed in rec.requests if listed),
        'blocked_by_domain': Counter(domain(h) for h in blocked).most_common(25),
        'listed_but_loaded_by_domain': Counter(domain(h) for h in listed_allowed).most_common(25),
        'unlisted_third_party_by_domain': Counter(domain(h) for h in unlisted_3p).most_common(15),
    }


def parse_step(spec: str) -> tuple[float, str, str]:
    t, _, rest = spec.partition(':')
    action, _, arg = rest.partition(':')
    return float(t), action, arg


def drive(app, opts):
    rec = Recorder(app) if app.tabs else None
    if opts.hidelog:  # who closes the pop-up panel?
        original_hide = app.popup.hide

        def logged_hide():
            if app.popup.visible:
                print('POPUP HIDE from:', ' <- '.join(f'{f.name}:{f.lineno}' for f in traceback.extract_stack()[-5:-1]), flush=True)
            original_hide()
        app.popup.hide = logged_hide
        app.popup.form.Deactivate += lambda s, e: print('POPUP Deactivate event', flush=True)
    steps = sorted((parse_step(s) for s in opts.step), key=lambda s: s[0])
    t0 = time.time()
    last = ['']
    timer = WinForms.Timer()
    timer.Interval = 500

    def log(msg):
        print(f'[{time.time() - t0:5.1f}s] {msg}', flush=True)

    def run_js(code, label):
        tab = app.current

        def done(task):
            log(f'{label}: {"faulted" if task.IsFaulted else task.Result}')
        tab.webview.ExecuteScriptAsync(code).ContinueWith(Action[Task[String]](done), tab.scheduler)

    def run_step(action, arg):
        tab = app.current
        if action == 'click':
            run_js(CLICK_JS % json.dumps(arg), f'click {arg!r}')
        elif action == 'toggle':
            log(f'toggle Shields (were {"up" if app.blocker.shields_up(tab.host) else "down"} for {tab.host})')
            app.toggle_shields()
        elif action == 'panel':
            {'shields': app.show_shields, 'menu': app.show_menu,
             'downloads': lambda: app.show_downloads(force=True)}[arg]()
        elif action == 'app':  # call an App method, or one on the current tab (open_find...); tab passed if required
            fn = getattr(app, arg, None) or getattr(tab, arg)
            required = [p for p in inspect.signature(fn).parameters.values() if p.default is inspect.Parameter.empty]
            fn(tab) if required else fn()
        elif action == 'hide':
            app.popup.hide()
        elif action == 'shot':
            log(f'shot {arg}: ' + ('ok' if screenshot(app, arg) else 'PrintWindow failed'))
        elif action == 'pageshot':  # the page as Chromium's compositor draws it (includes hardware video)
            def saved(task, path=arg):
                if task.IsFaulted:
                    log(f'pageshot {path}: failed')
                    return
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(base64.b64decode(json.loads(str(task.Result))['data']))
                log(f'pageshot {path}: ok')
            tab.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
                'Page.captureScreenshot', '{"format": "png"}').ContinueWith(Action[Task[String]](saved), tab.scheduler)
        elif action == 'report':
            print('REPORT ' + json.dumps(build_report(app, rec)), flush=True)
        elif action == 'newtab':
            app.new_tab(None)
        elif action == 'private':
            app.new_tab(None, private=True)
        elif action == 'nav':  # same as typing in the address bar + Enter
            tab.navigate(lite.to_url(arg))
            tab.webview.Focus()
        elif action == 'js':  # js:CODE, or js:@file.js
            if arg.startswith('@'):
                with open(arg[1:], encoding='utf-8') as f:
                    arg = f.read()
            run_js(arg, 'js')
        elif action == 'tab':
            app.select_nth(int(arg))
        elif action == 'tapel':  # a real (trusted) mouse click on the element, via DevTools input events
            def tap(task, core=tab.webview.CoreWebView2):
                x, y = json.loads(json.loads(str(task.Result)))
                for kind in ('mousePressed', 'mouseReleased'):
                    core.CallDevToolsProtocolMethodAsync('Input.dispatchMouseEvent', json.dumps(
                        {'type': kind, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1}))
                log(f'tapped {arg} at {x},{y}')
            code = (f'(() => {{ const r = document.querySelector({json.dumps(arg)}).getBoundingClientRect();'
                    ' return JSON.stringify([r.left + r.width / 2, r.top + r.height / 2]); })()')
            tab.webview.ExecuteScriptAsync(code).ContinueWith(Action[Task[String]](tap), tab.scheduler)
        elif action == 'uiclick':  # a real click on a toolbar element (by id) in chrome.html
            def tap_ui(task, core=app.chrome.CoreWebView2):
                x, y = json.loads(json.loads(str(task.Result)))
                for kind in ('mousePressed', 'mouseReleased'):
                    core.CallDevToolsProtocolMethodAsync('Input.dispatchMouseEvent', json.dumps(
                        {'type': kind, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1}))
                log(f'clicked toolbar #{arg} at {x:.0f},{y:.0f}')
            code = (f'(() => {{ const r = document.getElementById({json.dumps(arg)}).getBoundingClientRect();'
                    ' return JSON.stringify([r.left + r.width / 2, r.top + r.height / 2]); })()')
            app.chrome.CoreWebView2.ExecuteScriptAsync(code).ContinueWith(Action[Task[String]](tap_ui), tab.scheduler)
        elif action == 'mem':  # private memory of Lite's own processes (this python + its WebView2 processes)
            ps = ("$p = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq %d -or ($_.Name -eq 'msedgewebview2.exe' "
                  "-and $_.CommandLine -like '*LiteBrowser-scratch*') }; $p | ForEach-Object { $t = if ($_.CommandLine -match "
                  "'--type=([\\w-]+)') { $matches[1] } elseif ($_.Name -eq 'msedgewebview2.exe') { 'browser' } else { 'lite (python)' };"
                  " '{0}|{1}' -f $t, $_.PrivatePageCount }") % os.getpid()
            out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True).stdout
            groups = Counter()
            counts = Counter()
            for line in out.splitlines():
                kind, _, size = line.partition('|')
                if size.strip().isdigit():
                    groups[kind] += int(size) / 1e6
                    counts[kind] += 1
            log('memory (private MB): ' + ', '.join(f'{k} x{counts[k]}: {v:.0f}' for k, v in groups.most_common())
                + f' | total {sum(groups.values()):.0f} MB')
        elif action == 'confirm':  # answer the "this file can run programs" prompt
            app._finish_confirm(arg == 'yes')
        elif action == 'dlstate':  # WebView2's own view of each download
            log(f'popup visible={app.popup.visible} view={app.popup.view} ready={app.popup.ready} '
                f'webview2 download box open={tab.webview.CoreWebView2.IsDefaultDownloadDialogOpen}')
            for d in app.downloads:
                op = d.get('op')
                log(f"download {d['name']}: lite={d['state']} webview2=" + (
                    f'{op.State} reason={op.InterruptReason} bytes={op.BytesReceived}/{op.TotalBytesToReceive}'
                    if op is not None else '-'))
        else:
            log(f'unknown step {action!r}')

    def tick(sender, e):
        try:
            tab = app.current
            state = (f'counter={tab.blocked:<4} filters={"yes" if app.blocker.filters else "no "} '
                     f'scripts={len(tab.site_scripts)} panel={app.popup.view or "-"} url={tab.url[:70]}') if tab else 'no tab'
            if state != last[0]:
                log(state)
                last[0] = state
            while steps and steps[0][0] <= time.time() - t0:
                _, action, arg = steps.pop(0)
                run_step(action, arg)
            if time.time() - t0 >= opts.wait:
                timer.Stop()
                if rec:
                    print('REPORT ' + json.dumps(build_report(app, rec)), flush=True)
                app.form.Close()
        except Exception:
            traceback.print_exc()
            timer.Stop()
            app.form.Close()

    timer.Tick += tick
    timer.Start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url', nargs='?')
    ap.add_argument('--wait', type=float, default=25)
    ap.add_argument('--fresh', action='store_true', help='forget Shields choices and stats from earlier runs')
    ap.add_argument('--step', action='append', default=[])
    ap.add_argument('--navlog', action='store_true', help='print every top-level navigation of the first tab')
    ap.add_argument('--msglog', action='store_true', help='print every message the first tab sends to Lite')
    ap.add_argument('--map-host', help='resolve this hostname to 127.0.0.1 (test sites under a normal name)')
    ap.add_argument('--hidelog', action='store_true', help='print what closes the pop-up panel')
    opts = ap.parse_args()
    if opts.map_host:  # WebView2 reads this for every browser it starts in this process
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = f'--host-resolver-rules="MAP {opts.map_host} 127.0.0.1"'

    lite.DATA_DIR = os.path.join(tempfile.gettempdir(), 'LiteBrowser-scratch')
    if opts.fresh:
        for name in ('shields.json', 'stats.json'):
            path = os.path.join(lite.DATA_DIR, name)
            if os.path.exists(path):
                os.remove(path)

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    WinForms.Application.EnableVisualStyles()

    def ui_main():
        try:
            app = lite.App([opts.url] if opts.url else [])
            drive(app, opts)
            app.run()
        except Exception:
            traceback.print_exc()

    thread = Thread(ThreadStart(ui_main))
    thread.SetApartmentState(ApartmentState.STA)
    thread.Start()
    while thread.IsAlive:
        thread.Join(200)
    shields = os.path.join(lite.DATA_DIR, 'shields.json')
    if os.path.exists(shields):
        with open(shields, encoding='utf-8') as f:
            print('SHIELDS_FILE ' + f.read().replace('\n', ''), flush=True)


if __name__ == '__main__':
    main()
