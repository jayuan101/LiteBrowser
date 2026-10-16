"""Checks for browser/downloads.py - the antivirus scan of finished downloads.

    python scratch/test_downloads.py

The clean path uses the real antivirus through AMSI on a harmless file. The threat
path uses a stand-in AMSI that reports a detection, so no real malware (and no EICAR
test file, which would raise a real antivirus alert) is ever written.
"""
import ctypes
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from browser import downloads  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'   got={got!r} want={want!r}'))


def harmless(name='report.txt', size=4000):
    path = os.path.join(tempfile.mkdtemp(prefix='lite-dl-'), name)
    with open(path, 'wb') as f:
        f.write(b'quarterly report\n' * (size // 17 + 1))
    return path


s = downloads.Scanner()
print(f'antivirus behind AMSI on this PC: {s.name}')
if s.name:
    verdict, text = s.scan(harmless())
    check('real scan of a harmless file: clean, names the antivirus', (verdict, s.name in text), ('clean', True))
check('file gone before the scan: reported, not "clean"', s.scan(os.path.join(tempfile.gettempdir(), 'nope-lite.exe'))[0],
      'threat')

nobody = downloads.Scanner()
nobody.name = None
check('no antivirus registered: honest "unscanned"', nobody.scan(harmless())[0], 'unscanned')

old = downloads.MAX_SCAN
downloads.MAX_SCAN = 100
check('too large for the extra scan: "unscanned", not "clean"', s.scan(harmless(size=5000))[0], 'unscanned')
downloads.MAX_SCAN = old


class FakeAmsi:
    """Stands in for amsi.dll: reports a fixed result code / HRESULT."""
    def __init__(self, result, hr=0):
        self.result, self.hr = result, hr

    def AmsiOpenSession(self, ctx, session):
        return 0

    def AmsiCloseSession(self, ctx, session):
        return 0

    def AmsiScanBuffer(self, ctx, buf, length, name, session, result):
        ctypes.cast(result, ctypes.POINTER(ctypes.c_int))[0] = self.result
        return self.hr


fake = downloads.Scanner()
fake.name = 'TestAV'
fake._amsi = FakeAmsi(downloads.AMSI_RESULT_DETECTED)
path = harmless('invoice.exe')
verdict, text = fake.scan(path)
check('detection: reported as a threat', (verdict, 'TestAV found a threat' in text), ('threat', True))
check('detection: the file is deleted', os.path.exists(path), False)

fake._amsi = FakeAmsi(0x4001)  # AMSI_RESULT_BLOCKED_BY_ADMIN range
path = harmless('policy.exe')
check('blocked by admin policy: treated as a threat', fake.scan(path)[0], 'threat')

fake._amsi = FakeAmsi(1, hr=0x80070005)
check('scan error: "unscanned", never "clean"', fake.scan(harmless())[0], 'unscanned')

fake._amsi = FakeAmsi(1)
check('AMSI_RESULT_NOT_DETECTED: clean', fake.scan(harmless())[0], 'clean')

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
