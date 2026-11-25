"""The morning briefing: one summary of how this PC is doing and what is waiting, plus whether Windows has updates pending."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Callable

from .watch import list_processes

UPDATE_CACHE_SECONDS = 1800
_UPDATE_SCRIPT = ("$s = New-Object -ComObject Microsoft.Update.Session; "
                  "$r = $s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); $r.Updates.Count")


def reboot_pending() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")):
        try:
            winreg.CloseKey(winreg.OpenKey(hive, key))
            return True
        except OSError:
            continue
    return False


def _count_updates() -> int | None:
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _UPDATE_SCRIPT], capture_output=True, text=True, timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int((done.stdout or "").strip())
    except Exception:  # noqa: BLE001 - no PowerShell, a timeout, odd output: "unknown"
        return None


class Updates:
    """How many Windows updates are waiting. Asking Windows takes a while, so it is done in the background and remembered for half an hour."""

    def __init__(self, counter: Callable[[], int | None] = _count_updates, clock: Callable[[], float] = time.time, reboot: Callable[[], bool] = reboot_pending):
        self._counter, self._clock, self._reboot = counter, clock, reboot
        self._value: int | None = None
        self._at = 0.0
        self._busy = False
        self._lock = threading.Lock()

    def _work(self) -> None:
        value = self._counter()
        with self._lock:
            self._value, self._at, self._busy = value, self._clock(), False

    def state(self, wait: bool = False) -> dict:
        with self._lock:
            stale = self._clock() - self._at > UPDATE_CACHE_SECONDS or self._at == 0.0
            start = stale and not self._busy
            if start:
                self._busy = True
        if start:
            thread = threading.Thread(target=self._work, name="updates", daemon=True)
            thread.start()
            if wait:
                thread.join(150)
        with self._lock:
            return {"count": self._value, "checking": self._busy, "reboot": self._reboot()}


def build(status: dict, *, updates: dict, inbox: dict, tasks: list[dict], watchers: list[dict], alerts: int, notifications: int | None,
          processes: Callable[[int], list[dict]] = list_processes, now: float | None = None) -> dict:
    """The briefing as plain facts, and the same as a few short sentences the phone shows first."""
    now = time.time() if now is None else now
    lines: list[str] = []
    if status.get("available"):
        battery = status.get("battery")
        if battery:
            state = "charging" if battery["plugged"] else "on battery"
            lines.append(f"Battery {battery['percent']}% and {state}.")
        mem, disk = status["memory"]["percent"], status.get("disk")
        lines.append(f"Memory {mem}% used" + (f", {disk['free'] // 1024 ** 3} GB free on the system drive." if disk else "."))
        up = status.get("uptime_s", 0)
        if up >= 3600:
            lines.append(f"Up for {up // 86400} d {up % 86400 // 3600} h." if up >= 86400 else f"Up for {up // 3600} h.")
    if updates.get("reboot"):
        lines.append("Windows is waiting for a restart to finish updating.")
    elif updates.get("count"):
        lines.append(f"{updates['count']} Windows update{'s' if updates['count'] != 1 else ''} waiting.")
    today_end = now + 86400
    soon = [t for t in tasks if t["due"] <= today_end]
    if soon:
        lines.append(f"{len(soon)} reminder{'s' if len(soon) != 1 else ''} or task{'s' if len(soon) != 1 else ''} in the next 24 hours.")
    if watchers:
        lines.append(f"Watching {len(watchers)} thing{'s' if len(watchers) != 1 else ''}.")
    if inbox.get("to_phone"):
        lines.append(f"{inbox['to_phone']} file{'s' if inbox['to_phone'] != 1 else ''} waiting in To phone.")
    if notifications:
        lines.append(f"{notifications} PC notification{'s' if notifications != 1 else ''} to read.")
    if not lines:
        lines.append("Nothing needs you right now.")
    return {"lines": lines, "status": status if status.get("available") else {"available": False}, "updates": updates, "inbox": inbox, "alerts": alerts,
            "next": soon[:3], "top_programs": processes(4)}
