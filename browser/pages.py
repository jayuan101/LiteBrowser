"""Built-in HTML pages: the new tab page and the warning page shown instead of a risky site.

Everything is inline (no images, fonts or scripts from the network), and there is no
sponsored content of any kind.
"""
import json
from datetime import date
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


def new_tab_html(counts: dict | None = None, since: str = '', lists: str = '', private: bool = False) -> str:
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
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{title}</title><style>{css}</style></head>
<body><div class=clock id=clock></div><div class=greet id=greet></div>
<label class=search>{_SEARCH_ICON}<input id=q placeholder="Search with Brave or type a web address"></label>
{body}
<script>
function tick(){{const d=new Date();document.getElementById('clock').textContent=
d.toLocaleTimeString([],{{hour:'numeric',minute:'2-digit'}}).replace(/\\s?[AP]M$/i,'');
const h=d.getHours();document.getElementById('greet').textContent={_js_str('Private browsing') if private else
"h<5?'Good night':h<12?'Good morning':h<18?'Good afternoon':'Good evening'"};}}
tick();setInterval(tick,5000);
document.getElementById('q').addEventListener('keydown',e=>{{if(e.key==='Enter'&&e.target.value.trim())
window.chrome.webview.postMessage({{cmd:'go',q:e.target.value.trim()}})}});
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
