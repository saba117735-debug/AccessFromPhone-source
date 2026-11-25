"""The app's database: one SQLite file (data/app.db). Everything that must outlive a restart lives here.

SQLite needs no server and ships with Python, so a customer installs the app and the database already exists.
Only this module knows it is SQLite; the rest of the app asks the stores (audit, accounts, grants, jobs) for what it needs.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    time    TEXT NOT NULL,
    job     TEXT NOT NULL,
    event   TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_job ON audit (job);
CREATE INDEX IF NOT EXISTS audit_event ON audit (event);

CREATE TABLE IF NOT EXISTS owner (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    name     TEXT NOT NULL,
    password TEXT NOT NULL,
    recovery TEXT NOT NULL,
    created  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created    TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    last_ip    TEXT NOT NULL DEFAULT '',
    blocked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS push_subs (
    target    TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    keys      TEXT NOT NULL DEFAULT '',
    created   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS push_subs_device ON push_subs (device_id);

CREATE TABLE IF NOT EXISTS screen_grants (
    device_id TEXT PRIMARY KEY,
    until     REAL NOT NULL,
    control   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_access (
    device_id TEXT PRIMARY KEY,
    level     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_prefs (
    device_id TEXT PRIMARY KEY,
    data      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    path   TEXT PRIMARY KEY,
    change INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS folder_access (
    path   TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    change INTEGER NOT NULL DEFAULT 0,
    shared TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (path, device)
);

CREATE TABLE IF NOT EXISTS daily_inbox (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    name    TEXT NOT NULL DEFAULT '',
    body    TEXT NOT NULL DEFAULT '',
    size    INTEGER NOT NULL DEFAULT 0,
    device  TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS daily_inbox_created ON daily_inbox (created);

CREATE TABLE IF NOT EXISTS daily_tasks (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    action      TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    due         REAL NOT NULL,
    repeat      TEXT NOT NULL DEFAULT '',
    device      TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    created     REAL NOT NULL,
    last_run    REAL NOT NULL DEFAULT 0,
    last_result TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS daily_watch (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    then_do     TEXT NOT NULL DEFAULT '',
    device      TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    created     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_favs (
    path    TEXT PRIMARY KEY,
    label   TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    conversation TEXT NOT NULL,
    status       TEXT NOT NULL,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs (created_at);
CREATE INDEX IF NOT EXISTS jobs_conversation ON jobs (conversation);
"""


class Database:
    """A single shared connection, used from many threads one at a time."""

    def __init__(self, file: Path):
        self.file = Path(file)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.file, check_same_thread=False, isolation_level=None, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            if "install_hash" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(devices)")}:
                self._conn.execute("ALTER TABLE devices ADD COLUMN install_hash TEXT NOT NULL DEFAULT ''")  # which app install this entry is
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def transaction(self) -> "_Transaction":
        """`with db.transaction():` makes several statements succeed or fail together."""
        return _Transaction(self)

    def meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.run("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _Transaction:
    def __init__(self, db: Database):
        self._db = db

    def __enter__(self) -> Database:
        self._db._lock.acquire()
        self._db._conn.execute("BEGIN IMMEDIATE")
        return self._db

    def __exit__(self, kind, value, tb) -> None:
        try:
            self._db._conn.execute("ROLLBACK" if kind else "COMMIT")
        finally:
            self._db._lock.release()


def open_database(source: "Database | Path", legacy_name: str = "app.db") -> tuple["Database", Path | None]:
    """A store may be given the shared database, or (as the old file-based stores were) a file path.

    A path means: use app.db in that folder, and treat the path itself as an old file to import once.
    """
    if isinstance(source, Database):
        return source, None
    path = Path(source)
    return Database(path.parent / legacy_name), path


def retire(old_file: Path) -> None:
    """After an import the old file is kept, renamed, so nothing is lost and nothing is imported twice."""
    try:
        target = old_file.with_name(old_file.name + ".migrated")
        if target.exists():
            target.unlink()
        old_file.rename(target)
    except OSError:
        pass
