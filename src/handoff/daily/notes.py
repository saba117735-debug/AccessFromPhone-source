"""Notes and to-dos dictated or typed on the phone, saved as plain text files on this PC (one file a day), so any program can open them."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .inbox import InboxError

MAX_NOTE = 5000
DAYS_SHOWN = 14
_LINE = re.compile(r"^- (?:\[( |x)\] )?(\d{2}:\d{2}) (.*)$")


class Notes:
    def __init__(self, folder: Path, clock: Callable[[], float] = time.time):
        self.dir = Path(folder) / "Notes"
        self._clock = clock

    def _file(self, when: datetime) -> Path:
        return self.dir / f"Notes {when:%Y-%m-%d}.md"

    def add(self, text: str, todo: bool = False) -> dict:
        text = str(text or "").replace("\r\n", "\n").strip()
        if not text:
            raise InboxError("There is nothing to save.")
        if len(text) > MAX_NOTE:
            raise InboxError(f"That note is too long (up to {MAX_NOTE} characters).")
        when = datetime.fromtimestamp(self._clock())
        self.dir.mkdir(parents=True, exist_ok=True)
        body = text.replace("\n", "\n  ")  # a following line stays inside the same item
        line = f"- {'[ ] ' if todo else ''}{when:%H:%M} {body}\n"
        path = self._file(when)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
        return {"date": f"{when:%Y-%m-%d}", "time": f"{when:%H:%M}", "text": text, "todo": todo, "file": path.name}

    def recent(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out: list[dict] = []
        for path in sorted(self.dir.glob("Notes *.md"), reverse=True)[:DAYS_SHOWN]:
            date = path.stem[len("Notes "):]
            entries: list[dict] = []
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                found = _LINE.match(line)
                if found:
                    entries.append({"date": date, "time": found.group(2), "text": found.group(3), "todo": found.group(1) is not None, "file": path.name})
                elif line.startswith("  ") and entries:
                    entries[-1]["text"] += "\n" + line[2:]
            out.extend(reversed(entries))
        return out[:200]
