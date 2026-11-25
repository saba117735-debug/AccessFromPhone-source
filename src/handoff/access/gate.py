"""The one check every request from a phone passes before it can change anything.

Order: is it a known phone (done before this) → may its level do this → is it the phone holding the session. Reading (GET) needs no session.
It returns nothing when the request may go on, and raises AccessDenied (carrying the status and the words for the phone) when it may not.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import levels
from .sessions import SessionHeld, SessionManager
from .store import AccessStore


@dataclass
class AccessDenied(Exception):
    status: int
    code: str
    message: str
    extra: dict | None = None

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **(self.extra or {})}


class Gate:
    def __init__(self, store: AccessStore, sessions: SessionManager):
        self.store, self.sessions = store, sessions

    def check(self, method: str, path: str, device_id: str, device_name: str) -> None:
        self.sessions.touch(device_id)  # any call shows this phone is still there
        have = self.store.level(device_id)
        need = levels.level_for_route(method, path)
        if need is not None and not levels.at_least(have, need):
            raise AccessDenied(403, "level", levels.message_for(have, need), {"have": have, "need": need})
        if levels.needs_session(method, path) and have != "viewer":  # a view-only phone changes nothing, so it never takes (or blocks) a turn
            try:
                self.sessions.act(device_id, device_name, have)
            except SessionHeld as held:
                raise AccessDenied(409, "session_held", f"{held.holder} is using this PC. Ask them for a turn.", {"holder": held.holder}) from None

    def check_plan(self, steps: list[dict], device_id: str) -> None:
        """May this phone approve a plan that does these things? (Called when someone taps Approve.)"""
        have, need = self.store.level(device_id), levels.level_for_steps(steps)
        if not levels.at_least(have, need):
            raise AccessDenied(403, "level", levels.message_for(have, need), {"have": have, "need": need})
