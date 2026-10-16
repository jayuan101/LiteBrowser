"""Checks for browser/filters.py and browser/privacy.py.

    python scratch/test_filters.py            offline checks only
    python scratch/test_filters.py --live     also download the real lists (~10 MB) into a temp
                                              folder and check real decisions on CNN / YouTube URLs

Generated JavaScript is syntax-checked with `node --check` when Node is installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from browser import filters as fl, privacy  # noqa: E402

failures = 0
NODE = shutil.which('node')


def check(name, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      got={got!r}\n     want={want!r}'))


def js_ok(name, code):
    if not NODE:
        print(f'SKIP  {name} (node not installed)')
        return
    path = os.path.join(tempfile.mkdtemp(prefix='lite-js-'), 'check.js')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    r = subprocess.run([NODE, '--check', path], capture_output=True, text=True)
    check(name, r.returncode == 0 or r.stderr.strip()[:300], True)


# ---- !#if --------------------------------------------------------------------
for expr, want in [('env_chromium', True), ('env_firefox', False), ('!env_firefox', True),
                   ('env_chromium && !env_mobile', True), ('env_firefox || env_chromium', True),
                   ('(env_firefox || env_safari) && ext_ublock', False), ('!(env_mobile || false)', True),
                   ('cap_html_filtering', False), ('ext_ublock && (env_chromium || env_edge)', True)]:
    check(f'condition {expr!r}', fl.condition(expr), want)

# ---- preprocess ---------------------------------------------------------------
LIST = r"""
! comment
||plain.example^
||all.example^$all
||allopt.example^$all,domain=x.example
||from.example^$script,from=a.example|b.example
||s3p.example^$strict3p
||mp4.example^$mp4
@@*$ghide,domain=forum.example|~www.forum.example|other.example
/regex\.js$/
!#if env_firefox
||firefox-only.example^
!#else
||not-firefox.example^
!#endif
!#if env_chromium
!#if env_mobile
||mobile-chromium.example^
!#endif
||chromium.example^
!#endif
example.com##.ad
example.com##.gone:remove()
example.com#$#abp-snippet
example.com##^script:has-text(ad)
example.com#@#.ok
# a hosts-style comment
example.com##+js(set, adblock, false)
example.com##+js(trusted-set-cookie, a, b)
example.com#@#+js()
*$script,redirect-rule=noopjs,domain=news.example
@@||cdn.example/x.js$script,redirect-rule=noopjs
"""
rules, js, redirects = fl.preprocess(LIST, trusted=False)
check('redirect-rules split off (exceptions dropped)', redirects, ['*$script,redirect=noopjs,domain=news.example'])
check('network rules kept / rewritten', [r for r in rules if '##' not in r and '#@#' not in r], [
    '||plain.example^', '||all.example^', '||allopt.example^$domain=x.example',
    '||from.example^$script,domain=a.example|b.example', '||s3p.example^$3p',
    '||mp4.example^$media,redirect=noopmp4-1s', '@@||forum.example^$ghide', '@@||other.example^$ghide',
    r'/regex\.js$/', '||not-firefox.example^', '||chromium.example^'])
check('cosmetic rules kept, :remove() becomes hiding', [r for r in rules if '##' in r or '#@#' in r],
      ['example.com##.ad', 'example.com##.gone', 'example.com#@#.ok'])
check('untrusted list: trusted-* scriptlet dropped', js, ['example.com##+js(set, adblock, false)', 'example.com#@#+js()'])
_, js_trusted, _ = fl.preprocess(LIST, trusted=True)
check('trusted list keeps trusted-* scriptlets', len(js_trusted), 3)

# ---- scriptlet arguments --------------------------------------------------------
for body, want in [
    ('set, a, b', ['set', 'a', 'b']),
    ('', ['']),
    ("trusted-rpfr, '\"adPlacements\"', '\"no_ads\"', player?", ['trusted-rpfr', '"adPlacements"', '"no_ads"', 'player?']),
    (r'trusted-replace-xhr-response, /"adPlacements.*?([A-Z]"\}|"\}{2\,4})\}\]\,/, , /playlist\?list=|\/player(?:\?.+)?$/',
     ['trusted-replace-xhr-response', r'/"adPlacements.*?([A-Z]"\}|"\}{2,4})\}\],/', '',
      r'/playlist\?list=|\/player(?:\?.+)?$/']),
    ("acs, document.write, 'a,b'", ['acs', 'document.write', 'a,b']),
    (r"x, 'it\'s'", ['x', "it's"]),
    ('nowoif', ['nowoif']),
    ('a,', ['a', '']),
]:
    check(f'split_args {body[:40]!r}', fl.split_args(body), want)

check('parse_scriptlet', fl.parse_scriptlet('a.com,~b.a.com,ent.*>>##+js(set.js, x, 1)'),
      (['a.com', 'ent.*>>'], ['b.a.com'], False, 'set', ['x', '1']))
check('parse_scriptlet exception', fl.parse_scriptlet('a.com#@#+js()'), (['a.com'], [], True, '', []))
check('parse_scriptlet regex host only -> skipped', fl.parse_scriptlet('/^ex/##+js(nowoif)'), None)
check('host_keys', fl.host_keys('www.youtube.com'),
      ['www.youtube.com', 'www.*', 'www.youtube.*', 'youtube.com', 'youtube.*'])

# ---- scriptlet script generation (fake library) ---------------------------------
LIB = {'scriptlets': [
    {'names': ['set-constant', 'set-constant.js', 'ubo-set-constant.js', 'set.js', 'ubo-set.js'],
     'scriptlet': 'function setConstant(source,args){window.__set=[source.name].concat(args)}'},
    {'names': ['trusted-replace-node-text'],
     'scriptlet': 'function trustedReplaceNodeText(source,args){window.__rpnt=args}'},
    {'names': ['no-window-open-if', 'nowoif.js', 'ubo-nowoif.js'], 'scriptlet': 'function nowoif(source,args){}'},
]}
idx = fl.index_scriptlets([
    'example.com##+js(set, a, 1)',
    'example.com##+js(set-constant.js, a, 1)',  # same scriptlet under another name: deduplicated
    'example.*##+js(nowoif)',
    'example.com,~shop.example.com##+js(set, b, 2)',
    'example.com>>##+js(set, frames, 1)',
    'example.com##+js(rpnt, script, /ads/, x)',
    'example.com##+js(unknown-scriptlet, z)',
    'example.com#@#+js(set, a, 1)',
    'off.example#@#+js()',
    'off.example##+js(set, c, 3)',
])
f = fl.Filters(None, None, idx, LIB, {})
script = f.scriptlet_script('www.example.com')
check('script: own rules for subdomain + entity rule', ('ubo-set.js' in script, '["b", "2"]' in script, 'ubo-nowoif.js' in script), (True, True, True))
check('script: exception removes (set, a, 1) under every alias', '["a", "1"]' in script, False)
check('script: rpnt args reordered for AdGuard', '["script", "/ads/", "/ads/", "x"]' in script, True)
check('script: >> rule only for frames under the site', 'if (under) {\nrun(' in script and '["frames", "1"]' in script, True)
check('script: unknown scriptlet skipped', 'unknown' in script, False)
check('script: ~shop.example.com excluded', '["b", "2"]' in f.scriptlet_script('shop.example.com'), False)
check('script: #@#+js() turns all off', f.scriptlet_script('off.example'), '')
check('script: nothing for unrelated host', f.scriptlet_script('other.org'), '')
check('script: only one copy of each function', script.count('function setConstant'), 1)
js_ok('generated scriptlet script is valid JS', script)
js_ok('COSMETIC_JS is valid JS', fl.COSMETIC_JS)
js_ok('GPC_JS is valid JS', privacy.GPC_JS)

# ---- tracking links ---------------------------------------------------------------
for url, want in [
    ('https://shop.example/item?id=7&fbclid=IwAR0abc', 'https://shop.example/item?id=7'),
    ('https://a.example/?gclid=1&utm_source=news&x=%20y#frag', 'https://a.example/?utm_source=news&x=%20y#frag'),
    ('https://a.example/?fbclid=1', 'https://a.example/'),
    ('https://a.example/?q=fbclid', 'https://a.example/?q=fbclid'),
    ('https://a.example/path', 'https://a.example/path'),
    ('ftp://a.example/?gclid=1', 'ftp://a.example/?gclid=1'),
    ('https://a.example/?mc_eid=1&__hstc=2&keep=3', 'https://a.example/?keep=3'),
]:
    check(f'strip_tracking {url[:50]}', privacy.strip_tracking(url), want)

# ---- real lists -----------------------------------------------------------------
if '--live' in sys.argv:
    folder = tempfile.mkdtemp(prefix='lite-filters-')
    t = time.time()
    built, errors = fl.update(folder, ['||custom-blocked.example^'], None)
    print(f'\nupdate(): {time.time() - t:.1f}s, errors={errors}')
    check('all lists and AdGuard files downloaded', errors, [])
    check('engine built', built is not None, True)
    t = time.time()
    loaded = fl.load(folder, ['||custom-blocked.example^'])
    print(f'load() from cache: {time.time() - t:.2f}s; {loaded.describe()}')
    for label, flt in (('built', built), ('cached', loaded)):
        def blocked(url, page, kind):
            return flt.check(url, page, kind).matched
        cnn = 'https://www.cnn.com/'
        check(f'{label}: CNN ad + tracker scripts blocked', [blocked(u, cnn, 'script') for u in (
            'https://securepubads.g.doubleclick.net/tag/js/gpt.js', 'https://cdn.dianomi.com/js/dianomi.js',
            'https://tag.bounceexchange.com/2423/i.js', 'https://mssl.fwmrm.net/libs/adm/6.47.0/AdManager.js',
            'https://www.google-analytics.com/analytics.js', 'https://custom-blocked.example/x.js')], [True] * 6)
        check(f'{label}: CNN own assets allowed', [blocked(u, cnn, k) for u, k in (
            ('https://www.cnn.com/media/sites/cnn/cnn-logo.svg', 'image'),
            ('https://media.cnn.com/api/v1/images/stellar/prod/x.jpg', 'image'),
            ('https://cdn.jsdelivr.net/npm/jquery@3/dist/jquery.min.js', 'script'))], [False] * 3)
        yt = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        check(f'{label}: YouTube ad endpoints blocked', [blocked(u, yt, k) for u, k in (
            ('https://www.youtube.com/api/stats/ads?ver=2', 'xmlhttprequest'),
            ('https://www.youtube.com/pagead/viewthroughconversion/1/', 'image'),
            ('https://googleads.g.doubleclick.net/pagead/id', 'xmlhttprequest'))], [True] * 3)
        check(f'{label}: YouTube video + player allowed', [blocked(u, yt, k) for u, k in (
            ('https://rr3---sn-a5mekn6r.googlevideo.com/videoplayback?expire=1', 'xmlhttprequest'),
            ('https://www.youtube.com/s/player/abc/player_ias.vflset/en_US/base.js', 'script'),
            ('https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg', 'image'))], [False] * 3)
        stubs = {name: flt.check(u, cnn, 'script') for name, u in (
            ('IMA', 'https://imasdk.googleapis.com/js/sdkloader/ima3.js'),
            ('FreeWheel', 'https://mssl.fwmrm.net/libs/adm/6.47.0/AdManager.js'),
            ('GPT', 'https://securepubads.g.doubleclick.net/tag/js/gpt.js'))}
        check(f'{label}: blocked video-ad SDKs answered with stand-ins', {k: (r.matched, (r.redirect or '').startswith(
              'data:application/javascript')) for k, r in stubs.items()}, {k: (True, True) for k in stubs})
        check(f'{label}: a plain tracker still gets no stub', flt.check(
            'https://www.google-analytics.com/collect?v=1', cnn, 'ping').redirect or '', '')
        check(f'{label}: malware page flagged (uBlock badware list)', flt.is_dangerous('https://vlc.de/download'), True)
        check(f'{label}: normal pages not flagged', [flt.is_dangerous(u) for u in (
            'https://www.cnn.com/', 'https://www.youtube.com/', 'https://github.com/')], [False] * 3)
        yts = flt.scriptlet_script('www.youtube.com')
        check(f'{label}: YouTube gets its ad-removing scriptlets', all(s in yts for s in (
            'trusted-replace-xhr-response', 'trusted-replace-fetch-response', 'ubo-set')), True)
        css, generic, _ = flt.cosmetics('https://www.youtube.com/')
        check(f'{label}: YouTube cosmetic rules present', ('ytd-ad-slot-renderer' in css, generic), (True, False))
    js_ok('real YouTube scriptlet script is valid JS', loaded.scriptlet_script('www.youtube.com'))
    print(f'      YouTube scriptlet script: {len(loaded.scriptlet_script("www.youtube.com")) / 1000:.0f} KB')
    css, generic, _ = loaded.cosmetics('https://example.org/')
    print(f'      generic page CSS: {css.count("{")} rules')
    check('generic hiding is on for an ordinary site (scoped $ghide exceptions stay scoped)',
          (generic, css.count('{') > 100), (True, True))
    check('generic class/id rules match real ad classes, not ordinary ones',
          sorted(loaded.generic_selectors(['adsbygoogle-wrapper', 'ADBox', 'article-body'], ['ad-banner-atf', 'main'], set())),
          ['#ad-banner-atf', '.ADBox', '.adsbygoogle-wrapper'])
    for site in ('https://www.cnn.com/', 'https://www.youtube.com/', 'https://forum.activo.mx/'):
        print(f'      {site}: generic hiding {"on" if loaded.cosmetics(site)[1] else "off (list exception)"}')
    hosts = [h for h in loaded._add if '*' not in h]
    print(f'      sites with scriptlets: {len(hosts):,}')
    print('      pop-up check for common link redirectors (True = would be blocked as a pop-up):')
    for u in ('https://go.redirectingat.com/?id=1&url=https%3A%2F%2Fwww.bestbuy.com', 'https://click.linksynergy.com/deeplink?id=x',
              'https://amzn.to/3abc', 'https://bit.ly/3abc', 'https://t.co/abc', 'https://l.facebook.com/l.php?u=x',
              'https://www.awin1.com/cread.php?awinmid=1', 'https://ad.doubleclick.net/ddm/clk/1;2;3',
              'https://www.popads.net/pop.js', 'https://propellerads.com/'):
        print(f'        {str(loaded.check(u, "https://news.example/", "document").matched):5} {u}')
    shutil.rmtree(folder, ignore_errors=True)

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
