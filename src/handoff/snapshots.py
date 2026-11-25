"""Per-job snapshots so every change can be undone."""
from __future__ import annotations

import json
import shutil
from pathlib import Path


class SnapshotStore:
    def __init__(self, root: Path):
        self._root = Path(root)

    def _job_dir(self, job_id: str) -> Path:
        return self._root / job_id

    def _manifest(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "manifest.json"

    def _load(self, job_id: str) -> list[dict]:
        manifest = self._manifest(job_id)
        if not manifest.is_file():
            return []
        return json.loads(manifest.read_text(encoding="utf-8"))

    def _save(self, job_id: str, entries: list[dict]) -> None:
        self._manifest(job_id).write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def capture(self, job_id: str, target: Path) -> None:
        """Record the state of `target` before it is changed."""
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        entries = self._load(job_id)
        entry: dict = {"path": str(target), "existed": target.exists(), "backup": None}
        if target.is_file():
            backup = f"{len(entries):04d}.bak"
            shutil.copy2(target, job_dir / backup)
            entry["backup"] = backup
        entries.append(entry)
        self._save(job_id, entries)

    def undo(self, job_id: str) -> int:
        """Restore every captured path, newest first. Returns how many were restored."""
        entries = self._load(job_id)
        job_dir = self._job_dir(job_id)
        for entry in reversed(entries):
            target = Path(entry["path"])
            if entry["backup"]:
                shutil.copy2(job_dir / entry["backup"], target)
            elif not entry["existed"] and target.exists():
                target.unlink()
        self._save(job_id, [])
        return len(entries)
