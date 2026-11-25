"""One person at a time. The phone that holds the session may change things on this PC; the others can look, or ask for a turn.

  FREE ──A starts──▶ HELD(A) ──B asks──▶ ASKED ──A ends / A is idle or gone──▶ HELD(B)
                        ▲                  └──A continues / A is busy and silent──▶ HELD(A)

* A lease: the holder's phone is "there" while it keeps talking to the PC (its page checks in every couple of seconds). Gone for `lease` seconds = the session is free.
* Idle: nothing changed for `idle` seconds = the session is free.
* A takeover request waits `ask` seconds for the holder. An active holder who does not answer keeps the session (never kicked in the middle of a task);
  an idle or vanished holder loses it.
* After a "continue", the same phone waits `cooldown` seconds before asking again.
The Admin phone and the Owner can end any session at once. Time comes from an injectable clock, so every timing rule is tested without waiting.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable


class SessionHeld(Exception):
    """Someone else holds the session."""

    def __init__(self, holder: str, holder_id: str):
        super().__init__(f"{holder} is using this PC.")
        self.holder, self.holder_id = holder, holder_id


class SessionRefused(Exception):
    """A request that cannot be made right now (a cooldown, or nothing to take over)."""


@dataclass
class _Holder:
    device_id: str
    name: str
    level: str
    since: float
    seen: float       # last time this phone talked to the PC
    acted: float      # last time it changed something


@dataclass
class _Ask:
    device_id: str
    name: str
    level: str
    message: str
    at: float


@dataclass
class Settings:
    lease: float = 60.0
    idle: float = 300.0
    ask: float = 60.0
    cooldown: float = 120.0


class SessionManager:
    def __init__(self, settings: Settings | None = None, clock: Callable[[], float] = time.monotonic, on_event: Callable[..., None] | None = None):
        self.cfg = settings or Settings()
        self._now = clock
        self._emit = on_event or (lambda *a, **k: None)
        self._lock = threading.RLock()
        self._holder: _Holder | None = None
        self._ask: _Ask | None = None
        self._cooldown: dict[str, float] = {}  # device id -> when it may ask again
        self._outcome: dict[str, tuple[str, float, str]] = {}  # a requester's last answer: (what, when, holder name)

    # ------------------------------------------------------------------ internals (call with the lock held)

    def _expire(self) -> None:
        h, now = self._holder, self._now()
        if h is not None and (now - h.seen > self.cfg.lease or now - h.acted > self.cfg.idle):
            why = "lost" if now - h.seen > self.cfg.lease else "idle"
            self._holder = None
            self._emit("session_ended", device=h.name, device_id=h.device_id, reason=why)
            if self._ask is not None:  # somebody was waiting: their turn now
                self._grant_ask("the holder was gone" if why == "lost" else "the holder was idle")
        a = self._ask
        if a is not None and self._holder is not None and now - a.at > self.cfg.ask:
            self._ask = None  # the holder is busy and did not answer: they keep the session
            self._cooldown[a.device_id] = now + self.cfg.cooldown
            self._outcome[a.device_id] = ("kept", now, self._holder.name)
            self._emit("takeover_answered", device=a.name, device_id=a.device_id, answer="no answer, holder keeps it", holder=self._holder.name)

    def _start(self, device_id: str, name: str, level: str) -> _Holder:
        now = self._now()
        self._holder = _Holder(device_id, name, level, now, now, now)
        self._emit("session_started", device=name, device_id=device_id, level=level)
        return self._holder

    def _grant_ask(self, why: str) -> None:
        a, self._ask = self._ask, None
        if a is None:
            return
        self._start(a.device_id, a.name, a.level)
        self._outcome[a.device_id] = ("granted", self._now(), "")
        self._emit("takeover_answered", device=a.name, device_id=a.device_id, answer="handed over: " + why)

    # ------------------------------------------------------------------ what requests call

    def touch(self, device_id: str) -> None:
        """Any call from a phone shows it is still there. For the holder this renews the lease."""
        with self._lock:
            if self._holder is not None and self._holder.device_id == device_id:
                self._holder.seen = self._now()
            self._expire()

    def act(self, device_id: str, name: str, level: str) -> None:
        """A change is about to be made by this phone. Starts the session if it is free; raises SessionHeld if someone else has it."""
        with self._lock:
            self._expire()
            h = self._holder
            if h is None:
                self._start(device_id, name, level)
            elif h.device_id != device_id:
                raise SessionHeld(h.name, h.device_id)
            else:
                h.acted = h.seen = self._now()
                h.level = level

    def start(self, device_id: str, name: str, level: str) -> None:
        self.act(device_id, name, level)

    def end(self, device_id: str, reason: str = "ended") -> bool:
        """The holder ends their own session. Whoever was waiting gets it."""
        with self._lock:
            self._expire()
            h = self._holder
            if h is None or h.device_id != device_id:
                return False
            self._holder = None
            self._emit("session_ended", device=h.name, device_id=h.device_id, reason=reason)
            if self._ask is not None:
                self._grant_ask("the holder finished")
            return True

    def release(self, by_name: str) -> bool:
        """The Admin or the Owner ends whoever holds it. The waiting phone, if any, is NOT given it: the person who ended it chooses what happens next."""
        with self._lock:
            self._expire()
            h, self._holder = self._holder, None
            if h is None:
                return False
            self._ask = None
            self._emit("session_ended", device=h.name, device_id=h.device_id, reason=f"ended by {by_name}")
            return True

    def end_all(self, reason: str) -> None:
        """Pause: nobody holds anything and nobody waits."""
        with self._lock:
            if self._holder is not None:
                h, self._holder = self._holder, None
                self._emit("session_ended", device=h.name, device_id=h.device_id, reason=reason)
            self._ask = None

    def request_takeover(self, device_id: str, name: str, level: str, message: str = "") -> str:
        """Ask the holder for a turn. Returns "started" (it was free), "asked", or "mine" (you already have it)."""
        with self._lock:
            self._expire()
            h = self._holder
            if h is None:
                self._start(device_id, name, level)
                return "started"
            if h.device_id == device_id:
                return "mine"
            wait = self._cooldown.get(device_id, 0.0) - self._now()
            if wait > 0:
                raise SessionRefused(f"{h.name} asked to keep going. You can ask again in {int(wait) + 1} seconds.")
            if self._ask is not None and self._ask.device_id != device_id:
                raise SessionRefused(f"{self._ask.name} is already waiting for a turn.")
            self._ask = _Ask(device_id, name, level, " ".join(message.split())[:120], self._now())
            self._outcome.pop(device_id, None)
            self._emit("takeover_requested", device=name, device_id=device_id, holder=h.name, holder_id=h.device_id)
            return "asked"

    def cancel_request(self, device_id: str) -> None:
        with self._lock:
            if self._ask is not None and self._ask.device_id == device_id:
                self._ask = None

    def answer(self, holder_id: str, end_session: bool) -> str:
        """The holder's answer to a waiting request. Returns "handed over", "kept" or "nothing to answer"."""
        with self._lock:
            self._expire()
            h, a = self._holder, self._ask
            if h is None or h.device_id != holder_id or a is None:
                return "nothing to answer"
            if end_session:
                self._holder = None
                self._emit("session_ended", device=h.name, device_id=h.device_id, reason="handed over")
                self._grant_ask("the holder agreed")
                return "handed over"
            self._ask = None
            self._cooldown[a.device_id] = self._now() + self.cfg.cooldown
            self._outcome[a.device_id] = ("kept", self._now(), h.name)
            self._emit("takeover_answered", device=a.name, device_id=a.device_id, answer="holder continues", holder=h.name)
            return "kept"

    def take_over(self, device_id: str, name: str, level: str, by_name: str) -> None:
        """The Admin ends whoever holds the session and takes it. The holder is told when they next look."""
        with self._lock:
            self._expire()
            h = self._holder
            if h is not None and h.device_id != device_id:
                self._emit("session_ended", device=h.name, device_id=h.device_id, reason=f"taken over by {by_name}")
                self._outcome[h.device_id] = ("taken", self._now(), by_name)
            self._ask = None
            self._start(device_id, name, level)

    # ------------------------------------------------------------------ what the phone is shown

    def view(self, device_id: str) -> dict:
        with self._lock:
            self._expire()
            now, h, a = self._now(), self._holder, self._ask
            out: dict = {"state": "free" if h is None else "mine" if h.device_id == device_id else "other", "holder": None, "ask": None, "mine_ask": None, "notice": None}
            if h is not None:
                out["holder"] = {"name": h.name, "level": h.level, "since": int(now - h.since), "idle": int(now - h.acted)}
            if a is not None and h is not None:
                left = max(0, int(self.cfg.ask - (now - a.at)))
                if h.device_id == device_id:
                    out["ask"] = {"name": a.name, "level": a.level, "message": a.message, "expires_in": left}  # the holder is asked
                if a.device_id == device_id:
                    out["mine_ask"] = {"holder": h.name, "expires_in": left}  # you are the one waiting
            got = self._outcome.get(device_id)
            if got is not None and now - got[1] < 30:  # a short-lived note: "your turn", "not now", "an Admin took over"
                out["notice"] = {"kind": got[0], "by": got[2]}
            return out

    def holder_id(self) -> str | None:
        with self._lock:
            self._expire()
            return self._holder.device_id if self._holder else None
