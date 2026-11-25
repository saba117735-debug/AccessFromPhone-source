"""The PC's master volume, read and set through Windows' own audio service (Core Audio), so a phone can show a real slider.

No extra package: the few calls needed are made straight through ctypes. Every call opens and closes its own connection, so it is safe to use from
any thread. A PC with no sound device says so instead of failing.
"""
from __future__ import annotations

import sys
import uuid
from typing import Protocol

from .controls import ControlError

S_OK, S_FALSE, RPC_E_CHANGED_MODE = 0, 1, -2147417850
E_RENDER, E_MULTIMEDIA = 0, 1
CLSCTX_ALL = 23


class VolumeBackend(Protocol):
    def get(self) -> dict: ...
    def set_level(self, percent: int) -> dict: ...
    def set_muted(self, muted: bool) -> dict: ...


def _guid(text: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    raw = uuid.UUID(text).bytes_le
    out = GUID()
    ctypes.memmove(ctypes.byref(out), raw, 16)
    return out


class WindowsVolume:
    """IAudioEndpointVolume of the default speakers."""

    def available(self) -> bool:
        return sys.platform == "win32"

    def _call(self, pointer, index: int, *args_and_types):
        import ctypes

        types = [t for _, t in args_and_types]
        values = [v for v, _ in args_and_types]
        table = ctypes.cast(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
        result = function(pointer, *values)
        if result < 0:
            raise OSError(result)

    def _release(self, pointer) -> None:
        import ctypes

        if pointer:
            table = ctypes.cast(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(pointer)

    def _with_endpoint(self, work):
        import ctypes

        if sys.platform != "win32":
            raise ControlError("Volume control works on Windows only.")
        ole32 = ctypes.windll.ole32
        started = ole32.CoInitializeEx(None, 0)   # 0 = multithreaded: no message pump needed
        enumerator, device, endpoint = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            if started not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
                raise ControlError("Windows' audio service could not be started.")
            clsid, iid_enum, iid_ep = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E"), _guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), _guid("5CDF2C82-841E-4546-9722-0CF74078229A")
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid_enum), ctypes.byref(enumerator)) < 0:
                raise ControlError("Windows' audio service could not be reached.")
            try:
                self._call(enumerator, 4, (E_RENDER, ctypes.c_int), (E_MULTIMEDIA, ctypes.c_int), (ctypes.byref(device), ctypes.POINTER(ctypes.c_void_p)))
            except OSError:
                raise ControlError("This PC has no sound output to control.") from None
            self._call(device, 3, (ctypes.byref(iid_ep), ctypes.c_void_p), (CLSCTX_ALL, ctypes.c_ulong), (None, ctypes.c_void_p), (ctypes.byref(endpoint), ctypes.POINTER(ctypes.c_void_p)))
            return work(endpoint)
        except OSError as exc:
            raise ControlError(f"Windows refused the volume change ({exc.args[0] & 0xFFFFFFFF:#x}).") from None
        finally:
            for pointer in (endpoint, device, enumerator):
                self._release(pointer)
            if started in (S_OK, S_FALSE):
                ole32.CoUninitialize()

    def _read(self, endpoint) -> dict:
        import ctypes

        level, muted = ctypes.c_float(), ctypes.c_int()
        self._call(endpoint, 9, (ctypes.byref(level), ctypes.POINTER(ctypes.c_float)))
        self._call(endpoint, 15, (ctypes.byref(muted), ctypes.POINTER(ctypes.c_int)))
        return {"available": True, "level": max(0, min(100, round(level.value * 100))), "muted": bool(muted.value)}

    def get(self) -> dict:
        return self._with_endpoint(self._read)

    def set_level(self, percent: int) -> dict:
        import ctypes

        value = max(0, min(100, int(percent))) / 100.0

        def work(endpoint):
            self._call(endpoint, 7, (value, ctypes.c_float), (None, ctypes.c_void_p))
            if value > 0:   # moving the slider up is also "I want to hear it"
                self._call(endpoint, 14, (0, ctypes.c_int), (None, ctypes.c_void_p))
            return self._read(endpoint)

        return self._with_endpoint(work)

    def set_muted(self, muted: bool) -> dict:
        import ctypes

        def work(endpoint):
            self._call(endpoint, 14, (1 if muted else 0, ctypes.c_int), (None, ctypes.c_void_p))
            return self._read(endpoint)

        return self._with_endpoint(work)


def default_backend() -> VolumeBackend:
    return WindowsVolume()
