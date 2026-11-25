"""Policy engine: the last check before any action touches the PC.

Folders come in these kinds:
- writable (`allowed_folders`): can be read, searched and, with approval, changed.
- read-only (`read_only_folders`): can be read and searched, never changed, whatever anyone taps.
- granted: folders a phone's user allowed when asked. They can always be read; the user may also allow changing files
  there (to edit by hand). Both can be taken back. A granted folder belongs to the phone that was asked: other phones don't
  have it, unless the Admin shares it with them. The Admin phone (and the PC itself) can see and manage them all.
- protected: never available, whatever else is configured or granted (this app's own secrets, SSH keys, ...).
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import Action, ApprovalConfig
from .paths import is_inside


class PolicyError(Exception):
    """Base class for policy refusals."""


class PathDenied(PolicyError):
    """The path is not available."""


class OutsideAllowed(PathDenied):
    """The path is outside every allowed folder. The user may be asked to allow it."""

    def __init__(self, path: Path):
        super().__init__(f"{path} is outside the allowed folders")
        self.path = path


class ReadOnlyPath(PathDenied):
    """The path is in a read-only folder, so it cannot be changed."""


class ProtectedPath(PathDenied):
    """The path holds credentials or this app's own data. It is never available, and nobody can allow it."""

    def __init__(self, path: Path):
        super().__init__(
            f"{path} is a protected location (keys, credentials or this app's own data), "
            "so it can't be opened from the phone"
        )
        self.path = path


class ApprovalRequired(PolicyError):
    """The action needs the user's approval before it can run."""

    def __init__(self, action: Action, path: Path):
        super().__init__(f"{action.value} on {path} needs approval")
        self.action = action
        self.path = path


# Moving and copying are asked about like any other write.
_APPROVAL_AS = {Action.MOVE: Action.WRITE, Action.COPY: Action.WRITE}


def _is_root(path: Path) -> bool:
    return path == path.parent


@dataclass
class _Grant:
    owner: str | None  # the job it was allowed for, or None when it is kept
    can_change: bool  # the user also allowed changing files here (to edit them by hand)
    device: str = ""  # the phone that was asked and said yes ("" = from before phones had their own folders)
    shared: dict[str, bool] = field(default_factory=dict)  # other phones the Admin shared it with -> may they change files too


# Who is asking right now: (phone id, is it the Admin phone), or None for the PC itself (and tests), which sees every folder.
_VIEWER: contextvars.ContextVar = contextvars.ContextVar("handoff_viewer", default=None)


@contextlib.contextmanager
def viewing(device_id: str | None, admin: bool = False):
    """Everything inside is done on behalf of this phone: it sees only its own granted folders, and those shared with it (the Admin phone sees all)."""
    token = _VIEWER.set(None if device_id is None else (device_id, bool(admin)))
    try:
        yield
    finally:
        _VIEWER.reset(token)


def set_viewer(device_id: str | None, admin: bool = False) -> None:
    """Like `viewing`, for the request being served: the context is the request's own, so this ends with it."""
    _VIEWER.set(None if device_id is None else (device_id, bool(admin)))


class PolicyEngine:
    def __init__(
        self,
        allowed_folders: list[Path],
        approval: ApprovalConfig,
        read_only_folders: Iterable[Path] = (),
        protected_folders: Iterable[Path] = (),
    ):
        self._writable = [Path(f).resolve() for f in allowed_folders]
        self._read_only = [Path(f).resolve() for f in read_only_folders]
        # a drive root in this list would block everything, so it is ignored
        self._protected = [p for p in (Path(f).resolve() for f in protected_folders) if not _is_root(p)]
        self._require = {Action(a) for a in approval.require_for}
        self._granted: dict[tuple[Path, str], _Grant] = {}  # (folder, the phone it belongs to); changed from request threads, so guarded
        self._lock = threading.Lock()

    # -- who sees which granted folder ----------------------------------------

    @staticmethod
    def _effective(g: _Grant) -> bool | None:
        """None when the phone asking can't see this grant; otherwise whether it may change files there."""
        who = _VIEWER.get()
        if who is None:
            return g.can_change
        device, admin = who
        if admin or g.device == device:
            return g.can_change
        if device in g.shared:
            return g.can_change and g.shared[device]
        return None

    def _visible(self, kept_only: bool = False) -> list[tuple[Path, _Grant, bool]]:
        with self._lock:
            items = list(self._granted.items())
        out = []
        for (folder, _device), g in items:
            if kept_only and g.owner is not None:
                continue
            mode = self._effective(g)
            if mode is not None:
                out.append((folder, g, mode))
        return out

    # -- what is allowed ------------------------------------------------------

    def is_protected(self, path: str | Path) -> bool:
        real = Path(path).resolve()
        return any(is_inside(real, folder) for folder in self._protected)

    def _pinned_read_only(self, real: Path) -> bool:
        """In a folder that config.yaml marks read-only: nobody can allow changes there from the phone."""
        return any(is_inside(real, folder) for folder in self._read_only)

    def resolve(self, path: str | Path, *, for_write: bool = False) -> Path:
        """Return the real absolute path, or raise PathDenied.

        Resolving first means `..` segments and symlinks cannot escape the
        allowed folders. With `for_write`, only writable folders qualify.
        """
        real = Path(path).resolve()
        if self.is_protected(real):
            raise ProtectedPath(real)
        if any(is_inside(real, folder) for folder in self._writable):
            return real
        granted = [(f, mode) for f, _g, mode in self._visible()]
        if for_write and not self._pinned_read_only(real) and any(mode and is_inside(real, f) for f, mode in granted):
            return real  # the user allowed changing files in this folder
        in_read_only = self._pinned_read_only(real) or any(is_inside(real, f) for f, _ in granted)
        if in_read_only and not for_write:
            return real
        if in_read_only:
            raise ReadOnlyPath(f"{real} is in a read-only folder, so it cannot be changed")
        raise OutsideAllowed(real)

    def can_change(self, path: str | Path) -> bool:
        """Could this path be changed right now (a writable folder, or one the user allowed changes in)?"""
        try:
            self.resolve(path, for_write=True)
            return True
        except PathDenied:
            return False

    def can_grant(self, folder: str | Path, *, change: bool = False) -> str | None:
        """None if the user may be asked to allow this folder, otherwise why not."""
        real = Path(folder).resolve()
        if self.is_protected(real):
            return f"{real} is a protected location, so it can't be opened from the phone."
        if _is_root(real):
            return f"{real} is a whole drive. Name a specific folder instead."
        if not real.is_dir():
            return f"I can't find the folder {real}. Check the path."
        if change and self._pinned_read_only(real):
            return f"config.yaml marks {real} as read-only, so it can't be changed from the phone. Change that on the PC first."
        return None

    # -- access the user grants while the app runs -----------------------------

    def grant(self, folder: str | Path, job_id: str | None = None, *, change: bool = False, device: str | None = None) -> Path:
        """Allow reading `folder` (and with `change`, editing files in it), for one phone: `device`, or the phone being served.

        With a job id the access ends when that job does; without, it is kept. A one-off never downgrades access
        that is already kept, and allowing changes is never lost by a later read-only grant.
        """
        real = Path(folder).resolve()
        if device is None:
            who = _VIEWER.get()
            device = who[0] if who else ""
        with self._lock:
            current = self._granted.get((real, device))
            kept = job_id is None or (current is not None and current.owner is None)
            self._granted[(real, device)] = _Grant(None if kept else job_id, change or bool(current and current.can_change), device, dict(current.shared) if current else {})
        return real

    def revoke(self, folder: str | Path, owner: str | None = None) -> bool:
        """Take a folder back. A phone takes back its own (a folder only shared with it is not its to remove); the Admin phone and the PC remove it for everyone,
        or just one phone's entry when `owner` says whose."""
        real = Path(folder).resolve()
        who = _VIEWER.get()
        with self._lock:
            if owner is not None:
                keys = [(real, owner)]
            elif who is None or who[1]:
                keys = [k for k in self._granted if k[0] == real]
            else:
                keys = [(real, who[0])]
            return any([self._granted.pop(k, None) is not None for k in keys])

    def revoke_job(self, job_id: str) -> None:
        with self._lock:
            for key in [k for k, g in self._granted.items() if g.owner == job_id]:
                del self._granted[key]

    def granted(self) -> list[Path]:
        """The folders allowed and kept (not the one-off ones) that the asking phone can see."""
        return sorted({f for f, _g, _m in self._visible(kept_only=True)})

    def visible_roots(self) -> list[tuple[Path, str]]:
        """Where browsing may start: the allowed folders, the read-only ones, and the folders allowed and kept that the asking phone can see."""
        found: dict[Path, str] = {}
        for folder in self._writable:
            found.setdefault(folder, "writable")
        for folder in self._read_only:
            found.setdefault(folder, "read_only")
        for folder, can_change in self.granted_details():
            found.setdefault(folder, "granted_change" if can_change else "granted")
        return list(found.items())

    def granted_details(self) -> list[tuple[Path, bool]]:
        """Kept folders the asking phone can see, with whether changes are allowed there too."""
        merged: dict[Path, bool] = {}
        for folder, _g, mode in self._visible(kept_only=True):
            merged[folder] = merged.get(folder, False) or mode
        return sorted(merged.items())

    def folders_for_phone(self) -> list[dict]:
        """What the asking phone is shown: each kept folder, whether changes are allowed, and whether it is the phone's own or was shared with it."""
        who = _VIEWER.get()
        rows: dict[Path, dict] = {}
        for folder, g, mode in self._visible(kept_only=True):
            mine = who is None or bool(who[1]) or g.device == who[0]
            row = rows.setdefault(folder, {"path": str(folder), "change": False, "mine": False, "shared": False})
            row["change"] = row["change"] or mode
            row["mine"] = row["mine"] or mine
            row["shared"] = row["shared"] or not mine
        return [rows[k] for k in sorted(rows)]

    # -- sharing (the Admin chooses which other phones may use a phone's folder) ----

    def entries(self) -> list[dict]:
        """Every kept folder with its owner phone and who it is shared with (for the Admin phone and the PC page, and for saving)."""
        with self._lock:
            ordered = sorted(self._granted.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
            return [{"path": f, "device": d, "change": g.can_change, "shared": dict(g.shared)} for (f, d), g in ordered if g.owner is None]

    def restore(self, entries: list[dict]) -> None:
        """Bring saved entries back (at start)."""
        with self._lock:
            for e in entries:
                self._granted[(Path(e["path"]).resolve(), e.get("device", ""))] = _Grant(None, bool(e["change"]), e.get("device", ""), dict(e.get("shared", {})))

    def share(self, folder: str | Path, owner: str, targets: dict[str, bool]) -> bool:
        """Set which other phones may use this phone's folder (and whether they may change files there). Replaces the earlier list. False when there is no such folder."""
        real = Path(folder).resolve()
        with self._lock:
            g = self._granted.get((real, owner))
            if g is None or g.owner is not None:
                return False
            g.shared = {str(k): bool(v) for k, v in targets.items() if str(k) != owner}
            return True

    def forget_device(self, device_id: str) -> None:
        """A phone was removed: its folders go, and it is taken off every share list."""
        with self._lock:
            for key in [k for k in self._granted if k[1] == device_id]:
                del self._granted[key]
            for g in self._granted.values():
                g.shared.pop(device_id, None)

    # -- approvals -------------------------------------------------------------

    def needs_approval(self, action: Action) -> bool:
        action = _APPROVAL_AS.get(action, action)
        return action in self._require

    def authorize(self, action: Action, path: str | Path, approved: bool) -> Path:
        real = self.resolve(path, for_write=action.changes_files)
        if self.needs_approval(action) and not approved:
            raise ApprovalRequired(action, real)
        return real
