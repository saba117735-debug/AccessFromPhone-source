"""Show this PC's Windows notifications on the phone.

Windows keeps the notifications it shows in a small database in the person's own profile. This reads a COPY of it (nothing is changed or
dismissed on the PC) and keeps the newest few in memory only: they are never written anywhere by this app. It is off unless
`daily.notifications` is on, because a notification can hold a private message.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Callable

KEEP = 50
BACKFILL_SECONDS = 6 * 3600
FILETIME_EPOCH = 11644473600  # seconds between 1601 and 1970
MAX_PAYLOAD = 20_000


def default_database() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db" if base else None


def app_name(handler: str) -> str:
    """A readable app name from Windows' internal id ("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App" -> "WhatsAppDesktop")."""
    head = (handler or "").split("!")[0]
    head = re.sub(r"_[a-z0-9]{13}$", "", head)
    head = head.rsplit(".", 1)[-1] if "." in head and not head.lower().endswith(".exe") else head
    head = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", head.replace("_", " ")).strip()
    return head[:40] or "Windows"


def parse_payload(payload) -> tuple[str, str]:
    """(title, body) from a toast's XML. Anything that isn't a plain small toast gives empty text."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD or "<!DOCTYPE" in payload or "<!ENTITY" in payload:
        return "", ""
    try:
        root = ET.fromstring(payload.lstrip("\ufeff"))
    except ET.ParseError:
        return "", ""
    texts = [" ".join((t.text or "").split()) for t in root.iter("text")]
    texts = [t for t in texts if t]
    return (texts[0] if texts else "")[:200], (" ".join(texts[1:]))[:400]


def read_new(database: Path, after_id: int, since: float) -> tuple[list[dict], int]:
    """Toasts newer than `after_id` (and than `since`, seconds since 1970), oldest first, from a copy of the database; and the highest id seen."""
    tmp = Path(tempfile.mkdtemp(prefix="afp-wpn-"))
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(database) + suffix)
            if src.is_file():
                shutil.copy(src, tmp / ("w.db" + suffix))
        conn = sqlite3.connect(tmp / "w.db")
        try:
            rows = conn.execute(
                "SELECT n.Id, n.ArrivalTime, n.Payload, h.PrimaryId FROM Notification n LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
                "WHERE n.Type = 'toast' AND n.Id > ? ORDER BY n.Id", (after_id,)).fetchall()
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out, top = [], after_id
    for ident, arrival, payload, handler in rows:
        top = max(top, ident)
        when = (arrival or 0) / 10_000_000 - FILETIME_EPOCH
        if when < since:
            continue
        title, body = parse_payload(payload)
        if title or body:
            out.append({"id": ident, "app": app_name(handler or ""), "title": title, "body": body, "time": when})
    return out, top


class Mirror:
    def __init__(self, *, database: Path | None = None, clock: Callable[[], float] = time.time, on_new: Callable[[list[dict]], None] = lambda items: None,
                 reader: Callable[[Path, int, float], tuple[list[dict], int]] = read_new):
        self.database, self._clock, self._on_new, self._reader = database or default_database(), clock, on_new, reader
        self._items: deque[dict] = deque(maxlen=KEEP)
        self._hidden: set[int] = set()
        self._last_id: int | None = None
        self._lock = threading.Lock()
        self.problem = ""

    @property
    def available(self) -> bool:
        return self.database is not None and Path(self.database).is_file()

    def poll(self) -> list[dict]:
        if not self.available:
            self.problem = "Windows keeps no notification list on this PC."
            return []
        first = self._last_id is None
        try:
            items, top = self._reader(Path(self.database), self._last_id or 0, self._clock() - BACKFILL_SECONDS if first else 0)
        except Exception as exc:  # noqa: BLE001 - a locked or changed database is not worth a crash
            self.problem = f"Windows' notification list could not be read ({type(exc).__name__})."
            return []
        self.problem = ""
        with self._lock:
            self._last_id = top
            for item in items:
                self._items.append(item)
        if items and not first:
            self._on_new(items)
        return items

    def items(self) -> list[dict]:
        with self._lock:
            return [i for i in reversed(self._items) if i["id"] not in self._hidden]

    def dismiss(self, ident: int | None = None) -> int:
        """Hide one (or, with no id, all) from the phone's list. Nothing is dismissed on the PC itself."""
        with self._lock:
            ids = {i["id"] for i in self._items} if ident is None else {int(ident)}
            self._hidden |= ids
            return len(ids)
