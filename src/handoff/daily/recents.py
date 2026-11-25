"""Recent files and pinned places.

Recent files come from Windows' own Recent list. Pinned places are chosen on the phone and kept in the database. Neither lets the phone
open anything the file rules would not: tapping one goes through the same folder permission as the Files tab.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from ..db import Database
from . import places

MAX_FAVS = 30
RECENT_SECONDS = 30
_SCRIPT = ("$s = New-Object -ComObject WScript.Shell; "
           "Get-ChildItem \"$env:APPDATA\\Microsoft\\Windows\\Recent\" -Filter *.lnk -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 60 | "
           "ForEach-Object { [pscustomobject]@{ t = $s.CreateShortcut($_.FullName).TargetPath; m = [int64]([DateTimeOffset]$_.LastWriteTime).ToUnixTimeSeconds() } } | ConvertTo-Json -Compress")


class FavError(ValueError):
    """The place can't be pinned; the message says why."""


def _windows_recent() -> list[dict]:
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT], capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(done.stdout or "[]")
    except Exception:  # noqa: BLE001 - no PowerShell, odd output: no list
        return []
    data = [data] if isinstance(data, dict) else data
    return [{"path": d.get("t", ""), "time": d.get("m", 0)} for d in data if isinstance(d, dict) and d.get("t")]


class Recents:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time, source: Callable[[], list[dict]] = _windows_recent,
                 allowed: Callable[[str], bool] = lambda path: True):
        self.db, self._clock, self._source, self._allowed = db, clock, source, allowed
        self._cache: tuple[float, list[dict]] | None = None

    def recent_files(self, limit: int = 30) -> list[dict]:
        now = self._clock()
        if self._cache is None or now - self._cache[0] > RECENT_SECONDS:
            self._cache = (now, self._source())
        out, seen = [], set()
        for entry in self._cache[1]:
            path = entry["path"]
            key = path.lower()
            if key in seen or not os.path.exists(path) or not self._allowed(path):
                continue
            seen.add(key)
            p = Path(path)
            out.append({"path": path, "name": p.name or path, "folder": str(p.parent), "is_dir": p.is_dir(), "time": entry["time"]})
            if len(out) >= limit:
                break
        return out

    # -- pinned places ---------------------------------------------------------------------------------------------------

    def favourites(self) -> list[dict]:
        return [{"path": r["path"], "label": r["label"] or Path(r["path"]).name or r["path"], "exists": os.path.exists(r["path"])}
                for r in self.db.rows("SELECT * FROM daily_favs ORDER BY created")]

    def pin(self, path: str, label: str = "") -> dict:
        path = str(path or "").strip()
        if not path or len(path) > 1000:
            raise FavError("Pick a folder or a file to pin.")
        real = Path(path)
        if not real.exists():
            raise FavError("That place does not exist on this PC.")
        if self.db.one("SELECT COUNT(*) AS n FROM daily_favs")["n"] >= MAX_FAVS and not self.db.one("SELECT 1 FROM daily_favs WHERE path=?", (str(real),)):
            raise FavError(f"You can pin up to {MAX_FAVS} places. Unpin one first.")
        self.db.run("INSERT INTO daily_favs (path, label, created) VALUES (?, ?, ?) ON CONFLICT(path) DO UPDATE SET label=excluded.label", (str(real), " ".join(str(label or "").split())[:60], self._clock()))
        return next(f for f in self.favourites() if f["path"] == str(real))

    def unpin(self, path: str) -> bool:
        return self.db.run("DELETE FROM daily_favs WHERE path=?", (str(path),)) > 0

    @staticmethod
    def suggestions() -> list[dict]:
        return places.all_places()
