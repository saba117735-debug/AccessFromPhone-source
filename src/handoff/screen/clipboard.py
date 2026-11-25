"""The PC's clipboard, as plain text, for a phone that controls this PC: copy on the phone and paste on the PC, or copy on the PC and read it on the phone.

Text only (pictures and files are not carried). Reading the clipboard can show a password the PC owner just copied, so it is only for the phone that has taken control,
and every use is written to the activity log (that it happened and how long the text was, never the text itself).
"""
from __future__ import annotations

import sys
import time
from typing import Protocol

MAX_CLIP = 100_000   # characters either way


class ClipboardError(Exception):
    """The clipboard could not be read or written (another program holds it, or this is not Windows)."""


class Clipboard(Protocol):
    def get_text(self) -> str: ...
    def set_text(self, text: str) -> None: ...


class WindowsClipboard:
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._c = ctypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.OpenClipboard.argtypes = (wintypes.HWND,); u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = (wintypes.UINT,); u.GetClipboardData.restype = ctypes.c_void_p
        u.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p); u.SetClipboardData.restype = ctypes.c_void_p
        k.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t); k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = (ctypes.c_void_p,); k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = (ctypes.c_void_p,)
        k.GlobalFree.argtypes = (ctypes.c_void_p,)
        self._u, self._k = u, k

    def _open(self) -> None:
        for _ in range(10):   # another program may have it for a moment
            if self._u.OpenClipboard(None):
                return
            time.sleep(0.03)
        raise ClipboardError("Another program is using the clipboard. Try again in a moment.")

    def get_text(self) -> str:
        self._open()
        try:
            handle = self._u.GetClipboardData(self.CF_UNICODETEXT)
            if not handle:
                return ""   # empty, or something that is not text (a picture, files)
            pointer = self._k.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return self._c.wstring_at(pointer)
            finally:
                self._k.GlobalUnlock(handle)
        finally:
            self._u.CloseClipboard()

    def set_text(self, text: str) -> None:
        data = (text + "\0").encode("utf-16-le")
        handle = self._k.GlobalAlloc(self.GMEM_MOVEABLE, len(data))
        if not handle:
            raise ClipboardError("Not enough memory for that text.")
        pointer = self._k.GlobalLock(handle)
        self._c.memmove(pointer, data, len(data))
        self._k.GlobalUnlock(handle)
        self._open()
        try:
            self._u.EmptyClipboard()
            if not self._u.SetClipboardData(self.CF_UNICODETEXT, handle):
                self._k.GlobalFree(handle)
                raise ClipboardError("Windows did not accept the text.")
        finally:
            self._u.CloseClipboard()


def default_clipboard() -> Clipboard | None:
    return WindowsClipboard() if sys.platform == "win32" else None
