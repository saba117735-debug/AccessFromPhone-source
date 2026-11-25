"""Windows DPAPI at machine scope: what is protected can only be opened on this PC (not by copying the file somewhere else)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(data: bytes) -> bytes:
    source, _keep = _blob(data)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "AccessFromPhone unlock", None, None, None,
                                                  CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(out)):
        raise OSError("Windows could not protect the saved sign-in.")
    return _take(out)


def unprotect(data: bytes) -> bytes:
    source, _keep = _blob(data)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError("Windows could not open the saved sign-in (was it saved on another PC?).")
    return _take(out)
