"""The relay's list of PCs: an id (the phone's address), and a hash of the secret only that PC holds."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # lowercase, no look-alikes: it becomes part of a web address


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()  # a 192-bit random secret: no salt or slow hash needed


class Registry:
    def __init__(self, file: Path | str):
        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=15)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS pcs (pc_id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, created TEXT NOT NULL, last_seen TEXT)")
        self._lock = threading.Lock()

    def register(self) -> tuple[str, str]:
        """A new PC. Returns (pc_id, secret); the secret is shown once and only its hash is kept."""
        with self._lock:
            while True:
                pc_id = "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))  # 31^12: not guessable
                if not self._conn.execute("SELECT 1 FROM pcs WHERE pc_id=?", (pc_id,)).fetchone():
                    break
            secret = secrets.token_urlsafe(32)
            self._conn.execute("INSERT INTO pcs (pc_id, secret_hash, created) VALUES (?, ?, ?)", (pc_id, _hash(secret), _now()))
            return pc_id, secret

    def verify(self, pc_id: str, secret: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT secret_hash FROM pcs WHERE pc_id=?", (pc_id,)).fetchone()
        return bool(row) and bool(secret) and hmac.compare_digest(row[0], _hash(secret))

    def touch(self, pc_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE pcs SET last_seen=? WHERE pc_id=?", (_now(), pc_id))

    def exists(self, pc_id: str) -> bool:
        with self._lock:
            return bool(self._conn.execute("SELECT 1 FROM pcs WHERE pc_id=?", (pc_id,)).fetchone())

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM pcs").fetchone()[0]
