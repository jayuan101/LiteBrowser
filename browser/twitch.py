"""Twitch's video ads, which the filter lists cannot reach.

Twitch stitches its ads into the stream itself: the playlist the player downloads every
two seconds lists ad clips from the same servers as the stream, so there is no ad server
to block. TWITCH_JS runs in twitch.tv pages (with Shields up) and wraps the player's
worker. When a playlist has ad clips, the worker quietly fetches the same stream as
Twitch's embedded or pop-out player sees it - which usually has no ads - and plays that
until the break is over. If every copy has ads, the ad clips are cut out of the playlist:
the video pauses for the break instead of showing it.

The wrapped Worker is a Proxy of the real one, so to the page it still reads as native
code: Twitch's bot check at sign-in must not see a tampered browser. It shares one toString
wrapper with the fingerprinting script - two stacked wrappers failed that check.
"""
import json

from . import privacy

CLIENT_ID = 'kimne78kx3ncx6brgo4mv6wki5h1ko'  # the public client id twitch.tv itself uses

# Runs first inside the player's worker; cfg = {clientId, deviceId, channel}.
_WORKER_JS = r"""
((cfg) => {
  const realFetch = self.fetch.bind(self);
  const AD = 'stitched';  // CLASS="twitch-stitched-ad" / ID="stitched-ad-..." mark the ad clips
  const TYPES = ['embed', 'popout', 'autoplay'];  // players that often get the stream without ads
  const streams = new Map();  // media playlist URL -> {channel, usher, height}
  const backups = new Map();  // channel|type -> {at, variants}
  let inAd = false, mode = '';
  let bc = null;
  try { bc = new BroadcastChannel(cfg.channel); } catch (e) {}
  const tell = (what) => { try { if (bc) bc.postMessage(what); } catch (e) {} };
  const debug = (...what) => { if (cfg.debug) tell({debug: what.map(String).join(' ')}); };

  const variants = (text, base) => {
    const out = [], lines = text.split('\n');
    for (let i = 0; i < lines.length - 1; i++) {
      if (!lines[i].startsWith('#EXT-X-STREAM-INF')) continue;
      const res = (lines[i].match(/RESOLUTION=\d+x(\d+)/) || [])[1];
      const url = lines[i + 1].trim();
      if (url && !url.startsWith('#')) out.push({height: Number(res || 0), url: new URL(url, base).href});
    }
    return out;
  };
  const pick = (list, height) => list.find((v) => v.height === height)
    || list.filter((v) => v.height && v.height < height).sort((a, b) => b.height - a.height)[0]
    || list.slice().sort((a, b) => a.height - b.height)[0] || null;

  // The ad clips out, the live ones kept (each clip is an #EXTINF line and the URL after it).
  const strip = (text) => {
    const out = [], lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.startsWith('#EXTINF') && !line.includes(',live')) {
        if (out.length && out[out.length - 1].startsWith('#EXT-X-PROGRAM-DATE-TIME')) out.pop();
        i++;  // and its URL
        continue;
      }
      if (line.startsWith('#EXT-X-TWITCH-PREFETCH') || (line.startsWith('#EXT-X-DATERANGE') && line.includes(AD))) continue;
      out.push(line);
    }
    return out.join('\n');
  };

  const token = async (channel, type) => {
    // No Device-ID: a pre-roll belongs to the device that is watching, and the backup is nobody's.
    const headers = {'Client-ID': cfg.clientId, 'Content-Type': 'text/plain;charset=UTF-8'};
    if (cfg.deviceId && cfg.sendDevice) headers['Device-ID'] = cfg.deviceId;
    const r = await realFetch('https://gql.twitch.tv/gql', {method: 'POST', headers, body: JSON.stringify({
      operationName: 'PlaybackAccessToken_Template',
      query: 'query PlaybackAccessToken_Template($login: String!, $isLive: Boolean!, $vodID: ID!, $isVod: Boolean!, $playerType: String!) {  streamPlaybackAccessToken(channelName: $login, params: {platform: "web", playerBackend: "mediaplayer", playerType: $playerType}) @include(if: $isLive) {    value    signature    __typename  }  videoPlaybackAccessToken(id: $vodID, params: {platform: "web", playerBackend: "mediaplayer", playerType: $playerType}) @include(if: $isVod) {    value    signature    __typename  }}',
      variables: {isLive: true, login: channel, isVod: false, vodID: '', playerType: type}})});
    const t = ((await r.json()).data || {}).streamPlaybackAccessToken;
    return t && t.value && t.signature ? t : null;
  };

  let retryAt = 0;  // every copy had ads: look again in a few seconds, not on every playlist refresh
  const backup = async (info) => {
    if (Date.now() < retryAt) return null;
    const found = await tryBackups(info);
    if (!found) retryAt = Date.now() + 8000;
    return found;
  };
  const tryBackups = async (info) => {
    for (const type of TYPES) {
      const key = info.channel + '|' + type;
      try {
        let b = backups.get(key);
        if (!b || Date.now() - b.at > 5 * 60 * 1000) {
          const t = await token(info.channel, type);
          if (!t) { debug('no token', type); continue; }
          const u = new URL(info.usher);
          u.searchParams.set('sig', t.signature);
          u.searchParams.set('token', t.value);
          const r = await realFetch(u.href);
          if (!r.ok) { debug('usher', r.status, type); continue; }
          b = {at: Date.now(), variants: variants(await r.text(), u.href)};
          backups.set(key, b);
        }
        const v = pick(b.variants, info.height);
        if (!v) { debug('no variant', type, info.height); continue; }
        const r = await realFetch(v.url);
        const text = r.ok ? await r.text() : '';
        if (text.startsWith('#EXTM3U') && !text.includes(AD)) { debug('backup', type, v.height); return text; }
        debug('backup has ads too', type, r.status);
      } catch (e) { debug('backup failed', type, e); }
    }
    return null;
  };

  self.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || String(input);
    if (!/\.m3u8(\?|$)/.test(url) || !/\.ttvnw\.net\//.test(url)) return realFetch(input, init);
    const response = await realFetch(input, init);
    if (!response.ok) return response;
    const text = await response.text();
    const headers = new Headers(response.headers);
    headers.delete('content-length');
    headers.delete('content-encoding');
    const answer = (body) => new Response(body, {status: response.status, statusText: response.statusText, headers});
    const master = url.match(/usher\.ttvnw\.net\/api\/(?:v\d+\/)?channel\/hls\/([^/?]+)\.m3u8/);
    if (master) {
      for (const v of variants(text, url)) streams.set(v.url, {channel: decodeURIComponent(master[1]).toLowerCase(), usher: url, height: v.height});
      debug('master', master[1], streams.size);
      return answer(text);
    }
    if (cfg.debug && !self.__liteSeen) { self.__liteSeen = true; debug('media', url.slice(0, 140), 'known:', streams.has(url)); }
    if (!text.includes(AD)) {
      if (inAd) { inAd = false; mode = ''; tell('end'); }
      return answer(text);
    }
    if (!inAd) { inAd = true; tell('ad'); }
    const info = streams.get(url);
    if (!info) debug('unknown playlist', url.slice(0, 120), [...streams.keys()].map((k) => k.slice(0, 120)).join(' , '));
    const clean = info && await backup(info);
    const now = clean ? 'swap' : 'cut';  // cut: the video waits out the break - the page says so
    if (now !== mode) { mode = now; tell(now); }
    return answer(clean || strip(text));
  };
})
"""

_PAGE_JS = r"""
(() => {
  const host = location.hostname;
  if (!(host === 'twitch.tv' || host.endsWith('.twitch.tv')) || window.__liteTwitch) return;
  Object.defineProperty(window, '__liteTwitch', {value: true});
  const webview = window.chrome && window.chrome.webview;
  const post = (m) => { try { if (webview) webview.postMessage(m); } catch (e) {} };
  const channel = 'lite-twitch-' + Math.random().toString(36).slice(2);
  // While the ad clips are cut out the picture stands still: a small note on the player says why.
  let note = null;
  const showNote = (on) => {
    if (!on) { if (note) { note.remove(); note = null; } return; }
    if (note) return;
    const player = document.querySelector('.video-player__container, [data-a-target="video-player"]');
    note = document.createElement('div');
    const box = note.attachShadow({mode: 'closed'});
    box.innerHTML = '<div style="font:600 13px/1.4 system-ui,sans-serif;color:#fff;background:rgba(16,18,22,.86);'
      + 'border:1px solid rgba(251,84,43,.6);border-radius:10px;padding:8px 12px;max-width:320px">'
      + 'Lite is skipping an ad break - the stream continues when it is over.</div>';
    note.style.cssText = 'position:absolute;top:12px;left:12px;z-index:2147483647;pointer-events:none';
    if (player) { if (getComputedStyle(player).position === 'static') player.style.position = 'relative'; player.append(note); }
    else { note.style.position = 'fixed'; document.documentElement.append(note); }
  };
  try {
    new BroadcastChannel(channel).onmessage = (e) => {
      if (e.data === 'ad') post({lite: 'twitchad'});
      else if (e.data === 'cut') showNote(true);
      else if (e.data === 'swap' || e.data === 'end') showNote(false);
      else if (e.data && e.data.debug) post({lite: 'twitchad', debug: String(e.data.debug).slice(0, 600)});
    };
  } catch (e) {}
  const cookie = (name) => (document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)')) || [])[1] || '';
  const WORKER = %(worker)s;
  const source = (url) => {  // the player's worker: a small blob that imports the real script
    const xhr = new XMLHttpRequest();
    xhr.open('GET', url, false);
    xhr.send();
    return xhr.responseText;
  };
  const RealWorker = window.Worker;
  if (!RealWorker) return;
  const LiteWorker = new Proxy(RealWorker, {
    construct(target, args, newTarget) {
      try {
        const url = new URL(String(args[0]), location.href);
        const opts = args[1];
        if (url.protocol === 'blob:' && /(^|\.)twitch\.tv$/.test(new URL(url.pathname).hostname)
            && !(opts && opts.type === 'module')) {
          const code = source(url.href);
          if (/amazon-ivs|wasmworker/i.test(code)) {
            const cfg = {clientId: %(client)s, deviceId: decodeURIComponent(cookie('unique_id')), channel, debug: %(debug)s};
            const blob = new Blob([WORKER + '(' + JSON.stringify(cfg) + ');\n' + code], {type: 'text/javascript'});
            const wrapped = Reflect.construct(target, [URL.createObjectURL(blob), opts], newTarget);
            post({lite: 'twitchad', worker: true});
            return wrapped;
          }
        }
      } catch (e) {}
      return Reflect.construct(target, args, newTarget);
    },
  });
  // A Proxy prints as "function () { [native code] }"; the real one says "function Worker() ...".
  // toString answers for the original through Lite's one shared wrapper (privacy.disguise_js):
  // a wrapper of its own on top of the fingerprinting one is what Twitch's sign-in check caught.
  const disguise = %(disguise)s;
  disguise(LiteWorker, RealWorker);
  Object.defineProperty(window, 'Worker', {...Object.getOwnPropertyDescriptor(window, 'Worker'), value: LiteWorker});
})();
"""


def page_js(debug: bool = False) -> str:
    """debug: the worker reports each step of an ad break (scratch tests read them from tab.twitch_log)."""
    return _PAGE_JS % {'worker': json.dumps(_WORKER_JS), 'client': json.dumps(CLIENT_ID),
                       'debug': 'true' if debug else 'false', 'disguise': privacy.disguise_js()}


TWITCH_JS = page_js()


def applies(host: str) -> bool:
    host = (host or '').lower()
    return host == 'twitch.tv' or host.endswith('.twitch.tv')
