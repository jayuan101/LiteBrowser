"""Chrome extensions - FrankerFaceZ, BetterTTV, Dark Reader... - from the Chrome Web Store.

Lite fetches an extension's .crx from Google's update service, the same place Chrome gets
it, and checks the developer's signature first: the archive must be signed with the key
the extension's ID is made from, so what gets installed is that extension and nothing
else. It is unpacked into the profile, one folder per version (WebView2 drops an
extension whose files change), and handed to WebView2, which runs it in every normal tab -
not in private tabs. WebView2 draws no toolbar buttons for extensions, so an extension's
pop-up and options page open as tabs. Once a day Lite asks for newer versions.

This module is plain Python (download, verify, unpack, remember); app.py does the WebView2 part.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import struct
import time
import urllib.error
import urllib.request
import zipfile

UPDATE_SERVICE = 'https://clients2.google.com/service/update2/crx'
ID = re.compile(r'(?<![a-p])[a-p]{32}(?![a-p])')
MAX_CRX = 64 * 1024 * 1024
DAY = 24 * 3600
_SHA256_DIGEST_INFO = bytes.fromhex('3031300d060960864801650304020105000420')  # PKCS#1 v1.5, SHA-256
_RSA_OID = bytes.fromhex('2a864886f70d010101')


def parse_id(text: str) -> str:
    """The extension ID in a Chrome Web Store link, or a bare ID; '' when there is none."""
    found = ID.findall((text or '').strip().lower())
    return found[-1] if found else ''  # .../detail/<name>/<id>: the ID comes last


def id_of_key(public_key: bytes) -> str:
    """Chrome's extension ID: the first 128 bits of SHA-256 of the key, written with the letters a-p."""
    return ''.join(chr(ord('a') + int(c, 16)) for c in hashlib.sha256(public_key).hexdigest()[:32])


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r'\d+', v or '')[:4]) or (0,)


# ---- the .crx file: CRX3 = "Cr24", version 3, a protobuf header, then the zip -------------
def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError('damaged extension header')
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes):
    """The length-delimited fields of a protobuf message: (field number, bytes)."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, i = _varint(buf, i)
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise ValueError('damaged extension header')
            yield field, buf[i:i + n]
            i += n
        else:
            raise ValueError('damaged extension header')


def _der(buf: bytes, i: int) -> tuple[int, bytes, int]:
    if i + 2 > len(buf):
        raise ValueError('damaged key')
    tag, length, i = buf[i], buf[i + 1], i + 2
    if length & 0x80:
        n = length & 0x7F
        length, i = int.from_bytes(buf[i:i + n], 'big'), i + n
    if i + length > len(buf):
        raise ValueError('damaged key')
    return tag, buf[i:i + length], i + length


def _rsa_key(spki: bytes) -> tuple[int, int]:
    """(n, e) of a DER SubjectPublicKeyInfo RSA key."""
    tag, info, _ = _der(spki, 0)
    tag_a, algorithm, j = _der(info, 0)
    if tag != 0x30 or tag_a != 0x30 or _RSA_OID not in algorithm:
        raise ValueError('not an RSA key')
    tag, bits, _ = _der(info, j)
    tag_k, key, _ = _der(bits[1:], 0)  # the BIT STRING's first byte counts unused bits
    tag_n, n, k = _der(key, 0)
    tag_e, e, _ = _der(key, k)
    if (tag, tag_k, tag_n, tag_e) != (0x03, 0x30, 0x02, 0x02):
        raise ValueError('not an RSA key')
    return int.from_bytes(n, 'big'), int.from_bytes(e, 'big')


def _rsa_verified(spki: bytes, signature: bytes, data: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256, the signature Chrome checks on a .crx."""
    try:
        n, e = _rsa_key(spki)
    except ValueError:
        return False
    k = (n.bit_length() + 7) // 8
    if len(signature) != k or n < 2 ** 1023:
        return False
    em = pow(int.from_bytes(signature, 'big'), e, n).to_bytes(k, 'big')
    digest = _SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    return hmac.compare_digest(em, b'\x00\x01' + b'\xff' * (k - len(digest) - 3) + b'\x00' + digest)


def open_crx(data: bytes, expected_id: str) -> tuple[bytes, bytes]:
    """(the zip, the developer's public key) of a .crx whose signature checks out for expected_id."""
    if len(data) < 12 or data[:4] != b'Cr24':
        raise ValueError('the download is not a Chrome extension')
    version, size = struct.unpack('<II', data[4:12])
    if version != 3:
        raise ValueError(f'unsupported extension format (CRX{version})')
    header, archive = data[12:12 + size], data[12 + size:]
    proofs, signed = [], b''
    for field, value in _fields(header):
        if field == 2:  # sha256_with_rsa: the developer's key and signature
            proofs.append(dict(_fields(value)))
        elif field == 10000:  # signed_header_data: SignedData {crx_id}
            signed = value
    crx_id = dict(_fields(signed)).get(1, b'')
    ext_id = ''.join(chr(ord('a') + (b >> 4)) + chr(ord('a') + (b & 15)) for b in crx_id)
    if len(crx_id) != 16 or ext_id != expected_id:
        raise ValueError('the download is not the extension that was asked for')
    signing = b'CRX3 SignedData\x00' + struct.pack('<I', len(signed)) + signed + archive
    for proof in proofs:
        key, signature = proof.get(1, b''), proof.get(2, b'')
        if id_of_key(key) == ext_id and _rsa_verified(key, signature, signing):
            return archive, key
    raise ValueError("the extension's signature does not check out")


# ---- the unpacked extension ------------------------------------------------------------
def _read_manifest(folder: str) -> dict:
    with open(os.path.join(folder, 'manifest.json'), encoding='utf-8-sig') as f:
        manifest = json.load(f)
    if not isinstance(manifest, dict):
        raise ValueError('the extension has no manifest')
    return manifest


def describe(folder: str, manifest: dict) -> dict:
    """Name, version, pop-up and options page, as the settings page shows them."""
    name = str(manifest.get('name') or 'Extension')
    m = re.fullmatch(r'__MSG_(\w+)__', name)
    if m:  # a localized name: look it up in the extension's own messages
        for locale in (manifest.get('default_locale'), 'en', 'en_US'):
            try:
                with open(os.path.join(folder, '_locales', str(locale), 'messages.json'), encoding='utf-8-sig') as f:
                    messages = {k.lower(): v for k, v in json.load(f).items()}
                name = str(messages[m.group(1).lower()]['message'])
                break
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
    action = manifest.get('action') or manifest.get('browser_action') or manifest.get('page_action') or {}
    options = (manifest.get('options_ui') or {}).get('page') if isinstance(manifest.get('options_ui'), dict) else None
    return {'name': name[:80], 'version': str(manifest.get('version') or ''),
            'popup': str(action.get('default_popup') or '') if isinstance(action, dict) else '',
            'options': str(options or manifest.get('options_page') or '')}


def unpack(archive: bytes, folder: str, key: bytes | None = None) -> dict:
    """Extract the zip into `folder` (a new one) and return describe(); with the store's key in the
    manifest, WebView2 gives the extension its Chrome Web Store ID."""
    part = folder + '.part'
    shutil.rmtree(part, ignore_errors=True)
    root = os.path.realpath(part)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(root, info.filename))
            if os.path.commonpath([root, target]) != root:
                raise ValueError('the extension tries to write outside its folder')
        z.extractall(part)
    try:
        manifest = _read_manifest(part)
        if key:
            manifest['key'] = base64.b64encode(key).decode('ascii')
            with open(os.path.join(part, 'manifest.json'), 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=1)
        info = describe(part, manifest)
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(part, folder)
    except Exception:
        shutil.rmtree(part, ignore_errors=True)
        raise
    return info


# ---- Google's update service -----------------------------------------------------------
def _get(url: str, timeout: float, limit: int) -> tuple[int, bytes]:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError('the extension is too large')
        return r.status, data


def download(ext_id: str, browser_version: str, timeout: float = 60.0) -> bytes:
    url = (f'{UPDATE_SERVICE}?response=redirect&prodversion={browser_version}&acceptformat=crx2,crx3'
           f'&x=id%3D{ext_id}%26installsource%3Dondemand%26uc')
    status, data = _get(url, timeout, MAX_CRX)
    if status == 204 or not data:
        raise ValueError('the Chrome Web Store has no extension with that ID (or not for this browser)')
    return data


def latest_version(ext_id: str, current: str, browser_version: str, timeout: float = 20.0) -> str:
    """The newer version the store has, or '' when `current` is the newest."""
    url = (f'{UPDATE_SERVICE}?response=updatecheck&prodversion={browser_version}&acceptformat=crx2,crx3'
           f'&x=id%3D{ext_id}%26v%3D{current}%26uc')
    _, data = _get(url, timeout, 1 << 20)
    m = re.search(r'<updatecheck\b[^>]*>', data.decode('utf-8', 'replace'))
    tag = m.group(0) if m else ''
    v = re.search(r'\bversion="([^"]+)"', tag)
    ok = re.search(r'\bstatus="ok"', tag)
    return v.group(1) if ok and v and _version(v.group(1)) > _version(current) else ''


def explain(error: BaseException) -> str:
    """A short, plain reason for the settings page."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return 'the Chrome Web Store has no extension with that ID'
        return f'the Chrome Web Store answered with error {error.code}'
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return 'no connection to the Chrome Web Store'
    if isinstance(error, zipfile.BadZipFile):
        return 'the download was damaged'
    if isinstance(error, json.JSONDecodeError):
        return "its manifest.json can't be read"
    if isinstance(error, FileNotFoundError):
        return 'there is no manifest.json in it' if 'manifest' in str(error) else (error.strerror or str(error))
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, OSError):
        return error.strerror or str(error)
    return str(error) or type(error).__name__


class Extensions:
    """What Lite installed: extensions.json plus one folder per extension version under `folder`."""

    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(folder, 'extensions.json')
        self.items: dict[str, dict] = {}  # WebView2's extension ID -> entry
        self.checked = 0.0  # the last update check
        # Folders to delete at the next start, before WebView2 runs: a removed extension (WebView2
        # drops one whose files are gone) and the version an update replaced. Lite never calls
        # WebView2's RemoveAsync - after it, the WebView2 browser process outlives Lite (measured).
        self.trash: list[str] = []
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            items = data.get('items', {})
            self.items = {k: v for k, v in items.items() if isinstance(v, dict) and isinstance(v.get('folder'), str)}
            self.checked = float(data.get('checked', 0))
            self.trash = [t for t in data.get('trash', []) if isinstance(t, str)]
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def save(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'items': self.items, 'checked': self.checked, 'trash': self.trash}, f, indent=1)
        os.replace(tmp, self.path)

    def throw_away(self, folder: str) -> None:
        """Delete this folder at the next start (WebView2 may still be running it now)."""
        if folder not in self.trash and not any(e.get('folder') == folder for e in self.items.values()):
            self.trash.append(folder)
            self.save()

    def keep(self, folder: str) -> None:
        """A folder that is in use again (the same version added back) must not be deleted."""
        if folder in self.trash:
            self.trash.remove(folder)
            self.save()

    def empty_trash(self) -> None:
        """At start-up, before WebView2 runs."""
        if not self.trash:
            return
        for folder in self.trash:
            self.discard(folder)
        self.trash = [f for f in self.trash if os.path.isdir(f)]  # still locked: try again next time
        self.save()

    def fetch(self, ext_id: str, browser_version: str) -> dict:
        """Worker thread: download, verify and unpack a Web Store extension. The entry is not saved yet."""
        archive, key = open_crx(download(ext_id, browser_version), ext_id)
        staging = os.path.join(self.folder, ext_id, f'new-{int(time.time() * 1000)}')
        info = unpack(archive, staging, key)
        folder = os.path.join(self.folder, ext_id, re.sub(r'[^\w.]', '_', info['version']) or 'unknown')
        if os.path.abspath(folder) != os.path.abspath(staging):
            if os.path.isdir(folder):
                folder = staging  # that version's folder exists (in use, or removed this session): keep this copy beside it
            else:
                os.replace(staging, folder)
        return dict(info, folder=folder, store_id=ext_id, source='store', enabled=True)

    def copy_folder(self, path: str) -> dict:
        """An unpacked extension folder the user picked: copied in, so editing the original does not
        make WebView2 drop it."""
        info = describe(path, _read_manifest(path))
        tag = hashlib.sha256(os.path.abspath(path).lower().encode('utf-8')).hexdigest()[:12]
        folder = os.path.join(self.folder, f'folder-{tag}', f'{re.sub(r"[^\w.]", "_", info["version"])}-{int(time.time())}')
        shutil.copytree(path, folder)
        return dict(info, folder=folder, source='folder', origin=os.path.abspath(path), enabled=True)

    def forget(self, ext_id: str) -> dict | None:
        """Drop an entry; its files go at the next start."""
        entry = self.items.pop(ext_id, None)
        if entry:
            self.save()
            self.throw_away(entry['folder'])
        return entry

    def discard(self, folder: str) -> None:
        """Delete an extension version folder - only ever one inside self.folder."""
        root = os.path.realpath(self.folder)
        target = os.path.realpath(folder)
        if target != root and os.path.commonpath([root, target]) == root:
            shutil.rmtree(target, ignore_errors=True)
            parent = os.path.dirname(target)
            if parent != root and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)

    def due_for_update_check(self) -> bool:
        return time.time() - self.checked > DAY
