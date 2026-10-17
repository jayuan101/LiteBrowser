"""Offline checks for browser/twitch.py: the player-worker code runs in node against a fake Twitch.

    python scratch/test_twitch.py

The fake serves a master playlist, a media playlist with stitched ad clips, access tokens
for the backup player types, and backup playlists with or without ads - no network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import filters, privacy, twitch  # noqa: E402

failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      {detail}'))


HARNESS = r"""
const W = 'https://video-weaver.fra05.hls.ttvnw.net/v1/playlist/';
const master = (prefix) => '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,FRAME-RATE=60.000\n'
  + W + prefix + '1080.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=852x480\n' + W + prefix + '480.m3u8\n';
const live = (n) => `#EXT-X-PROGRAM-DATE-TIME:2026-10-16T12:00:0${n}Z\n#EXTINF:2.000,live\nhttps://edge.ttvnw.net/live${n}.ts\n`;
const ad = (n) => `#EXT-X-PROGRAM-DATE-TIME:2026-10-16T12:00:0${n}Z\n#EXTINF:2.000,Amazon|12345\nhttps://edge.ttvnw.net/ad${n}.ts\n`;
const head = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:100\n';
const ADTAG = '#EXT-X-DATERANGE:ID="stitched-ad-1",CLASS="twitch-stitched-ad",START-DATE="2026-10-16T12:00:00Z",DURATION=30\n';
let backupHasAds = false, requests = [];
const routes = (url, init) => {
  requests.push(url);
  if (url.startsWith('https://gql.twitch.tv/gql')) {
    const body = JSON.parse(init.body);
    if (init.headers['Client-ID'] !== CLIENT || body.variables.login !== 'somechannel') return {status: 400, text: '{}'};
    return {status: 200, text: JSON.stringify({data: {streamPlaybackAccessToken: {value: 'tok-' + body.variables.playerType, signature: 'sig-' + body.variables.playerType}}})};
  }
  if (url.includes('usher.ttvnw.net')) {
    const sig = new URL(url).searchParams.get('sig');
    return {status: 200, text: master(sig === 'orig' ? 'orig' : sig.replace('sig-', ''))};
  }
  if (url === W + 'orig1080.m3u8') return {status: 200, text: head + ADTAG + ad(1) + ad(2) + '#EXT-X-TWITCH-PREFETCH:https://edge.ttvnw.net/ad3.ts\n' + live(3)};
  if (url === W + 'clean1080.m3u8') return {status: 200, text: head + live(1) + live(2)};
  if (url.startsWith(W)) return {status: 200, text: backupHasAds ? head + ADTAG + ad(1) : head + live(7) + live(8)};
  return {status: 404, text: ''};
};
globalThis.self = globalThis;
self.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : input.url;
  const r = routes(url, init || {});
  return new Response(r.text, {status: r.status, headers: {'content-type': 'application/vnd.apple.mpegurl', 'content-length': String(r.text.length)}});
};
const told = [];
globalThis.BroadcastChannel = class { constructor(name) { this.name = name; } postMessage(m) { told.push(m); } };
const CLIENT = CLIENT_ID_VALUE;
(WORKER)({clientId: CLIENT, deviceId: 'dev1', channel: 'lite-twitch-test'});

(async () => {
  const out = {};
  await self.fetch('https://usher.ttvnw.net/api/v2/channel/hls/somechannel.m3u8?sig=orig&token=t&allow_source=true');
  out.clean = await (await self.fetch(W + 'clean1080.m3u8')).text();
  requests = [];
  out.backup = await (await self.fetch(W + 'orig1080.m3u8')).text();
  out.backupRequests = requests.slice();
  requests = [];
  out.backupAgain = await (await self.fetch(W + 'orig1080.m3u8')).text();
  out.backupAgainRequests = requests.slice();
  backupHasAds = true;
  out.stripped = await (await self.fetch(W + 'orig1080.m3u8')).text();
  out.afterAd = await (await self.fetch(W + 'clean1080.m3u8')).text();
  out.other = (await (await self.fetch('https://example.com/x.m3u8')).status);
  out.told = told;
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({error: String(e && e.stack || e)})); });
"""

NODE = shutil.which('node')
if not NODE:
    print('SKIP  node is not installed')
    sys.exit(0)
path = os.path.join(tempfile.mkdtemp(prefix='lite-twitch-'), 'harness.js')
with open(path, 'w', encoding='utf-8') as f:
    f.write(HARNESS.replace('(WORKER)', twitch._WORKER_JS.strip()).replace('CLIENT_ID_VALUE', json.dumps(twitch.CLIENT_ID)))
r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
try:
    out = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    out = {'error': r.stdout + r.stderr}
check('the worker code runs', 'error' not in out, out.get('error', '')[:600])
if 'error' not in out:
    check('a playlist without ads passes through untouched', out['clean'].count(',live') == 2 and 'Amazon' not in out['clean'])
    check('during an ad break the ad-free backup stream is played', 'live7.ts' in out['backup'] and 'ad1.ts' not in out['backup'],
          out['backup'])
    check('... asking for the embed player first', any('gql.twitch.tv' in u for u in out['backupRequests'])
          and any('sig=sig-embed' in u for u in out['backupRequests']), str(out['backupRequests']))
    check('... at the same quality (1080p)', any(u.endswith('/embed1080.m3u8') for u in out['backupRequests']))
    check('the backup token is reused for the rest of the break',
          not any('gql.twitch.tv' in u or 'usher' in u for u in out['backupAgainRequests']), str(out['backupAgainRequests']))
    st = out['stripped']
    check('when every copy has ads, the ad clips are cut out', 'ad1.ts' not in st and 'ad2.ts' not in st
          and 'ad3.ts' not in st and 'live3.ts' in st and 'stitched' not in st, st)
    check('... with their timestamps, keeping the playlist header', st.startswith('#EXTM3U') and st.count('PROGRAM-DATE-TIME') == 1, st)
    check('the page hears about the break once, how it was handled, and its end',
          out['told'] == ['ad', 'swap', 'cut', 'end'], str(out['told']))
    check('other sites\' playlists are left alone', out['other'] == 404)

check('the page script parses and wraps only the Twitch player worker',
      'new Proxy(RealWorker' in twitch.TWITCH_JS and 'amazon-ivs|wasmworker' in twitch.TWITCH_JS)

# Twitch's sign-in check refused Lite ("Your browser is not currently supported") while this
# script and the fingerprinting one each stacked a toString wrapper on the page. Both real
# scripts run here, in either order, against a stand-in for the page's built-ins.
SHARED = r"""
Error.stackTraceLimit = 100;
const vm = require('vm');
const nativeTS = Function.prototype.toString;
const foreignTS = vm.runInNewContext('Function.prototype.toString');  // another realm's, untouched
const depth = (ts) => { try { ts.call({}); } catch (e) { return e.stack.split('\n').length; } return -1; };
const base = depth(nativeTS);
const put = (name, value) => {
  try { Object.defineProperty(globalThis, name, {value, configurable: true, writable: true}); } catch (e) { globalThis[name] = value; }
};
class HTMLCanvasElement { getContext() { return null; } toDataURL() { return 'data:,'; } toBlob() {} }
class CanvasRenderingContext2D { getImageData() { return {data: new Uint8ClampedArray(4)}; } }
class Navigator { get hardwareConcurrency() { return 8; } }
put('window', globalThis);
put('location', {hostname: 'www.twitch.tv', href: 'https://www.twitch.tv/login', ancestorOrigins: []});
put('HTMLCanvasElement', HTMLCanvasElement);
put('CanvasRenderingContext2D', CanvasRenderingContext2D);
put('Navigator', Navigator);
put('navigator', new Navigator());
put('document', {createElement: () => new HTMLCanvasElement(), querySelector: () => null, cookie: ''});
put('Worker', function Worker() {});
put('BroadcastChannel', class { constructor() {} });
const origWorker = Worker, origToDataURL = HTMLCanvasElement.prototype.toDataURL;
const origCores = Object.getOwnPropertyDescriptor(Navigator.prototype, 'hardwareConcurrency').get;
__SCRIPTS__
const ts = Function.prototype.toString;
const cores = Object.getOwnPropertyDescriptor(Navigator.prototype, 'hardwareConcurrency').get;
let constructible = true;
try { Reflect.construct(String, [], ts); } catch (e) { constructible = false; }
const f = function plain() { return 1; }, g = function other() {};
const wrongKey = ts.call(f, 'not-the-key', g);
console.log(JSON.stringify({
  layers: depth(ts) - base,
  worker: String(window.Worker) === nativeTS.call(origWorker) && window.Worker !== origWorker,
  canvas: String(HTMLCanvasElement.prototype.toDataURL) === nativeTS.call(origToDataURL)
    && HTMLCanvasElement.prototype.toDataURL !== origToDataURL,
  cores: String(cores) === nativeTS.call(origCores) && cores !== origCores,
  self: [ts.name, ts.length, constructible, Object.prototype.hasOwnProperty.call(ts, 'prototype'), String(ts)],
  wrongKey: wrongKey === nativeTS.call(f) && String(f) === nativeTS.call(f),
  keyHidden: !foreignTS.call(ts).includes(__KEY__),
}));
"""
fp_js = privacy.fingerprint_js(privacy.new_session_key())
for order, scripts in (('fingerprinting first', (fp_js, twitch.TWITCH_JS)),
                       ('Twitch script first', (twitch.TWITCH_JS, fp_js))):
    path = os.path.join(tempfile.mkdtemp(prefix='lite-twitch-'), 'shared.js')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(SHARED.replace('__KEY__', json.dumps(privacy._DISGUISE_KEY)).replace('__SCRIPTS__', ';\n'.join(scripts)))
    r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = {'error': (r.stdout + r.stderr)[-600:]}
    check(f'{order}: both scripts ran', 'error' not in got, got.get('error', ''))
    if 'error' in got:
        continue
    check(f'{order}: one toString wrapper on the page, not two', got['layers'] == 1, str(got['layers']))
    check(f'{order}: the wrapped Worker, canvas export and core count all print as the originals',
          got['worker'] and got['canvas'] and got['cores'], str(got))
    check(f'{order}: toString itself looks native (name, length, not a constructor)',
          got['self'] == ['toString', 0, False, False, 'function toString() { [native code] }'], str(got['self']))
    check(f'{order}: registering needs the session key, which the wrapper does not show',
          got['wrongKey'] and got['keyHidden'], str(got))
check('fingerprinting protection stays off on Twitch (its sign-in check refuses it) and on elsewhere',
      not privacy.farbles('www.twitch.tv') and not privacy.farbles('twitch.tv') and not privacy.farbles('passport.twitch.tv')
      and privacy.farbles('www.example.com') and privacy.farbles('nottwitch.tv') and privacy.farbles('twitch.tv.evil.example'))
check('it only runs on twitch.tv',twitch.applies('www.twitch.tv') and twitch.applies('player.twitch.tv')
      and not twitch.applies('nottwitch.tv') and not twitch.applies('twitch.tv.evil.example'))
f = filters.Filters.__new__(filters.Filters)  # just the scriptlet part, no engine needed
f._add, f._except, f._names, f._code, f._scripts = {}, {}, {}, [], {}
check('twitch.tv gets the fix as a site script (and it counts as a fix)',
      twitch.TWITCH_JS in f.scriptlet_script('www.twitch.tv') and f.fixes('www.twitch.tv') == 1
      and f.scriptlet_script('www.example.com') == '')

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
