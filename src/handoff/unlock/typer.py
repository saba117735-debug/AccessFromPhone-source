"""Types the sign-in at the lock screen.

This runs as a short-lived helper process on the sign-in desktop (the helper service starts it there; see winsession.py). It is told what
to do on its standard input (never on the command line, where other programs could read it), does it, and exits. Nothing is written to a
file or a log: the secret exists in memory only while the keys are being sent.

What it does, in the order the helper asks for it: say which desktop is getting the keyboard; click the middle of the screen (that dismisses the
picture-and-clock lock screen and brings up the sign-in box); type the characters and press Enter. Sending is split from planning so the plan is tested on its own: tests/test_unlock.py.
"""
from __future__ import annotations

import functools
import json
import sys
import time

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x0002, 0x0004, 0x8000


def plan_text(text: str) -> list[tuple[str, int]]:
    """The key events for typing `text`: ("unicode_down", unit), ("unicode_up", unit) for each UTF-16 unit, so any character works whatever the keyboard layout is."""
    events: list[tuple[str, int]] = []
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        unit = int.from_bytes(raw[i:i + 2], "little")
        events += [("unicode_down", unit), ("unicode_up", unit)]
    return events


def plan_enter() -> list[tuple[str, int]]:
    return [("vk_down", VK_RETURN), ("vk_up", VK_RETURN)]


@functools.lru_cache(maxsize=1)
def _structures():  # built once: a second copy would be a different class, and ctypes refuses a pointer to a different INPUT
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _Union(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _Union)]

    return ctypes, INPUT, KEYBDINPUT, MOUSEINPUT


def _user32():
    """user32 with the argument types spelled out (a bare ctypes call sends handles and sizes the wrong way on a 64-bit PC)."""
    import ctypes
    from ctypes import wintypes

    ctypes_, INPUT, _, _ = _structures()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.restype, user32.SendInput.argtypes = wintypes.UINT, [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetThreadDesktop.restype, user32.GetThreadDesktop.argtypes = wintypes.HANDLE, [wintypes.DWORD]
    user32.OpenInputDesktop.restype, user32.OpenInputDesktop.argtypes = wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.CloseDesktop.restype, user32.CloseDesktop.argtypes = wintypes.BOOL, [wintypes.HANDLE]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    return user32


def _desktop_name(user32, handle) -> str:
    import ctypes
    from ctypes import wintypes

    buffer, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
    if not user32.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):  # 2 = UOI_NAME
        return f"(name unavailable, Windows error {ctypes.get_last_error()})"
    return buffer.value


def diagnose() -> str:
    """Where this helper is standing, for the person to read when typing failed: which desktop it is on, which one is getting the keyboard, which session."""
    import ctypes
    from ctypes import wintypes

    try:
        user32 = _user32()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        mine = _desktop_name(user32, user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
        shown = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if shown:
            receiving = _desktop_name(user32, shown)
            user32.CloseDesktop(shown)
        else:
            receiving = f"(cannot be opened, Windows error {ctypes.get_last_error()})"
        session = wintypes.DWORD()
        kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session))
        return f"this helper is on desktop '{mine}' in session {session.value}; the desktop getting the keyboard is '{receiving}'"
    except Exception as exc:  # noqa: BLE001
        return f"could not look at the desktops ({type(exc).__name__})"


def _send(events: list[tuple[str, int]], delay: float) -> None:
    ctypes, INPUT, KEYBDINPUT, _ = _structures()
    user32 = _user32()
    for kind, value in events:
        item = INPUT(type=INPUT_KEYBOARD)
        if kind.startswith("unicode"):
            item.ki = KEYBDINPUT(0, value, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if kind.endswith("up") else 0), 0, 0)
        else:
            item.ki = KEYBDINPUT(value, 0, KEYEVENTF_KEYUP if kind.endswith("up") else 0, 0, 0)
        if user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
            raise OSError(f"Windows would not take the key press (Windows error {ctypes.get_last_error()})")
        time.sleep(delay)


def _click_middle() -> None:
    ctypes, INPUT, _, MOUSEINPUT = _structures()
    user32 = _user32()
    for flags in (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE):
        item = INPUT(type=INPUT_MOUSE)
        item.mi = MOUSEINPUT(32768, 32768, 0, flags, 0, 0)  # the middle of the screen, in Windows' 0..65535 coordinates
        if user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
            raise OSError(f"Windows would not take the mouse click (Windows error {ctypes.get_last_error()})")
        time.sleep(0.05)


def input_desktop_name() -> str:
    """Which desktop is getting the keyboard right now: "Default" (the normal desktop, which the picture-and-clock lock screen is drawn on) or "Winlogon" (the sign-in box)."""
    import ctypes

    user32 = _user32()
    shown = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not shown:
        return ""
    try:
        return _desktop_name(user32, shown)
    finally:
        user32.CloseDesktop(shown)


def run(request: dict) -> int:
    """Probe, wake, or wake-wait-type-Enter. 0 = it was done."""
    if request.get("probe"):  # which desktop is getting the keyboard? (printed back to the helper, which decides what to do next)
        print(input_desktop_name())
        return 0
    if request.get("wake_only"):  # on the normal desktop: dismiss the picture-and-clock lock screen so the sign-in box appears
        try:
            _click_middle()
            return 0
        except OSError as exc:
            print(f"wake failed: {exc}")
            print(diagnose())
            return 3
    secret = request.get("secret", "")
    if not isinstance(secret, str) or not secret:
        return 2
    try:
        if request.get("wake", True):
            _click_middle()
            time.sleep(float(request.get("wake_wait", 1.6)))
        _send(plan_text(secret), float(request.get("key_delay", 0.02)))
        time.sleep(0.15)
        if request.get("submit", True):
            _send(plan_enter(), 0.05)
        return 0
    except OSError as exc:
        print(f"typing failed: {exc}")  # (this goes back to the helper, never to a log with the sign-in: nothing here holds it)
        print(diagnose())
        return 3
    finally:
        secret = ""  # (the interpreter frees it right after; this only avoids keeping a name for it)


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except ValueError:
        return 2
    return run(request if isinstance(request, dict) else {})


if __name__ == "__main__":
    sys.exit(main())
