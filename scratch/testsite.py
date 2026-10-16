"""A tiny local site for testing Lite's protections without depending on real websites.

    python scratch/testsite.py [PORT]      (default 8765)

/            page with generic ad containers, a tracker image, a pop-up on load and one on click
/echo        the request headers it received, as JSON (Sec-GPC check)
/file.txt    a harmless text download
/tool.exe    a harmless file with a program's extension (triggers Lite's download warning)
"""
import http.server
import json
import sys

PAGE = """<!doctype html><html><head><title>Lite test site</title></head><body style="font:16px sans-serif">
<h1>Lite test site</h1>
<div class="ADBox" id="t-class" style="height:60px;background:#fc0">generic ad class (EasyList ##.ADBox)</div>
<div id="ad-banner-atf" style="height:60px;background:#fc0">generic ad id (EasyList ###ad-banner-atf)</div>
<div class="article-body" id="t-content">ordinary content - must stay visible</div>
<img id="t-tracker" src="https://www.google-analytics.com/collect?v=1&t=pageview" width="1" height="1">
<button id="t-popup">open pop-up (click)</button>
<a id="t-link" href="/echo?keep=1&fbclid=IwAR0test&gclid=abc">link with tracking parameters</a>
<a id="t-dl" href="/file.txt">download text</a> <a id="t-exe" href="/tool.exe">download tool.exe</a>
<script>
window.__results = {};
setTimeout(() => { window.__results.autoPopup = window.open('/echo?popup=auto') ? 'opened' : 'blocked'; }, 300);
document.getElementById('t-popup').onclick = () => {
  window.__results.clickPopup = window.open('/echo?popup=click') ? 'opened' : 'blocked';
};
window.__check = () => {
  const shown = (id) => getComputedStyle(document.getElementById(id)).display !== 'none';
  return JSON.stringify({...window.__results, gpc: navigator.globalPrivacyControl,
    classHidden: !shown('t-class'), idHidden: !shown('ad-banner-atf'), contentVisible: shown('t-content')});
};
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, extra=None):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/':
            self._send(PAGE.encode(), 'text/html')
        elif path == '/echo':
            self._send(json.dumps({'path': self.path, 'headers': dict(self.headers)}, indent=1).encode(), 'application/json')
        elif path == '/file.txt':
            self._send(b'hello from the Lite test site\n' * 100, 'text/plain',
                       {'Content-Disposition': 'attachment; filename="lite-test.txt"'})
        elif path == '/tool.exe':
            self._send(b'This is not a real program, just a test file with an .exe name.\n' * 50,
                       'application/octet-stream', {'Content-Disposition': 'attachment; filename="lite-test-tool.exe"'})
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print('testsite:', getattr(self, 'path', fmt % args), flush=True)  # no path: e.g. an HTTPS attempt


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    host = sys.argv[2] if len(sys.argv) > 2 else '127.0.0.1'  # 127.0.0.2: a raw IP Lite warns about
    http.server.ThreadingHTTPServer((host, port), Handler).serve_forever()
