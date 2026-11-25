"""Mouse and keyboard from the phone, onto this PC's main display.

Two layers, so the rules can be tested without moving a real mouse:
  clean_event / clean_events   check what the phone sent and turn it into a small fixed shape (never trusts it: unknown kinds, wild numbers, long text, odd keys are refused)
  Injector                     applies clean events through a backend; the real backend is Windows' SendInput, a test passes a recorder

Positions arrive as fractions of the picture (0 to 1), so they do not depend on how big the phone's view was or how the picture was scaled. Windows maps them onto the main
display, which is the one the picture shows. Windows keeps its own secure screens (sign-in, permission prompts, Ctrl+Alt+Del) away from programs: input does nothing there.
"""
from __future__ import annotations

import sys
import threading
from typing import Protocol


class InputError(Exception):
    """The phone sent something that is not a valid input event."""


MAX_BATCH = 40
MAX_TEXT = 200

# Keys by name. Letters, digits and one-character symbols are accepted as themselves.
VK = {"enter": 0x0D, "backspace": 0x08, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "space": 0x20, "insert": 0x2D, "win": 0x5B, "printscreen": 0x2C, "pause": 0x13, "capslock": 0x14, "menu": 0x5D, **{f"f{i}": 0x6F + i for i in range(1, 13)}}
EXTENDED = {0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x5B, 0x2C, 0x5D}
MODS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
BUTTONS = ("left", "right", "middle")
KINDS = ("move", "click", "down", "up", "scroll", "key", "text")


class Backend(Protocol):
    def move(self, x: float, y: float) -> None: ...
    def button(self, which: str, down: bool) -> None: ...
    def wheel(self, notches: int) -> None: ...
    def key(self, vk: int, down: bool, extended: bool = False) -> None: ...
    def char(self, ch: str) -> None: ...


def _fraction(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InputError(f"{name} must be a number") from None
    if number != number or number < -0.01 or number > 1.01:  # NaN, or well outside the picture
        raise InputError(f"{name} is outside the screen")
    return min(1.0, max(0.0, number))


def clean_event(raw: object) -> dict:
    """One event from the phone, checked and put in a fixed shape. Raises InputError."""
    if not isinstance(raw, dict):
        raise InputError("an event must be an object")
    kind = raw.get("t")
    if kind not in KINDS:
        raise InputError("unknown event")
    event: dict = {"t": kind}
    if kind in ("move", "click", "down", "up", "scroll"):
        if kind != "scroll" or "x" in raw:
            event["x"], event["y"] = _fraction(raw.get("x"), "x"), _fraction(raw.get("y"), "y")
    if kind in ("click", "down", "up"):
        button = raw.get("button", "left")
        if button not in BUTTONS:
            raise InputError("unknown button")
        event["button"] = button
    if kind == "scroll":
        try:
            event["dy"] = max(-20, min(20, int(round(float(raw.get("dy", 0))))))
        except (TypeError, ValueError):
            raise InputError("dy must be a number") from None
    if kind == "key":
        key = str(raw.get("key", "")).lower()
        if key not in VK and not (len(key) == 1 and key.isprintable()):
            raise InputError("unknown key")
        mods = raw.get("mods", [])
        if not isinstance(mods, list) or any(m not in MODS for m in mods) or len(mods) > 4:
            raise InputError("unknown modifier")
        event["key"], event["mods"] = key, sorted(set(mods))
    if kind == "text":
        text = raw.get("text", "")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
            raise InputError("text must be 1 to %d characters" % MAX_TEXT)
        event["text"] = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
        if not event["text"]:
            raise InputError("nothing to type")
    return event


def clean_events(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_BATCH:
        raise InputError("send 1 to %d events at a time" % MAX_BATCH)
    return [clean_event(e) for e in raw]


def _vk_for(key: str) -> int:
    if key in VK:
        return VK[key]
    ch = key.upper()
    if ch.isalnum() and len(ch) == 1:
        return ord(ch)  # A-Z and 0-9 are their own virtual-key codes
    return 0  # a symbol: typed as a character instead


class Injector:
    """Applies events, and remembers what it pressed and has not yet let go of (a mouse button in a drag, a key), so `release_all` can let go of it all:
    if the phone's connection drops mid-drag, nothing is left held down on this PC."""

    def __init__(self, backend: Backend | None = None):
        self.backend = backend if backend is not None else (WindowsBackend() if sys.platform == "win32" else None)
        self._held_buttons: set[str] = set()
        self._held_keys: dict[int, bool] = {}   # virtual key -> extended
        self._lock = threading.Lock()

    def holding(self) -> bool:
        with self._lock:
            return bool(self._held_buttons or self._held_keys)

    def release_all(self) -> int:
        """Let go of every button and key that is held down. Returns how many. Never raises: it runs when things go wrong."""
        b = self.backend
        with self._lock:
            buttons, keys = sorted(self._held_buttons), dict(self._held_keys)
            self._held_buttons.clear(); self._held_keys.clear()
        if b is None:
            return 0
        done = 0
        for which in buttons:
            try:
                b.button(which, False); done += 1
            except Exception:  # noqa: BLE001
                pass
        for vk, extended in keys.items():
            try:
                b.key(vk, False, extended); done += 1
            except Exception:  # noqa: BLE001
                pass
        return done

    def _key(self, b, vk: int, down: bool, extended: bool = False) -> None:
        if not down:
            with self._lock:
                self._held_keys.pop(vk, None)
        b.key(vk, down, extended)
        if down:   # held only once Windows took it
            with self._lock:
                self._held_keys[vk] = extended

    @property
    def available(self) -> bool:
        return self.backend is not None

    def apply(self, events: list[dict]) -> int:
        """Do these (already cleaned) events. Returns how many were done."""
        b = self.backend
        if b is None:
            raise InputError("This PC can't be controlled from here (Windows only).")
        for e in events:
            kind = e["t"]
            if "x" in e:
                b.move(e["x"], e["y"])
            if kind in ("click", "down", "up"):
                if kind in ("click", "down"):
                    with self._lock:
                        self._held_buttons.add(e["button"])
                    b.button(e["button"], True)
                if kind in ("click", "up"):
                    b.button(e["button"], False)
                    with self._lock:
                        self._held_buttons.discard(e["button"])
            elif kind == "scroll" and e["dy"]:
                b.wheel(e["dy"])
            elif kind == "key":
                try:
                    for m in e["mods"]:
                        self._key(b, MODS[m], True)
                    vk = _vk_for(e["key"])
                    if vk:
                        self._key(b, vk, True, vk in EXTENDED)
                        self._key(b, vk, False, vk in EXTENDED)
                    else:
                        b.char(e["key"])
                finally:   # a shortcut never leaves Ctrl or Alt held down, even if Windows refused part of it
                    for m in reversed(e["mods"]):
                        try:
                            self._key(b, MODS[m], False)
                        except Exception:  # noqa: BLE001
                            pass
            elif kind == "text":
                for ch in e["text"]:
                    if ch == "\n":
                        b.key(VK["enter"], True); b.key(VK["enter"], False)
                    elif ch == "\t":
                        b.key(VK["tab"], True); b.key(VK["tab"], False)
                    else:
                        b.char(ch)
        return len(events)


class WindowsBackend:
    """Windows' SendInput. Created only on Windows."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._c = ctypes
        ulong_ptr = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class _Union(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _Union)]

        self._MOUSEINPUT, self._KEYBDINPUT, self._INPUT = MOUSEINPUT, KEYBDINPUT, INPUT
        self._send = ctypes.windll.user32.SendInput
        self._send.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._send.restype = wintypes.UINT

    def _go(self, inp) -> None:
        if self._send(1, self._c.byref(inp), self._c.sizeof(self._INPUT)) != 1:
            raise InputError("Windows did not accept the input (a protected window is in front, or nobody is signed in).")

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        inp = self._INPUT(type=0)
        inp.mi = self._MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        self._go(inp)

    def move(self, x: float, y: float) -> None:
        self._mouse(0x0001 | 0x8000, int(x * 65535), int(y * 65535))  # MOVE | ABSOLUTE: 0..65535 over the main display

    def button(self, which: str, down: bool) -> None:
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}[which]
        self._mouse(flags[0] if down else flags[1])

    def wheel(self, notches: int) -> None:
        self._mouse(0x0800, data=int(notches) * 120)

    def _key(self, vk: int, scan: int, flags: int) -> None:
        inp = self._INPUT(type=1)
        inp.ki = self._KEYBDINPUT(vk, scan, flags, 0, 0)
        self._go(inp)

    def key(self, vk: int, down: bool, extended: bool = False) -> None:
        self._key(vk, 0, (0x0001 if extended else 0) | (0 if down else 0x0002))

    def char(self, ch: str) -> None:
        data = ch.encode("utf-16-le")  # a character outside the basic range is two 16-bit units, sent one after the other
        for i in range(0, len(data), 2):
            code = data[i] | (data[i + 1] << 8)
            self._key(0, code, 0x0004)            # UNICODE, key down
            self._key(0, code, 0x0004 | 0x0002)   # UNICODE, key up
