"""Built-in HTML pages: the new tab page and the warning page shown instead of a risky site.

Everything is inline (no images, fonts or scripts from the network), and there is no
sponsored content of any kind.
"""
import json
from datetime import date, datetime
from html import escape


def _js_str(s: str) -> str:
    return json.dumps(s).replace('<', '\\u003c')


_BASE = """
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
color:#eceef2;font:15px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;background:#101216}
svg{fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
"""

_SEARCH_ICON = '<svg width=20 height=20 viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>'
_INCOGNITO = ('<svg width=44 height=44 viewBox="0 0 24 24"><path d="M3 11h18M5.5 11l1.6-5.2a1 1 0 0 1 1-.7h7.8a1 1 0 0 1 '
              '1 .7L18.5 11"/><circle cx="7.5" cy="16" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/><path d="M10 16h4"/></svg>')

_NEW_TAB_CSS = _BASE + """
body{background:radial-gradient(1100px 560px at 50% -12%,var(--glow),transparent 70%),
radial-gradient(800px 480px at 88% 112%,var(--glow2),transparent 70%),#101216}
.clock{font-size:84px;font-weight:300;letter-spacing:-3px;line-height:1}
.greet{color:#9aa0ab;margin:10px 0 30px;font-size:16px}
.search{width:min(640px,88vw);height:54px;display:flex;align-items:center;gap:12px;padding:0 22px;color:#8d93a0;
background:#1c1f25;border:1px solid #2b2f37;border-radius:27px;box-shadow:0 12px 40px #0008;transition:.15s}
.search:focus-within{border-color:var(--accent);box-shadow:0 0 0 4px var(--ring),0 12px 40px #0008}
.search input{flex:1;background:transparent;border:0;outline:0;color:#fff;font:inherit;font-size:17px}
.search input::placeholder{color:#6c727d}
.stats{display:flex;gap:14px;margin-top:44px;flex-wrap:wrap;justify-content:center}
.stat{width:190px;padding:18px 20px;border-radius:18px;background:#ffffff08;border:1px solid #ffffff0f}
.stat .n{font-size:32px;font-weight:700;letter-spacing:-1px;font-variant-numeric:tabular-nums}
.stat .l{color:#9aa0ab;font-size:13px}
.o .n{color:#ff8f3f}.g .n{color:#3fd18f}.p .n{color:#b9a0ff}
.foot{margin-top:24px;color:#6c727d;font-size:12.5px;text-align:center;line-height:1.7}
.private{max-width:560px;text-align:center;color:#b8bcc6;margin-top:30px;font-size:14px}
.private h2{color:#e7defe;font-size:19px;font-weight:600;margin:12px 0 6px}
.private .icon{color:#b9a0ff}
"""


def new_tab_html(counts: dict | None = None, since: str = '', lists: str = '', private: bool = False,
                 banner: dict | None = None) -> str:
    counts = counts or {}
    accent, ring, glow, glow2 = (('#a07bff', '#a07bff33', '#5b3fb855', '#a07bff22') if private else
                                 ('#fb542b', '#fb542b29', '#3a1f4d55', '#fb542b1f'))
    try:
        since_text = date.fromisoformat(since).strftime('%b %d, %Y').replace(' 0', ' ')
    except ValueError:
        since_text = since
    if private:
        body = f"""<div class=private><div class=icon>{_INCOGNITO}</div><h2>This is a private tab</h2>
<p>Lite forgets the sites you visit, cookies and site data from private tabs once you close the last one.
Shields choices made here are not saved, and the page check is off.</p>
<p style="color:#7d838e;font-size:13px">Your internet provider and the sites you visit can still see your activity.</p></div>"""
        title = 'Private tab'
    else:
        stopped = counts.get('popups', 0) + counts.get('dangerous', 0)
        body = f"""<div class=stats>
<div class="stat o"><div class=n>{counts.get('blocked', 0):,}</div><div class=l>Ads &amp; trackers blocked</div></div>
<div class="stat g"><div class=n>{counts.get('links', 0):,}</div><div class=l>Tracking links cleaned</div></div>
<div class="stat p"><div class=n>{stopped:,}</div><div class=l>Pop-ups &amp; malware pages stopped</div></div>
</div><div class=foot>Counting since {escape(since_text)}{' &middot; ' + escape(lists) if lists else ''}<br>
Lite never shows ads, sponsored tiles or sponsored images.</div>"""
        title = 'New tab'
    css = _NEW_TAB_CSS.replace('var(--accent)', accent).replace('var(--ring)', ring)
    css = css.replace('var(--glow2)', glow2).replace('var(--glow)', glow)
    banner_html = ''
    if banner:
        banner_html = (f'<div class=banner role=button id=banner><b>{escape(banner.get("name") or banner["tag"])}'
                       f'</b> is available<span>Click to open the download page</span></div>')
        css += """.banner{margin:0 0 26px;padding:12px 22px;border-radius:14px;cursor:pointer;text-align:center;
background:#1c1f25;border:1px solid #2b2f37;color:#eceef2;font-size:14px}
.banner b{color:var(--accent2)}
.banner span{display:block;color:#8d93a0;font-size:12.5px;margin-top:2px}
.banner:hover{border-color:var(--accent)}"""
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{title}</title><style>{css}</style></head>
<body><div class=clock id=clock></div><div class=greet id=greet></div>
<label class=search>{_SEARCH_ICON}<input id=q placeholder="Search with Brave or type a web address"></label>
{banner_html}
{body}
<script>
function tick(){{const d=new Date();document.getElementById('clock').textContent=
d.toLocaleTimeString([],{{hour:'numeric',minute:'2-digit'}}).replace(/\\s?[AP]M$/i,'');
const h=d.getHours();document.getElementById('greet').textContent={_js_str('Private browsing') if private else
"h<5?'Good night':h<12?'Good morning':h<18?'Good afternoon':'Good evening'"};}}
tick();setInterval(tick,5000);
document.getElementById('q').addEventListener('keydown',e=>{{if(e.key==='Enter'&&e.target.value.trim())
window.chrome.webview.postMessage({{cmd:'go',q:e.target.value.trim()}})}});
const banner=document.getElementById('banner');
if(banner)banner.addEventListener('click',()=>window.chrome.webview.postMessage({{cmd:'update'}}));
</script></body></html>"""


_WARN_CSS = _BASE + """
body{background:radial-gradient(900px 500px at 50% -10%,var(--glow),transparent 70%),#101216}
.card{width:min(600px,90vw);padding:36px 40px;border-radius:20px;background:#1a1d23;border:1px solid #ffffff0d;
box-shadow:0 20px 60px #0009}
.icon{width:56px;height:56px;border-radius:16px;display:grid;place-items:center;color:#fff;background:var(--c);
box-shadow:0 8px 24px var(--glow)}
h1{margin:18px 0 8px;font-size:24px;font-weight:600}
p{margin:8px 0;color:#b8bcc6}.small{font-size:13px;color:#7d838e}
code{display:block;margin:14px 0;padding:10px 14px;border-radius:10px;background:#0f1115;color:#9ecbff;
word-break:break-all;font:13px/1.5 Consolas,monospace}
.btns{display:flex;gap:10px;margin-top:24px}
button{height:40px;padding:0 20px;border:0;border-radius:10px;font:inherit;font-size:14px;cursor:pointer}
.primary{background:#fb542b;color:#fff}.primary:hover{background:#ff6a40}
.ghost{background:#ffffff10;color:#dfe1e6}.ghost:hover{background:#ffffff1c}
"""

_KINDS = {  # title, colour
    'block': ('Lite blocked this page', '#e5484d'),
    'danger': ('Dangerous site ahead', '#e5484d'),
    'warn': ('This page looks risky', '#e8a33d'),
    'ads': ('Lite stopped an ad or tracking page', '#fb542b'),
}


def blocked_html(url: str, reason: str, level: str, kind: str | None = None) -> str:
    """level 'block' has no way through; 'warn' offers "Continue anyway" (allowed for the session)."""
    title, color = _KINDS.get(kind or level, _KINDS['warn'])
    proceed = '' if level == 'block' else '<button class=ghost onclick="go(\'proceed\')">Continue anyway</button>'
    note = ('Nothing from this page has been loaded.' if level == 'block' else
            'Nothing from this page has been loaded yet. Continue only if you trust it - '
            'Lite will not ask again for this site until you restart it.')
    css = _WARN_CSS.replace('var(--c)', color).replace('var(--glow)', color + '40')
    icon = ('<svg width=30 height=30 viewBox="0 0 24 24"><path d="M12 2.8l7.5 2.9v5.6c0 4.9-3.3 8.6-7.5 9.9-4.2-1.3'
            '-7.5-5-7.5-9.9V5.7z"/><path d="M12 8v4.5M12 15.6v.1"/></svg>')
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{escape(title)}</title><style>{css}</style></head>
<body><div class=card><div class=icon>{icon}</div><h1>{escape(title)}</h1>
<p>{escape(reason)}</p><code>{escape(url)}</code><p class=small>{note}</p>
<div class=btns><button class=primary onclick="go('back')">Go back</button>{proceed}</div>
</div><script>
function go(cmd){{window.chrome.webview.postMessage({{cmd:cmd,url:{_js_str(url)}}})}}
</script></body></html>"""

LIBRARY_CSS = _BASE + """
body{justify-content:flex-start;padding:6vh 0 40px}
.wrap{width:min(760px,92vw)}
h1{font-size:22px;font-weight:600;margin:0 0 18px;display:flex;gap:16px;align-items:baseline}
h1 .views{display:flex;gap:6px;margin-left:auto}
h1 button{background:transparent;border:1px solid #2b2f37;color:#9aa0ab;border-radius:9px;padding:6px 14px;
font:inherit;font-size:13px;cursor:pointer}
h1 button.on{color:#fff;border-color:var(--accent);background:#fb542b22}
input.q{width:100%;height:40px;margin-bottom:14px;padding:0 16px;border-radius:20px;border:1px solid #2b2f37;
background:#17191e;color:#fff;font:inherit;outline:0}
input.q:focus{border-color:var(--accent)}
.list{display:flex;flex-direction:column;gap:2px}
.row{display:flex;align-items:center;gap:12px;padding:9px 14px;border-radius:10px}
.row:hover{background:#ffffff08}
.row .t{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;cursor:pointer}
.row .t small{display:block;color:#6c727d;font-size:12px;overflow:hidden;text-overflow:ellipsis}
.row .when{color:#6c727d;font-size:12px;flex:none}
.row .x{flex:none;width:26px;height:26px;border-radius:7px;display:grid;place-items:center;color:#6c727d;cursor:pointer}
.row .x:hover{background:#ff6b6b22;color:#ff6b6b}
.empty{color:#6c727d;text-align:center;padding:40px 0}
.danger{margin-top:18px;background:transparent;border:1px solid #4a2626;color:#ff8b8b;border-radius:9px;
padding:8px 16px;font:inherit;font-size:13px;cursor:pointer}
.danger:hover{background:#ff6b6b1a}
"""


def _when(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    today = date.today()
    if d.date() == today:
        return d.strftime('%H:%M')
    if (today - d.date()).days < 7:
        return d.strftime('%a')
    return d.strftime('%b %d')


def library_html(bookmarks: list[dict], history: list[dict], view: str = 'history') -> str:
    """Bookmarks and history in one page; rows are filtered live by the search box."""
    view = 'bookmarks' if view == 'bookmarks' else 'history'
    title = 'Bookmarks' if view == 'bookmarks' else 'History'
    rows = bookmarks if view == 'bookmarks' else history
    items = ''.join(
        f"""<div class=row data-u={_js_str(e['url'])}><div class=t data-open=1><div>{escape(e['title'])}</div>
<small>{escape(e['url'])}</small></div><span class=when>{'' if view == 'bookmarks' else escape(_when(e['ts']))}</span>
<span class=x title="{'Remove bookmark' if view == 'bookmarks' else 'Delete'}">&#10005;</span></div>"""
        for e in rows)
    if not rows:
        items = (f'<div class=empty>No bookmarks yet. Click the star in the toolbar (or press Ctrl+D) to add one.</div>'
                 if view == 'bookmarks' else '<div class=empty>Nothing here yet - pages you open will show up.</div>')
    clear = ('<button class=danger id=clearhist>Delete all history</button>' if view == 'history' and rows else '')
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{title}</title><style>{LIBRARY_CSS}</style></head>
<body><div class=wrap><h1>{title}<span class=views>
<button id=v-bookmarks data-v=bookmarks>Bookmarks</button>
<button id=v-history data-v=history>History</button></span></h1>
<input class=q id=q placeholder="Filter by title or address...">
<div class=list id=list>{items}</div>{clear}</div>
<script>
const view={_js_str(view)};
for(const b of document.querySelectorAll('h1 button'))b.addEventListener('click',()=>
window.chrome.webview.postMessage({{cmd:'library',v:b.dataset.v}}));
document.getElementById('q').addEventListener('input',e=>{{
  const q=e.target.value.toLowerCase();
  for(const r of document.querySelectorAll('.row'))
    r.style.display=r.textContent.toLowerCase().includes(q)?'':'none'}});
document.getElementById('list').addEventListener('click',e=>{{
  const row=e.target.closest('.row');if(!row)return;
  if(e.target.classList.contains('x'))
    window.chrome.webview.postMessage({{cmd:view==='bookmarks'?'unstar':'delhist',url:row.dataset.u}});
  else if(e.target.closest('[data-open]'))
    window.chrome.webview.postMessage({{cmd:'go',q:row.dataset.u}})}});
const ch=document.getElementById('clearhist');
if(ch)ch.addEventListener('click',()=>window.chrome.webview.postMessage({{cmd:'clearhist'}}));
for(const b of document.querySelectorAll('h1 button'))b.classList.toggle('on',b.dataset.v===view);
</script></body></html>"""


READER_CSS = _BASE + """
body{justify-content:flex-start;padding:8vh 0 60px}
.wrap{width:min(680px,90vw)}
.mast{color:#6c727d;font-size:12.5px;margin-bottom:34px;display:flex;gap:14px;align-items:center}
.mast .site{color:#fb542b;cursor:pointer}
h1{font-size:30px;font-weight:600;line-height:1.25;margin:0 0 26px;letter-spacing:-.5px}
#article{font-size:var(--fs,18px);line-height:1.65;color:#dfe1e6;overflow-wrap:break-word}
#article p{margin:0 0 1.1em}
#article h2{font-size:1.35em;margin:1.6em 0 .5em;color:#fff}
#article h3{font-size:1.15em;margin:1.4em 0 .4em;color:#fff}
#article li{margin:.3em 0}
#article blockquote{margin:1.2em 0;padding-left:16px;border-left:3px solid #fb542b55;color:#b8bcc6}
.tools{position:fixed;top:14px;right:18px;display:flex;gap:8px}
.tools button{width:36px;height:36px;border-radius:10px;border:1px solid #2b2f37;background:#1c1f25cc;
color:#dfe1e6;font:inherit;font-size:16px;cursor:pointer}
.tools button:hover{border-color:var(--accent)}
.leave{margin-top:40px;background:transparent;border:1px solid #2b2f37;color:#9aa0ab;border-radius:9px;
padding:8px 18px;font:inherit;font-size:13px;cursor:pointer}
.leave:hover{color:#fff;border-color:var(--accent)}
"""


def reader_html(article: dict) -> str:
    """A clean-text view of an article. Blocks are plain text extracted by READER_JS."""
    parts, in_list = [], False
    for e in article.get('blocks', []):
        x = e.get('x')
        if not isinstance(x, str) or not x.strip():
            continue
        if e['t'] == 'li':
            if not in_list:
                parts.append('<ul>')
                in_list = True
            parts.append(f'<li>{escape(x)}</li>')
            continue
        if in_list:
            parts.append('</ul>')
            in_list = False
        tag = e['t']
        parts.append(f'<{tag}>{escape(x)}</{tag}>')
    if in_list:
        parts.append('</ul>')
    blocks = ''.join(parts)
    title = escape(article.get('title') or 'Reader')
    url = str(article.get('url') or '')
    host = escape(url.split('/')[2] if url.count('/') > 1 else '')
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{title}</title><style>{READER_CSS}</style></head>
<body><div class=wrap><div class=mast>Reader view<span class=site id=site>{host}</span></div>
<h1>{title}</h1><div id=article>{blocks or '<p>No readable text was found on this page.</p>'}</div>
<button class=leave id=leave>Leave reader view</button></div>
<div class=tools><button id=minus title="Smaller text">&minus;</button><button id=plus title="Larger text">+</button></div>
<script>
let fs=18;const a=document.getElementById('article');
const set=()=>a.style.setProperty('--fs',fs+'px'),adj=d=>{{fs=Math.min(28,Math.max(14,fs+d));set()}};
document.getElementById('plus').addEventListener('click',()=>adj(2));
document.getElementById('minus').addEventListener('click',()=>adj(-2));
set();
for(const el of['leave','site'])document.getElementById(el).addEventListener('click',()=>
window.chrome.webview.postMessage({{cmd:'leave'}}));
</script></body></html>"""


def sleeping_html(url: str) -> str:
    """A sleeping tab's placeholder: one click (or switching to it) reloads the page."""
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Tab asleep</title><style>{_BASE}
.card{{text-align:center;color:#9aa0ab}}.zz{{font-size:44px;margin-bottom:10px}}
button{{margin-top:18px;background:#fb542b;color:#fff;border:0;border-radius:9px;padding:9px 22px;
font:inherit;font-size:14px;cursor:pointer}}</style></head>
<body><div class=card><div class=zz>&#127769;</div><h2 style="color:#eceef2;margin:0 0 6px">This tab is asleep</h2>
<p style="max-width:440px">Lite freed its memory because it has been in the background for a while.<br>
<code style="word-break:break-all;font-size:12px">{escape(url)}</code></p>
<button onclick="go()">Reload this tab</button></div>
<script>function go(){{window.chrome.webview.postMessage({{cmd:'wake'}})}}</script></body></html>"""


# JavaScript injected into the current page. Both are run with ExecuteScriptAsync
# (which ignores a page's CSP) and both are self-contained: the find bar keeps its
# own state, and the reader collects plain text without any library.

FIND_JS = r"""
(() => {
  const BAR = 'lite-find-bar';
  if (document.getElementById(BAR)) { document.getElementById(BAR).remove(); }
  const bar = document.createElement('div');
  bar.id = BAR;
  const accent = '#fb542b';
  bar.style.cssText = 'position:fixed;top:8px;right:16px;z-index:2147483647;display:flex;gap:6px;align-items:center;'
    + 'padding:7px 9px;border-radius:12px;background:#1c1f25;border:1px solid #2b2f37;'
    + 'box-shadow:0 10px 34px #000a;font:13px/1 system-ui,sans-serif;color:#eceef2';
  const input = document.createElement('input');
  input.placeholder = 'Find in page';
  input.style.cssText = 'width:180px;background:#17191e;border:1px solid #2b2f37;border-radius:8px;'
    + 'color:#fff;font:inherit;padding:6px 10px;outline:0';
  const info = document.createElement('span');
  info.style.cssText = 'color:#9aa0ab;min-width:64px;text-align:center;font-variant-numeric:tabular-nums';
  const mk = (t, title) => { const b = document.createElement('button'); b.textContent = t; b.title = title;
    b.style.cssText = 'width:26px;height:26px;border-radius:7px;border:1px solid #2b2f37;background:transparent;'
      + 'color:#eceef2;font:inherit;cursor:pointer'; return b; };
  const prev = mk('\u2191', 'Previous match (Shift+Enter)');
  const next = mk('\u2193', 'Next match (Enter)');
  const close = mk('\u2715', 'Close (Esc)');
  bar.append(input, info, prev, next, close);
  document.documentElement.appendChild(bar);
  input.focus();

  const HIGHLIGHT = 'lite-find-hl';
  let matches = [], at = -1, last = '';
  const style = document.createElement('style');
  style.textContent = '::' + HIGHLIGHT + '{background:#fb542b88;color:#fff}'
    + '::' + HIGHLIGHT + '-active{background:#fb542b;color:#fff}';
  document.head.appendChild(style);

  function clear() { for (const m of matches) m.style.backgroundColor = ''; matches = []; at = -1; }

  function collect(needle) {
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    const nodes = [];
    for (let n; (n = walker.nextNode());) {
      if (!n.parentElement.closest('#' + BAR) && n.nodeValue && n.nodeValue.toLowerCase().includes(needle)) nodes.push(n);
    }
    return nodes;
  }

  function run(dir) {
    const needle = input.value;
    if (needle !== last) {
      last = needle;
      clear();
      if (needle) {
        const nodes = collect(needle.toLowerCase());
        const hl = new Highlight(...nodes);
        try { CSS.highlights.set(HIGHLIGHT, hl); } catch (e) {}
        for (const n of nodes) matches.push(n.parentElement);
      }
    }
    if (!matches.length) { info.textContent = needle ? '0/0' : ''; try { CSS.highlights.delete(HIGHLIGHT); } catch (e) {} return; }
    at = (at + (dir === -1 ? -1 : 1) + matches.length) % matches.length;
    info.textContent = (at + 1) + '/' + matches.length;
    matches[at].scrollIntoView({block: 'center', behavior: 'smooth'});
  }

  input.addEventListener('input', () => run(1));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { run(e.shiftKey ? -1 : 1); e.preventDefault(); }
    if (e.key === 'Escape') { e.stopPropagation(); closeBar(); }
  });
  next.addEventListener('click', () => run(1));
  prev.addEventListener('click', () => run(-1));

  function closeBar() { bar.remove(); style.remove(); try { CSS.highlights.delete(HIGHLIGHT); } catch (e) {} }
  close.addEventListener('click', closeBar);
})()
"""

READER_JS = r"""
(() => {
  const text = (el) => el.innerText.replace(/\s+/g, ' ').trim();
  const root = document.querySelector('article') || document.querySelector('main')
    || [...document.querySelectorAll('div')].filter(d => d.querySelectorAll('p').length > 3
      && d.querySelectorAll('p').length * 2 > d.querySelectorAll('*').length * 0.3)
      .sort((a, b) => b.innerText.length - a.innerText.length)[0] || document.body;
  const blocks = [];
  for (const el of root.querySelectorAll('h1,h2,h3,p,li,blockquote,pre')) {
    if (!el.offsetParent && el.tagName !== 'PRE') continue;
    if (el.closest('nav,aside,footer,form,button,noscript') || el.closest('#lite-find-bar')) continue;
    const t = {H1: 'h2', H2: 'h2', H3: 'h3', P: 'p', LI: 'li', BLOCKQUOTE: 'blockquote', PRE: 'p'}[el.tagName];
    const x = text(el);
    if (t && x.length > 1 && !blocks.some(b => b.x === x)) blocks.push({t, x});
  }
  const byline = document.querySelector('meta[name=author]');
  return JSON.stringify({
    title: document.title.replace(/\s*[-|]\s*[^-|]*$/, ''),
    byline: byline ? byline.content : '',
    url: location.href.split('#')[0],
    blocks: blocks.slice(0, 600),
  });
})()
"""
