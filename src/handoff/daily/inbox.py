"""The inbox between the phone and this PC.

To the PC: text, links and files the phone sends. Text can go on the PC's clipboard, a link can be opened in the PC's browser, a file
waits in the Inbox folder. Nothing sent here is ever run or opened by the app (the folder is just a place); an executable is marked so the
phone can warn before anyone opens it.
To the phone: whatever the person drops into the "To phone" folder on the PC shows in the phone's list, ready to save.
"""
from __future__ import annotations

import os
import re
import secrets
import time
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..db import Database

MAX_TEXT = 20_000
MAX_ITEMS = 300
LIST_LIMIT = 100
RISKY = {".exe", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".hta", ".jar", ".lnk", ".reg", ".dll", ".cpl", ".msc", ".pif"}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_ID = re.compile(r"^[a-f0-9]{16}$")


class InboxError(ValueError):
    """The phone sent something the inbox won't take; the message says why."""


def safe_name(name: str) -> str:
    """A file name that is safe on Windows: no folders, no forbidden characters, no reserved device names, never empty."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = _BAD_CHARS.sub("_", base).strip(" .")
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    if stem.lower() in _RESERVED:
        stem = "_" + stem
    cleaned = (stem + ("." + ext if ext else "")).strip(" .")
    if not cleaned:
        cleaned = "file"
    if len(cleaned) > 120:
        ext = Path(cleaned).suffix[:20]
        cleaned = cleaned[: 120 - len(ext)] + ext
    return cleaned


def link_of(text: str) -> str | None:
    """The address when the whole text is one web link, otherwise None."""
    value = text.strip()
    if not value or any(ch.isspace() for ch in value) or len(value) > 2000:
        return None
    parts = urlparse(value)
    return value if parts.scheme in ("http", "https") and parts.netloc else None


def is_risky(name: str) -> bool:
    return Path(name).suffix.lower() in RISKY


class Upload:
    """A file arriving in pieces. Nothing is kept unless it all arrives within the size limit."""

    def __init__(self, inbox: "Inbox", item_id: str, name: str, path: Path, device: str):
        self._inbox, self.id, self.name, self.path, self.device = inbox, item_id, name, path, device
        self.size = 0
        self._file = open(path, "wb")

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self._inbox.max_bytes:
            self.abort()
            raise InboxError(f"That file is larger than the {self._inbox.max_bytes // (1024 * 1024)} MB limit.")
        self._file.write(chunk)

    def commit(self) -> dict:
        self._file.close()
        if self.size == 0:
            self.path.unlink(missing_ok=True)
            raise InboxError("That file is empty.")
        return self._inbox._record(self.id, "file", self.name, "", self.size, self.device)

    def abort(self) -> None:
        try:
            self._file.close()
        finally:
            self.path.unlink(missing_ok=True)


class Inbox:
    def __init__(self, db: Database, folder: Path, max_bytes: int, clock: Callable[[], float] = time.time,
                 clipboard=None, opener: Callable[[str], object] | None = None):
        self.db, self.folder, self.max_bytes, self._clock = db, Path(folder), int(max_bytes), clock
        self.clipboard = clipboard
        self._opener = opener or webbrowser.open

    @property
    def to_pc_dir(self) -> Path:
        return self.folder / "Inbox"

    @property
    def to_phone_dir(self) -> Path:
        return self.folder / "To phone"

    # -- from the phone ------------------------------------------------------------------------------------------------

    def _record(self, item_id: str, kind: str, name: str, body: str, size: int, device: str) -> dict:
        now = self._clock()
        self.db.run("INSERT INTO daily_inbox (id, kind, name, body, size, device, created) VALUES (?, ?, ?, ?, ?, ?, ?)", (item_id, kind, name, body, size, device[:60], now))
        self._trim()
        return self._public(self.db.one("SELECT * FROM daily_inbox WHERE id=?", (item_id,)))

    def add_text(self, text: str, device: str = "") -> dict:
        text = str(text or "").replace("\r\n", "\n").strip()
        if not text:
            raise InboxError("There is nothing to send.")
        if len(text) > MAX_TEXT:
            raise InboxError(f"That text is too long (up to {MAX_TEXT} characters).")
        link = link_of(text)
        return self._record(secrets.token_hex(8), "link" if link else "text", "", link or text, len(text), device)

    def open_upload(self, name: str, device: str = "") -> Upload:
        item_id = secrets.token_hex(8)
        clean = safe_name(name)
        self.to_pc_dir.mkdir(parents=True, exist_ok=True)
        return Upload(self, item_id, clean, self.to_pc_dir / f"{item_id}_{clean}", device)

    def _trim(self) -> None:
        extra = self.db.rows("SELECT id, kind FROM daily_inbox ORDER BY created DESC LIMIT -1 OFFSET ?", (MAX_ITEMS,))
        for row in extra:
            self._delete_files(row["id"])
            self.db.run("DELETE FROM daily_inbox WHERE id=?", (row["id"],))

    def _delete_files(self, item_id: str) -> None:
        if self.to_pc_dir.is_dir():
            for found in self.to_pc_dir.glob(f"{item_id}_*"):
                found.unlink(missing_ok=True)

    # -- reading and acting on what arrived -----------------------------------------------------------------------------

    def _public(self, row) -> dict:
        item = {"id": row["id"], "kind": row["kind"], "name": row["name"], "size": row["size"], "from": row["device"], "time": row["created"], "text": "", "risky": False}
        if row["kind"] in ("text", "link"):
            item["text"] = row["body"][:2000]
            item["more"] = len(row["body"]) > 2000
        else:
            item["risky"] = is_risky(row["name"])
        return item

    def items(self) -> list[dict]:
        return [self._public(r) for r in self.db.rows("SELECT * FROM daily_inbox ORDER BY created DESC LIMIT ?", (LIST_LIMIT,))]

    def _row(self, item_id: str):
        if not _ID.match(str(item_id)):
            raise InboxError("That item is not in the inbox.")
        row = self.db.one("SELECT * FROM daily_inbox WHERE id=?", (item_id,))
        if row is None:
            raise InboxError("That item is not in the inbox.")
        return row

    def text_of(self, item_id: str) -> str:
        row = self._row(item_id)
        if row["kind"] not in ("text", "link"):
            raise InboxError("That item is a file, not text.")
        return row["body"]

    def file_of(self, item_id: str) -> tuple[Path, str]:
        row = self._row(item_id)
        if row["kind"] != "file":
            raise InboxError("That item is not a file.")
        path = self.to_pc_dir / f"{row['id']}_{row['name']}"
        if not path.is_file():
            raise InboxError("That file is no longer on the PC.")
        return path, row["name"]

    def remove(self, item_id: str) -> None:
        self._row(item_id)
        self._delete_files(item_id)
        self.db.run("DELETE FROM daily_inbox WHERE id=?", (item_id,))

    def copy_to_clipboard(self, item_id: str) -> int:
        text = self.text_of(item_id)
        if self.clipboard is None:
            raise InboxError("This PC's clipboard can't be reached from here (Windows only).")
        try:
            self.clipboard.set_text(text)
        except Exception as exc:  # noqa: BLE001 - the clipboard says why in its own words
            raise InboxError(str(exc) or "The clipboard could not be written.") from None
        return len(text)

    def open_link(self, item_id: str) -> str:
        row = self._row(item_id)
        url = link_of(row["body"]) if row["kind"] == "link" else None
        if url is None:
            raise InboxError("That item is not a web link.")
        self._opener(url)
        return url

    # -- from the PC to the phone ---------------------------------------------------------------------------------------

    def to_phone(self) -> list[dict]:
        folder = self.to_phone_dir
        if not folder.is_dir():
            return []
        found = []
        for entry in os.scandir(folder):
            try:
                if entry.is_file(follow_symlinks=False) and not entry.name.startswith((".", "~$")):
                    st = entry.stat()
                    found.append({"name": entry.name, "size": st.st_size, "time": st.st_mtime, "risky": is_risky(entry.name)})
            except OSError:
                continue
        found.sort(key=lambda f: f["time"], reverse=True)
        return found[:LIST_LIMIT]

    def to_phone_file(self, name: str) -> Path:
        """A file from the To phone folder, by its exact name. Anything that is not directly in that folder is refused."""
        if not name or name in (".", "..") or any(ch in name for ch in '/\\:\x00'):
            raise InboxError("That file is not in the To phone folder.")
        folder = self.to_phone_dir.resolve()
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
            raise InboxError("That file is not in the To phone folder.")
        return path

    def counts(self) -> dict:
        row = self.db.one("SELECT COUNT(*) AS n FROM daily_inbox")
        return {"in_inbox": row["n"], "to_phone": len(self.to_phone())}
