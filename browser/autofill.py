"""Saving and filling passwords for the vault in passwords.py.

A small script in every page (LOGIN_JS, top frame only) tells Lite whether the page has
sign-in fields, and what you typed when you sign in - Lite then offers to save it. A saved
password goes into a page only when you pick it from the key button's panel: Lite's own
window, which no page can click for you. The fill script checks the page's origin itself
before it touches a field, so a page that moved on, a hidden form on another site, or a
script on this one can never collect a password without you choosing to sign in.
"""
import json
from urllib.parse import urlsplit

# Shared by both scripts: which inputs are the password and the username of a sign-in form.
_FIELDS_JS = r"""
  const USERISH = /user|e-?mail|login|account|identifier|phone/i;
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  const isPw = (el) => el instanceof HTMLInputElement && el.type === 'password';
  const texty = (el) => el instanceof HTMLInputElement && (el.type === 'text' || el.type === 'email' || el.type === 'tel');
  const userish = (el) => el.type === 'email' || /username|email/i.test(el.autocomplete)
    || USERISH.test(el.name + ' ' + el.id + ' ' + (el.getAttribute('aria-label') || '') + ' ' + el.placeholder);
  const passwords = () => [...document.querySelectorAll('input[type=password]')].filter(shown);
  const userFor = (pw) => {
    const scope = (pw && pw.form) || document;
    const named = scope.querySelector('input[autocomplete~="username" i]');  // may be hidden: a later step
    if (named && texty(named)) return named;
    if (!pw) return [...scope.querySelectorAll('input')].find((i) => texty(i) && userish(i) && shown(i)) || null;
    const before = (i) => texty(i) && shown(i) && (i.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (pw.form) return [...pw.form.querySelectorAll('input')].filter(before).pop() || null;
    for (let box = pw.parentElement, n = 0; box && box !== document.body && n < 6; box = box.parentElement, n++) {
      const found = [...box.querySelectorAll('input')].filter(before).pop();  // the nearest one, not a search box
      if (found) return found;
    }
    return null;
  };
"""

LOGIN_JS = r"""
(() => {
  const webview = window.chrome && window.chrome.webview;
  if (window.top !== window || !webview) return;
  const post = webview.postMessage.bind(webview), later = window.setTimeout.bind(window);
""" + _FIELDS_JS + r"""
  let fields = null, timer = 0, sent = '';  // null: every page reports once, even one without inputs
  const check = () => {
    timer = 0;
    if (!document.getElementsByTagName('input').length) { report(''); return; }
    report(passwords().length ? 'password' : (userFor(null) ? 'username' : ''));
  };
  const report = (now) => { if (now !== fields) { fields = now; post({lite: 'loginform', fields: now}); } };
  const soon = () => { if (!timer) timer = later(check, 500); };
  new MutationObserver(soon).observe(document, {childList: true, subtree: true, attributes: true,
    attributeFilter: ['type', 'style', 'class', 'hidden']});
  document.addEventListener('focusin', soon, true);
  window.addEventListener('load', soon);

  // What you typed, when you sign in: Lite offers to save it once the sign-in form is gone.
  const capture = () => {
    const pws = passwords().filter((p) => p.value);
    if (!pws.length) return;
    let pw = pws[0].value;
    if (pws.length > 1) {  // signing up or changing the password: the new one is the one typed twice
      const values = pws.map((p) => p.value);
      pw = values.find((v, i) => values.indexOf(v) !== i) || values[values.length - 1];
    }
    const u = userFor(pws[0]);
    const user = u ? u.value.trim() : '';
    if (user + '\n' + pw === sent) return;
    sent = user + '\n' + pw;
    post({lite: 'login', user, pw});
  };
  document.addEventListener('submit', capture, true);
  document.addEventListener('click', (e) => {
    if (e.isTrusted && e.target instanceof Element
        && e.target.closest('button, input[type=submit], input[type=image], [role=button]')) capture();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.isTrusted && e.key === 'Enter' && (isPw(e.target) || texty(e.target))) capture();
  }, true);
  // A username typed on its own step (the password comes on the next page)
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (e.isTrusted && texty(t) && userish(t) && t.value.trim()) post({lite: 'username', value: t.value.trim()});
  }, true);
})();
"""

_FILL_JS = r"""
(() => {
  if (location.origin !== %(origin)s) return 'origin';
""" + _FIELDS_JS + r"""
  const user = %(user)s, password = %(password)s;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  const put = (el, value) => {  // the way typing does it, so sites built on React and friends see it too
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const pws = passwords().filter((p) => !/new-password/i.test(p.autocomplete));
  const pw = pws.includes(document.activeElement) ? document.activeElement : (pws[0] || null);
  const u = userFor(pw);
  if (u && user) put(u, user);
  if (pw) put(pw, password);
  return u || pw ? 'filled' : 'no fields';
})()
"""


def origin(url: str) -> str:
    """'https://example.com' - the page's origin as location.origin spells it; '' for anything but http(s)."""
    try:
        p = urlsplit(url or '')
        host, port = (p.hostname or '').lower(), p.port
    except ValueError:
        return ''
    scheme = p.scheme.lower()
    if scheme not in ('http', 'https') or not host:
        return ''
    if ':' in host:
        host = f'[{host}]'  # IPv6
    default = {'http': 80, 'https': 443}[scheme]
    return f'{scheme}://{host}' + (f':{port}' if port and port != default else '')


def site_host(url: str) -> str:
    """The host a login belongs to, without 'www.': what the panels name and "Never" remembers."""
    try:
        return (urlsplit(url or '').hostname or '').lower().removeprefix('www.')
    except ValueError:
        return ''


def fill_js(page_origin: str, user: str, password: str) -> str:
    """The script that types one login into the page - and does nothing unless the page is page_origin."""
    return _FILL_JS % {'origin': json.dumps(page_origin), 'user': json.dumps(user), 'password': json.dumps(password)}
