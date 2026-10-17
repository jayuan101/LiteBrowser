"""Checks for browser/extensions.py: Web Store links, .crx signatures, unpacking, the store file.

    python scratch/test_extensions.py

Offline: it makes its own signed .crx with a fresh RSA key. With a connection it also
fetches the real FrankerFaceZ from Google's update service and checks that one.
"""
import base64
import hashlib
import io
import json
import os
import random
import struct
import sys
import tempfile
import urllib.error
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from browser import extensions as ex  # noqa: E402

failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f'{"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'\n      {detail}'))


# ---- a tiny RSA + CRX3 writer, just for the test ------------------------------------
def _prime(bits):
    while True:
        n = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(n % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)) and all(
                pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)):
            return n


def _der(tag, body):
    n = len(body)
    length = bytes([n]) if n < 128 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return bytes([tag]) + length + body


def _int(v):
    b = v.to_bytes((v.bit_length() + 8) // 8, 'big')
    return _der(0x02, b)


def rsa_key():
    e = 65537
    while True:
        p, q = _prime(1024), _prime(1024)
        n, phi = p * q, (p - 1) * (q - 1)
        if p != q and n.bit_length() == 2048 and phi % e:
            d = pow(e, -1, phi)
            spki = _der(0x30, _der(0x30, _der(0x06, bytes.fromhex('2a864886f70d010101')) + _der(0x05, b''))
                        + _der(0x03, b'\x00' + _der(0x30, _int(n) + _int(e))))
            return n, d, spki


def sign(n, d, data):
    k = (n.bit_length() + 7) // 8
    digest = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(data).digest()
    em = b'\x00\x01' + b'\xff' * (k - len(digest) - 3) + b'\x00' + digest
    return pow(int.from_bytes(em, 'big'), d, n).to_bytes(k, 'big')


def pb(field, value):  # one length-delimited protobuf field
    key, out = (field << 3) | 2, b''
    for v in (key, len(value)):
        while True:
            out += bytes([(v & 0x7F) | (0x80 if v > 0x7F else 0)])
            v >>= 7
            if not v:
                break
    return out + value


def crx(archive, n, d, spki, crx_id=None, tamper=False):
    ext_id = ex.id_of_key(spki)
    raw_id = crx_id or bytes((ord(ext_id[i]) - 97) << 4 | (ord(ext_id[i + 1]) - 97) for i in range(0, 32, 2))
    signed = pb(1, raw_id)
    signature = sign(n, d, b'CRX3 SignedData\x00' + struct.pack('<I', len(signed)) + signed + archive)
    header = pb(2, pb(1, spki) + pb(2, signature)) + pb(10000, signed)
    return b'Cr24' + struct.pack('<II', 3, len(header)) + header + (archive[:-1] + b'X' if tamper else archive)


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


# ---- Web Store links --------------------------------------------------------------
FFZ = 'fadndhdgpmmaapbmfcknlfgcflmmmieb'
check('link: new Web Store', ex.parse_id(f'https://chromewebstore.google.com/detail/frankerfacez/{FFZ}?hl=en') == FFZ)
check('link: old Web Store', ex.parse_id(f'https://chrome.google.com/webstore/detail/frankerfacez/{FFZ}') == FFZ)
check('link: a bare ID, any case', ex.parse_id(FFZ.upper()) == FFZ)
check('link: nothing to find', ex.parse_id('https://example.com/detail/x') == '' and ex.parse_id('') == '')
check('link: 33 letters is not an ID', ex.parse_id('a' * 33) == '')

# ---- signatures ----------------------------------------------------------------------
n, d, spki = rsa_key()
ext_id = ex.id_of_key(spki)
manifest = {'manifest_version': 3, 'name': '__MSG_appName__', 'version': '1.2.3', 'default_locale': 'en',
            'action': {'default_popup': 'popup.html'}, 'options_ui': {'page': 'opt.html'}}
archive = zip_of({'manifest.json': json.dumps(manifest), '_locales/en/messages.json': json.dumps({'appName': {'message': 'Test Ext'}}),
                  'popup.html': '<p>hi</p>'})
good = crx(archive, n, d, spki)
got, key = ex.open_crx(good, ext_id)
check('a signed .crx opens, and gives back its zip and key', got == archive and key == spki)
for label, blob, want in (('a changed archive', crx(archive, n, d, spki, tamper=True), ext_id),
                          ('another extension ID', good, 'a' * 32),
                          ('a header claiming another ID', crx(archive, n, d, spki, crx_id=b'\x00' * 16), '\x61' * 32),
                          ('not a .crx at all', b'PK\x03\x04' + archive, ext_id),
                          ('an old CRX2 file', b'Cr24' + struct.pack('<II', 2, 0) + archive, ext_id),
                          ('a cut-off file', good[:40], ext_id)):
    try:
        ex.open_crx(blob, want)
        check(f'refused: {label}', False, 'accepted')
    except ValueError as e:
        check(f'refused: {label}', True, str(e))
n2, d2, spki2 = rsa_key()
forged = crx(archive, n2, d2, spki2, crx_id=bytes((ord(ext_id[i]) - 97) << 4 | (ord(ext_id[i + 1]) - 97) for i in range(0, 32, 2)))
try:
    ex.open_crx(forged, ext_id)
    check("refused: signed with someone else's key under this ID", False, 'accepted')
except ValueError:
    check("refused: signed with someone else's key under this ID", True)

# ---- unpacking ------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix='lite-ext-')
info = ex.unpack(archive, os.path.join(tmp, 'one'), key)
check('unpack: name from the locale, version, pop-up and options',
      info == {'name': 'Test Ext', 'version': '1.2.3', 'popup': 'popup.html', 'options': 'opt.html'}, str(info))
with open(os.path.join(tmp, 'one', 'manifest.json'), encoding='utf-8') as f:
    written = json.load(f)
check('unpack: the store key goes into the manifest (so the ID stays the store ID)',
      base64.b64decode(written['key']) == spki and ex.id_of_key(base64.b64decode(written['key'])) == ext_id)
try:
    ex.unpack(zip_of({'../evil.txt': 'x', 'manifest.json': '{}'}), os.path.join(tmp, 'two'))
    check('unpack: a path outside the folder is refused', False, 'accepted')
except ValueError:
    check('unpack: a path outside the folder is refused', not os.path.exists(os.path.join(tmp, 'evil.txt'))
          and not os.path.exists(os.path.join(tmp, 'two')))
try:
    ex.unpack(zip_of({'readme.txt': 'no manifest'}), os.path.join(tmp, 'three'))
    check('unpack: no manifest is refused', False)
except (OSError, ValueError) as e:
    check('unpack: no manifest is refused, and says so', ex.explain(e) == 'there is no manifest.json in it'
          and not os.path.exists(os.path.join(tmp, 'three')), ex.explain(e))

# ---- the store file -------------------------------------------------------------------
store_dir = os.path.join(tmp, 'store')
s = ex.Extensions(store_dir)
real_download = ex.download
ex.download = lambda i, v, timeout=60: good if i == ext_id else (_ for _ in ()).throw(ValueError('unknown'))
try:
    entry = s.fetch(ext_id, '155.0.0.0')
finally:
    ex.download = real_download
check('fetch: unpacked into extensions/<id>/<version>',
      entry['folder'] == os.path.join(store_dir, ext_id, '1.2.3') and os.path.isfile(os.path.join(entry['folder'], 'popup.html'))
      and entry['store_id'] == ext_id and entry['source'] == 'store', str(entry))
s.items[ext_id] = dict(entry, id=ext_id)
s.save()
check('the store file survives a restart', ex.Extensions(store_dir).items.get(ext_id, {}).get('version') == '1.2.3')
check('an update check is due on a fresh store', s.due_for_update_check())
src = os.path.join(tmp, 'dev-ext')
os.makedirs(src)
with open(os.path.join(src, 'manifest.json'), 'w', encoding='utf-8') as f:
    json.dump({'manifest_version': 3, 'name': 'Dev', 'version': '0.1'}, f)
copied = s.copy_folder(src)
check('a folder extension is copied in, not used in place', copied['folder'].startswith(store_dir)
      and os.path.isfile(os.path.join(copied['folder'], 'manifest.json')) and copied['source'] == 'folder')
outside = os.path.join(tmp, 'keep-me')
os.makedirs(outside)
s.discard(outside)
s.discard(store_dir)
check('discard never deletes outside the store (nor the store itself)', os.path.isdir(outside) and os.path.isdir(store_dir))
s.forget(ext_id)
again = ex.Extensions(store_dir)
check('forget drops the entry; its files wait for the next start (WebView2 may be running it)',
      ext_id not in again.items and os.path.isdir(entry['folder']) and entry['folder'] in again.trash)
again.keep(entry['folder'])
check('a folder added back is taken out of the trash', entry['folder'] not in ex.Extensions(store_dir).trash)
again.throw_away(entry['folder'])
again.empty_trash()
check('at the next start the trash is emptied', not os.path.exists(os.path.join(store_dir, ext_id)) and again.trash == []
      and ex.Extensions(store_dir).trash == [])
s.items['x'] = {'folder': copied['folder']}
s.throw_away(copied['folder'])
check('a folder in use is never thrown away', copied['folder'] not in s.trash)

# ---- plain reasons ---------------------------------------------------------------------
check('explain: unknown ID', ex.explain(urllib.error.HTTPError('u', 404, 'x', None, None)).startswith('the Chrome Web Store has no'))
check('explain: offline', ex.explain(urllib.error.URLError('down')) == 'no connection to the Chrome Web Store')
check('explain: damaged zip', ex.explain(zipfile.BadZipFile()) == 'the download was damaged')

# ---- the real thing, when online ------------------------------------------------------
try:
    data = ex.download(FFZ, '155.0.0.0', timeout=20)
except Exception as e:
    print(f'SKIP  real FrankerFaceZ download ({ex.explain(e)})')
else:
    real_archive, real_key = ex.open_crx(data, FFZ)
    check('the real FrankerFaceZ .crx verifies under its store ID', ex.id_of_key(real_key) == FFZ and len(real_archive) > 100_000)
    newest = ex.latest_version(FFZ, '0.0.1', '155.0.0.0')
    check('the update check reports a newer version than 0.0.1, and none than itself',
          bool(newest) and ex.latest_version(FFZ, newest, '155.0.0.0') == '', newest)

print(f'\n{failures} failure(s)')
sys.exit(1 if failures else 0)
