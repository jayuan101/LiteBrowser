"""Checks for browser/instance.py: one Lite per profile, later starts hand their links over.

    python scratch/test_instance.py

Runs real separate processes on throwaway profile folders - never the Lite you use.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from multiprocessing import connection

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import instance  # noqa: E402

failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      {detail}'))


# An owner process: claims the profile, waits `delay` before listening, answers `answer`
# (True: links taken; False: closing) and writes every message it gets to `out`.
OWNER = r'''
import json, sys, time
sys.path.insert(0, ROOT)
from browser import instance
data, out, delay, answer, life = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4] == '1', float(sys.argv[5])
owner = instance.claim(data, [])
print('owner' if owner else 'handed', flush=True)
time.sleep(delay)
def take(urls):
    with open(out, 'a', encoding='utf-8') as f:
        f.write(json.dumps(urls) + '\n')
    return answer
print('serving', owner.serve(take), flush=True)
time.sleep(life)
'''.replace('ROOT', repr(ROOT))

tmp = tempfile.mkdtemp(prefix='lite-instance-')
script = os.path.join(tmp, 'owner.py')
with open(script, 'w', encoding='utf-8') as f:
    f.write(OWNER)


def start_owner(name, delay=0.0, answer=True, life=6.0):
    data, out = os.path.join(tmp, name), os.path.join(tmp, name + '.txt')
    p = subprocess.Popen([sys.executable, script, data, out, str(delay), '1' if answer else '0', str(life)],
                         stdout=subprocess.PIPE, text=True)
    first = p.stdout.readline().strip()
    return p, data, out, first


def received(out):
    try:
        with open(out, encoding='utf-8') as f:
            return [json.loads(line) for line in f]
    except OSError:
        return []


# 1. the plain case: a second start hands its links over and is done
p, data, out, first = start_owner('plain')
time.sleep(0.5)
t = time.monotonic()
second = instance.claim(data, ['https://a.example/', 'https://b.example/'])
took = time.monotonic() - t
check('the first start owns the profile', first == 'owner', first)
check('a second start hands its links to it', second is None, repr(second))
check('... at once', took < 2, f'{took:.2f}s')
check('... and the owner got them in order', received(out) == [['https://a.example/', 'https://b.example/']], str(received(out)))
check('a start without links still reaches it (a new tab)', instance.claim(data, []) is None and received(out)[-1] == [])

# 2. other profiles are not touched
other = instance.claim(os.path.join(tmp, 'someone-else'), [])
check('another profile folder gets its own owner', other is not None and other._mutex)
other.close()

# 3. a wrong key gets nothing: no other account can pose as Lite
try:
    c = connection.Client(instance.pipe_name(data), family='AF_PIPE', authkey=b'x' * 32)
    c.send_bytes(b'{"urls": ["https://evil.example/"]}')
    check('a wrong key is refused', False, 'connected')
except Exception as e:
    check('a wrong key is refused', type(e).__name__ in ('AuthenticationError', 'EOFError', 'OSError',
                                                          'ConnectionResetError', 'BrokenPipeError'), repr(e))
time.sleep(0.3)
check('... and its link never opened', all('https://evil.example/' not in r for r in received(out)))
p.wait(timeout=15)

# 4. after the owner exits, the next start takes over
after = instance.claim(data, ['https://c.example/'])
check('when the owner is gone, the next start owns the profile', after is not None and after._mutex)
after.close()
del after  # (the mutex handle is released when this test process exits)

# 5. an owner that is still starting: the second start waits for it to listen
p, data, out, _ = start_owner('slow', delay=2.0)
t = time.monotonic()
second = instance.claim(data, ['https://late.example/'])
took = time.monotonic() - t
check('a second start waits for an owner that is still starting', second is None and 1.5 < took < 9, f'{took:.2f}s')
check('... and its link arrives', received(out) == [['https://late.example/']], str(received(out)))
p.wait(timeout=15)

# 6. an owner that is closing (answers "closing"): the second start takes over once it exits
p, data, out, _ = start_owner('closing', answer=False, life=2.0)
time.sleep(0.5)
t = time.monotonic()
second = instance.claim(data, ['https://next.example/'])
took = time.monotonic() - t
check('a start during closing becomes the owner when the old one exits',
      second is not None and second._mutex and 1.0 < took < 9, f'{took:.2f}s')
if second:
    second.close()
p.wait(timeout=15)

# 7. an owner that never answers: give up after `wait`, open a window anyway (but do not own)
p, data, out, _ = start_owner('stuck', delay=30, life=1)
t = time.monotonic()
second = instance.claim(data, ['https://lost.example/'], wait=2)
took = time.monotonic() - t
check('an owner that never answers: the link opens in a window of its own',
      second is not None and not second._mutex and second.serve(lambda u: True) is False and took < 5, f'{took:.2f}s')
p.kill()
p.wait()

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
