"""Is this PC at its lock screen?

On Windows 10 and 11 the sign-in screen is drawn by LogonUI.exe, which runs in the console session while the PC is locked and not otherwise.
That is the signal used here. It is a heuristic, not a promise: if it is ever wrong the typed sign-in simply has no effect on a PC that
was not locked (it is typed on the secure desktop only, which exists only while locked).
"""
from __future__ import annotations

import os
from typing import Callable


def _session_of(pid: int) -> int | None:
    import ctypes

    session = ctypes.c_uint32()
    if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
        return int(session.value)
    return None


def console_session() -> int | None:
    import ctypes

    sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    return None if sid == 0xFFFFFFFF else int(sid)


def logonui_in_console(processes: Callable[[], list[tuple[str, int]]] | None = None, session_of: Callable[[int], int | None] = _session_of,
                       console: Callable[[], int | None] = console_session) -> bool | None:
    """True/False, or None when it can't be told (not Windows, or no access to the process list)."""
    if processes is None and os.name != "nt":
        return None
    try:
        if processes is None:
            import psutil

            processes = lambda: [(p.info["name"] or "", p.info["pid"]) for p in psutil.process_iter(["name", "pid"])]  # noqa: E731
        want = console()
        if want is None:
            return None
        return any(name.lower() == "logonui.exe" and session_of(pid) == want for name, pid in processes())
    except Exception:  # noqa: BLE001
        return None


def is_locked() -> bool | None:
    return logonui_in_console()
