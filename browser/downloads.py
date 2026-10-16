"""Download safety: every download is checked before it starts and scanned when it finishes.

Before: the download address is checked against the malware lists (URLhaus is a
list of links that serve malware), and files that can run programs need an
explicit "Download anyway". After: the finished file is handed to the antivirus
installed on this PC through Windows' Antimalware Scan Interface (AMSI) - that is
Microsoft Defender, or Sophos, Norton, Avast... if one of those replaced it. A
file the antivirus flags is deleted. WebView2 also runs SmartScreen and Windows'
own attachment check on every download.
"""
import ctypes
import os
import threading
import winreg
from ctypes import wintypes

MAX_SCAN = 256 * 1024 * 1024  # larger files (disk images, games) are left to the antivirus's own scanning
AMSI_RESULT_DETECTED = 32768
AMSI_BLOCKED_BY_ADMIN = range(0x4000, 0x5000)


def fallback_folder() -> str:
    """Where downloads go when Windows' Downloads folder cannot be used: the profile's own
    Downloads folder, else one inside Lite's data folder."""
    home = os.path.join(os.path.expanduser('~'), 'Downloads')
    if os.path.isdir(home):
        return home
    folder = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'LiteBrowser', 'Downloads')
    os.makedirs(folder, exist_ok=True)
    return folder


def fallback_path(path: str, folder: str | None = None) -> str:
    """`path` when its folder exists or can be made; otherwise the same name in a folder that works.

    Windows' Downloads folder can live on a drive that is not there (an unplugged USB disk),
    and WebView2 then fails every download. A new name never overwrites a file already there.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path
    except (OSError, ValueError):
        pass
    folder = folder or fallback_folder()
    name = os.path.basename(path) or 'download'
    stem, ext = os.path.splitext(name)
    candidate, n = os.path.join(folder, name), 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f'{stem} ({n}){ext}')
        n += 1
    return candidate


def antivirus_name() -> str | None:
    """The product AMSI scans with, e.g. 'Sophos' or 'Microsoft Defender'; None if there is none."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\AMSI\Providers') as key:
            clsid = winreg.EnumKey(key, 0)
        name = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf'CLSID\{clsid}') or ''
    except OSError:
        return None
    if any(w in name.lower() for w in ('defender', 'windows', 'microsoft')):
        return 'Microsoft Defender'
    return name.split(' AMSI')[0].split(' Antimalware')[0].strip() or 'your antivirus'


class Scanner:
    def __init__(self):
        self.name = antivirus_name()
        self._lock = threading.Lock()
        self._amsi = None
        self._context = ctypes.c_void_p()

    def _init(self) -> bool:
        if self._amsi is None:
            amsi = ctypes.WinDLL('amsi.dll')
            amsi.AmsiInitialize.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
            amsi.AmsiOpenSession.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            amsi.AmsiCloseSession.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            amsi.AmsiScanBuffer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, wintypes.LPCWSTR,
                                            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            if amsi.AmsiInitialize('Lite browser', ctypes.byref(self._context)) != 0:
                raise OSError('AMSI is not available')
            self._amsi = amsi
        return True

    def scan(self, path: str) -> tuple[str, str]:
        """('clean' | 'threat' | 'unscanned', what to tell the user). Slow: run on a worker thread."""
        av = self.name or 'your antivirus'
        if not os.path.exists(path):
            return 'threat', f'The file is gone - {av} probably removed it as a threat'
        if not self.name:
            return 'unscanned', 'No antivirus is registered with Windows, so Lite could not scan it'
        if os.path.getsize(path) > MAX_SCAN:
            return 'unscanned', f'Too large for an extra scan - {av} checks it when it is opened'
        try:
            with open(path, 'rb') as f:
                data = f.read()
            with self._lock:
                self._init()
                session, result = ctypes.c_void_p(), ctypes.c_int(0)
                self._amsi.AmsiOpenSession(self._context, ctypes.byref(session))
                try:
                    buf = ctypes.create_string_buffer(data, len(data))
                    hr = self._amsi.AmsiScanBuffer(self._context, buf, len(data), path, session, ctypes.byref(result))
                finally:
                    self._amsi.AmsiCloseSession(self._context, session)
        except OSError as e:
            return 'unscanned', f'Could not scan it: {e}'
        if hr != 0:
            return 'unscanned', f'{av} could not scan it (error 0x{hr & 0xFFFFFFFF:08X})'
        if result.value >= AMSI_RESULT_DETECTED or result.value in AMSI_BLOCKED_BY_ADMIN:
            try:
                os.remove(path)
                return 'threat', f'{av} found a threat - Lite deleted the file'
            except OSError:
                return 'threat', f'{av} found a threat - do not open this file'
        return 'clean', f'No threats found by {av}'
