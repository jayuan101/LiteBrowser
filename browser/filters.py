"""Filter lists and the blocking engine - the same approach Brave uses.

Brave blocks ads with its adblock-rust engine and the community filter lists.
Lite runs that engine too (the `adblock` package: adblock-rust compiled for
Python, as used by qutebrowser) with the nine lists uBlock Origin turns on by
default. The lists hold three kinds of rules:

  network    ||ads.example^             the request is never sent, or gets a harmless stub
  cosmetic   example.com##.ad-slot      the leftover element is hidden with CSS
  scriptlet  example.com##+js(set, ...) a few lines of script disarm ad or anti-adblock
                                        code before the page's own scripts run - this is
                                        what removes YouTube's video ads

The engine handles network and cosmetic rules. Scriptlet rules are pulled out
here and run with AdGuard's open-source scriptlet library, which implements
uBlock Origin's scriptlets under the same names. That code runs inside every
page, so it is pinned by SHA-256.

Everything lives in %LOCALAPPDATA%\\LiteBrowser\\filters. Lists are re-downloaded
when they expire and the compiled engine reloads in about 0.1 s. Until the very
first download has finished, blocker.BUILTIN keeps blocking the big ad networks.
"""
import base64
import hashlib
import json
import os
import re
import time
import urllib.request
from typing import NamedTuple

try:
    import adblock  # pip install adblock
except ImportError:
    adblock = None


class FilterList(NamedTuple):
    name: str
    urls: tuple[str, ...]  # mirrors, tried in order
    max_age_hours: int
    trusted: bool = False  # uBlock's own lists may use trusted-* scriptlets, which rewrite page code
    safety: bool = False  # malware and scam lists: also checked before a whole page is opened
    hosts_format: bool = False
    safety_only: bool = False  # big network-only list: kept in the malware engine alone, not twice in memory


UBO = ('https://ublockorigin.github.io/uAssets/filters/%s.min.txt', 'https://ublockorigin.pages.dev/filters/%s.min.txt')

LISTS = [
    FilterList('easylist', ('https://easylist.to/easylist/easylist.txt',
                            'https://ublockorigin.github.io/uAssets/thirdparties/easylist.txt'), 96),
    FilterList('easyprivacy', ('https://easylist.to/easylist/easyprivacy.txt',
                               'https://ublockorigin.github.io/uAssets/thirdparties/easyprivacy.txt'), 96),
    FilterList('peter-lowe', ('https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/thirdparties/'
                              'pgl.yoyo.org/as/serverlist',
                              'https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=1'
                              '&mimetype=plaintext'), 96, hosts_format=True),
    *(FilterList(f'ubo-{name}', tuple(u % name for u in UBO), hours, trusted=True, safety=name == 'badware')
      for name, hours in (('filters', 96), ('privacy', 96), ('unbreak', 96), ('quick-fixes', 12), ('badware', 96))),
    FilterList('urlhaus', ('https://malware-filter.gitlab.io/urlhaus-filter/urlhaus-filter-ag-online.txt',
                           'https://curbengh.github.io/malware-filter/urlhaus-filter-ag-online.txt'), 24, safety=True,
               safety_only=True),
]

ADGUARD_VERSION = '2.5.1'
ADGUARD_URL = f'https://cdn.jsdelivr.net/npm/@adguard/scriptlets@{ADGUARD_VERSION}/dist/'
ADGUARD_FILES = {  # pinned: scriptlets run inside every page, redirects answer blocked requests
    'scriptlets.corelibs.json': '5c76ecdb58ecaf975df54aa447e2cc5db762330eab3d334e271829c6b95c0184',
    'redirects.json': '6c330edf25e288a93946dd4a2b21357d63edb460efd323c66002eede6732bcd9',
}

CACHE_VERSION = 3  # bump when preprocess() or the cache layout changes so cached engines get rebuilt

# Lite's own redirect-rules: when a video-ad SDK is blocked anyway, answer with AdGuard's
# stand-in that reports "no ads", so the video player still starts the actual video.
LITE_REDIRECTS = [
    '||imasdk.googleapis.com/js/sdkloader/ima3.js$script,redirect=google-ima.js',
    '||imasdk.googleapis.com/js/sdkloader/ima3_dai.js$script,redirect=google-ima3-dai',
    '||fwmrm.net/libs/adm/*/AdManager.js$script,redirect=freewheel-admanager',
    '||securepubads.g.doubleclick.net/tag/js/gpt.js$script,redirect=googletagservices_gpt.js',
    '||www.googletagservices.com/tag/js/gpt.js$script,redirect=googletagservices_gpt.js',
    '||pagead2.googlesyndication.com/pagead/js/adsbygoogle.js$script,redirect=googlesyndication_adsbygoogle.js',
]
MAX_DOWNLOAD = 20_000_000
MIN_RULES = 100  # a "list" with fewer rules is an error page, keep the old copy

# uBlock Origin's `!#if` tokens that are true for Lite (WebView2 is Chromium). All others are false.
ENV = {'env_chromium', 'ext_ublock', 'cap_user_stylesheet'}

# Network options adblock-rust 0.5 drops, rewritten to ones it understands with the same uBO meaning.
OPTION_REWRITES = {'all': '', 'strict3p': '3p', 'strict1p': '1p', 'mp4': 'media,redirect=noopmp4-1s',
                   'empty': 'redirect=nooptext'}
COSMETIC_EXCEPTIONS = {'ghide', 'generichide', 'ehide', 'elemhide', 'shide', 'specifichide'}

# uBO scriptlet names that AdGuard's library files under another name.
UBO_NAMES = {
    'trusted-rpfr': 'trusted-replace-fetch-response', 'trusted-rpxr': 'trusted-replace-xhr-response',
    'trusted-set': 'trusted-set-constant', 'rpnt': 'trusted-replace-node-text',
    'replace-node-text': 'trusted-replace-node-text', 'trusted-rpnt': 'trusted-replace-node-text',
    'nobab': 'prevent-bab',
}
REPLACE_NODE_TEXT = {'rpnt', 'replace-node-text', 'trusted-rpnt', 'trusted-replace-node-text'}

# CoreWebView2WebResourceContext name -> the request type filter lists use.
REQUEST_TYPES = {
    'Document': 'sub_frame', 'Stylesheet': 'stylesheet', 'Image': 'image', 'Media': 'media', 'Font': 'font',
    'Script': 'script', 'XmlHttpRequest': 'xmlhttprequest', 'Fetch': 'xmlhttprequest', 'Websocket': 'websocket',
    'Ping': 'ping', 'CspViolationReport': 'csp_report',
}

# Registered once per tab, runs in the top frame of every page: asks Lite for the page's hide
# rules, then keeps reporting new class names and ids so generic rules (##.ad-banner) can be
# matched against what is really on the page. The CSS goes in constructed style sheets, which
# work before <html> exists and are not subject to the page's Content-Security-Policy.
COSMETIC_JS = r"""
(() => {
  const webview = window.chrome && window.chrome.webview;
  if (window.top !== window || !webview) return;
  const post = webview.postMessage.bind(webview), later = window.setTimeout.bind(window);
  const token = Math.random().toString(36).slice(2);
  const site = new CSSStyleSheet(), generic = new CSSStyleSheet();
  const adopt = () => {
    const sheets = document.adoptedStyleSheets;
    if (!sheets.includes(site)) document.adoptedStyleSheets = [...sheets, site, generic];
  };
  const seen = new Set();
  let collect = false, classes = [], ids = [], timer = 0;
  const flush = () => {
    timer = 0;
    if (classes.length || ids.length) post({lite: 'cosmetic', token, href: location.href, classes, ids});
    classes = [];
    ids = [];
  };
  const note = (el) => {
    if (el.id && !seen.has('#' + el.id)) { seen.add('#' + el.id); ids.push(el.id); }
    if (el.classList) for (const c of el.classList) if (!seen.has('.' + c)) { seen.add('.' + c); classes.push(c); }
  };
  const scan = (node) => {
    if (node.nodeType !== 1) return;
    note(node);
    for (const el of node.querySelectorAll('[id],[class]')) note(el);
  };
  const soon = () => { if (!timer && (classes.length || ids.length)) timer = later(flush, 50); };
  new MutationObserver((records) => {
    adopt();
    if (!collect) return;
    for (const r of records) {
      if (r.type === 'attributes') note(r.target);
      else for (const n of r.addedNodes) scan(n);
    }
    soon();
  }).observe(document, {childList: true, subtree: true, attributes: true, attributeFilter: ['id', 'class']});
  webview.addEventListener('message', (e) => {
    const d = e.data;
    if (!d || d.lite !== 'css' || d.token !== token) return;
    if (d.css) site.replaceSync(d.css);
    for (const s of d.rules || []) {
      try { generic.insertRule(s + '{display:none!important}', generic.cssRules.length); } catch (err) {}
    }
    adopt();
    if (d.generic && !collect) {
      collect = true;
      if (document.documentElement) scan(document.documentElement);
      soon();
    }
  });
  adopt();
  post({lite: 'hello', token, href: location.href});
})();
"""


# ---- list preprocessing -----------------------------------------------------
def condition(expr: str) -> bool:
    """Evaluate a uBO `!#if` expression such as 'env_chromium && !env_mobile'."""
    tokens = re.findall(r'\w+|&&|\|\||[!()]', expr)
    pos = 0

    def atom() -> bool:
        nonlocal pos
        tok = tokens[pos] if pos < len(tokens) else ''
        pos += 1
        if tok == '!':
            return not atom()
        if tok == '(':
            value = either()
            pos += 1  # the closing ')'
            return value
        return tok in ENV

    def both() -> bool:
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos] == '&&':
            pos += 1
            value = atom() and value  # atom() first, so its tokens are always consumed
        return value

    def either() -> bool:
        nonlocal pos
        value = both()
        while pos < len(tokens) and tokens[pos] == '||':
            pos += 1
            value = both() or value
        return value

    return either()


def _needs_trust(name: str) -> bool:
    """Scriptlets that can rewrite page code; uBO only accepts them from its own lists."""
    return name.startswith('trusted-') or name in ('rpnt', 'replace-node-text')


def _rewrite_options(line: str) -> str:
    head, sep, opts = line.rpartition('$')
    if not sep or not head:
        return line
    parts = opts.split(',')
    if not any(p in OPTION_REWRITES or p.startswith('from=') for p in parts):
        return line
    out = []
    for p in parts:
        if p.startswith('from='):
            out.append('domain=' + p[5:])
        elif p in OPTION_REWRITES:
            if OPTION_REWRITES[p]:
                out.append(OPTION_REWRITES[p])
        else:
            out.append(p)
    return head + ('$' + ','.join(out) if out else '')


def _scoped_exception(line: str) -> list[str] | None:
    """'@@*$ghide,domain=a.com|b.com' -> ['@@||a.com^$ghide', '@@||b.com^$ghide'].

    adblock-rust 0.5 ignores domain= on these and would switch cosmetic filtering off
    for every site; one hostname-anchored exception per domain means the same thing."""
    if not line.startswith('@@'):
        return None
    head, sep, opts = line[2:].rpartition('$')
    parts = opts.split(',')
    if not sep or head not in ('', '*') or not COSMETIC_EXCEPTIONS.intersection(parts):
        return None
    domains = [p[7:] for p in parts if p.startswith('domain=')]
    rest = ','.join(p for p in parts if not p.startswith('domain='))
    if not domains:
        return None
    return [f'@@||{d}^${rest}' for d in domains[0].split('|') if d and d[0] != '~' and not d.endswith('.*')]


def preprocess(text: str, trusted: bool) -> tuple[list[str], list[str], list[str]]:
    """Split one list into rules for the engine, the scriptlet (##+js) rules Lite runs itself,
    and uBO redirect-rules ("if this is blocked anyway, answer with that stub")."""
    rules, scriptlets, redirects = [], [], []
    active: list[bool] = []  # one entry per open !#if: is that branch on
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('!#if '):
            active.append(condition(line[5:]))
        elif line.startswith('!#else'):
            if active:
                active[-1] = not active[-1]
        elif line.startswith('!#endif'):
            if active:
                active.pop()
        elif not line or line[0] == '!' or not all(active):
            continue
        elif '#+js(' in line:
            name = line.split('#+js(', 1)[1].split(',', 1)[0].strip(' )')
            if trusted or not _needs_trust(name):
                scriptlets.append(line)
        elif any(s in line for s in ('#$#', '#%#', '#?#', '##^', '$$')):
            continue  # AdGuard/ABP-only syntax and HTML filtering, which Chromium cannot do
        elif '##' in line or '#@#' in line:
            rules.append(line[:-len(':remove()')] if line.endswith(':remove()') else line)
        elif line[0] == '#':
            continue  # comment
        elif 'redirect-rule=' in line:
            if not line.startswith('@@'):  # adblock-rust 0.5 has no redirect-rule; Filters.check() emulates it
                redirects.append(_rewrite_options(line).replace('redirect-rule=', 'redirect='))
        else:
            line = _rewrite_options(line)
            rules.extend(_scoped_exception(line) or [line])
    return rules, scriptlets, redirects


def split_args(body: str) -> list[str]:
    """uBO scriptlet arguments: comma-separated, a quoted argument ('' "" ``) may contain commas,
    and outside quotes a backslash-comma is a literal comma."""
    args, i, n = [], 0, len(body)
    while True:
        while i < n and body[i] == ' ':
            i += 1
        if i < n and body[i] in '\'"`':
            quote, j, buf = body[i], i + 1, []
            while j < n and body[j] != quote:
                if body[j] == '\\' and j + 1 < n and body[j + 1] == quote:
                    j += 1
                buf.append(body[j])
                j += 1
            k = j + 1
            while k < n and body[k] == ' ':
                k += 1
            if j < n and (k >= n or body[k] == ','):
                args.append(''.join(buf))
                if k >= n:
                    return args
                i = k + 1
                continue
        buf, j = [], i
        while j < n and body[j] != ',':
            if body[j] == '\\' and j + 1 < n and body[j + 1] == ',':
                j += 1
            buf.append(body[j])
            j += 1
        args.append(''.join(buf).strip())
        if j >= n:
            return args
        i = j + 1


_SCRIPTLET_RULE = re.compile(r'^(.*?)#(@?)#\+js\((.*)\)$')


def parse_scriptlet(line: str):
    """'a.com,~b.a.com##+js(set, x, 1)' -> (['a.com'], ['b.a.com'], False, 'set', ['x', '1'])."""
    m = _SCRIPTLET_RULE.match(line)
    if not m:
        return None
    hosts, exception, body = m.groups()
    args = split_args(body)
    include, exclude = [], []
    for h in hosts.split(','):
        h = h.strip().lower()
        if h and not h.startswith('/'):  # regex hostnames are rare; skipped
            (exclude if h.startswith('~') else include).append(h.lstrip('~'))
    if not include and not (exception and not hosts):
        return None  # Lite only runs scriptlets on the sites a rule names
    name = args[0].removesuffix('.js') if args else ''
    return include, exclude, bool(exception), name, args[1:]


def index_scriptlets(lines: list[str]) -> dict:
    add: dict[str, list] = {}
    exceptions: dict[str, list] = {}
    for line in lines:
        parsed = parse_scriptlet(line)
        if not parsed:
            continue
        include, exclude, is_exception, name, args = parsed
        for host in include:
            subframes = host.endswith('>>')  # uBO: also run in every frame inside this site
            host = host.removesuffix('>>')
            if is_exception:
                exceptions.setdefault(host, []).append([name, args])
            else:
                add.setdefault(host, []).append([name, args, exclude, subframes])
    return {'add': add, 'except': exceptions}


def host_keys(host: str) -> list[str]:
    """Every key a rule for this host can be filed under: www.a.com -> www.a.com, a.com, a.*, ..."""
    labels = host.split('.')
    keys = []
    for i in range(len(labels) - 1):
        keys.append('.'.join(labels[i:]))
        keys.extend('.'.join(labels[i:j]) + '.*' for j in range(i + 1, len(labels)))
    return keys or [host]


# ---- the engine -------------------------------------------------------------
class Filters:
    def __init__(self, engine, safety, scriptlets: dict, library: dict | None, info: dict, redirects=None):
        self.engine = engine
        self.safety = safety
        self.redirects = redirects  # redirect-rules: only consulted for requests that are blocked anyway
        self.info = info
        self._add = scriptlets.get('add', {})
        self._except = scriptlets.get('except', {})
        self._names: dict[str, int] = {}
        self._code: list[str] = []
        for item in (library or {}).get('scriptlets', []):
            for n in item['names']:
                self._names.setdefault(n, len(self._code))
            self._code.append(item['scriptlet'])
        self._scripts: dict[str, str] = {}

    # network
    def check(self, url: str, source_url: str, request_type: str):
        result = self.engine.check_network_urls(url, source_url, request_type)
        if not result.matched:
            malware = self.safety.check_network_urls(url, source_url, request_type)  # URLhaus lives only here
            return malware if malware.matched else result
        if not result.redirect and self.redirects is not None:
            stub = self.redirects.check_network_urls(url, source_url, request_type)
            if stub.redirect:
                return stub  # blocked either way, but the page gets a stand-in instead of an error
        return result

    def is_dangerous(self, url: str) -> bool:
        """Is this whole page on a malware/scam list (URLhaus, uBlock badware risks)?"""
        return self.safety.check_network_urls(url, url, 'document').matched

    # cosmetic
    def cosmetics(self, url: str) -> tuple[str, bool, set]:
        """(CSS for this page, may generic ##.class rules apply, exceptions to pass back with them)."""
        r = self.engine.url_cosmetic_resources(url)
        css = [f'{s}{{display:none!important}}' for s in r.hide_selectors]
        css += [f'{s}{{{";".join(styles)}}}' for s, styles in r.style_selectors.items()]
        return '\n'.join(css), not r.generichide, r.exceptions

    def generic_selectors(self, classes: list[str], ids: list[str], exceptions: set) -> list[str]:
        return self.engine.hidden_class_id_selectors(classes, ids, exceptions)

    # scriptlets
    def _resolve(self, name: str) -> tuple[str, int] | None:
        """uBO scriptlet name -> (name to hand AdGuard's code, index of its code), None if unknown.

        uBO's alias is preferred because some AdGuard scriptlets switch to uBO's argument
        rules when they are called by it (set -> 'ubo-set.js')."""
        name = UBO_NAMES.get(name, name)
        for candidate in (f'ubo-{name}.js', f'{name}.js', f'ubo-{name}', name):
            if candidate in self._names:
                return candidate, self._names[candidate]
        return None

    @staticmethod
    def _rules(table: dict, key: str) -> list:
        value = table.get(key) or []
        return json.loads(value) if isinstance(value, str) else value  # kept as text until a site needs it

    def _calls(self, host: str):
        keys = host_keys(host)
        found = set(keys)

        def ident(name, args):
            resolved = self._resolve(name)
            return (resolved[1] if resolved else name), tuple(args)

        exceptions = [rule for k in keys for rule in self._rules(self._except, k)]
        if any(not name for name, _ in exceptions):
            return [], []  # example.com#@#+js() turns all scriptlets off there
        off = {ident(n, a) for n, a in exceptions}
        own, sub, seen = [], [], set()
        for k in keys:
            for name, args, exclude, subframes in self._rules(self._add, k):
                resolved = self._resolve(name)
                key = ident(name, args)
                if not resolved or key in off or (key, subframes) in seen or any(x in found for x in exclude):
                    continue
                seen.add((key, subframes))
                if name in REPLACE_NODE_TEXT and len(args) >= 3:
                    args = [args[0], args[1], args[1], args[2]]  # uBO (node, pattern, repl) -> AdGuard's order
                (sub if subframes else own).append((resolved[0], resolved[1], args))
        return own, sub

    def fixes(self, host: str) -> int:
        """How many scriptlets run on this site (shown in the Shields panel)."""
        own, sub = self._calls(host) if self._code else ([], [])
        return len(own) + len(sub)

    def scriptlet_script(self, host: str) -> str:
        """JS that runs this site's scriptlets at document start, or '' if it has none.

        It is registered per tab and stays registered, so it checks where it is running:
        its own rules only in documents from this host, `host>>` rules in every frame of a
        page whose top-level site is this host."""
        if host in self._scripts:
            return self._scripts[host]
        own, sub = self._calls(host) if self._code else ([], [])
        script = ''
        if own or sub:
            used = sorted({i for _, i, _ in own + sub})
            slot = {i: n for n, i in enumerate(used)}

            def calls(items):
                return '\n'.join(f'run({slot[i]}, {json.dumps(name)}, {json.dumps(args)});' for name, i, args in items)

            script = (
                '(() => {\n'
                f'const host = {json.dumps(host)};\n'
                'const own = location.hostname === host;\n'
                'let top = location.hostname;\n'
                'try { const a = location.ancestorOrigins; if (a && a.length) top = new URL(a[a.length - 1]).hostname; }'
                ' catch (e) {}\n'
                f'const under = {"true" if sub else "false"} && top === host;\n'
                'if (!own && !under) return;\n'
                'const fns = [\n' + ',\n'.join(self._code[i] for i in used) + '\n];\n'
                'const run = (i, name, args) => { try { fns[i].call(window, {name, args, engine: "corelibs", '
                f'version: {json.dumps(ADGUARD_VERSION)}, verbose: false, ruleText: ""}}, args); }} catch (e) {{}} }};\n'
                'if (own) {\n' + calls(own) + '\n}\n'
                'if (under) {\n' + calls(sub) + '\n}\n'
                '})();'
            )
        if len(self._scripts) > 512:
            self._scripts.clear()
        self._scripts[host] = script
        return script

    def describe(self, short: bool = False) -> str:
        lists = f'{len(self.info.get("lists", []))} filter lists, {self.info.get("rules", 0):,} rules'
        if short:
            return lists
        age = time.time() - self.info.get('built', 0)
        when = ('just now' if age < 3600 else f'{age / 3600:.0f} h ago' if age < 2 * 86400
                else f'{age / 86400:.0f} days ago')
        return f'{lists} (EasyList, EasyPrivacy, uBlock Origin, URLhaus) - updated {when}'


# ---- download, build, cache -------------------------------------------------
def _read_json(path: str):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path: str, data: bytes) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _sha256(path: str) -> str:
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ''


def _download(urls) -> bytes:
    error: Exception = OSError('no URL')
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Lite browser filter update)'})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(MAX_DOWNLOAD + 1)
            if len(data) > MAX_DOWNLOAD:
                raise ValueError('file too large')
            return data
        except (OSError, ValueError) as e:
            error = e
    raise error


def _rule_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and line.lstrip()[0] not in '!#[')


def _extra_hash(extra_rules: list[str]) -> str:
    return hashlib.sha256('\n'.join(extra_rules).encode()).hexdigest()[:16]


def _add_redirects(engine, folder: str) -> None:
    """Stubs (an empty script, a 1x1 gif, a fake Google IMA...) that blocked requests can get
    instead of an error, so pages and video players keep working without the ad code."""
    for r in _read_json(os.path.join(folder, 'redirects.json')) or []:
        mime, _, encoding = r['contentType'].partition(';')
        content = r['content'] if encoding == 'base64' else base64.b64encode(r['content'].encode()).decode()
        try:
            engine.add_resource(r['title'], mime, content, r.get('aliases') or [])
        except Exception:
            pass  # a stub the engine cannot store: that request is simply blocked


def _library(folder: str) -> dict | None:
    path = os.path.join(folder, 'scriptlets.corelibs.json')
    if _sha256(path) != ADGUARD_FILES['scriptlets.corelibs.json']:
        return None  # missing or modified: run no scriptlets rather than unknown code
    return _read_json(path)


def build(folder: str, extra_rules: list[str]) -> 'Filters | None':
    """Compile the downloaded lists into engines and save them. Takes about a second."""
    main, safety, redirect = adblock.FilterSet(), adblock.FilterSet(), adblock.FilterSet()
    js_lines, redirect_lines, used, rules = [], list(LITE_REDIRECTS), [], 0
    for spec in LISTS:
        try:
            with open(os.path.join(folder, spec.name + '.txt'), encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError:
            continue
        used.append(spec.name)
        if spec.hosts_format:
            main.add_filter_list(text, format='hosts')
            rules += _rule_count(text)
            continue
        lines, js, redirects = preprocess(text, spec.trusted)
        body = '\n'.join(lines)
        if not spec.safety_only:
            main.add_filter_list(body)
        if spec.safety:
            safety.add_filter_list(body)
        js_lines += js
        redirect_lines += redirects
        rules += len(lines) + len(js) + len(redirects)
    if not used:
        return None
    if extra_rules:
        main.add_filter_list('\n'.join(extra_rules))
    engine = adblock.Engine(main, optimize=True)
    _add_redirects(engine, folder)
    safety_engine = adblock.Engine(safety, optimize=True)
    redirect.add_filter_list('\n'.join(redirect_lines))
    redirect_engine = adblock.Engine(redirect, optimize=True)
    _add_redirects(redirect_engine, folder)
    # Each site's rules stay JSON text until a page on that site needs them (~20 MB less memory).
    scriptlets = {table: {host: json.dumps(rules) for host, rules in hosts.items()}
                  for table, hosts in index_scriptlets(js_lines).items()}
    info = {'version': CACHE_VERSION, 'built': time.time(), 'lists': used, 'rules': rules,
            'extra': _extra_hash(extra_rules)}
    engine.serialize_to_file(os.path.join(folder, 'engine.dat'))
    safety_engine.serialize_to_file(os.path.join(folder, 'safety.dat'))
    redirect_engine.serialize_to_file(os.path.join(folder, 'redirect.dat'))
    _write(os.path.join(folder, 'scriptlets.json'), json.dumps(scriptlets).encode())
    _write(os.path.join(folder, 'info.json'), json.dumps(info).encode())
    return Filters(engine, safety_engine, scriptlets, _library(folder), info, redirect_engine)


def load(folder: str, extra_rules: list[str]) -> 'Filters | None':
    """The cached engines (fast, no network), rebuilt from cached lists if they are outdated."""
    if adblock is None:
        return None
    info = _read_json(os.path.join(folder, 'info.json'))
    if not info or info.get('version') != CACHE_VERSION or info.get('extra') != _extra_hash(extra_rules):
        return build(folder, extra_rules) if os.path.isdir(folder) else None
    try:
        engines = []
        for name in ('engine.dat', 'safety.dat', 'redirect.dat'):
            engines.append(adblock.Engine(adblock.FilterSet()))
            engines[-1].deserialize_from_file(os.path.join(folder, name))
    except Exception:  # missing, truncated or from another adblock version
        return build(folder, extra_rules)
    scriptlets = _read_json(os.path.join(folder, 'scriptlets.json')) or {}
    return Filters(engines[0], engines[1], scriptlets, _library(folder), info, engines[2])


def update(folder: str, extra_rules: list[str], current: 'Filters | None') -> tuple['Filters | None', list[str]]:
    """Download whatever has expired and rebuild if anything changed. Network: run on a worker thread.

    Returns (new filters or None if nothing changed, errors)."""
    if adblock is None:
        return None, ['the adblock package is not installed (pip install adblock)']
    os.makedirs(folder, exist_ok=True)
    errors, changed = [], False
    for name, sha in ADGUARD_FILES.items():
        path = os.path.join(folder, name)
        if _sha256(path) == sha:
            continue
        try:
            data = _download([ADGUARD_URL + name])
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError('content does not match the pinned SHA-256')
            _write(path, data)
            changed = True
        except (OSError, ValueError) as e:
            errors.append(f'{name}: {e}')
    now = time.time()
    for spec in LISTS:
        path = os.path.join(folder, spec.name + '.txt')
        if os.path.exists(path) and now - os.path.getmtime(path) < spec.max_age_hours * 3600:
            continue
        try:
            data = _download(spec.urls)
            if _rule_count(data.decode('utf-8', 'replace')) < MIN_RULES:
                raise ValueError('not a filter list')
            _write(path, data)
            changed = True
        except (OSError, ValueError) as e:
            errors.append(f'{spec.name}: {e}')
    if not changed and current is not None:
        return None, errors
    return build(folder, extra_rules), errors
