"""A key combination on THIS PC's own keyboard that cuts a phone's control (and looking) at once: the panic key.

A phone can never press it, because it is a real global hotkey (Windows' RegisterHotKey): only someone at this PC's keyboard can. Windows only; elsewhere it does nothing.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("handoff.screen")

MOD = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def parse_combo(text: str) -> tuple[int, int] | None:
    """"ctrl+alt+shift+x" -> (modifier flags, virtual key). None when it is empty or cannot be used (it needs a modifier and one key)."""
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods, key = 0, parts[-1]
    for m in parts[:-1]:
        if m not in MOD:
            return None
        mods |= MOD[m]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x6F + int(key[1:])
    elif key in ("esc", "escape"):
        vk = 0x1B
    elif key in ("pause", "break"):
        vk = 0x13
    else:
        return None
    return mods, vk


def describe(text: str) -> str:
    return "+".join(p.strip().capitalize() for p in text.split("+") if p.strip())


class PanicKey:
    def __init__(self, combo: str, action: Callable[[], None]):
        self.combo, self.action, self.registered = combo, action, False
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()

    def start(self) -> bool:
        parsed = parse_combo(self.combo)
        if sys.platform != "win32" or parsed is None:
            return False
        self._thread = threading.Thread(target=self._run, args=parsed, name="panic-key", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self.registered

    def _run(self, mods: int, vk: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        self.registered = bool(user32.RegisterHotKey(None, 1, mods | NOREPEAT, vk))   # fails if another program already owns the combination
        self._ready.set()
        if not self.registered:
            log.warning("the panic key %s could not be registered (another program uses it)", self.combo)
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.action()
                    except Exception:  # noqa: BLE001
                        log.exception("the panic key action failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._tid and self.registered:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
