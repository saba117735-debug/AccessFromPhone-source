"""Black out this PC's screen from the phone, while the PC keeps running and the phone keeps seeing (and controlling) the real desktop.

Why not just switch the monitor off? On a PC with "Modern Standby" (S0 Low Power Idle), Windows freezes every program, this app too, and may drop
the Wi-Fi as soon as the display goes off, so the phone can no longer reach it. This keeps the display ON as far as Windows knows and only
covers it with a black window:

  - The window is hidden from screen capture (SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE), so the phone's picture is the real desktop,
    not black. If this Windows can't hide it from capture the black-out is refused: it would show the phone a black screen.
  - Only REAL input at this PC (its own mouse or keyboard) takes the black away. Mouse and keys sent from a phone are marked "injected" by
    Windows and are ignored, so controlling the PC from the phone does not light the monitor up.
  - The window is click-free to leave: the touch that wakes the screen is swallowed, so it never clicks something you could not see.
  - On a laptop the backlight is turned right down as well (and given back afterwards, even after a crash), so the panel is as dark as it can be
    without Windows ever believing the display is off.
  - The cover lets the pointer through, so the PC's real pointer (and its real shape, which the phone draws) is still there.
  - While it is up it asks Windows not to sleep or turn the display off.
  - Turning it off, the panic key, and the app closing all remove it, so nobody is left with a black screen and no way back.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("handoff.blackout")

WDA_EXCLUDEFROMCAPTURE = 0x11          # Windows 10 version 2004 (build 19041) and newer
MIN_BUILD = 19041
ARM_AFTER = 1.2                        # seconds the PC is left alone after the screen goes dark (the tap that asked is not "someone at the PC")
MOVE_PIXELS = 12                       # a mouse that only jitters a little does not count as a person


def wake_decision(kind: str, injected: bool, moved: float, armed: bool) -> bool:
    """Should this input at the PC take the black away? `kind`: move, button, wheel or key. Injected input (from a phone) never does."""
    if injected or not armed:
        return False
    if kind == "move":
        return moved >= MOVE_PIXELS
    return kind in ("button", "wheel", "key")


def _powershell(script: str, timeout: float = 15) -> str:
    """Run a short PowerShell script with no window; its output, or "" when it could not run."""
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (done.stdout or "").strip()
    except Exception:  # noqa: BLE001 - no PowerShell, a timeout: the cover works without dimming
        return ""


_SET = ("Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Select-Object -First 1) "
        "-MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness=[byte]{level}}} | Out-Null")


def dim_backlight() -> int | None:
    """Turn the laptop's backlight right down. Returns the brightness it had (0 to 100), or None where that cannot be done (a desktop's own
    monitor): the black cover alone is then all there is. It stays an ordinary "display on" to Windows, so nothing freezes."""
    now = _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | Select-Object -First 1).CurrentBrightness")
    try:
        before = int(now)
    except ValueError:
        return None
    if before > 0:
        _powershell(_SET.format(level=0))
    return before


def restore_backlight(level: int) -> None:
    _powershell(_SET.format(level=max(1, min(100, int(level)))))


def recover(state_file: Path | None) -> bool:
    """A power cut or a crash while the screen was dark must not leave a laptop at 0%: put back what the note says, once, at the next start."""
    if state_file is None or not Path(state_file).is_file():
        return False
    try:
        level = int(json.loads(Path(state_file).read_text(encoding="utf-8")).get("brightness", 0))
    except (OSError, ValueError, TypeError):
        level = 0
    if level > 0:
        restore_backlight(level)
    try:
        Path(state_file).unlink()
    except OSError:
        pass
    return True


def supported() -> bool:
    """True on a Windows that can hide a window from screen capture."""
    return os.name == "nt" and sys.getwindowsversion().build >= MIN_BUILD


class Blackout:
    """One black cover for the whole desktop. `on()` and `off()` are safe from any thread and safe to repeat."""

    def __init__(self, on_wake: Callable[[], None] | None = None, state_file: Path | None = None, dim: bool = True) -> None:
        self.on_wake = on_wake              # told when someone at the PC took the black away
        self.state_file = state_file        # where the brightness to give back is noted while the screen is dark (see `recover`)
        self.dim = dim                      # also turn the laptop's backlight down: the nearest thing to the screen being off
        self._dim_thread: threading.Thread | None = None
        self._restore_thread: threading.Thread | None = None
        self._level: int | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._ready = threading.Event()
        self._error = ""
        self._stop = threading.Event()

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and bool(self._hwnd)

    def on(self) -> str:
        """Cover the screen. Returns "" when it is dark, or a sentence saying why it could not be."""
        if not supported():
            return "This PC's Windows cannot hide the black cover from the phone's picture, so the screen was left as it is."
        with self._lock:
            if self.active:
                return ""
            self._ready.clear()
            self._error = ""
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="blackout", daemon=True)
            self._thread.start()
        if not self._ready.wait(5):
            self.off()
            return "The black cover did not start in time."
        if not self._error and self.dim:
            self._dim_thread = threading.Thread(target=self._dim, name="blackout-dim", daemon=True)
            self._dim_thread.start()
        return self._error

    def _dim(self) -> None:
        level = dim_backlight()
        if level is None or level <= 0:
            return
        self._level = level
        if self.state_file is not None:
            try:
                Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)
                Path(self.state_file).write_text(json.dumps({"brightness": level}), encoding="utf-8")
            except OSError:
                pass

    def _restore(self) -> None:
        dim = self._dim_thread
        if dim is not None:
            dim.join(20)                     # the dimming may still be running if the cover was taken away very quickly
        level, self._level = self._level, None
        if level:
            restore_backlight(level)
            if self.state_file is not None:
                try:
                    Path(self.state_file).unlink()
                except OSError:
                    pass

    def close(self) -> None:
        """The app is ending: take the cover away AND wait until the brightness is back."""
        self.off()
        thread = self._restore_thread
        if thread is not None:
            thread.join(25)

    def off(self) -> bool:
        """Take the black away. Returns whether there was any."""
        with self._lock:
            thread, hwnd = self._thread, self._hwnd
            if thread is None or not thread.is_alive():
                self._thread, self._hwnd = None, 0
                return False
            self._stop.set()
        try:
            import ctypes

            WM_CLOSE = 0x0010
            if hwnd:
                ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        thread.join(3)
        with self._lock:
            self._thread, self._hwnd = None, 0
        return True

    # ------------------------------------------------------------------------------------------- the window (its own thread)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class MSLL(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        class KBLL(ctypes.Structure):
            _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        user32.SetCursor.argtypes = [wintypes.HANDLE]
        ctypes.windll.gdi32.GetStockObject.restype = wintypes.HBRUSH
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        WM_DESTROY, WM_CLOSE, WM_DISPLAYCHANGE = 0x0002, 0x0010, 0x007E
        WS_POPUP, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000000, 0x8, 0x80, 0x08000000
        WS_EX_LAYERED, WS_EX_TRANSPARENT, LWA_ALPHA = 0x80000, 0x20, 0x2   # click-through: the pointer, and its real shape, are those of whatever is underneath
        HWND_TOPMOST, SWP_NOACTIVATE, SWP_SHOWWINDOW = ctypes.c_void_p(-1), 0x0010, 0x0040
        WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
        WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
        BUTTON_DOWN = {0x0201, 0x0204, 0x0207, 0x020B}   # left, right, middle, X buttons
        KEY_DOWN = {0x0100, 0x0104}
        LLMHF_INJECTED, LLKHF_INJECTED = 0x1, 0x10
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2

        state = {"armed_at": time.monotonic() + ARM_AFTER, "origin": None, "woke": False}
        hooks: list = []
        keep = []  # the callbacks must outlive the hooks, or Windows calls freed memory

        def cover() -> None:
            x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)       # the virtual screen: every monitor
            w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)

        def wake() -> None:
            if state["woke"]:
                return
            state["woke"] = True
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            if self.on_wake:
                try:
                    threading.Thread(target=self.on_wake, name="blackout-wake", daemon=True).start()
                except Exception:  # noqa: BLE001
                    pass

        def armed() -> bool:
            return time.monotonic() >= state["armed_at"]

        @HOOKPROC
        def mouse_hook(code, wparam, lparam):
            if code >= 0 and not state["woke"]:
                info = ctypes.cast(lparam, ctypes.POINTER(MSLL)).contents
                injected = bool(info.flags & LLMHF_INJECTED)
                if wparam == WM_MOUSEMOVE:
                    if state["origin"] is None:
                        state["origin"] = (info.x, info.y)
                    ox, oy = state["origin"]
                    moved = ((info.x - ox) ** 2 + (info.y - oy) ** 2) ** 0.5
                    if wake_decision("move", injected, moved, armed()):
                        wake()
                elif wparam in BUTTON_DOWN or wparam in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                    kind = "wheel" if wparam in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL) else "button"
                    if wake_decision(kind, injected, 0.0, armed()):
                        wake()
                        return 1   # this touch only wakes the screen: it must not click something that was never seen
            return user32.CallNextHookEx(None, code, wparam, lparam)

        @HOOKPROC
        def key_hook(code, wparam, lparam):
            if code >= 0 and not state["woke"] and wparam in KEY_DOWN:
                info = ctypes.cast(lparam, ctypes.POINTER(KBLL)).contents
                if wake_decision("key", bool(info.flags & LLKHF_INJECTED), 0.0, armed()):
                    wake()
                    return 1
            return user32.CallNextHookEx(None, code, wparam, lparam)

        @WNDPROC
        def wndproc(h, msg, wparam, lparam):
            if msg == WM_DISPLAYCHANGE and hwnd:
                cover()                         # a monitor was added or removed: cover them all again
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(h)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(h, msg, wparam, lparam)

        hwnd = 0
        registered = False
        class_name = f"AfpBlackout{os.getpid()}_{int(time.time() * 1000) % 100000}"
        try:
            instance = kernel32.GetModuleHandleW(None)
            wc = WNDCLASS()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = wndproc, instance, class_name
            wc.hbrBackground = ctypes.windll.gdi32.GetStockObject(4)   # BLACK_BRUSH
            wc.hCursor = None
            if not user32.RegisterClassW(ctypes.byref(wc)):
                raise OSError("could not register the window class")
            registered = True
            hwnd = user32.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT, class_name, "AccessFromPhone black-out", WS_POPUP,
                                          0, 0, 1, 1, None, None, instance, None)
            if not hwnd:
                raise OSError("could not create the window")
            user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
            if not user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA):
                raise OSError("could not make the cover solid")
            if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
                user32.DestroyWindow(hwnd)
                hwnd = 0
                raise OSError("this Windows cannot hide the cover from screen capture")
            keep += [wndproc, mouse_hook, key_hook]
            hooks.append(user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_hook, instance, 0))
            hooks.append(user32.SetWindowsHookExW(WH_KEYBOARD_LL, key_hook, instance, 0))
            if not all(hooks):
                raise OSError("could not watch this PC's own mouse and keyboard (so nothing could take the black away)")
            kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            cover()
            self._hwnd = int(hwnd)
        except Exception as exc:  # noqa: BLE001 - say why; never leave a half-made cover behind
            self._error = f"Could not black out the screen: {exc}."
            log.warning("blackout: %s", exc)
            for hook in hooks:
                if hook:
                    user32.UnhookWindowsHookEx(hook)
            if hwnd:
                user32.DestroyWindow(hwnd)
            if registered:
                user32.UnregisterClassW(class_name, kernel32.GetModuleHandleW(None))
            self._ready.set()
            return
        self._ready.set()
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
                if self._stop.is_set() and not user32.IsWindow(hwnd):
                    break
        finally:
            for hook in hooks:
                if hook:
                    user32.UnhookWindowsHookEx(hook)
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            user32.UnregisterClassW(class_name, kernel32.GetModuleHandleW(None))
            self._hwnd = 0
            if self.dim:                    # however it ended (a touch at the PC, the phone, the panic key): the light comes back
                self._restore_thread = threading.Thread(target=self._restore, name="blackout-restore", daemon=True)
                self._restore_thread.start()


# One cover for the whole app: the power action, the Home screen, the panic key and shutting down all talk to this.
blackout = Blackout()
