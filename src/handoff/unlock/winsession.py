"""Starts a process on the sign-in desktop of the person's session. Only a program running as the system can do this.

The unlock helper is a scheduled task that runs as SYSTEM. SYSTEM lives in session 0 with no screen, so to type at the lock screen it
copies its own token, points the copy at the console session, and starts the typing helper (typer.py) on "winsta0\\Winlogon", which is
the desktop the lock screen is on. What to type is written to that process's standard input through a pipe that only it can read.

Every Windows call below has its argument types spelled out. Without that, ctypes sends handles as 32-bit numbers (or refuses them, as it
did with the "current process" handle), which fails on a 64-bit PC.

This cannot be run, or tested, without being SYSTEM and having a locked screen; tests/test_unlock.py checks everything around it.
"""
from __future__ import annotations

import json
import os
import sys


def python_for_helper() -> str:
    """The python.exe to start the typing helper with (a console one: it must not open a window on the lock screen)."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def _api():
    """The Windows functions used here, each with its argument and result types."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    H, D, B, P, V = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER, ctypes.c_void_p

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", D), ("lpSecurityDescriptor", V), ("bInheritHandle", B)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", D), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", D), ("dwY", D), ("dwXSize", D), ("dwYSize", D), ("dwXCountChars", D), ("dwYCountChars", D), ("dwFillAttribute", D),
                    ("dwFlags", D), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", V),
                    ("hStdInput", H), ("hStdOutput", H), ("hStdError", H)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", H), ("hThread", H), ("dwProcessId", D), ("dwThreadId", D)]

    def proto(fn, restype, *argtypes):
        fn.restype, fn.argtypes = restype, list(argtypes)
        return fn

    api = type("Api", (), {})()
    api.ctypes, api.wintypes = ctypes, wintypes
    api.SECURITY_ATTRIBUTES, api.STARTUPINFOW, api.PROCESS_INFORMATION = SECURITY_ATTRIBUTES, STARTUPINFOW, PROCESS_INFORMATION
    api.GetCurrentProcess = proto(kernel32.GetCurrentProcess, H)
    api.WTSGetActiveConsoleSessionId = proto(kernel32.WTSGetActiveConsoleSessionId, D)
    api.CloseHandle = proto(kernel32.CloseHandle, B, H)
    api.CreatePipe = proto(kernel32.CreatePipe, B, P(H), P(H), P(SECURITY_ATTRIBUTES), D)
    api.ReadFile = proto(kernel32.ReadFile, B, H, V, D, P(D), V)
    api.SetHandleInformation = proto(kernel32.SetHandleInformation, B, H, D, D)
    api.WriteFile = proto(kernel32.WriteFile, B, H, V, D, P(D), V)
    api.WaitForSingleObject = proto(kernel32.WaitForSingleObject, D, H, D)
    api.TerminateProcess = proto(kernel32.TerminateProcess, B, H, wintypes.UINT)
    api.GetExitCodeProcess = proto(kernel32.GetExitCodeProcess, B, H, P(D))
    api.OpenProcessToken = proto(advapi32.OpenProcessToken, B, H, D, P(H))
    api.DuplicateTokenEx = proto(advapi32.DuplicateTokenEx, B, H, D, V, ctypes.c_int, ctypes.c_int, P(H))
    api.SetTokenInformation = proto(advapi32.SetTokenInformation, B, H, ctypes.c_int, V, D)
    api.CreateProcessAsUserW = proto(advapi32.CreateProcessAsUserW, B, H, wintypes.LPCWSTR, wintypes.LPWSTR, V, V, B, D, V, wintypes.LPCWSTR,
                                     P(STARTUPINFOW), P(PROCESS_INFORMATION))
    return api


class Result:
    """What the typing helper did: its exit code, and whatever it printed (it never prints the sign-in)."""

    def __init__(self, code: int, output: str):
        self.code, self.output = code, output


WINLOGON, DEFAULT = "winsta0\\Winlogon", "winsta0\\Default"


def type_at_lock_screen(request: dict, run=None, sleep=None, clock=None, seconds: float = 24.0) -> Result:
    """Gets the sign-in box showing, then types into it.

    Windows draws the lock screen you see first (the picture and the clock) on the NORMAL desktop, as a program in your own session, and only
    a click or key press brings up the sign-in box, which lives on a separate secure desktop. Input can only be sent to the desktop that is
    getting the keyboard, so: look which one that is; if it is the normal one, click it to wake the lock screen (from a helper on that desktop);
    when the secure one is up, give it a moment to draw, then type there (from a helper on that desktop).
    """
    import time as _time

    run, sleep, clock = run or run_on, sleep or _time.sleep, clock or _time.monotonic
    deadline, notes, woke = clock() + seconds, [], 0
    while clock() < deadline:
        seen = run(WINLOGON, {"probe": True}, 8)
        name = (seen.output.strip().splitlines() or [""])[-1].strip() if seen.code == 0 else ""
        notes.append(f"keyboard is on '{name or '?'}'" if seen.code == 0 else f"probe failed: {seen.output.strip()[:200]}")
        if name.lower() == "winlogon":
            sleep(1.2)  # the sign-in box needs a moment to finish drawing, or the first characters are lost
            typed = run(WINLOGON, {**request, "wake": False}, 25)
            return typed if typed.code == 0 else Result(typed.code, "; ".join(notes + [typed.output]))
        if name.lower() == "default" and woke < 3:
            woke += 1
            waking = run(DEFAULT, {"wake_only": True}, 10)
            notes.append("woke the lock screen" if waking.code == 0 else f"waking failed: {waking.output.strip()[:200]}")
            sleep(1.5)
            continue
        sleep(0.7)
    return Result(4, "The sign-in box never appeared. " + "; ".join(list(dict.fromkeys(notes))[-6:]))  # (each different thing seen, once)


def run_on(desktop: str, request: dict, timeout_seconds: float = 25.0) -> Result:
    """Runs `python -m handoff.unlock.typer` on the given desktop (winsta0\\Winlogon or winsta0\\Default) with `request` on its standard input."""
    if os.name != "nt":
        raise OSError("Unlocking works on Windows only.")
    w = _api()
    ctypes, wintypes = w.ctypes, w.wintypes

    TOKEN_ALL_ACCESS, MAXIMUM_ALLOWED, SecurityImpersonation, TokenPrimary, TokenSessionId = 0xF01FF, 0x02000000, 2, 1, 12
    STARTF_USESTDHANDLES, CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT = 0x100, 0x08000000, 0x400
    HANDLE_FLAG_INHERIT, NO_SESSION = 0x1, 0xFFFFFFFF

    def fail(what: str):
        raise OSError(f"{what} (Windows error {ctypes.get_last_error()}).")

    session = w.WTSGetActiveConsoleSessionId()
    if session == NO_SESSION:
        raise OSError("There is no one at the PC's screen to sign in.")

    token, primary = wintypes.HANDLE(), wintypes.HANDLE()
    if not w.OpenProcessToken(w.GetCurrentProcess(), TOKEN_ALL_ACCESS, ctypes.byref(token)):
        fail("Couldn't open the helper's own token")
    handles = [token]
    try:
        if not w.DuplicateTokenEx(token, MAXIMUM_ALLOWED, None, SecurityImpersonation, TokenPrimary, ctypes.byref(primary)):
            fail("Couldn't copy the system token")
        handles.append(primary)
        sid = wintypes.DWORD(session)
        if not w.SetTokenInformation(primary, TokenSessionId, ctypes.byref(sid), ctypes.sizeof(sid)):
            fail("Couldn't point the token at the screen's session (the helper must run as the system)")

        attrs = w.SECURITY_ATTRIBUTES(ctypes.sizeof(w.SECURITY_ATTRIBUTES), None, True)
        read_end, write_end = wintypes.HANDLE(), wintypes.HANDLE()
        if not w.CreatePipe(ctypes.byref(read_end), ctypes.byref(write_end), ctypes.byref(attrs), 0):
            fail("Couldn't make the pipe")
        handles += [read_end, write_end]
        w.SetHandleInformation(write_end, HANDLE_FLAG_INHERIT, 0)  # only the helper gets the reading end
        out_read, out_write = wintypes.HANDLE(), wintypes.HANDLE()
        if not w.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), ctypes.byref(attrs), 0):
            fail("Couldn't make the second pipe")
        handles += [out_read, out_write]
        w.SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0)  # only the helper gets the writing end

        startup = w.STARTUPINFOW()
        startup.cb = ctypes.sizeof(w.STARTUPINFOW)
        startup.lpDesktop = desktop
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput = read_end
        startup.hStdOutput = out_write
        startup.hStdError = out_write
        info = w.PROCESS_INFORMATION()
        command = ctypes.create_unicode_buffer(f'"{python_for_helper()}" -I -m handoff.unlock.typer')
        if not w.CreateProcessAsUserW(primary, None, command, None, None, True, CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, None, None,
                                      ctypes.byref(startup), ctypes.byref(info)):
            fail("Couldn't start the typing helper on the sign-in screen")
        handles += [info.hProcess, info.hThread]
        payload = json.dumps(request).encode("utf-8")
        buffer = ctypes.create_string_buffer(payload, len(payload))
        written = wintypes.DWORD()
        w.WriteFile(write_end, ctypes.cast(buffer, ctypes.c_void_p), len(payload), ctypes.byref(written), None)
        w.CloseHandle(write_end)
        handles.remove(write_end)
        w.CloseHandle(out_write)  # ours is closed, so reading ends when the helper does
        handles.remove(out_write)
        if w.WaitForSingleObject(info.hProcess, int(timeout_seconds * 1000)) != 0:
            w.TerminateProcess(info.hProcess, 1)
            raise OSError("The typing helper took too long.")
        code = wintypes.DWORD()
        w.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        chunk, got, said = ctypes.create_string_buffer(4096), wintypes.DWORD(), b""
        while len(said) < 4096 and w.ReadFile(out_read, ctypes.cast(chunk, ctypes.c_void_p), 4096, ctypes.byref(got), None) and got.value:
            said += chunk.raw[:got.value]
        return Result(int(code.value), said.decode("utf-8", "replace").strip())
    finally:
        for handle in handles:
            if handle:
                w.CloseHandle(handle)
