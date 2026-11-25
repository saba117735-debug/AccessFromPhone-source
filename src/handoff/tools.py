"""File tools the agent may call. Every call passes through the policy engine."""
from __future__ import annotations

import fnmatch
import os
import shutil
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .audit import AuditLog
from .config import Action
from .paths import is_inside
from .policy import PolicyEngine
from .snapshots import SnapshotStore

MAX_MATCHES = 200
MAX_SCANNED = 50_000
SKIPPED_DIRS = frozenset({".git", "node_modules"})  # huge and rarely what you are looking for


def name_matcher(pattern: str):
    """Build a case-insensitive file-name matcher.

    `*.bak` and `report?.txt` work as written. `.bak` means `*.bak`, and a plain
    word such as `invoice` means `*invoice*`.
    """
    text = pattern.strip().lower()
    if not text:
        raise ValueError("the search pattern is empty")
    if "/" in text or "\\" in text:
        raise ValueError("the pattern must be a file name such as *.bak, not a path")
    if not any(ch in text for ch in "*?["):
        text = "*" + text if text.startswith(".") else f"*{text}*"
    return lambda name: fnmatch.fnmatchcase(name.lower(), text)


@dataclass(frozen=True)
class FindResult:
    matches: list[Path]
    hit_match_limit: bool = False
    hit_scan_limit: bool = False
    skipped: frozenset[str] = field(default_factory=frozenset)
    searched: Path | None = None

    def render(self) -> str:
        """Plain text for the model and the phone. It always names the folder searched."""
        count = len(self.matches)
        found = f"{count} matching file(s)" if count else "no matching files"
        head = f"Searched {self.searched or 'the folder'} and its sub-folders: {found}"
        lines = [head, *(str(p) for p in self.matches)]
        if self.hit_match_limit:
            lines.append(f"... stopped after {MAX_MATCHES} matches; there may be more")
        if self.hit_scan_limit:
            lines.append(f"... stopped after {MAX_SCANNED} files; the search is incomplete")
        if self.skipped:
            lines.append(f"(skipped folders: {', '.join(sorted(self.skipped))})")
        return "\n".join(lines)


class FileTools:
    def __init__(
        self,
        job_id: str,
        policy: PolicyEngine,
        snapshots: SnapshotStore,
        audit: AuditLog,
    ):
        self._job = job_id
        self._policy = policy
        self._snapshots = snapshots
        self._audit = audit

    def list_dir(self, path: str | Path) -> list[str]:
        real = self._policy.authorize(Action.READ, path, approved=True)
        self._audit.record(self._job, "list_dir", path=real)
        return sorted(p.name for p in real.iterdir())

    def read_file(self, path: str | Path) -> str:
        real = self._policy.authorize(Action.READ, path, approved=True)
        self._audit.record(self._job, "read_file", path=real)
        return real.read_text(encoding="utf-8")

    def write_bytes(self, path: str | Path, data: bytes, approved: bool = False) -> Path:
        """Write a file exactly as given, keeping its line endings. Snapshotted, so it can be undone."""
        real = self._policy.authorize(Action.WRITE, path, approved)
        self._snapshots.capture(self._job, real)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(data)
        self._audit.record(self._job, "write_file", path=real, bytes=len(data), how="edited by hand")
        return real

    def read_lines(self, path: str | Path, count: int, tail: bool = False) -> str:
        """The first (or last) `count` lines of a file, read as a stream so a huge log costs no memory."""
        real = self._policy.authorize(Action.READ, path, approved=True)
        self._audit.record(self._job, "read_file", path=real, lines=f"{'last' if tail else 'first'} {count}")
        with real.open(encoding="utf-8", errors="replace") as handle:
            if tail:
                window: deque[str] = deque(maxlen=count)
                total = 0
                for line in handle:
                    window.append(line)
                    total += 1
                rows, note = list(window), (f"last {count} of {total} lines" if total > count else "")
            else:
                rows = []
                more = False
                for line in handle:
                    if len(rows) == count:
                        more = True
                        break
                    rows.append(line)
                note = f"first {count} lines; the file has more" if more else ""
        text = "".join(rows).rstrip("\n")
        return f"{text}\n... ({note})" if note else text

    def find_files(self, path: str | Path, pattern: str) -> FindResult:
        """Find files by name in `path` and every folder below it."""
        real = self._policy.authorize(Action.READ, path, approved=True)
        if not real.is_dir():
            raise NotADirectoryError(f"{real} is not a folder")
        matches_name = name_matcher(pattern)

        matches: list[Path] = []
        skipped: set[str] = set()
        scanned = 0
        hit_matches = hit_scan = False
        for dirpath, dirnames, filenames in os.walk(real):
            keep = []
            for name in sorted(dirnames):
                if name in SKIPPED_DIRS:
                    skipped.add(name)
                elif is_inside((Path(dirpath) / name).resolve(), real):  # never leave the folder via a link
                    keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                scanned += 1
                if scanned > MAX_SCANNED:
                    hit_scan = True
                    break
                if matches_name(name):
                    if len(matches) >= MAX_MATCHES:
                        hit_matches = True
                        break
                    matches.append(Path(dirpath) / name)
            if hit_scan or hit_matches:
                break

        self._audit.record(
            self._job, "find_files", path=real, pattern=pattern, found=len(matches)
        )
        return FindResult(matches, hit_matches, hit_scan, frozenset(skipped), real)

    def write_file(self, path: str | Path, content: str, approved: bool = False) -> Path:
        real = self._policy.authorize(Action.WRITE, path, approved)
        self._snapshots.capture(self._job, real)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(content, encoding="utf-8")
        self._audit.record(self._job, "write_file", path=real, chars=len(content))
        return real

    def delete_file(self, path: str | Path, approved: bool = False) -> None:
        real = self._policy.authorize(Action.DELETE, path, approved)
        self._snapshots.capture(self._job, real)
        real.unlink()
        self._audit.record(self._job, "delete_file", path=real)

    def move_file(self, source: str | Path, destination: str | Path, approved: bool = False) -> Path:
        """Move or rename one file. Both ends must be in writable folders."""
        src = self._policy.authorize(Action.MOVE, source, approved)
        dst = self._policy.resolve(destination, for_write=True)
        self._check_transfer(src, dst)
        self._snapshots.capture(self._job, src)
        self._snapshots.capture(self._job, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        self._audit.record(self._job, "move_file", path=src, to=dst)
        return dst

    def copy_file(self, source: str | Path, destination: str | Path, approved: bool = False) -> Path:
        """Copy one file. The source may be in a read-only folder; the copy must not be."""
        src = self._policy.authorize(Action.COPY, source, approved)
        dst = self._policy.resolve(destination, for_write=True)
        self._check_transfer(src, dst)
        self._snapshots.capture(self._job, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self._audit.record(self._job, "copy_file", path=src, to=dst)
        return dst

    @staticmethod
    def _check_transfer(src: Path, dst: Path) -> None:
        if not src.is_file():
            raise FileNotFoundError(f"{src} is not a file (only single files can be moved or copied for now)")
        if dst.exists():
            raise FileExistsError(f"{dst} already exists; choose another name")

    def undo(self) -> int:
        restored = self._snapshots.undo(self._job)
        self._audit.record(self._job, "undo", restored=restored)
        return restored
