"""Watching this PC for the moments worth telling the phone about BEFORE they happen: the battery running low, and Windows going to
sleep, shutting down or signing out. Once the PC is off the network it cannot tell the phone anything, so the notice has to go first.

Everything that decides something is plain Python and tested; the Windows window that listens for power messages is small.
"""
from __future__ import annotations

import itertools
import logging
import os
import threading
from typing import Callable

from . import sysinfo

log = logging.getLogger("handoff.pcwatch")


# -- battery ----------------------------------------------------------------------------

class BatteryAlert:
    """Says when the battery has just got low (and once more when critical), and then stays quiet until it has been charged."""

    def __init__(self, low: int = 15, critical: int = 5):
        self.low, self.critical = low, critical
        self._level: str | None = None  # the last alert given during this discharge
        self._plugged: bool | None = None  # what the charger was doing at the last look (None: not looked yet)

    def power_change(self, battery: dict | None) -> str | None:
        """"pc_plugged" / "pc_unplugged" the moment the charger is connected or removed (never on the first look), else None."""
        if not battery:
            return None
        plugged = bool(battery.get("plugged"))
        before, self._plugged = self._plugged, plugged
        if before is None or before == plugged:
            return None
        return "pc_plugged" if plugged else "pc_unplugged"

    def check(self, battery: dict | None) -> int | None:
        """The percentage to warn about now, or None. `battery` is sysinfo.read_battery()'s answer (None on a desktop PC)."""
        if not battery:
            return None
        if battery.get("plugged"):
            self._level = None
            return None
        percent = int(battery.get("percent", 100))
        if percent > self.low + 5:  # charged well past it: warn again next time it runs down
            self._level = None
            return None
        if percent <= self.critical and self._level != "critical":
            self._level = "critical"
            return percent
        if percent <= self.low and self._level is None:
            self._level = "low"
            return percent
        return None


# -- Windows: sleep, shutdown, sign-out ----------------------------------------------------

WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION, WM_POWERBROADCAST = 0x10, 0x11, 0x16, 0x218
PBT_APMSUSPEND = 0x4
ENDSESSION_CLOSEAPP, ENDSESSION_LOGOFF = 0x1, 0x80000000


def kind_for(message: int, wparam: int, lparam: int) -> str | None:
    """What a Windows power or session message means for the phone, or None for everything else."""
    if message == WM_POWERBROADCAST and wparam == PBT_APMSUSPEND:
        return "pc_sleep"
    if message == WM_ENDSESSION and wparam:  # the session really is ending (not a cancelled shutdown)
        if lparam & (ENDSESSION_LOGOFF | ENDSESSION_CLOSEAPP):
            return "pc_closing"  # signed out, or closed for an update: the PC stays on, this app does not
        return "pc_shutdown"  # Windows does not say whether it is a shutdown or a restart
    return None


class PowerWatch:
    """A hidden window whose only job is to hear Windows say it is going to sleep, shutting down or signing out. Windows only."""

    _names = itertools.count(1)

    def __init__(self, on_kind: Callable[[str], None]):
        self._on_kind = on_kind
        self.hwnd = 0
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._failed = False

    def start(self) -> bool:
        if os.name != "nt":
            return False
        self._thread = threading.Thread(target=self._run, name="power-watch", daemon=True)
        self._thread.start()
        return self._ready.wait(3) and not self._failed

    def stop(self) -> None:
        if os.name == "nt" and self.hwnd:
            import ctypes

            ctypes.WinDLL("user32").PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        proc_type = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", proc_type), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = lresult
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

        def handle(hwnd, message, wparam, lparam):
            try:
                if message == WM_QUERYENDSESSION:
                    return 1  # never hold up a shutdown
                kind = kind_for(message, wparam or 0, (lparam or 0) & 0xFFFFFFFF)
                if kind:
                    self._on_kind(kind)
                if message == 0x2:  # WM_DESTROY
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                log.exception("power message failed")
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        callback = proc_type(handle)  # kept alive for as long as the window exists
        instance = kernel32.GetModuleHandleW(None)
        name = f"AccessFromPhonePowerWatch{os.getpid()}_{next(self._names)}"
        window_class = WNDCLASSW(0, callback, 0, 0, instance, None, None, None, None, name)
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            self._failed = True
            self._ready.set()
            return
        # A real (hidden) top-level window: a message-only window is never sent Windows' broadcast messages.
        self.hwnd = user32.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, None, None, instance, None)
        if not self.hwnd:
            self._failed = True
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnregisterClassW(name, instance)
        self.hwnd = 0


# -- put it together ---------------------------------------------------------------------

def start(notifier, battery_reader: Callable[[], dict | None] | None = None, interval: float = 60.0) -> Callable[[], None]:
    """Begin watching. Returns the function that stops it. Does nothing (and costs nothing) when notifications are off."""
    if not notifier.enabled:
        return lambda: None
    done = threading.Event()
    alert = BatteryAlert(notifier.cfg.battery_low_percent)
    reader = battery_reader or sysinfo.read_battery

    def battery_loop() -> None:
        while True:
            try:
                battery = reader()
                change = alert.power_change(battery)
                if change:
                    notifier.notify_pc(change, percent=int(battery.get("percent", 0)))
                percent = alert.check(battery)
                if percent is not None:
                    notifier.notify_pc("pc_battery", percent=percent)
            except Exception:
                log.exception("battery check failed")
            if done.wait(interval):
                return

    threading.Thread(target=battery_loop, name="battery-watch", daemon=True).start()
    power = PowerWatch(lambda kind: notifier.notify_pc(kind, wait=True, timeout=2.0))
    if power.start():
        log.info("watching for sleep and shutdown")

    def stop() -> None:
        done.set()
        power.stop()

    return stop
