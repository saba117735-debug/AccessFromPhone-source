"""Reminders and scheduled power actions ("lock this PC at 11 pm", "remind me at 5").

A task runs on this PC at its time, even with the phone away. Power actions are limited to what `power.remote_actions` allows. A task the PC
missed (it was off or asleep) never runs a power action late: a PC is not put to sleep at some surprising later time. A reminder that was
missed still tells the phone, marked as late.
"""
from __future__ import annotations

import math
import secrets
import time
from datetime import datetime, timedelta
from typing import Callable

from ..db import Database

REPEATS = ("", "daily", "weekdays", "weekly")
KINDS = ("remind", "power")
MAX_TASKS = 50
MAX_AHEAD = 366 * 86400
MISSED_AFTER = 600  # seconds late after which a task counts as missed
MAX_TEXT = 120


class ScheduleError(ValueError):
    """The task can't be saved; the message says why."""


def next_due(due: float, repeat: str, now: float) -> float | None:
    """The first time after `now` that a repeating task is due (None for a task that does not repeat). Uses the PC's own clock, so it follows daylight saving."""
    if repeat not in ("daily", "weekdays", "weekly"):
        return None
    when = datetime.fromtimestamp(due)
    for _ in range(800):
        when += timedelta(days=7 if repeat == "weekly" else 1)
        if repeat == "weekdays" and when.weekday() >= 5:
            continue
        if when.timestamp() > now:
            return when.timestamp()
    return None


class Scheduler:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time, notify: Callable[[str, str, str], None],
                 run_power: Callable[[str], str], allowed_power: Callable[[], set[str]], note: Callable[..., None] = lambda *a, **k: None):
        self.db, self._clock, self._notify, self._run_power, self._allowed, self._note = db, clock, notify, run_power, allowed_power, note

    def add(self, *, kind: str, due: float, repeat: str = "", action: str = "", text: str = "", device: str = "", device_name: str = "") -> dict:
        now = self._clock()
        if kind not in KINDS:
            raise ScheduleError("That kind of task is not known.")
        if repeat not in REPEATS:
            raise ScheduleError("That repeat is not known.")
        if not isinstance(due, (int, float)) or not math.isfinite(due) or due < now - 30:
            raise ScheduleError("That time has already passed." if isinstance(due, (int, float)) and math.isfinite(due) else "Pick a real time.")
        if due > now + MAX_AHEAD:
            raise ScheduleError("Pick a time within the next year.")
        text = " ".join(str(text or "").split())[:MAX_TEXT]
        if kind == "remind":
            if not text:
                raise ScheduleError("Say what to be reminded about.")
            action = ""
        else:
            if action not in self._allowed():
                raise ScheduleError("That action is switched off for the phone. To allow it, add it to power.remote_actions in config.yaml on the PC.")
        if self.db.one("SELECT COUNT(*) AS n FROM daily_tasks")["n"] >= MAX_TASKS:
            raise ScheduleError(f"There are already {MAX_TASKS} scheduled tasks. Remove one first.")
        task_id = secrets.token_hex(6)
        self.db.run("INSERT INTO daily_tasks (id, kind, action, text, due, repeat, device, device_name, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, kind, action, text, float(due), repeat, device, device_name[:60], now))
        return self._public(self.db.one("SELECT * FROM daily_tasks WHERE id=?", (task_id,)))

    @staticmethod
    def _public(row) -> dict:
        return {"id": row["id"], "kind": row["kind"], "action": row["action"], "text": row["text"], "due": row["due"], "repeat": row["repeat"],
                "by": row["device_name"], "last_run": row["last_run"], "last_result": row["last_result"]}

    def list(self) -> list[dict]:
        return [self._public(r) for r in self.db.rows("SELECT * FROM daily_tasks ORDER BY due")]

    def get(self, task_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM daily_tasks WHERE id=?", (task_id,))
        return self._public(row) if row else None

    def remove(self, task_id: str) -> bool:
        return self.db.run("DELETE FROM daily_tasks WHERE id=?", (task_id,)) > 0

    def tick(self) -> list[dict]:
        """Run what is due. Returns what ran, for the tests."""
        now = self._clock()
        ran = []
        for row in self.db.rows("SELECT * FROM daily_tasks WHERE due <= ? ORDER BY due", (now,)):
            late = now - row["due"] > MISSED_AFTER
            result = self._run(row, late)
            ran.append({"id": row["id"], "kind": row["kind"], "late": late, "result": result})
            again = next_due(row["due"], row["repeat"], now)
            if again is None:
                self.db.run("DELETE FROM daily_tasks WHERE id=?", (row["id"],))
            else:
                self.db.run("UPDATE daily_tasks SET due=?, last_run=?, last_result=? WHERE id=?", (again, now, result, row["id"]))
        return ran

    def _run(self, row, late: bool) -> str:
        try:
            if row["kind"] == "remind":
                self._notify("Reminder" + (" (late)" if late else ""), row["text"], "reminder")
                self._note("reminder_fired", text=row["text"], late=late)
                return "late" if late else "done"
            if late:  # never act on an old appointment
                self._note("scheduled_power_missed", action=row["action"])
                self._notify("A scheduled action was missed", f"{row['action']} was due while this PC was off, so it was not done.", "reminder")
                return "missed"
            if row["action"] not in self._allowed():
                self._note("scheduled_power_refused", action=row["action"])
                return "switched off"
            self._note("scheduled_power", action=row["action"], by=row["device_name"])
            self._notify("Scheduled action", f"Doing it now: {row['action']}.", "reminder")
            self._run_power(row["action"])
            return "done"
        except Exception as exc:  # noqa: BLE001 - one task failing must never stop the others
            return f"failed: {type(exc).__name__}"
