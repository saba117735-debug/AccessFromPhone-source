"""The file browser's listing: one folder at a time, sorted and paged, so a folder of 50,000 files is as quick as one of 5."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from . import imaging

SCAN_LIMIT = 100_000  # a folder bigger than this is listed from its first 100,000 entries, and says so
PAGE_MAX = 500

VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".ogv": "video/ogg"}
AUDIO = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
         ".flac": "audio/flac", ".aac": "audio/aac"}
STREAMABLE = {**VIDEO, **AUDIO}  # the only kinds served as a stream; everything else is opened in the viewer or downloaded

_ARCHIVE = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".iso"}
_SHEET = {".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".ods"}
_DOC = {".doc", ".docx", ".rtf", ".odt", ".ppt", ".pptx", ".odp"}
_APP = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".apk", ".dll", ".sys"}
_TEXT = {".txt", ".md", ".log", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".html", ".htm", ".css", ".js", ".ts",
         ".py", ".java", ".kt", ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".sql", ".env", ".csv"}

SORTS = ("name", "modified", "size", "type")

_HIDDEN = 0x2 | 0x4  # Windows FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def type_of(name: str) -> str:
    """What the icon should be. This only picks a picture: what a tap does is decided when the file is opened."""
    ext = Path(name).suffix.lower()
    if imaging.is_image(name):
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in _ARCHIVE:
        return "archive"
    if ext in _SHEET:
        return "sheet"
    if ext in _DOC:
        return "doc"
    if ext in _APP:
        return "app"
    if ext in _TEXT:
        return "text"
    return "file"


def _is_hidden(entry: os.DirEntry, stat: os.stat_result) -> bool:
    if entry.name.startswith("."):
        return True
    return bool(getattr(stat, "st_file_attributes", 0) & _HIDDEN)


def list_folder(folder: Path, *, q: str = "", sort: str = "name", order: str = "asc", hidden: bool = False,
                offset: int = 0, limit: int = 200, is_blocked: Callable[[Path], bool] = lambda p: False) -> dict:
    """The entries directly inside `folder`: folders first, then files, in the order asked for.

    `is_blocked` hides folders that must never be shown (this app's own secrets and the like). Anything that can't be
    read, or vanishes while we look, is skipped rather than failing the whole listing."""
    sort = sort if sort in SORTS else "name"
    needle = q.strip().casefold()
    limit = max(1, min(int(limit), PAGE_MAX))
    offset = max(0, int(offset))
    folders: list[dict] = []
    files: list[dict] = []
    scanned = 0
    truncated = False
    with os.scandir(folder) as entries:
        for entry in entries:
            scanned += 1
            if scanned > SCAN_LIMIT:
                truncated = True
                break
            if needle and needle not in entry.name.casefold():
                continue
            try:
                is_dir = entry.is_dir()
                stat = entry.stat()
            except OSError:
                continue
            is_hidden = _is_hidden(entry, stat)
            if is_hidden and not hidden:
                continue
            path = folder / entry.name
            if is_dir:
                if is_blocked(path):
                    continue
                folders.append({"name": entry.name, "path": str(path), "kind": "folder", "type": "folder", "size": 0,
                                "modified": stat.st_mtime, "hidden": is_hidden})
            else:
                files.append({"name": entry.name, "path": str(path), "kind": "file", "type": type_of(entry.name), "size": stat.st_size,
                              "modified": stat.st_mtime, "hidden": is_hidden})
    reverse = order == "desc"
    keys = {
        "name": lambda e: e["name"].casefold(),
        "modified": lambda e: (e["modified"], e["name"].casefold()),
        "size": lambda e: (e["size"], e["name"].casefold()),
        "type": lambda e: (Path(e["name"]).suffix.casefold(), e["name"].casefold()),
    }
    folders.sort(key=keys["name" if sort in ("size", "type") else sort], reverse=reverse if sort in ("name", "modified") else False)
    files.sort(key=keys[sort], reverse=reverse)
    ordered = folders + files
    return {
        "entries": ordered[offset: offset + limit], "total": len(ordered), "folders": len(folders), "files": len(files),
        "offset": offset, "truncated": truncated, "more": offset + limit < len(ordered),
    }
