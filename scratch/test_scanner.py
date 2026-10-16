"""Offline checks for browser/scanner.py - no API key, no network.

Every HTTP call the real anthropic SDK makes is answered by an httpx MockTransport,
so this verifies the exact request body and how each documented Fable 5.1 response
shape (normal, refusal, fallback-served, max_tokens, HTTP error) is handled.

    python scratch/test_scanner.py
"""
import json
import os
import sys
import tempfile

import anthropic
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from browser import scanner as sc  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'   got={got!r} want={want!r}'))


def message(content, stop_reason='end_turn', model='claude-fable-5-1', **extra):
    return {'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': model, 'content': content,
            'stop_reason': stop_reason, 'stop_sequence': None,
            'usage': {'input_tokens': 1400, 'output_tokens': 90}, **extra}


THINKING = {'type': 'thinking', 'thinking': '', 'signature': 'sig'}  # display "omitted" is the default


def text(verdict):
    return {'type': 'text', 'text': json.dumps(verdict)}


requests, replies = [], []


def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    status, body = replies.pop(0)
    return httpx.Response(status, json=body)


def page(origin):
    return {'origin': origin, 'url': origin + '/login', 'title': 'Sign in', 'https': True,
            'text': 'Verify your account now', 'forms': [], 'script_hosts': [], 'iframes': 0,
            'hidden_iframes': 0, 'external_links': 3}


cache_path = os.path.join(tempfile.mkdtemp(prefix='lite-scan-'), 'scan_cache.json')
s = sc.FableScanner({'anthropic_api_key': 'sk-test-not-a-real-key'}, cache_path)
s._client = anthropic.Anthropic(api_key='sk-test-not-a-real-key', max_retries=0,
                                http_client=httpx.Client(transport=httpx.MockTransport(handler)))

# 1. normal verdict: thinking block (empty text) + JSON text block
replies.append((200, message([THINKING, text({'risk': 'dangerous', 'reason': 'PayPal login on a non-PayPal domain'})])))
r = s.scan(page('https://paypa1-login.example'))
check('normal: verdict parsed past the thinking block', (r['risk'], r['reason']),
      ('dangerous', 'PayPal login on a non-PayPal domain'))

req = requests[-1]
body = json.loads(req.content)
check('request goes to /v1/messages (beta)', (req.method, req.url.path, req.url.params.get('beta')),
      ('POST', '/v1/messages', 'true'))
check('fallback beta header sent', req.headers.get('anthropic-beta'), 'server-side-fallback-2026-07-01')
check('model id', body['model'], 'claude-fable-5-1')
check('fallbacks="default"', body.get('fallbacks'), 'default')
check('effort low', body.get('output_config'), {'effort': 'low'})
check('max_tokens leaves room for thinking', body['max_tokens'], 1024)
check('no thinking / sampling params (400 on Fable 5.1)',
      [k for k in ('thinking', 'temperature', 'top_p', 'top_k') if k in body], [])
check('no assistant prefill', [m['role'] for m in body['messages']], ['user'])
check('system prompt marks page data as untrusted', 'untrusted data' in body['system'], True)
check('key only in header, never in body', ('sk-test' in req.content.decode(), req.headers.get('x-api-key')),
      (False, 'sk-test-not-a-real-key'))

# 2. cached verdict: no second HTTP call, 7-day TTL
n = len(requests)
r = s.scan(page('https://paypa1-login.example'))
check('cache hit: no new request', (len(requests) - n, r.get('cached')), (0, True))
s._cache['https://paypa1-login.example']['ts'] -= 6 * 24 * 3600
check('verdict still cached after 6 days', s.cached('https://paypa1-login.example') is not None, True)
s._cache['https://paypa1-login.example']['ts'] -= 2 * 24 * 3600
check('verdict expires after 7 days', s.cached('https://paypa1-login.example'), None)

# 3. refusal: HTTP 200, empty content, stop_details.category
replies.append((200, message([], stop_reason='refusal',
                             stop_details={'type': 'refusal', 'category': 'cyber', 'explanation': None})))
r = s.scan(page('https://declined.example'))
check('refusal: unknown, says why', (r['risk'], r['reason']),
      ('unknown', 'Claude declined to rate this page (cyber)'))
check('refusal: cached briefly (no re-bill on every visit)', s.cached('https://declined.example') is not None, True)
s._cache['https://declined.example']['ts'] -= 2 * 3600
check('refusal: retried after an hour, not a week', s.cached('https://declined.example'), None)

replies.append((200, message([], stop_reason='refusal', stop_details=None)))
r = s.scan(page('https://declined2.example'))
check('refusal with null stop_details', r['reason'], 'Claude declined to rate this page (no category)')

# 4. fallback served the answer: fallback block precedes the text
replies.append((200, message([{'type': 'fallback', 'from': {'model': 'claude-fable-5-1'}, 'to': {'model': 'claude-opus-4-8'}},
                              text({'risk': 'suspicious', 'reason': 'Urgent payment demand'})], model='claude-opus-4-8')))
r = s.scan(page('https://rescued.example'))
check('fallback-served verdict parsed', (r['risk'], r['reason']), ('suspicious', 'Urgent payment demand'))

# 5. ran out of tokens while thinking (the old max_tokens=200 failure mode)
replies.append((200, message([THINKING], stop_reason='max_tokens')))
r = s.scan(page('https://truncated.example'))
check('max_tokens: unknown, not a fake verdict', r, {'risk': 'unknown', 'reason': 'Scanner returned no verdict'})
s._cache['https://truncated.example']['ts'] -= 2 * 3600
check('max_tokens: retried after an hour', s.cached('https://truncated.example'), None)

# 6. HTTP error: never crashes the browser, never cached
replies.append((400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'bad request'}}))
r = s.scan(page('https://error.example'))
check('HTTP 400: unknown with error type', (r['risk'], r['reason'].startswith('Scan failed: BadRequestError')),
      ('unknown', True))
check('HTTP 400: not cached', s.cached('https://error.example'), None)

# 7. cache file on disk round-trips
s2 = sc.FableScanner({'anthropic_api_key': 'x'}, cache_path)
check('cache persisted to disk', s2.cached('https://rescued.example')['risk'], 'suspicious')

# 8. scanning off
off = sc.FableScanner({}, cache_path + '.off')
check('no key: disabled' if not os.environ.get('ANTHROPIC_API_KEY') else 'no key: (env key set, skipped)',
      off.enabled if not os.environ.get('ANTHROPIC_API_KEY') else False, False)

# 9. verdict parser
for raw, want in [('{"risk":"SAFE","reason":"ok"}', 'safe'),
                  ('Sure! {"risk": "dangerous", "reason": "x"} done', 'dangerous'),
                  ('{"risk": "maybe"}', 'unknown'), ('{bad json}', 'unknown'), ('', 'unknown')]:
    check(f'parse {raw[:30]!r}', sc._parse_verdict(raw)['risk'], want)

check('all canned replies consumed', replies, [])
print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
