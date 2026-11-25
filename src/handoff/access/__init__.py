"""Who may do what on this PC, and who is using it right now.

  levels.py    the rules: what each level may do (pure, tested on its own)
  store.py     the Admin phone and each phone's level, in the PC's database
  sessions.py  one person at a time: the session, takeover requests, timeouts
  gate.py      the single check every request from a phone passes
The server builds one `Access` and calls `access.gate.check(...)` for each request; the pages only ever ask, the PC decides.
"""
from __future__ import annotations

from typing import Callable

from ..db import Database
from . import levels
from .gate import AccessDenied, Gate
from .sessions import SessionHeld, SessionManager, SessionRefused, Settings
from .store import AccessStore

__all__ = ["Access", "AccessDenied", "Gate", "SessionHeld", "SessionManager", "SessionRefused", "Settings", "AccessStore", "levels"]


class Access:
    def __init__(self, db: Database, settings: Settings | None = None, on_event: Callable[..., None] | None = None, clock=None):
        self.store = AccessStore(db)
        kwargs = {"clock": clock} if clock is not None else {}
        self.sessions = SessionManager(settings, on_event=on_event, **kwargs)
        self.gate = Gate(self.store, self.sessions)

    def view(self, device_id: str) -> dict:
        """Everything the phone's Access screen and banner need, in one answer."""
        out = self.sessions.view(device_id)
        holder = self.sessions.holder_id()
        if holder and out.get("holder"):
            out["holder"]["level"] = self.store.level(holder)  # the level now, not the one they started with (the Admin may have changed it since)
        level = self.store.level(device_id)
        out["me"] = {"role": self.store.role(device_id), "level": level, "level_name": levels.NAMES[level]}
        return out
