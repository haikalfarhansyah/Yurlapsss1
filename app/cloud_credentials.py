"""Atomic private session persistence. Windows uses DPAPI bound to the OS user."""
import base64
import ctypes
import json
import os
from pathlib import Path


def _dpapi(data, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    source = ctypes.create_string_buffer(data)
    blob = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    crypt = ctypes.windll.crypt32
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR,
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel = ctypes.windll.kernel32
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(blob), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(blob), "YurLaps", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise OSError("Penyimpanan sesi Windows tidak tersedia")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class CredentialStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            wrapper = json.loads(self.path.read_text(encoding="utf-8"))
            if wrapper.get("dpapi"):
                return json.loads(_dpapi(base64.b64decode(wrapper["dpapi"]), True))
            return wrapper["session"]
        except (OSError, ValueError, KeyError):
            return {}

    def save(self, session):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(session).encode("utf-8")
        wrapper = {"dpapi": base64.b64encode(_dpapi(raw)).decode()} if os.name == "nt" else {"session": session}
        temp = self.path.with_suffix(".tmp")
        fd = os.open(str(temp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(wrapper, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)
        if os.name != "nt":
            os.chmod(self.path, 0o600)
