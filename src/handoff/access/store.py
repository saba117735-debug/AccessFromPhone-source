"""Who is the Admin phone, and what level each other phone has. Kept in the PC's own database; only the PC page (this PC) or the Admin phone changes it.

Nothing is stored for a phone nobody has set: it is a plain user at the default level. The Admin is the phone the Owner picked on the PC page; if
the Owner never picked one, it is the phone that was paired first (the Owner's own phone, on a normal install), so an upgrade changes nothing for one phone.
"""
from __future__ import annotations

from ..db import Database
from . import levels

ADMIN_KEY = "access_admin"  # a device id, or "none" when the Owner chose to have no Admin phone


class AccessStore:
    def __init__(self, db: Database):
        self._db = db

    # -- who is the Admin
    def admin_id(self) -> str | None:
        chosen = self._db.meta(ADMIN_KEY)
        if chosen == "none":
            return None
        if chosen and self._db.one("SELECT 1 FROM devices WHERE id=? AND blocked=0", (chosen,)):
            return chosen
        row = self._db.one("SELECT id FROM devices WHERE blocked=0 ORDER BY created ASC, rowid ASC LIMIT 1")
        return row["id"] if row and not chosen else None  # a chosen Admin that was removed leaves no Admin until the Owner picks another

    def set_admin(self, device_id: str | None) -> None:
        self._db.set_meta(ADMIN_KEY, device_id or "none")

    # -- levels
    def level(self, device_id: str) -> str:
        if device_id == self.admin_id():
            return "admin"
        row = self._db.one("SELECT level FROM device_access WHERE device_id=?", (device_id,))
        found = row["level"] if row else levels.DEFAULT_LEVEL
        return found if found in levels.GRANTABLE else levels.DEFAULT_LEVEL  # "admin" is never stored: it is a role, not a level

    def role(self, device_id: str) -> str:
        return "admin" if device_id == self.admin_id() else "user"

    def set_level(self, device_id: str, level: str) -> None:
        if level not in levels.GRANTABLE:
            raise ValueError("Pick view only, read and write, or operator.")
        self._db.run("INSERT INTO device_access (device_id, level) VALUES (?, ?) ON CONFLICT(device_id) DO UPDATE SET level=excluded.level", (device_id, level))

    def forget(self, device_id: str) -> None:
        self._db.run("DELETE FROM device_access WHERE device_id=?", (device_id,))
        if self._db.meta(ADMIN_KEY) == device_id:
            self._db.set_meta(ADMIN_KEY, "none")

    def people(self) -> list[dict]:
        """Every phone with its role and level, for the lists on the PC page and the Admin phone."""
        admin = self.admin_id()
        rows = self._db.rows("SELECT id, name, last_seen, blocked FROM devices ORDER BY created ASC, rowid ASC")
        return [{"id": r["id"], "name": r["name"], "last_seen": r["last_seen"], "blocked": bool(r["blocked"]),
                 "role": "admin" if r["id"] == admin else "user", "level": self.level(r["id"])} for r in rows]
