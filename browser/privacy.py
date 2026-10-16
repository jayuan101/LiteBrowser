"""Brave-style privacy features that are not ad blocking.

- Tracking-link cleaning: click identifiers such as fbclid and gclid let ad
  networks follow you from one site to the next. They are removed from the
  address before a page is opened (Brave calls this query filtering).
- Global Privacy Control: every request says "do not sell or share my data"
  (the Sec-GPC header) and pages can read navigator.globalPrivacyControl.
  California and Colorado treat this signal as a legally binding opt-out.
- Fingerprinting protection, the way Brave does it ("farbling"): the values
  trackers hash to recognise a PC without cookies - canvas pixels, audio
  samples, WebGL pixels, the CPU core count - get a tiny, invisible change.
  The change is random, but the same for one site for the whole session, so
  a site cannot tell anything is off, yet every site (and every restart) sees
  a different fingerprint and cannot match them up.
"""
import random
from urllib.parse import unquote_plus, urlsplit, urlunsplit

# Cross-site click and email identifiers. Campaign tags like utm_source are left alone:
# they name the campaign, not the person, and sites use them for referral credit.
TRACKING_PARAMS = frozenset("""
fbclid gclid gclsrc gbraid wbraid dclid msclkid yclid ymclid ysclid twclid ttclid li_fat_id igshid
epik srsltid mc_eid _hsenc __hsfp __hssc __hstc hsCtaTracking mkt_tok oly_anon_id oly_enc_id
rb_clickid s_cid vero_conv vero_id wickedid _openstat ml_subscriber ml_subscriber_hash ss_email_id
bsft_clkid bsft_uid irclickid _branch_match_id _branch_referrer unicorn_click_id at_recipient_id
at_recipient_list sc_customer sc_eh sc_uid et_rid guce_referrer guce_referrer_sig _kx
""".split())

GPC_JS = """
(() => {
  if ('globalPrivacyControl' in Navigator.prototype) return;
  try {
    Object.defineProperty(Navigator.prototype, 'globalPrivacyControl',
      {get() { return true; }, configurable: true, enumerable: true});
  } catch (e) {}
})();
"""


# Registered per tab and run at document start in every frame. `%d` is the session key.
# The per-site seed comes from the key and the top-level site, so frames inside a page
# see the same values as the page itself (as Brave does) and a different site sees others.
_FINGERPRINT_JS = r"""
(() => {
  if (window.__liteFarbled) return;
  Object.defineProperty(window, '__liteFarbled', {value: true});
  const KEY = %d;
  let top = location.hostname;
  try { const a = location.ancestorOrigins; if (a && a.length) top = new URL(a[a.length - 1]).hostname; } catch (e) {}
  const labels = top.split('.');
  const site = labels.slice(labels.length > 2 && labels[labels.length - 2].length <= 3 ? -3 : -2).join('.');
  let seed = 2166136261 ^ KEY;  // FNV-1a of the site name, mixed with the session key
  for (let i = 0; i < site.length; i++) { seed ^= site.charCodeAt(i); seed = Math.imul(seed, 16777619); }
  const prng = (s) => { s = (s ^ seed) >>> 0 || 0x9e3779b9; return () => {
    s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return (s >>> 0) / 4294967296; }; };
  const fudge = 0.99995 + prng(0xa0d10)() * 0.0001;  // audio: samples scaled by up to 1/20000 - inaudible

  const webview = window.chrome && window.chrome.webview;
  let reported = false;
  const report = (what) => {
    if (reported || !webview) return;
    reported = true;
    try { webview.postMessage({lite: 'fingerprint', what, href: location.href}); } catch (e) {}
  };

  // Wrapped functions still read as native code, so pages do not notice the wrapping.
  const natives = new WeakMap();
  const nativeToString = Function.prototype.toString;
  Function.prototype.toString = function () {
    return nativeToString.call(natives.get(this) || this);
  };
  natives.set(Function.prototype.toString, nativeToString);
  const wrap = (proto, name, make) => {
    if (!proto) return;
    const desc = Object.getOwnPropertyDescriptor(proto, name);
    if (!desc || typeof desc.value !== 'function') return;
    const original = desc.value;
    const wrapped = make(original);
    Object.defineProperty(wrapped, 'name', {value: name, configurable: true});
    natives.set(wrapped, original);
    Object.defineProperty(proto, name, {...desc, value: wrapped});
  };

  // ---- canvas: flip the lowest bit of one colour channel in about 1 in 20 pixels ----
  const MAX_PIXELS = 16 * 1024 * 1024;
  const farblePixels = (data) => {
    if (!data || data.length > MAX_PIXELS * 4) return;
    const r = prng(data.length);
    for (let i = 0; i < data.length; i += 4) {
      if (r() < 0.05) data[i + ((r() * 3) | 0)] ^= 1;
    }
  };
  const getContext = HTMLCanvasElement.prototype.getContext;
  const getImageData = CanvasRenderingContext2D.prototype.getImageData;
  const copyFarbled = (canvas) => {  // a farbled copy to export from, so the real canvas is untouched
    if (!canvas.width || !canvas.height || canvas.width * canvas.height > MAX_PIXELS) return canvas;
    const copy = document.createElement('canvas');
    copy.width = canvas.width;
    copy.height = canvas.height;
    const ctx = getContext.call(copy, '2d');
    ctx.drawImage(canvas, 0, 0);
    const image = getImageData.call(ctx, 0, 0, copy.width, copy.height);
    farblePixels(image.data);
    ctx.putImageData(image, 0, 0);
    return copy;
  };
  wrap(CanvasRenderingContext2D.prototype, 'getImageData', (orig) => function (...args) {
    const image = orig.apply(this, args);
    report('canvas');
    farblePixels(image && image.data);
    return image;
  });
  wrap(HTMLCanvasElement.prototype, 'toDataURL', (orig) => function (...args) {
    report('canvas');
    try { return orig.apply(copyFarbled(this), args); } catch (e) { return orig.apply(this, args); }
  });
  wrap(HTMLCanvasElement.prototype, 'toBlob', (orig) => function (cb, ...args) {
    report('canvas');
    try { return orig.call(copyFarbled(this), cb, ...args); } catch (e) { return orig.call(this, cb, ...args); }
  });
  if (window.OffscreenCanvasRenderingContext2D) {
    wrap(OffscreenCanvasRenderingContext2D.prototype, 'getImageData', (orig) => function (...args) {
      const image = orig.apply(this, args);
      report('canvas');
      farblePixels(image && image.data);
      return image;
    });
  }

  // ---- WebGL: the pixels read back get the same treatment ----
  for (const gl of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!gl) continue;
    wrap(gl.prototype, 'readPixels', (orig) => function (...args) {
      const result = orig.apply(this, args);
      report('webgl');
      const pixels = args[6];
      if (pixels && pixels.BYTES_PER_ELEMENT === 1) farblePixels(pixels);
      return result;
    });
  }

  // ---- audio: every sample scaled by the site's fudge factor ----
  const farbledArrays = new WeakSet();
  const farbleSamples = (arr) => {
    if (!arr || farbledArrays.has(arr)) return;
    farbledArrays.add(arr);
    if (arr instanceof Float32Array) for (let i = 0; i < arr.length; i++) arr[i] *= fudge;
    else { const r = prng(arr.length); for (let i = 0; i < arr.length; i++) if (r() < 0.05) arr[i] ^= 1; }
  };
  if (window.AudioBuffer) {
    wrap(AudioBuffer.prototype, 'getChannelData', (orig) => function (...args) {
      const data = orig.apply(this, args);
      report('audio');
      farbleSamples(data);
      return data;
    });
    wrap(AudioBuffer.prototype, 'copyFromChannel', (orig) => function (dest, ...args) {
      const result = orig.call(this, dest, ...args);
      report('audio');
      farbledArrays.delete(dest);
      farbleSamples(dest);
      return result;
    });
  }
  if (window.AnalyserNode) {
    for (const name of ['getFloatFrequencyData', 'getByteFrequencyData', 'getFloatTimeDomainData', 'getByteTimeDomainData']) {
      wrap(AnalyserNode.prototype, name, (orig) => function (arr, ...args) {
        const result = orig.call(this, arr, ...args);
        report('audio');
        farbledArrays.delete(arr);
        farbleSamples(arr);
        return result;
      });
    }
  }

  // ---- hardware: the core count is somewhere between 2 and the real number ----
  const cores = Object.getOwnPropertyDescriptor(Navigator.prototype, 'hardwareConcurrency');
  if (cores && cores.get) {
    const real = cores.get.call(navigator) || 2;
    const fake = 2 + Math.floor(prng(0xc0de5)() * (real - 1));
    const getter = function hardwareConcurrency() { return fake; };
    natives.set(getter, cores.get);
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {...cores, get: getter});
  }
})();
"""


def new_session_key() -> int:
    """A fresh fingerprinting key: one per App for normal tabs and another for private tabs."""
    return random.getrandbits(31)


def fingerprint_js(session_key: int) -> str:
    return _FINGERPRINT_JS % session_key


def strip_tracking(url: str) -> str:
    """The URL without tracking parameters; everything else is kept byte for byte."""
    parts = urlsplit(url)
    if not parts.query or parts.scheme not in ('http', 'https'):
        return url
    fields = parts.query.split('&')
    kept = [f for f in fields if unquote_plus(f.split('=', 1)[0]) not in TRACKING_PARAMS]
    if len(kept) == len(fields):
        return url
    return urlunsplit(parts._replace(query='&'.join(kept)))
