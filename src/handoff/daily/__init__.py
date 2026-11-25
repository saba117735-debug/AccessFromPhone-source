"""Everyday helpers for the phone: quick controls, an inbox, notes, reminders, watchers, PC notifications, a briefing, recents, printing and snapshots.

`Daily` owns them and the one background thread that runs reminders and watchers. The web routes are in `routes.py`.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from .. import power, sysinfo
from ..config import Settings
from ..push import Message
from . import audio_volume, briefing, controls, places
from .inbox import Inbox
from .notes import Notes
from .notifications import Mirror
from .recents import Recents
from .schedule import Scheduler
from .watch import Watchers

TICK_SECONDS = 5
SCHEDULABLE_POWER = ("lock", "screen_off", "sleep", "hibernate", "restart", "shutdown")
_PC_KIND = {"sleep": "pc_sleep", "hibernate": "pc_sleep", "restart": "pc_restart", "shutdown": "pc_shutdown"}
ALERT_KEEP = 40
NOTIFY_GAP_SECONDS = 60


class Daily:
    def __init__(self, settings: Settings, manager, notifier, *, clipboard=None, clock: Callable[[], float] = time.time,
                 mirror: Mirror | None = None, power_runner: Callable[[str], str] | None = None, volume=None, brightness_shell=None):
        self.settings = settings
        self.cfg = settings.daily
        self.manager, self.notifier, self._clock = manager, notifier, clock
        self._power = power_runner or power.schedule
        db, folder = manager.db, self.cfg.folder
        self.folder = Path(folder)
        self._alerts: deque[dict] = deque(maxlen=ALERT_KEEP)
        self._alert_ids = itertools.count(1)
        self._alert_lock = threading.Lock()
        self._last_mirror_push = 0.0
        self.inbox = Inbox(db, folder, self.cfg.max_upload_mb * 1024 * 1024, clock=clock, clipboard=clipboard if settings.screen.clipboard else None)
        self.notes = Notes(folder, clock=clock)
        common = dict(clock=clock, notify=self.notify, run_power=self.run_power, allowed_power=self.allowed_power, note=manager.note)
        self.scheduler = Scheduler(db, **common)
        self.watchers = Watchers(db, **common, resolve_folder=self._watch_folder)
        self.mirror = mirror or Mirror(clock=clock, on_new=self._mirror_news)
        self.recents = Recents(db, clock=clock, allowed=self._may_list)
        self.updates = briefing.Updates(clock=clock)
        self.volume = volume or audio_volume.default_backend()
        self._brightness_shell = brightness_shell
        self._brightness_seen: tuple[float, dict] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the sliders ---------------------------------------------------------------------------------------------------

    def brightness_state(self) -> dict:
        """Reading a laptop's brightness goes through PowerShell, which takes a moment, so the answer is kept for a few seconds."""
        now = self._clock()
        if self._brightness_seen and now - self._brightness_seen[0] < 4:
            return self._brightness_seen[1]
        state = controls.brightness_state(self._brightness_shell)
        self._brightness_seen = (now, state)
        return state

    def brightness_set(self, level: int, screen_dark: bool = False) -> dict:
        state = controls.set_brightness(level, shell=self._brightness_shell, screen_dark=screen_dark)
        self._brightness_seen = (self._clock(), state)
        return state

    # -- what is switched on -------------------------------------------------------------------------------------------

    def features(self) -> dict:
        """What the phone may offer, for /api/info."""
        if not self.cfg.enabled:
            return {"enabled": False}
        return {
            "enabled": True, "controls": controls.supported(), "power": sorted(self.allowed_power()),
            "printing": self.cfg.printing, "notifications": self.cfg.notifications, "webcam": self.cfg.webcam,
            "folders": {"inbox": str(self.inbox.to_pc_dir), "to_phone": str(self.inbox.to_phone_dir), "notes": str(self.notes.dir)},
            "max_upload_mb": self.cfg.max_upload_mb,
        }

    def allowed_power(self) -> set[str]:
        return {a for a in self.settings.power.remote_actions if a in SCHEDULABLE_POWER}

    # -- what happens on this PC for the phone ---------------------------------------------------------------------------

    def run_power(self, key: str) -> str:
        kind = _PC_KIND.get(key)
        if kind:
            try:
                self.notifier.notify_pc(kind)  # before it happens: afterwards there is no network to say it with
            except Exception:  # noqa: BLE001
                pass
        return self._power(key)

    def _watch_folder(self, path: str) -> Path:
        """A folder a watcher may look at: one of the usual folders, or one the phone may read."""
        for place in places.all_places():
            if Path(place["path"]) == Path(path):
                return Path(path)
        return self.manager.resolve_folder(path)

    def _may_list(self, path: str) -> bool:
        info = self.manager.item_info(path)
        return bool(info.get("allowed") or info.get("needs_access"))

    # -- telling the phone ---------------------------------------------------------------------------------------------

    def notify(self, title: str, body: str, kind: str) -> None:
        """Show it in the phone's page, and push it. A push passes through Google or Apple, so it holds the words only when push.show_details is on."""
        with self._alert_lock:
            self._alerts.append({"id": next(self._alert_ids), "title": title, "body": body, "kind": kind, "time": self._clock()})
        shown = body if self.settings.push.show_details else {"watch": "Something you were watching has finished."}.get(kind, "Tap to see it.")
        message = Message(title, shown, "", kind, ttl=900)
        threading.Thread(target=lambda: self._push(message), name="daily-push", daemon=True).start()

    def tell(self, message: Message) -> None:
        """A notification to every phone, off the caller's thread (sending can be slow and must never delay or break an answer)."""
        threading.Thread(target=lambda: self._push(message), name="daily-push", daemon=True).start()

    def _push(self, message: Message) -> None:
        try:
            self.notifier.send(message)
        except Exception:  # noqa: BLE001 - a notification is never worth breaking a task over
            pass

    def alerts(self, after: int = 0) -> list[dict]:
        with self._alert_lock:
            return [a for a in self._alerts if a["id"] > after]

    def _mirror_news(self, items: list[dict]) -> None:
        now = self._clock()
        if now - self._last_mirror_push < NOTIFY_GAP_SECONDS:
            return
        self._last_mirror_push = now
        first = items[-1]
        body = f"{first['app']}: {first['title']}" if self.settings.push.show_details else f"{len(items)} new on your PC."
        threading.Thread(target=lambda: self._push(Message("New notifications on your PC", body, "", "pcnotice", ttl=900)), name="daily-push", daemon=True).start()

    # -- the briefing --------------------------------------------------------------------------------------------------

    def briefing(self) -> dict:
        shown = self.mirror.items() if self.cfg.notifications else []
        return briefing.build(sysinfo.current(), updates=self.updates.state(), inbox=self.inbox.counts(), tasks=self.scheduler.list(),
                              watchers=self.watchers.list(), alerts=len(self.alerts()), notifications=len(shown) if self.cfg.notifications else None, now=self._clock())

    # -- the background thread -----------------------------------------------------------------------------------------

    def tick(self) -> None:
        for step in (self.scheduler.tick, self.watchers.tick, self.mirror.poll if self.cfg.notifications else None):
            if step is None:
                continue
            try:
                step()
            except Exception:  # noqa: BLE001 - one part failing must never stop the others
                pass

    def start(self) -> None:
        if not self.cfg.enabled or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(TICK_SECONDS):
                self.tick()

        self._thread = threading.Thread(target=loop, name="daily", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
