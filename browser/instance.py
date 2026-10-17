"""One Lite at a time: a second start hands its links to the Lite that is already open.

Windows opens a link from another app by starting Lite.exe with it. Two Lites on one
profile undo each other's work: each one's cookie sweep signs you out of the sites only
the other has open, and each rewrites bookmarks, history and settings from its own copy.
So the first Lite owns a mutex and listens on a named pipe; a later start sends its links
there and exits. Both ends prove they know a key kept in the profile, which only this
Windows account can read, so nothing else can pose as Lite on either end of the pipe.
"""
import ctypes
import hashlib
import json
import os
import threading
import time
from ctypes import wintypes
from multiprocessing import connection

WAIT_OBJECT_0, WAIT_ABANDONED = 0, 0x80
ASFW_ANY = -1  # AllowSetForegroundWindow: whichever process answers may take the foreground
MAX_MESSAGE = 1 << 21  # a data: link can be long
MAX_URLS = 50

_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _tag(data_dir: str) -> str:
    """Names are per profile folder, so a test profile never talks to the Lite you use."""
    return hashlib.sha256(os.path.normcase(os.path.abspath(data_dir)).encode('utf-8')).hexdigest()[:20]


def pipe_name(data_dir: str) -> str:
    return rf'\\.\pipe\LiteBrowser-{_tag(data_dir)}'


def mutex_name(data_dir: str) -> str:
    return rf'Local\LiteBrowser-{_tag(data_dir)}'


def _key_path(data_dir: str) -> str:
    return os.path.join(data_dir, 'instance.key')


def _read_key(data_dir: str) -> bytes | None:
    try:
        with open(_key_path(data_dir), 'rb') as f:
            key = f.read()
    except OSError:
        return None
    return key if len(key) >= 32 else None


def _own_key(data_dir: str) -> bytes:
    """The key on disk, or a new one; only the owner ever writes it."""
    key = _read_key(data_dir)
    if key:
        return key
    key = os.urandom(32)
    os.makedirs(data_dir, exist_ok=True)
    tmp = f'{_key_path(data_dir)}.{os.getpid()}.tmp'
    with open(tmp, 'wb') as f:
        f.write(key)
    os.replace(tmp, _key_path(data_dir))
    return key


def _owned(wait_result: int) -> bool:
    return wait_result in (WAIT_OBJECT_0, WAIT_ABANDONED)  # abandoned: the last owner exited without letting go


class Owner:
    """This process owns the profile; serve() takes the links of later starts."""

    def __init__(self, data_dir: str, mutex):
        self.data_dir = data_dir
        self._mutex = mutex  # held for the life of the process (by the thread that called claim)
        self._listener = None

    def serve(self, on_links) -> bool:
        """Listen for later starts on a worker thread. on_links(urls) runs on that thread and
        returns False when Lite can no longer take them (it is closing). False: not listening."""
        if not self._mutex:
            return False  # another Lite owns the profile and did not answer: this one only shows its links
        try:
            key = _own_key(self.data_dir)
            self._listener = connection.Listener(pipe_name(self.data_dir), family='AF_PIPE')
        except OSError:
            return False  # e.g. the pipe name is taken: links from other apps then open windows of their own
        threading.Thread(target=self._accept, args=(key, on_links), daemon=True, name='lite-instance').start()
        return True

    def _accept(self, key: bytes, on_links):
        while True:
            try:
                conn = self._listener.accept()
            except OSError:
                return  # the listener was closed
            # The handshake waits on the other end: one that never answers must not stop the next start.
            threading.Thread(target=self._handle, args=(conn, key, on_links), daemon=True).start()

    @staticmethod
    def _handle(conn, key: bytes, on_links):
        with conn:
            try:
                connection.deliver_challenge(conn, key)
                connection.answer_challenge(conn, key)
                msg = json.loads(conn.recv_bytes(MAX_MESSAGE))
                urls = msg.get('urls') if isinstance(msg, dict) else None
                urls = [u for u in urls if isinstance(u, str) and u][:MAX_URLS] if isinstance(urls, list) else []
                conn.send_bytes(b'ok' if on_links(urls) else b'closing')
            except Exception:
                pass  # a failed handshake, a dropped connection or a bad message: the sender retries or gives up

    def close(self):
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None


def _send(data_dir: str, urls: list[str]) -> bool:
    """True once the Lite that owns the profile has opened the links."""
    key = _read_key(data_dir)
    if not key:
        return False
    try:
        conn = connection.Client(pipe_name(data_dir), family='AF_PIPE', authkey=key)
    except Exception:
        return False  # not listening yet (still starting) or any more (closing), or not a Lite at all
    with conn:
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)  # its window may come to the front
            conn.send_bytes(json.dumps({'urls': list(urls)[:MAX_URLS]}).encode('utf-8'))
            return conn.poll(5) and conn.recv_bytes(16) == b'ok'
        except Exception:
            return False


def claim(data_dir: str, urls: list[str], wait: float = 10.0) -> Owner | None:
    """Own the profile, or hand `urls` to the Lite that does. None: a running Lite took them.

    A Lite that is starting is not listening yet, and one that is closing no longer takes
    links: keep trying for `wait` seconds, and take over the moment it exits.
    """
    mutex = _k32.CreateMutexW(None, False, mutex_name(data_dir))
    if not mutex:
        return Owner(data_dir, None)
    deadline = time.monotonic() + wait
    while True:
        if _owned(_k32.WaitForSingleObject(mutex, 0)):
            return Owner(data_dir, mutex)
        if _send(data_dir, urls):
            _k32.CloseHandle(mutex)
            return None
        if time.monotonic() >= deadline:
            _k32.CloseHandle(mutex)
            return Owner(data_dir, None)  # the open Lite does not answer: rather a second window than a lost link
        if _owned(_k32.WaitForSingleObject(mutex, 250)):
            return Owner(data_dir, mutex)
