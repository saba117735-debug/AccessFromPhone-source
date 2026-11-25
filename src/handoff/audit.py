"""Append-only audit log, kept in the database. A log from before the database (one JSON object per line) is imported once."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .db import Database, open_database, retire


class AuditLog:
    def __init__(self, source: Database | Path, legacy_file: Path | None = None):
        self._db, from_path = open_database(source)
        legacy = legacy_file or from_path
        if legacy is not None:
            self._import(legacy)

    def record(self, job_id: str, event: str, **details: object) -> None:
        self._db.run(
            "INSERT INTO audit (time, job, event, details) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), job_id, event, json.dumps({k: str(v) for k, v in details.items()})),
        )

    def read(self, limit: int = 2000) -> list[dict]:
        """The newest `limit` entries, oldest first."""
        rows = self._db.rows("SELECT time, job, event, details FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        entries = []
        for row in reversed(rows):
            try:
                details = json.loads(row["details"])
            except ValueError:
                details = {}
            entries.append({"time": row["time"], "job": row["job"], "event": row["event"], **details})
        return entries

    def as_text(self) -> str:
        """The whole log, one JSON object per line (for export and for tests)."""
        return "\n".join(json.dumps(e) for e in self.read(1_000_000))

    # ------------------------------------------------------------------ the old file
    def _import(self, file: Path) -> None:
        """Bring in a log written before the database existed. Damaged lines are skipped. Done once, then the file is renamed."""
        if not file.is_file():
            return
        rows = []
        try:
            with file.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and "event" in entry:
                        details = {k: v for k, v in entry.items() if k not in ("time", "job", "event")}
                        rows.append((str(entry.get("time", "")), str(entry.get("job", "-")), str(entry["event"]), json.dumps(details)))
        except OSError:
            return
        with self._db.transaction():
            self._db._conn.executemany("INSERT INTO audit (time, job, event, details) VALUES (?, ?, ?, ?)", rows)
        retire(file)
