"""The Pause switch: one tap on a phone stops every phone from reaching this PC, until the owner resumes it on the PC itself.

The state is a small file next to the database, so a restart of the app does not quietly switch phone access back on.
A phone can only turn it ON. Turning it OFF is possible only on the PC (its own page, 127.0.0.1), never from a phone.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path


class PauseSwitch:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("paused") else None
        except (OSError, ValueError):
            return None

    @property
    def paused(self) -> bool:
        return self._state is not None

    def info(self) -> dict:
        s = self._state
        return {"paused": s is not None, "by": (s or {}).get("by", ""), "since": (s or {}).get("since", "")}

    def pause(self, by: str) -> None:
        with self._lock:
            self._state = {"paused": True, "by": by, "since": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(self._state), encoding="utf-8")
            except OSError:
                pass  # still paused for this run; it just would not survive a restart

    def resume(self) -> None:
        with self._lock:
            self._state = None
            try:
                self._path.unlink()
            except OSError:
                pass


class Burst:
    """Counts how many times something happened lately, to tell a slip of the finger from someone guessing."""

    def __init__(self, limit: int = 3, window: float = 600.0, quiet: float = 600.0, clock=time.monotonic):
        self.limit, self.window, self.quiet, self._clock = limit, window, quiet, clock
        self._hits: deque[float] = deque()
        self._last_alert = -1e9

    def hit(self) -> bool:
        """Record one. True exactly when this one crosses the limit and nobody was told in the last `quiet` seconds."""
        now = self._clock()
        self._hits.append(now)
        while self._hits and now - self._hits[0] > self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit and now - self._last_alert > self.quiet:
            self._last_alert = now
            return True
        return False
