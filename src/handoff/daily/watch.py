"""Watchers: tell the phone when something on this PC finishes.

  process   a program stops running (an export, an installer, a game)
  folder    a new file lands in a folder and stops growing (a download is finished)
  quiet     the processor goes quiet (a render or an update has finished)

Each one fires once, then goes away. It may also do one power action afterwards ("...then put the PC to sleep"), limited to what
`power.remote_actions` allows.
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path
from typing import Callable

from ..db import Database
from .schedule import MAX_TEXT

try:
    import psutil
except ImportError:  # the rest of the app works without it
    psutil = None

KINDS = ("process", "folder", "quiet")
MAX_WATCHERS = 20
TEMP_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload", ".!ut", ".aria2")
STABLE_TICKS = 2          # a file must keep the same size this many checks in a row
QUIET_TICKS = 6           # the processor must stay quiet this many checks in a row (30 s at the usual pace)
GONE_TICKS = 2            # a program must be missing this many checks in a row


class WatchError(ValueError):
    """The watcher can't be set; the message says why."""


def _working_sets() -> dict[int, int]:
    """Memory in use by each program (bytes), straight from Windows. psutil asks Windows the same thing through a slow route that takes seconds on a PC
    with hundreds of programs; this takes a few hundredths of a second. Programs this user may not look into are left out."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    found: dict[int, int] = {}
    for pid in psutil.pids():
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                found[pid] = counters.WorkingSetSize
        finally:
            kernel32.CloseHandle(handle)
    return found


def list_processes(limit: int = 25) -> list[dict]:
    """The programs running now, biggest memory first, one line per program name."""
    if psutil is None:
        return []
    fast: dict[int, int] = {}
    if sys.platform == "win32":
        try:
            fast = _working_sets()
        except Exception:  # noqa: BLE001 - fall back to the slower, more general way below
            fast = {}
    totals: dict[str, dict] = {}
    for proc in psutil.process_iter(["name"] if fast else ["name", "memory_info"]):
        info = proc.info
        name = info.get("name") or ""
        if not name or name.lower() in ("system", "system idle process", "registry", "memory compression", "secure system"):
            continue
        if fast:
            if proc.pid not in fast:
                continue
            rss = fast[proc.pid]
        else:
            rss = info.get("memory_info").rss if info.get("memory_info") else 0
        entry = totals.setdefault(name, {"name": name, "count": 0, "memory_mb": 0})
        entry["count"] += 1
        entry["memory_mb"] += round(rss / 1048576)
    return sorted(totals.values(), key=lambda e: e["memory_mb"], reverse=True)[:limit]


def process_running(name: str) -> bool:
    if psutil is None:
        return False
    wanted = name.lower()
    return any((p.info.get("name") or "").lower() == wanted for p in psutil.process_iter(["name"]))


def folder_files(folder: Path) -> dict[str, int]:
    found = {}
    try:
        for entry in os.scandir(folder):
            if entry.is_file(follow_symlinks=False):
                found[entry.name] = entry.stat().st_size
    except OSError:
        pass
    return found


def _not_locked(path: Path) -> bool:
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


class Watchers:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time, notify: Callable[[str, str, str], None],
                 run_power: Callable[[str], str], allowed_power: Callable[[], set[str]], note: Callable[..., None] = lambda *a, **k: None,
                 running: Callable[[str], bool] | None = None, cpu: Callable[[], float] | None = None, files: Callable[[Path], dict[str, int]] | None = None,
                 resolve_folder: Callable[[str], Path] | None = None):
        self.db, self._clock, self._notify, self._run_power, self._allowed, self._note = db, clock, notify, run_power, allowed_power, note
        self._running = running or process_running
        self._cpu = cpu or (lambda: psutil.cpu_percent(interval=None) if psutil else 100.0)
        self._files = files or folder_files
        self._resolve_folder = resolve_folder or (lambda p: Path(p))
        self._state: dict[str, dict] = {}

    def add(self, *, kind: str, target: str, then: str = "", device: str = "", device_name: str = "") -> dict:
        if kind not in KINDS:
            raise WatchError("That kind of watcher is not known.")
        if then and then not in self._allowed():
            raise WatchError("That action is switched off for the phone. To allow it, add it to power.remote_actions in config.yaml on the PC.")
        if self.db.one("SELECT COUNT(*) AS n FROM daily_watch")["n"] >= MAX_WATCHERS:
            raise WatchError(f"There are already {MAX_WATCHERS} watchers. Remove one first.")
        target = str(target or "").strip()
        if kind == "process":
            if not target or len(target) > 100 or any(ch in target for ch in '/\\*?"<>|'):
                raise WatchError("Pick a program from the list.")
            if not self._running(target):
                raise WatchError(f"{target} is not running now.")
            label = f"{target} to finish"
        elif kind == "folder":
            folder = self._resolve_folder(target)
            if not folder.is_dir():
                raise WatchError("That folder does not exist on this PC.")
            target, label = str(folder), f"A new file in {folder.name or folder}"
        else:
            try:
                limit = int(target or 15)
            except ValueError:
                raise WatchError("Pick how quiet the processor must get.") from None
            if not 1 <= limit <= 60:
                raise WatchError("Pick a level between 1% and 60%.")
            target, label = str(limit), f"The PC to go quiet (under {limit}% busy)"
        watch_id = secrets.token_hex(6)
        self.db.run("INSERT INTO daily_watch (id, kind, target, label, then_do, device, device_name, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (watch_id, kind, target, label[:MAX_TEXT], then, device, device_name[:60], self._clock()))
        return self._public(self.db.one("SELECT * FROM daily_watch WHERE id=?", (watch_id,)))

    @staticmethod
    def _public(row) -> dict:
        return {"id": row["id"], "kind": row["kind"], "target": row["target"], "label": row["label"], "then": row["then_do"], "by": row["device_name"], "since": row["created"]}

    def list(self) -> list[dict]:
        return [self._public(r) for r in self.db.rows("SELECT * FROM daily_watch ORDER BY created")]

    def remove(self, watch_id: str) -> bool:
        self._state.pop(watch_id, None)
        return self.db.run("DELETE FROM daily_watch WHERE id=?", (watch_id,)) > 0

    def tick(self) -> list[dict]:
        fired = []
        for row in self.db.rows("SELECT * FROM daily_watch ORDER BY created"):
            try:
                message = self._check(row)
            except Exception:  # noqa: BLE001 - one watcher failing must never stop the others
                continue
            if message is None:
                continue
            self.db.run("DELETE FROM daily_watch WHERE id=?", (row["id"],))
            self._state.pop(row["id"], None)
            self._finish(row, message)
            fired.append({"id": row["id"], "kind": row["kind"], "message": message})
        return fired

    def _finish(self, row, message: str) -> None:
        then = row["then_do"]
        body = message + (f" Now: {then}." if then else "")
        self._note("watch_fired", kind=row["kind"], label=row["label"], then=then)
        try:
            self._notify("Finished", body, "watch")
        except Exception:  # noqa: BLE001
            pass
        if then and then in self._allowed():
            try:
                self._run_power(then)
            except Exception:  # noqa: BLE001
                pass

    def _check(self, row) -> str | None:
        state = self._state.setdefault(row["id"], {})
        kind, target = row["kind"], row["target"]
        if kind == "process":
            state["gone"] = 0 if self._running(target) else state.get("gone", 0) + 1
            return f"{target} has finished." if state["gone"] >= GONE_TICKS else None
        if kind == "quiet":
            state["quiet"] = state.get("quiet", 0) + 1 if self._cpu() < int(target) else 0
            return "The PC has gone quiet. Whatever it was busy with is probably done." if state["quiet"] >= QUIET_TICKS else None
        folder = Path(target)
        now = self._files(folder)
        if "base" not in state:  # what was already there is not news
            state["base"], state["seen"] = set(now), {}
            return None
        for name, size in now.items():
            if name in state["base"] or name.lower().endswith(TEMP_SUFFIXES):
                continue
            last = state["seen"].get(name)
            same = last is not None and last[0] == size
            state["seen"][name] = (size, last[1] + 1 if same else 1)
            if state["seen"][name][1] >= STABLE_TICKS and size > 0 and _not_locked(folder / name):
                return f"{name} has arrived in {folder.name or folder}."
        return None
