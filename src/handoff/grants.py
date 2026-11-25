"""Folders the user allowed from the phone (kept between restarts), and the places nobody can allow."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Settings
from .db import Database, open_database, retire

# Under the user's home folder. Credentials, keys and browser profiles: a stolen phone must not be able to read these.
_PROTECTED_IN_HOME = (
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".config/gcloud",
    "AppData/Roaming/Microsoft/Credentials", "AppData/Roaming/Microsoft/Protect",
    "AppData/Local/Microsoft/Credentials", "AppData/Local/Google/Chrome/User Data",
    "AppData/Local/Microsoft/Edge/User Data", "AppData/Roaming/Mozilla/Firefox",
    "AppData/Local/BraveSoftware", "NTUSER.DAT",
)


def protected_locations(settings: Settings) -> list[Path]:
    """Never available: this app's own data (the access token, the https key, the audit log), credentials, Windows."""
    own = [
        settings.server.token_file.parent, settings.logging.audit_file.parent, settings.storage.snapshots_dir.parent,
        settings.storage.results_dir.parent, settings.storage.grants_file.parent, settings.storage.database.parent, settings.server.tls_dir.parent,
    ]
    home = Path.home()
    places = [*own, *(home / part for part in _PROTECTED_IN_HOME), *settings.agent.protected_folders]
    if os.name == "nt":
        places.append(Path(os.environ.get("SystemRoot", r"C:\Windows")))
    unique: dict[Path, None] = {}
    for place in places:
        unique[Path(place).resolve()] = None
    return list(unique)


class GrantStore:
    """The folders phones chose to keep allowing, and whether they may be changed too. Kept in the database.

    Each entry belongs to the phone that was asked and said yes (`device`; "" for the few made before phones had their own), and can be shared with other phones
    by the Admin (`shared`: phone id -> may change files there too)."""

    def __init__(self, source: Database | Path, legacy_file: Path | None = None):
        self._db, from_path = open_database(source)
        legacy = legacy_file or from_path
        if legacy is not None:
            self._import(legacy)
        self._migrate()

    def _migrate(self) -> None:
        """Folders kept before phones had their own list belong to no phone: they are carried over once, and each phone is asked again."""
        old = self._db.rows("SELECT path, change FROM grants")
        if not old:
            return
        with self._db.transaction():
            self._db._conn.executemany("INSERT OR IGNORE INTO folder_access (path, device, change, shared) VALUES (?, '', ?, '{}')", [(r["path"], r["change"]) for r in old])
            self._db._conn.execute("DELETE FROM grants")

    # -- the whole picture (who owns what, who it is shared with)
    def load_entries(self) -> list[dict]:
        out = []
        for r in self._db.rows("SELECT path, device, change, shared FROM folder_access ORDER BY path, device"):
            try:
                shared = {str(k): bool(v) for k, v in json.loads(r["shared"] or "{}").items()}
            except (ValueError, AttributeError):
                shared = {}
            out.append({"path": Path(r["path"]), "device": r["device"], "change": bool(r["change"]), "shared": shared})
        return out

    def save_entries(self, entries: list[dict]) -> None:
        with self._db.transaction():
            self._db._conn.execute("DELETE FROM folder_access")
            self._db._conn.executemany("INSERT OR REPLACE INTO folder_access (path, device, change, shared) VALUES (?, ?, ?, ?)",
                                       [(str(e["path"]), e.get("device", ""), int(bool(e["change"])), json.dumps(e.get("shared", {}), sort_keys=True)) for e in sorted(entries, key=lambda e: (str(e["path"]), e.get("device", "")))])

    # -- the simple view (a folder and whether changes are allowed), as before
    def load(self) -> list[tuple[Path, bool]]:
        merged: dict[Path, bool] = {}
        for e in self.load_entries():
            merged[e["path"]] = merged.get(e["path"], False) or e["change"]
        return sorted(merged.items())

    def save(self, folders: list[tuple[Path, bool]]) -> None:
        self.save_entries([{"path": f, "device": "", "change": c, "shared": {}} for f, c in folders])

    def _import(self, file: Path) -> None:
        """The older grants.json: plain paths meant read-only, newer entries say whether editing is allowed."""
        if not file.is_file():
            return
        found: list[tuple[str, int]] = []
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            for entry in data.get("folders", []):
                if isinstance(entry, str):
                    found.append((entry, 0))
                elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    found.append((entry["path"], int(bool(entry.get("change")))))
        except (OSError, ValueError, AttributeError):
            pass
        with self._db.transaction():
            self._db._conn.executemany("INSERT OR IGNORE INTO grants (path, change) VALUES (?, ?)", found)
        retire(file)
