"""Job lifecycle: plan, wait for approval, act, undo."""
from __future__ import annotations

import codecs
import difflib
import functools
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from .audit import AuditLog
from .cancel import CURRENT, CancelToken, Cancelled
from .commands import CommandRunner
from .names import best_match
from .config import Action, Settings
from .db import Database
from .grants import GrantStore, protected_locations
from . import browse as listing, imaging, power
from .paths import is_inside
from .pause import PauseSwitch
from .planner import DemoPlanner, Plan, PlanError, Planner, Step, mentioned_paths
from .policy import OutsideAllowed, PathDenied, PolicyEngine, PolicyError, ProtectedPath, ReadOnlyPath, viewing
from .snapshots import SnapshotStore
from .tools import FileTools
from .unlock import PinRequired, UnlockError
from .unlock.client import AgentClient
from .appctl import AppController, AppError

MAX_OUTPUT_CHARS = 20_000


class Status(str, Enum):
    PLANNING = "planning"
    AWAITING = "awaiting_approval"
    RUNNING = "running"
    STOPPED = "stopped"
    NEEDS_ACCESS = "needs_access"  # waiting for the user to allow a folder
    DONE = "done"
    REJECTED = "rejected"
    FAILED = "failed"
    UNDONE = "undone"
    EXPIRED = "expired"


class JobNotFound(Exception):
    pass


class JobStateError(Exception):
    pass


class FileTooBig(Exception):
    pass


class FileChanged(Exception):
    """The file on the PC is not the one the editor was opened with."""


# What the phone can show as a picture or a document, with the type it is sent as. Never HTML: that would run scripts.
VIEWABLE = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".bmp": "image/bmp", ".ico": "image/x-icon", ".svg": "image/svg+xml", ".pdf": "application/pdf",
}


def _decode(data: bytes) -> str | None:
    """The text of a UTF-8 file (BOM removed), or None when it is binary or in another encoding."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig" if data.startswith(codecs.BOM_UTF8) else "utf-8")
    except UnicodeDecodeError:
        return None


@dataclass
class Job:
    id: str
    prompt: str
    created_at: datetime
    status: Status = Status.PLANNING
    awaiting_since: datetime | None = None
    summary: str = ""
    steps: list[Step] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    error: str | None = None
    activity: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # approved files the phone may download
    conversation: str = "default"  # which chat on the phone this belongs to
    access: list[str] = field(default_factory=list)  # folders the user is being asked to allow
    focus: str = ""  # the file the phone was showing when this was asked: what "it" and "that file" mean
    open: str = ""  # a file the phone should open in its viewer or editor
    open_mode: str = "edit"  # "view" or "edit"
    frozen: dict | None = None  # a request from before a restart: shown as it was saved, never run again
    owner: str = ""  # the phone (device id) that asked: only that phone sees this request

    def to_dict(self) -> dict:
        if self.frozen is not None:
            return {**self.frozen, "status": self.status.value, "error": self.error}
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status.value,
            "summary": self.summary,
            "steps": [
                {"action": s.action.value, "path": s.path, "detail": s.detail(),
                 **({"edit": {"old": s.edit[0][:400], "new": s.edit[1][:400]}} if s.edit else {}),
                 **({"warning": power.ACTIONS[s.content].warning} if s.action is Action.POWER and s.content in power.ACTIONS else {}),
                 **({"needs_pin": True} if s.action is Action.POWER and s.content == "unlock" else {}),
                 **({"power": s.content} if s.action is Action.POWER and s.content else {}),
                 **({"app": s.app_kind, "warning": _app_warning(s)} if s.action is Action.APP else {}),
                 **(describe_target(s.path) if s.action is Action.SEND else {})}
                for s in self.steps
            ],
            "outputs": self.outputs,
            "changed": self.changed,
            "files": self.files,
            "conversation": self.conversation,
            "owner": self.owner,
            "access": self.access,
            "open": self.open,
            "open_mode": self.open_mode,
            "activity": list(self.activity),
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


_DESCRIBED: dict[str, tuple[float, dict]] = {}


def describe_target(path: str) -> dict:
    """What a download would be, for the approval card: a file's size, or a folder's file count and total size (it is sent as a zip)."""
    now = time.monotonic()
    hit = _DESCRIBED.get(path)
    if hit and now - hit[0] < 30:
        return hit[1]
    info: dict = {}
    try:
        p = Path(path)
        if p.is_file():
            info = {"size": p.stat().st_size, "kind": "file"}
        elif p.is_dir():
            files = total = 0
            deadline = now + 2.0
            for root, _dirs, names in os.walk(p):
                for n in names:
                    try:
                        total += os.stat(os.path.join(root, n)).st_size
                        files += 1
                    except OSError:
                        pass
                if time.monotonic() > deadline:
                    info = {"kind": "folder", "files": files, "size": total, "partial": True}
                    break
            else:
                info = {"kind": "folder", "files": files, "size": total}
    except OSError:
        info = {}
    if len(_DESCRIBED) > 200:
        _DESCRIBED.clear()
    _DESCRIBED[path] = (now, info)
    return info


def _app_warning(step: Step) -> str:
    if step.app_kind == "teams_reply":
        return f"This types your words into Teams on the PC and presses Enter, so {step.pattern} gets them exactly as written. It can't be unsent from here. The PC must be unlocked, and you shouldn't use its keyboard for a few seconds."
    if step.app_kind in ("teams_unread", "teams_chat"):
        return "Teams comes to the front on the PC for a few seconds, and opening a chat marks it as read. Nothing is sent. The PC must be unlocked."
    return ""


def policy_allows_apps(apps) -> bool:
    return callable(apps)


class _ReadContext:
    """What the planner may read while it plans. Always inside the configured folders."""

    def __init__(
        self,
        policy: PolicyEngine,
        base,
        tools: FileTools,
        roots: tuple[list[str], list[str]],
        history: list[dict] | None = None,
        commands_on: bool = False,
        last_file: str | None = None,
        apps: Callable | None = None,
        last_teams: str | None = None,
    ):
        self._last_file = last_file
        self.start_apps = apps if (apps is not None and policy_allows_apps(apps)) else None
        self._last_teams = last_teams
        self._policy = policy
        self._base = base
        self._tools = tools
        self._roots = roots
        self._history = history or []
        self._commands_on = commands_on
        self.calls: list[str] = []  # what the planner actually looked at, shown on the phone
        self.denied: list[Path] = []  # folders it tried that are not allowed (the user may be asked)

    def roots(self) -> tuple[list[str], list[str]]:
        """(writable folders, read-only folders), so the model knows where it may look."""
        return self._roots

    def history(self) -> list[dict]:
        """Earlier requests and what became of them, so follow-ups make sense."""
        return self._history

    def _real(self, path: str) -> Path:
        try:
            return self._policy.resolve(self._base / path)
        except OutsideAllowed as exc:
            if exc.path not in self.denied:
                self.denied.append(exc.path)
            raise

    def commands_enabled(self) -> bool:
        return self._commands_on

    def last_file(self) -> str | None:
        """The file this chat last read or sent, so "read 50 lines from it" knows what "it" is."""
        return self._last_file

    def last_teams(self) -> str | None:
        """The person whose Teams chat this conversation was just about, so "reply: I'm coming" knows who to send it to."""
        return self._last_teams

    def find_files(self, path: str, pattern: str) -> str:
        real = self._real(path)
        result = self._tools.find_files(real, pattern)
        self.calls.append(f"searched {real} for {pattern}: {len(result.matches)} found")
        return result.render()

    def look_for(self, path: str) -> dict:
        """Does this file exist? If not, which real file in its folder did the user probably mean?

        Quiet on purpose: it is a check, not something the AI looked at, so it adds no "Looked at" line.
        """
        real = self._real(path)
        if real.exists():
            return {"exists": True}
        folder = real.parent
        if not folder.is_dir():
            return {"exists": False, "folder": str(folder), "folder_missing": True}
        try:
            names = [p.name for p in folder.iterdir() if p.is_file()]
        except OSError:
            names = []
        match = best_match(real.name, names)
        return {"exists": False, "folder": str(folder), "auto": match.auto, "maybe": match.maybe}

    def list_dir(self, path: str) -> list[str]:
        real = self._real(path)
        if not real.is_dir():
            raise NotADirectoryError(f"{path} is not a folder")
        names = self._tools.list_dir(real)
        self.calls.append(f"listed {real}")
        return names

    def read_file(self, path: str) -> str:
        real = self._real(path)
        text = self._tools.read_file(real)
        self.calls.append(f"read {real}")
        return text


def _saves(method):
    """After a request changes, write it to the database, so a restart or a crash loses nothing that was already decided."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)
        self._sync()
        return result
    return wrapper


class JobManager:
    def __init__(
        self,
        settings: Settings,
        planner: Planner | None = None,
        now: Callable[[], datetime] | None = None,
        unlocker: AgentClient | None = None,
        apps: AppController | None = None,
    ):
        self._settings = settings
        self._apps = apps or AppController()
        self._unlocker = unlocker or AgentClient(settings.unlock.agent_port, timeout=settings.unlock.wait_seconds + 20)
        self._unlock_pins: dict[str, str] = {}  # job id -> the unlock PIN just given with the approval: kept in memory only, used once, never saved or logged
        self._planner = planner or DemoPlanner()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._base = settings.agent.allowed_folders[0]
        self._policy = PolicyEngine(
            settings.agent.allowed_folders,
            settings.approval,
            settings.agent.read_only_folders,
            protected_locations(settings),
        )
        self.db = Database(settings.storage.database)
        self.pause_switch = PauseSwitch(settings.storage.database.parent / "paused.flag")  # "Pause phone access": on from any phone, off only on this PC
        self._grants = GrantStore(self.db, settings.storage.grants_file)
        self.is_admin: Callable[[str], bool] = lambda device_id: False  # set by the server: is this phone the Admin phone (it sees and manages every phone's folders)
        self._policy.restore([e for e in self._grants.load_entries() if self._policy.can_grant(e["path"], change=e["change"]) is None])  # what phones chose to keep allowing, if still safe and present
        self._snapshots = SnapshotStore(settings.storage.snapshots_dir)
        self._audit = AuditLog(self.db, settings.logging.audit_file)
        self._runner = CommandRunner(settings.commands, self._policy, self._audit)
        self._jobs: dict[str, Job] = {}
        self._tokens: dict[str, CancelToken] = {}  # lets Stop reach a command or a slow model
        self._offered: set[Path] = set()  # files a job changed or the user approved sending
        self._downloads: dict[str, tuple[Path, float]] = {}  # download link key -> (file, expiry)
        self._streams: dict[str, tuple[Path, str, float]] = {}  # stream link id -> (file, type, expiry): video and audio played from the PC
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._saved: dict[str, str] = {}  # job id -> the JSON last written to the database
        self.on_event: Callable[[Job, str, float], None] | None = None  # told when a request needs the person or ends (push notifications)
        self.on_pc_event: Callable[[str], None] | None = None  # told just before this PC sleeps, restarts or shuts down at a phone's request
        self._worked_since: dict[str, datetime] = {}  # job id -> when the PC last started working on it
        self._restore_jobs()

    # -- remembering requests across restarts --------------------------------

    _FINISHED = {"done", "rejected", "failed", "undone", "expired", "stopped"}

    def _restore_jobs(self, keep: int = 1000) -> None:
        """Bring back the newest requests, so chats are still there after a restart.

        A request that was still being worked on or waiting for an answer can't carry on (its plan is gone):
        it is shown as failed or expired instead of hanging forever.
        """
        for row in reversed(self.db.rows("SELECT data FROM jobs ORDER BY created_at DESC LIMIT ?", (keep,))):
            try:
                d = json.loads(row["data"])
                status = Status(d["status"])
                job = Job(d["id"], d.get("prompt", ""), datetime.fromisoformat(d["created_at"]), status=status,
                          summary=d.get("summary", ""), outputs=list(d.get("outputs", [])), changed=list(d.get("changed", [])),
                          error=d.get("error"), activity=list(d.get("activity", [])), files=list(d.get("files", [])),
                          conversation=d.get("conversation", "default"), access=list(d.get("access", [])),
                          open=d.get("open", ""), open_mode=d.get("open_mode", "edit"), frozen=d, owner=d.get("owner", ""))
            except (ValueError, KeyError, TypeError):
                continue
            if status in (Status.PLANNING, Status.RUNNING):
                job.status, job.error = Status.FAILED, "The PC was restarted while this was running."
            elif status in (Status.AWAITING, Status.NEEDS_ACCESS):
                job.status, job.error = Status.EXPIRED, "The PC was restarted before this was answered."
            self._jobs[job.id] = job
            self._saved[job.id] = row["data"]
            for name in (*job.files, *job.changed):  # files this request offered can still be downloaded
                try:
                    self._offered.add((self._base / name).resolve())
                except (OSError, ValueError):
                    pass
        self._sync()

    def _sync(self) -> None:
        """Write requests that changed since the last write. Called on every poll from the phone, and on shutdown."""
        for job in list(self._jobs.values()):
            if job.status.value in self._FINISHED and job.id in self._saved and job.frozen is not None:
                if job.to_dict()["status"] == json.loads(self._saved[job.id])["status"]:
                    continue
            data = json.dumps(job.to_dict(), sort_keys=True)
            if self._saved.get(job.id) == data:
                continue
            self.db.run(
                "INSERT INTO jobs (id, created_at, conversation, status, data) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
                (job.id, job.created_at.isoformat(), job.conversation, job.status.value, data))
            self._saved[job.id] = data

    def flush(self) -> None:
        self._sync()

    # -- public API ---------------------------------------------------------

    @_saves
    def submit(self, prompt: str, conversation: str | None = None, focus: str | None = None, owner: str = "") -> Job:
        with self._lock:
            job = Job(uuid.uuid4().hex[:8], prompt, self._now(), conversation=conversation or "default", focus=focus or "", owner=owner)
            self._jobs[job.id] = job
            self._tokens[job.id] = CancelToken()
            self._audit.record(job.id, "submitted", prompt=prompt, conversation=job.conversation, device_id=owner)
        self._start_planning(job)
        return job

    def _announce(self, job: Job, event: str) -> None:
        """Tell whoever listens (the notifier) that this request needs the person, or is over. Never lets that break the request."""
        since = self._worked_since.pop(job.id, job.created_at) if event in ("done", "failed") else self._worked_since.get(job.id, job.created_at)
        if self.on_event is None:
            return
        try:
            self.on_event(job, event, max(0.0, (self._now() - since).total_seconds()))
        except Exception:  # noqa: BLE001
            pass

    def _as_owner(self, job: Job, action, *args):
        """Do this for the phone that asked: it sees its own folders (and those shared with it), not another phone's. A thread starts with no one asking, so it is said here."""
        who = job.owner
        with viewing(who if who else None, bool(who) and self.is_admin(who)):
            return action(*args)

    def _start_planning(self, job: Job) -> None:
        self._worked_since[job.id] = self._now()
        if getattr(self._planner, "is_slow", False):
            thread = threading.Thread(target=self._as_owner, args=(job, self._plan_job, job), daemon=True)
            self._threads.append(thread)
            thread.start()
        else:
            self._as_owner(job, self._plan_job, job)

    @_saves
    def decide_access(self, job_id: str, decision: str) -> Job:
        """The user's answer to "may I open this folder?": always (kept), once (this request only), or deny."""
        with self._lock:
            job = self._require(job_id, Status.NEEDS_ACCESS)
            folders = list(job.access)
            if self._now() - (job.awaiting_since or job.created_at) > timedelta(minutes=self._settings.approval.timeout_minutes):
                job.status = Status.EXPIRED
                job.access = []
                self._audit.record(job.id, "expired")
                return job
            if decision == "deny":
                job.status = Status.REJECTED
                job.access = []
                job.error = "You didn't allow access to " + ", ".join(folders) + "."
                self._audit.record(job.id, "access_denied", folders=", ".join(folders))
                return job
            keep = decision == "always"
            for folder in folders:
                if self._policy.can_grant(folder) is None:
                    self._policy.grant(folder, None if keep else job.id, device=job.owner)   # the folder is the asking phone's
            if keep:
                self._save_grants()
            self._audit.record(job.id, "access_granted", mode=decision, folders=", ".join(folders))
            # start over with the access in place: the plan, the typo fixes and the reading all work as normal
            job.access, job.summary, job.steps, job.outputs, job.error = [], "", [], [], None
            job.activity, job.awaiting_since, job.status = [], None, Status.PLANNING
            self._tokens[job.id] = CancelToken()
        self._start_planning(job)  # outside the lock: planning takes it again
        return job

    def _save_grants(self) -> None:
        self._grants.save_entries(self._policy.entries())

    def revoke_access(self, folder: str, owner: str | None = None) -> list[str]:
        """Take back access that was kept. A phone takes back its own; the Admin phone (or the PC) takes it back from everyone, or from one phone when `owner` says which.
        Returns the folders the asking phone can still use."""
        if self._policy.revoke(folder, owner):
            self._save_grants()
            self._audit.record("-", "access_revoked", path=folder, **({"device_id": owner} if owner else {}))
        return [str(f) for f in self._policy.granted()]

    def granted_folders(self) -> list[str]:
        return [str(f) for f in self._policy.granted()]

    def granted_details(self) -> list[dict]:
        """The folders the asking phone can use: its own, and those shared with it (the Admin phone sees all). Each says whether it is the phone's own."""
        return [{"path": row["path"], "change": row["change"], "mine": row["mine"], "shared": row["shared"]} for row in self._policy.folders_for_phone()]

    def folder_entries(self) -> list[dict]:
        """Every kept folder with the phone it belongs to and who it is shared with, for the Admin phone and the PC page."""
        return [{"path": str(e["path"]), "owner": e["device"], "change": e["change"], "shared": e["shared"]} for e in self._policy.entries()]

    def share_folder(self, folder: str, owner: str, targets: dict[str, bool]) -> bool:
        """The Admin chooses which other phones may use a phone's folder (and whether they may also change files there)."""
        done = self._policy.share(folder, owner, targets)
        if done:
            self._save_grants()
            self._audit.record("-", "folder_shared", path=str(folder), owner=owner, shared_with=",".join(sorted(targets)), device_id=owner)
        return done

    def forget_phone(self, device_id: str) -> None:
        """A phone was removed: its folders go, and it is taken off every share list."""
        self._policy.forget_device(device_id)
        self._save_grants()

    def grant_folder(self, folder: str, change: bool) -> list[dict]:
        """The user, in the editor, allows reading (and with `change`, editing) a folder. Kept until removed."""
        if change and not self._settings.agent.allow_edit_grants:
            raise PathDenied("Allowing changes outside the writable folders is turned off in config.yaml (agent.allow_edit_grants).")
        reason = self._policy.can_grant(folder, change=change)
        if reason:
            raise PathDenied(reason)
        self._policy.grant(folder, None, change=change)
        self._save_grants()
        self._audit.record("-", "access_granted", mode="edit" if change else "read", folders=str(folder))
        return self.granted_details()

    # -- the phone's editor ------------------------------------------------------

    def open_file(self, path: str) -> dict:
        """What the viewer and editor need: the kind of file, its text, whether it can be changed, and a fingerprint."""
        real = self._policy.resolve(self._base / path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        size = real.stat().st_size
        stat = real.stat()
        info = {
            "path": str(real), "name": real.name, "folder": str(real.parent), "size": size, "modified": stat.st_mtime,
            "kind": "text", "mime": "", "can_change": self._policy.can_change(real),
            "editable": False, "reason": "", "text": "", "sha": "", "lines": 0,
        }
        agent = self._settings.agent
        mime = VIEWABLE.get(real.suffix.lower())
        if mime:  # a picture or a PDF: shown by the phone itself, fetched separately
            info.update(kind="pdf" if mime == "application/pdf" else "image", mime=mime)
            if agent.max_download_mb and size > agent.max_download_mb * 1024 * 1024:
                info["reason"] = f"This file is {size // 1024 // 1024} MB, over the {agent.max_download_mb} MB limit."
            elif info["kind"] == "image":
                dims = imaging.size_of(real)
                small = size <= agent.max_image_mb * 1024 * 1024
                info.update(width=dims[0] if dims else 0, height=dims[1] if dims else 0,
                            editable_image=small and imaging.available(),  # the picture editor can open it (saving needs the folder to allow changes)
                            saves_as_is=real.suffix.lower() in imaging.EDITABLE)  # GIF, icons and SVG are saved as a PNG copy instead
                if small:
                    info["sha"] = hashlib.sha256(real.read_bytes()).hexdigest()  # so a save can tell the file changed meanwhile
            self._audit.record("-", "opened_in_viewer", path=real)
            return info
        if size > agent.max_view_kb * 1024:
            info["kind"] = "toobig"
            info["reason"] = (f"This file is {size // 1024} KB and the viewer shows files up to {agent.max_view_kb} KB. "
                              "Download it, or ask the AI about a specific part.")
            return info
        data = real.read_bytes()
        text = _decode(data)
        if text is None:
            info["kind"] = "binary"
            info["reason"] = "This isn't a UTF-8 text file, so it can only be downloaded."
            return info
        # The browser's text box turns every line break into \n, so the editor is given \n too. Saving puts the file's own
        # style back (see save_file), and the fingerprint below is of the real bytes.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        small = size <= agent.max_edit_kb * 1024
        info.update(text=text, sha=hashlib.sha256(data).hexdigest(), editable=small, lines=text.count("\n") + 1)
        if not small:
            info["reason"] = f"This file is {size // 1024} KB, over the {agent.max_edit_kb} KB the editor can change. You can read it here."
        self._audit.record("-", "opened_in_editor", path=real)
        return info

    def raw_file(self, path: str) -> tuple[Path, str]:
        """An image or a PDF to show in the viewer, with the type to send it as. Read access only; nothing else is served."""
        real = self._policy.resolve(self._base / path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        mime = VIEWABLE.get(real.suffix.lower())
        if not mime:
            raise PathDenied("Only pictures and PDFs are shown this way.")
        self._check_size(real)
        return real, mime

    # -- pictures: the gallery, thumbnails, and saving an edited picture -------------------------

    GALLERY_LIMIT = 1000

    def list_images(self, folder: str) -> dict:
        """The pictures directly inside a folder, newest first, for the gallery."""
        real = self._policy.resolve(self._base / (folder or "."))
        if not real.is_dir():
            raise FileNotFoundError(f"{real} is not a folder")
        items = []
        with os.scandir(real) as entries:
            for entry in entries:
                try:
                    if entry.is_file() and imaging.is_image(entry.name):
                        stat = entry.stat()
                        items.append({"name": entry.name, "path": str(real / entry.name), "size": stat.st_size, "modified": stat.st_mtime})
                except OSError:
                    continue  # a file that vanished or can't be read is simply not listed
        items.sort(key=lambda i: (-i["modified"], i["name"].lower()))
        self._audit.record("-", "opened_gallery", path=real, count=len(items))
        return {"folder": str(real), "name": real.name or str(real), "total": len(items), "images": items[: self.GALLERY_LIMIT],
                "truncated": len(items) > self.GALLERY_LIMIT, "can_change": self._policy.can_change(real / "x.png")}

    def thumb(self, path: str, size: int) -> tuple[bytes, str]:
        """A small copy of one picture for the gallery."""
        real = self._policy.resolve(self._base / path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        if not imaging.is_image(real) or real.suffix.lower() in (".svg", ".ico"):
            raise PathDenied("No thumbnail for this kind of file.")
        self._check_size(real)
        return imaging.thumbnail(real, size, self._settings.storage.database.parent / "thumbs")

    # -- the file browser: folders you can walk through, and files you can open, play or fetch -----------------

    STREAM_MINUTES = 120
    DOWNLOAD_HOURS = 6
    STREAM_MAX = 300

    def browse(self, path: str, *, q: str = "", sort: str = "name", order: str = "asc", hidden: bool = False, offset: int = 0, limit: int = 200) -> dict:
        """One folder's contents, or (with no path) the places browsing may start from. Reading only."""
        if not path.strip():
            places = []
            for folder, kind in self._policy.visible_roots():
                if folder.is_dir():
                    places.append({"name": folder.name or str(folder), "path": str(folder), "kind": "folder", "type": "root", "size": 0,
                                   "modified": 0, "hidden": False, "access": kind})
            self._audit.record("-", "browsed_roots", count=len(places))
            return {"path": "", "name": "My folders", "root": True, "crumbs": [], "parent": None, "entries": places, "total": len(places),
                    "folders": len(places), "files": 0, "offset": 0, "truncated": False, "more": False, "can_change": False}
        real = self._policy.resolve(self._base / path)
        if not real.is_dir():
            raise FileNotFoundError(f"{real} is not a folder")
        data = listing.list_folder(real, q=q, sort=sort, order=order, hidden=hidden, offset=offset, limit=limit,
                                   is_blocked=self._policy.is_protected)
        homes = [f for f, _ in self._policy.visible_roots() if is_inside(real, f)]
        home = max(homes, key=lambda f: len(f.parts)) if homes else real
        crumbs = [{"name": home.name or str(home), "path": str(home)}]
        walk = home
        for part in real.relative_to(home).parts if real != home else ():
            walk = walk / part
            crumbs.append({"name": part, "path": str(walk)})
        parent = crumbs[-2]["path"] if len(crumbs) > 1 else ""  # "" = back to the list of places
        if offset == 0:  # paging through one folder is one visit
            self._audit.record("-", "browsed_folder", path=real, count=data["total"])
        data.update(path=str(real), name=real.name or str(real), root=False, crumbs=crumbs, parent=parent,
                    can_change=self._policy.can_change(real / "x"))
        return data

    def resolve_file(self, path: str) -> Path:
        """A file this phone may read (the folder rules decide), for features that act on it, such as printing."""
        real = self._policy.resolve(self._base / path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        return real

    def resolve_folder(self, path: str) -> Path:
        """A folder this phone may read (the folder rules decide), for features that watch it."""
        real = self._policy.resolve(self._base / path)
        if not real.is_dir():
            raise FileNotFoundError(f"{real} is not a folder")
        return real

    def stream_link(self, path: str) -> dict:
        """A short-lived link to play one video or audio file from the PC. The player can't send this phone's token, so the link
        itself is the key: unguessable, for that one file, and gone after a while."""
        real = self._policy.resolve(self._base / path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        mime = listing.STREAMABLE.get(real.suffix.lower())
        if not mime:
            raise PathDenied("Only video and audio can be played this way.")
        now = time.monotonic()
        with self._lock:
            for key in [k for k, (_, _, until) in self._streams.items() if until <= now]:
                del self._streams[key]
            while len(self._streams) >= self.STREAM_MAX:
                self._streams.pop(min(self._streams, key=lambda k: self._streams[k][2]))
            key = secrets.token_urlsafe(18)
            self._streams[key] = (real, mime, now + self.STREAM_MINUTES * 60)
        self._audit.record("-", "streamed", path=real)
        return {"url": f"/stream/{key}", "mime": mime, "name": real.name, "size": real.stat().st_size}

    def stream_file(self, key: str) -> tuple[Path, str]:
        """The file behind a stream link, checked again now: a folder taken back stops its streams too."""
        with self._lock:
            found = self._streams.get(key)
        if found is None or found[2] <= time.monotonic():
            raise PathDenied("This link has run out.")
        real, mime, _ = found
        self._policy.resolve(real)  # raises when the folder is no longer allowed
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        return real, mime

    def offer_file(self, path: str) -> dict:
        """The person tapped Download on a file in the browser: that tap is the approval, so the file may now be fetched."""
        real = self._policy.resolve(self._base / path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        self._check_size(real)
        with self._lock:
            self._offered.add(real)
        return {"path": str(real), "name": real.name, "size": real.stat().st_size}

    def offer_folder(self, path: str) -> dict:
        """The person picked a folder to download (long-press on the PC's screen): pack it into one zip, and offer that zip like any file."""
        real = self._policy.resolve(self._base / path)
        if not real.is_dir():
            raise FileNotFoundError(f"{real} is not a folder")
        limit = self._settings.agent.max_download_mb
        root = Path(tempfile.gettempdir()) / "afp_zips"
        cutoff = time.time() - 86400
        if root.is_dir():  # zips made for earlier picks are not kept for ever
            for old in root.glob("screen-*"):
                try:
                    if old.stat().st_mtime < cutoff:
                        for f in old.glob("*"):
                            f.unlink(missing_ok=True)
                        old.rmdir()
                except OSError:
                    pass
        job_id = "screen-" + uuid.uuid4().hex[:10]
        try:
            zipped = self._zip_folder(real, job_id, None, limit * 1024 * 1024 if limit else 0)
            self._check_size(zipped)
        except Exception:
            made = root / job_id
            if made.is_dir():
                for f in made.glob("*"):
                    f.unlink(missing_ok=True)
                made.rmdir()
            raise
        with self._lock:
            self._offered.add(zipped.resolve())
        self._audit.record("-", "send_file", path=real)
        return {"path": str(zipped.resolve()), "name": zipped.name, "size": zipped.stat().st_size}

    def item_info(self, path: str | Path) -> dict:
        """What a phone may be told about a file or folder it long-pressed on the PC's screen: its name and size, and whether it may be downloaded now
        (or which folder would have to be allowed first). Nothing about a protected place beyond that it cannot be opened."""
        real = Path(path)
        info = {"path": str(real), "name": real.name or str(real), "folder": real.is_dir(), "size": 0, "allowed": False, "needs_access": "", "reason": ""}
        try:
            real = self._policy.resolve(real)
        except OutsideAllowed as exc:
            info["needs_access"] = str(exc.path if exc.path.is_dir() else exc.path.parent)
            info["reason"] = "This folder isn't allowed yet."
            return info
        except PathDenied as exc:
            info["reason"] = str(exc)
            return info
        try:
            if real.is_file():
                info["size"] = real.stat().st_size
                limit = self._settings.agent.max_download_mb
                if limit and info["size"] > limit * 1024 * 1024:
                    info["reason"] = f"{real.name} is larger than the {limit} MB download limit."
                    return info
            info["allowed"] = True
        except OSError:
            info["reason"] = "Windows doesn't let this app open that."
        return info

    @_saves
    def save_image(self, path: str, data: bytes, base_sha: str | None, conversation: str | None = None, unique: bool = False, note: str = "", owner: str = "") -> dict:
        """Save a picture the user edited on the phone. Their tap on Save is the approval; it is snapshotted, so it can be undone.
        `unique`: it is a new copy, so a name already taken gets (2), (3)... and nothing is replaced."""
        real = self._policy.resolve(self._base / path, for_write=True)  # ReadOnlyPath / OutsideAllowed when not allowed
        width, height = imaging.check_upload(data, real.suffix, self._settings.agent.max_image_mb * 1024 * 1024)  # ValueError: not a usable picture
        if unique and real.exists():
            real = self._policy.resolve(real.with_name(imaging.unique_name(real.parent, real.name)), for_write=True)
        current = real.read_bytes() if real.exists() else b""
        if not unique and base_sha is not None and hashlib.sha256(current).hexdigest() != base_sha:
            raise FileChanged(f"{real.name} changed on the PC after you opened it.")
        digest = hashlib.sha256(data).hexdigest()
        if data == current:
            return {"saved": False, "path": str(real), "name": real.name, "sha": digest, "width": width, "height": height, "job": None}
        note = " ".join(note.split())[:120]
        with self._lock:
            job = Job(uuid.uuid4().hex[:8], f"Edited picture {real.name} by hand", self._now(), conversation=conversation or "default", status=Status.DONE, owner=owner)
            job.summary = f"Saved your edits to {real.name}"
            job.steps = [Step(Action.WRITE, str(real))]
            job.outputs = [f"Saved {real.name}: {width}×{height}" + (f" ({note})." if note else ".")]
            self._jobs[job.id] = job
            self._tokens[job.id] = CancelToken()
            self._audit.record(job.id, "submitted", prompt=job.prompt, conversation=job.conversation)
        self._tools(job.id).write_bytes(real, data, approved=True)
        self._offered.add(real)
        job.changed.append(str(real))
        self._audit.record(job.id, "done", changed=1)
        return {"saved": True, "path": str(real), "name": real.name, "sha": digest, "width": width, "height": height, "job": job.to_dict()}

    @_saves
    def save_file(self, path: str, text: str, base_sha: str | None, conversation: str | None = None, owner: str = "") -> dict:
        """Save the user's own edits. Their tap on Save is the approval; the change is snapshotted, so it can be undone."""
        real = self._policy.resolve(self._base / path, for_write=True)  # ReadOnlyPath / OutsideAllowed when not allowed
        current = real.read_bytes() if real.exists() else b""
        if base_sha is not None and hashlib.sha256(current).hexdigest() != base_sha:
            raise FileChanged(f"{real.name} changed on the PC after you opened it.")
        if len(text.encode("utf-8")) > self._settings.agent.max_edit_kb * 1024:
            raise FileTooBig(f"That is larger than the {self._settings.agent.max_edit_kb} KB the editor saves.")
        had_bom, crlf = current.startswith(codecs.BOM_UTF8), b"\r\n" in current
        body = text.replace("\r\n", "\n").replace("\r", "\n")  # the browser's box uses \n; the file keeps its own style
        data = (codecs.BOM_UTF8 if had_bom else b"") + (body.replace("\n", "\r\n") if crlf else body).encode("utf-8")
        if data == current:
            return {"saved": False, "sha": hashlib.sha256(current).hexdigest(), "job": None}
        before = (_decode(current) or "").splitlines()
        added = removed = 0
        for line in difflib.ndiff(before, body.splitlines()):
            added += line.startswith("+ ")
            removed += line.startswith("- ")
        with self._lock:
            job = Job(uuid.uuid4().hex[:8], f"Edited {real.name} by hand", self._now(), conversation=conversation or "default", status=Status.DONE, owner=owner)
            job.summary = f"Saved your edits to {real.name}"
            job.steps = [Step(Action.WRITE, str(real))]
            job.outputs = [f"Saved. {added} line{'s' if added != 1 else ''} added, {removed} removed."]
            self._jobs[job.id] = job
            self._tokens[job.id] = CancelToken()
            self._audit.record(job.id, "submitted", prompt=job.prompt, conversation=job.conversation)
        self._tools(job.id).write_bytes(real, data, approved=True)
        self._offered.add(real)
        job.changed.append(str(real))
        self._audit.record(job.id, "done", changed=1)
        return {"saved": True, "sha": hashlib.sha256(data).hexdigest(), "job": job.to_dict()}

    @_saves
    def approve(self, job_id: str, pin: str | None = None) -> Job:
        with self._lock:
            needs_pin = any(s.action is Action.POWER and s.content == "unlock" for s in self._require(job_id, Status.AWAITING).steps)
        if needs_pin:
            if not pin:
                raise PinRequired("Type your unlock PIN to approve this.")
            self._unlocker.check_pin(pin)  # a wrong PIN raises here, so the request stays waiting and can be tried again
        with self._lock:
            job = self._require(job_id, Status.AWAITING)
            if needs_pin:
                self._unlock_pins[job.id] = pin or ""
            timeout = timedelta(minutes=self._settings.approval.timeout_minutes)
            if self._now() - (job.awaiting_since or job.created_at) > timeout:
                job.status = Status.EXPIRED
                self._audit.record(job.id, "expired")
                self._settle(job)
                return job
            self._audit.record(job.id, "approved")
            self._worked_since[job.id] = self._now()
            if any(s.action is Action.RUN for s in job.steps):
                job.status = Status.RUNNING  # a command can take a while: do not hold the lock
                thread = threading.Thread(target=self._as_owner, args=(job, self._run, job, True), daemon=True)
                self._threads.append(thread)
                thread.start()
                return job
            self._as_owner(job, self._run, job, True)
            return job

    @_saves
    def reject(self, job_id: str) -> Job:
        with self._lock:
            job = self._require(job_id, Status.AWAITING)
            job.status = Status.REJECTED
            self._audit.record(job.id, "rejected")
            self._settle(job)
            return job

    @_saves
    def stop(self, job_id: str) -> Job:
        """Stop a job that is thinking or running a command. Kills the command, or hangs up on the model."""
        with self._lock:
            job = self.get(job_id)
            if job.status not in (Status.PLANNING, Status.RUNNING):
                raise JobStateError(f"job is {job.status.value}, so there is nothing to stop")
            token = self._tokens.get(job.id)
            if token:
                token.cancel()
            job.status = Status.STOPPED
            self._audit.record(job.id, "stopped")
            self._settle(job)
            return job

    @_saves
    def undo(self, job_id: str) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job.status not in (Status.DONE, Status.STOPPED):
                raise JobStateError(f"job is {job.status.value}, not done")
            if not job.changed:
                raise JobStateError("this job changed nothing, so there is nothing to undo")
            self._tools(job.id).undo()
            job.status = Status.UNDONE
            return job

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def list(self) -> list[Job]:
        self._sync()
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def note(self, event: str, **details: object) -> None:
        """Something that happened outside any request (a phone paired, a sign-in), for the audit trail."""
        self._audit.record("-", event, **details)

    def audit_events(self, limit: int = 2000) -> list[dict]:
        """Everything the audit log holds, including jobs from before a restart."""
        return self._audit.read(limit)

    def file_for_download(self, relative_path: str) -> Path:
        """Return a real path for download. Only files a job offered can be fetched."""
        real = (self._base / relative_path).resolve()
        if self._policy.is_protected(real):
            raise ProtectedPath(real)
        if real not in self._offered:
            raise PathDenied(f"{real} was not offered for download by any request")
        self._check_size(real)
        self._audit.record("-", "download", path=real)
        return real

    def _zip_folder(self, folder: Path, job_id: str, token: CancelToken | None = None, budget: int = 0) -> Path:
        """Pack a folder into one zip in a temporary place, skipping protected files and links. It is written as it goes, so size is no problem."""
        out_dir = Path(tempfile.gettempdir()) / "afp_zips" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{folder.name or 'folder'}.zip"
        total = 0
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for root, dirs, names in os.walk(folder):
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d)) and not self._policy.is_protected(Path(root, d))]
                if not dirs and not names:
                    zf.write(root, os.path.relpath(root, folder.parent) + "/")
                for n in names:
                    full = Path(root, n)
                    if full.is_symlink() or self._policy.is_protected(full):
                        continue
                    if token is not None:
                        token.check()
                    if budget:  # a phone asked for this folder: stop packing as soon as it is clearly too big, not after minutes of work
                        try:
                            total += full.stat().st_size
                        except OSError:
                            continue
                        if total > budget:
                            raise FileTooBig(f"{folder.name} is larger than the {budget // (1024 * 1024)} MB download limit")
                    try:
                        zf.write(full, os.path.relpath(full, folder.parent))
                    except OSError:  # locked or vanished: leave it out rather than fail the whole folder
                        continue
        return target

    def download_link(self, path: str) -> dict:
        """A link that streams one offered file to the phone (any size). The key is the permission, like a stream link."""
        real = self.file_for_download(path)
        if not real.is_file():
            raise FileNotFoundError(f"{real} is not a file")
        now = time.monotonic()
        with self._lock:
            for key in [k for k, (_, until) in self._downloads.items() if until <= now]:
                del self._downloads[key]
            key = secrets.token_urlsafe(18)
            self._downloads[key] = (real, now + self.DOWNLOAD_HOURS * 3600)
        st = real.stat()
        return {"url": f"/dl/{key}", "name": real.name, "size": st.st_size, "modified": int(st.st_mtime)}

    def downloadable(self, key: str) -> Path:
        with self._lock:
            found = self._downloads.get(key)
        if found is None or found[1] <= time.monotonic() or not found[0].is_file():
            raise PathDenied("This link has run out.")
        return found[0]

    def _check_size(self, real: Path) -> None:
        limit = self._settings.agent.max_download_mb
        if limit and real.is_file() and real.stat().st_size > limit * 1024 * 1024:
            raise FileTooBig(f"{real.name} is larger than the {limit} MB download limit")

    @_saves
    def join_background(self, timeout: float = 10) -> None:
        """Wait for background planning to finish (used by tests and shutdown)."""
        for thread in list(self._threads):
            thread.join(timeout)

    # -- internals ----------------------------------------------------------

    def _tools(self, job_id: str) -> FileTools:
        return FileTools(job_id, self._policy, self._snapshots, self._audit)

    def _require(self, job_id: str, expected: Status) -> Job:
        job = self.get(job_id)
        if job.status is not expected:
            raise JobStateError(f"job is {job.status.value}, not {expected.value}")
        return job

    def _plan_job(self, job: Job) -> None:
        token = self._tokens.get(job.id) or CancelToken()
        reset = CURRENT.set(token)  # so the call to the model can be hung up on
        try:
            self._plan_job_inner(job, token)
        finally:
            CURRENT.reset(reset)
            self._settle(job)

    def _plan_job_inner(self, job: Job, token: CancelToken) -> None:
        # Planning can be slow (a local model), so it runs outside the lock.
        agent = self._settings.agent
        if agent.ask_for_access:
            # A folder the user typed that is not allowed: ask now. Starting a slow model first only wastes time.
            outside = []
            for typed in mentioned_paths(job.prompt):
                try:
                    self._policy.resolve(typed)
                except OutsideAllowed as exc:
                    outside.append(exc.path)
                except PathDenied:
                    pass  # protected or similar: the normal flow explains it
            if outside:
                with self._lock:
                    self._ask_or_fail(job, outside)
                return
        context = _ReadContext(
            self._policy,
            self._base,
            self._tools(job.id),
            ([str(f) for f in agent.allowed_folders], [str(f) for f in agent.read_only_folders]),
            self._history_before(job),
            self._runner.enabled,
            self._last_file_before(job),
            self._apps.apps if self._settings.apps.enabled else None,
            self._last_teams_before(job) if self._settings.apps.teams else None,
        )
        job.activity = context.calls  # live, so the phone can show progress while it thinks
        try:
            plan = self._planner.plan(job.prompt, context)
        except Cancelled:
            return  # stop() already marked it
        except PlanError as exc:
            if not token.cancelled:
                self._fail_locked(job, str(exc))
            return
        except Exception as exc:  # never leave a job stuck on "planning"
            if not token.cancelled:
                self._fail_locked(job, f"{type(exc).__name__}: {exc}")
            return

        with self._lock:
            if token.cancelled or job.status is not Status.PLANNING:
                return  # stopped while the model was still answering: throw the answer away
            if context.denied and self._settings.agent.ask_for_access:
                self._ask_or_fail(job, context.denied)  # it tried a folder that is not allowed: ask, then start over
                return
            try:
                self._load_plan(job, plan)
            except OutsideAllowed as exc:
                if self._settings.agent.ask_for_access:
                    self._ask_or_fail(job, [exc.path])
                else:
                    self._fail(job, self._explain(exc))
                return
            except (PolicyError, PlanError) as exc:
                self._fail(job, self._explain(exc))
                return
            if plan.reply is not None and not job.steps:
                job.outputs.append(plan.reply)
                job.status = Status.DONE
                self._audit.record(job.id, "replied")
                self._announce(job, "done")
            elif any(self._policy.needs_approval(s.action) or (s.action is Action.POWER and s.content == "unlock") or self._app_needs_approval(s) for s in job.steps):  # unlocking, Teams and (by default) opening an app always ask, whatever config.yaml says
                job.status = Status.AWAITING
                job.awaiting_since = self._now()
                self._audit.record(job.id, "awaiting_approval", summary=job.summary)
                self._announce(job, "awaiting")
            else:
                self._run(job, approved=False)

    def _load_plan(self, job: Job, plan: Plan) -> None:
        for step in plan.steps:  # refuse early so the phone never shows a denied plan
            self._check_step(step)
        job.summary = plan.summary
        job.steps = list(plan.steps)

    @property
    def unlocker(self) -> AgentClient:
        """The unlock helper's client (the PC's own page uses it to show whether unlocking is on)."""
        return self._unlocker

    def _check_unlock(self) -> None:
        info = self._unlocker.status()
        if not info.get("running"):
            raise PlanError("The unlock helper isn't running on this PC. Run  python -m handoff.unlock.setup status  at the PC to see what is missing. If you haven't set it up yet, run  python -m handoff.unlock.setup install  in an administrator PowerShell.")
        if not info.get("configured"):
            raise PlanError("The unlock helper is running but has no saved sign-in. Run  python -m handoff.unlock.setup install  at the PC.")
        if not info.get("enabled"):
            raise PlanError("Unlocking from the phone is switched off on this PC.")
        if info.get("attention"):
            wait = info.get("auto_resume_in") or 0
            if wait:
                minutes = max(1, round(wait / 60))
                raise PlanError(f"Unlocking paused because the last tries didn't work. It will try again by itself in about {minutes} minute{'s' if minutes != 1 else ''}, so ask again then. To try sooner, check the saved sign-in is still right, then open this PC's own page (the one with the connect code) and tap Start trying again under Unlock.")
            raise PlanError("Unlocking has stopped itself because the last tries didn't work, even after trying again by itself. Check the saved sign-in is still right, then open this PC's own page (the one with the connect code) and tap Start trying again under Unlock.")
        if info.get("locked_for"):
            raise PlanError(f"Too many wrong unlock PINs. It is paused for about {max(1, round(info['locked_for'] / 60))} more minute(s).")
        if info.get("locked") is False:
            raise PlanError("This PC isn't locked, so there is nothing to unlock.")

    def _app_needs_approval(self, step: Step) -> bool:
        return step.action is Action.APP and (step.app_kind != "open" or self._settings.apps.approve_open)

    def _check_app(self, step: Step) -> None:
        apps = self._settings.apps
        if step.app_kind == "open":
            if not apps.enabled:
                raise PlanError("Opening apps from the phone is switched off. To allow it, set apps.enabled to true in config.yaml on the PC.")
            return
        if not apps.teams:
            raise PlanError("Using Teams from the phone is switched off. To allow it, set apps.teams to true in config.yaml on the PC.")
        try:
            info = self._unlocker.status()
        except Exception:  # noqa: BLE001 - no helper installed: the PC may still be unlocked
            info = {}
        if info.get("locked") is True:
            raise PlanError("The PC is locked, and Windows doesn't let anything use Teams on a locked screen. Say “unlock my PC” first, then ask again.")

    def _check_step(self, step: Step) -> None:
        if step.action is Action.APP:
            self._check_app(step)
            return
        if step.action is Action.POWER:
            kind = step.content or ""
            if kind not in power.ACTIONS:
                raise PlanError(f"I don't know the power action {kind!r}.")
            if kind == "unlock":  # allowed by the helper being installed (that took an administrator and a PIN), not by power.remote_actions
                self._check_unlock()
                return
            if kind not in self._settings.power.remote_actions and not (kind == "screen_on" and "screen_off" in self._settings.power.remote_actions):
                raise PlanError(f"{power.ACTIONS[kind].summary} from the phone is switched off. To allow it, add \"{kind}\" to power.remote_actions in config.yaml on the PC.")
            return
        if step.action is Action.RUN:
            self._runner.check(step.content or "")
        if step.action in (Action.MOVE, Action.COPY):
            if not step.target:
                raise PlanError(f"{step.action.value} needs a destination")
            self._changeable(step.target)
        if step.action.changes_files:
            self._changeable(step.path)
        else:
            self._policy.resolve(self._base / step.path)  # reading: an unlisted folder can be asked about

    def _changeable(self, path: str) -> Path:
        """A place where something may be changed. Outside the writable folders that is a plain refusal, not a question."""
        try:
            return self._policy.resolve(self._base / path, for_write=True)
        except OutsideAllowed as exc:
            writable = ", ".join(str(f) for f in self._settings.agent.allowed_folders)
            raise PathDenied(f"{exc.path} is outside the allowed folders for changes. I can only change files in: {writable}") from None

    def _history_before(self, job: Job) -> list[dict]:
        """The last few requests before this one, as chat messages for the model."""
        turns = self._settings.llm.memory_turns
        if not turns:
            return []
        earlier = self._earlier(job)
        messages: list[dict] = []
        for past in earlier[-turns:]:
            messages.append({"role": "user", "content": past.prompt})
            messages.append({"role": "assistant", "content": self._describe(past)})
        return messages

    def _ask_or_fail(self, job: Job, paths: list[Path]) -> None:
        """The request needs folders that are not allowed. Ask the user to allow them, or say why that can't be done."""
        folders: list[Path] = []
        for path in paths:
            path = Path(path)
            folder = path if path.is_dir() else path.parent if (path.is_file() or path.suffix) else path
            reason = self._policy.can_grant(folder)
            if reason:
                self._fail(job, reason)
                return
            if folder not in folders:
                folders.append(folder)
        job.access = [str(f) for f in folders]
        job.status = Status.NEEDS_ACCESS
        job.awaiting_since = self._now()
        self._audit.record(job.id, "access_requested", folders=", ".join(job.access))
        self._announce(job, "needs_access")

    def _earlier(self, job: Job) -> list[Job]:
        """Earlier, finished jobs of the same chat, oldest first."""
        return [
            j for j in self.list()[::-1]
            if j.id != job.id and j.conversation == job.conversation
            and j.created_at < job.created_at and j.status is not Status.PLANNING
        ]

    def _last_teams_before(self, job: Job) -> str | None:
        """Who the last request in this chat was about in Teams (only if it was the very last thing done), for "reply: ..." with no name."""
        earlier = self._earlier(job)
        if not earlier:
            return None
        for step in earlier[-1].steps:
            if step.action is Action.APP and step.app_kind in ("teams_chat", "teams_reply") and step.pattern:
                return step.pattern
        return None

    def _last_file_before(self, job: Job) -> str | None:
        """The file this chat was last about, if it still exists. A file the phone was showing comes first."""
        if job.focus and (self._base / job.focus).is_file():
            return str((self._base / job.focus).resolve())
        for past in reversed(self._earlier(job)[-20:]):
            paths = [s.path for s in reversed(past.steps) if s.action in (Action.READ, Action.SEND, Action.WRITE)]
            paths += [a[len("read "):] for a in reversed(past.activity) if a.startswith("read ")]
            for path in paths:
                real = self._base / path
                if real.is_file():
                    return str(real)
        return None

    def _explain(self, exc: Exception) -> str:
        """A refusal in words the phone can act on: what is allowed, and how the PC's owner allows more."""
        text = str(exc)
        if not isinstance(exc, PathDenied) or isinstance(exc, ReadOnlyPath):
            return text
        agent = self._settings.agent
        allowed = [str(f) for f in [*agent.allowed_folders, *agent.read_only_folders]]
        denied = text.split(" is outside")[0]
        folder = os.path.dirname(denied) if os.path.splitext(denied)[1] else denied
        return (
            f"{text}.\nI can only use: {', '.join(allowed)}.\n"
            "To allow another folder, add it to config.yaml on the PC and restart AccessFromPhone:\n"
            f"agent:\n  read_only_folders:\n    - {folder}\n"
            "(Use allowed_folders instead if I should also be able to change files there.) "
            "For safety this can't be changed from the phone."
        )

    @staticmethod
    def _describe(job: Job) -> str:
        parts = [job.summary] if job.summary else []
        if job.steps:
            parts.append("Proposed: " + "; ".join(f"{s.action.value} {s.path}" for s in job.steps))
        for o in job.outputs:
            if job.steps and len(o) > 200:  # a file listing or file text: shortened, so it cannot be copied instead of looking again
                o = o[:200] + "\n... (shortened; use a tool to see the current contents)"
            parts.append(o[:500])
        if job.files:
            parts.append("Sent to the phone: " + ", ".join(job.files))
        if job.error:
            parts.append(f"Error: {job.error}")
        parts.append(f"[{job.status.value.replace('_', ' ')}]")
        return "\n".join(parts)[:600]  # small on purpose: this PC reads about 25 tokens a second

    def _fail_locked(self, job: Job, message: str) -> None:
        with self._lock:
            self._fail(job, message)

    def _fail(self, job: Job, message: str) -> None:
        job.status = Status.FAILED
        job.error = message
        self._audit.record(job.id, "failed", error=message)
        self._announce(job, "failed")
        self._settle(job)

    def _settle(self, job: Job) -> None:
        """When a job is over, the folders the user allowed "just this once" for it are closed again."""
        if job.status not in (Status.PLANNING, Status.AWAITING, Status.RUNNING, Status.NEEDS_ACCESS):
            self._policy.revoke_job(job.id)

    def _run(self, job: Job, approved: bool) -> None:
        tools = self._tools(job.id)
        token = self._tokens.get(job.id) or CancelToken()
        try:
            for step in job.steps:
                token.check()
                self._check_step(step)
                if step.action is Action.APP:
                    if not approved and self._app_needs_approval(step):
                        raise PlanError("This needs your approval first.")
                    try:
                        lines = self._apps.run(step.app_kind, step.pattern or "", step.content)
                    except AppError as exc:
                        self._audit.record(job.id, "app_failed", kind=step.app_kind, reason=str(exc)[:160])
                        raise PlanError(str(exc)) from None
                    self._audit.record(job.id, "app", kind=step.app_kind, target=step.pattern or "")  # (never the message text)
                    job.outputs.append("\n".join(line for line in lines if line is not None).strip())
                    continue
                if step.action is Action.POWER:
                    self._policy.authorize(Action.POWER, self._base, approved)  # never without the person's approval
                    kind = {"sleep": "pc_sleep", "hibernate": "pc_sleep", "restart": "pc_restart", "shutdown": "pc_shutdown"}.get(step.content or "")
                    if kind and self.on_pc_event:
                        try:
                            self.on_pc_event(kind)  # before it happens: afterwards there is no network to say it with
                        except Exception:  # noqa: BLE001
                            pass
                    if step.content == "unlock":
                        if not approved:
                            raise PlanError("Unlocking needs your approval and your unlock PIN.")
                        pin = self._unlock_pins.pop(job.id, "")
                        try:
                            message = self._unlocker.unlock(pin)
                        except UnlockError as exc:
                            self._audit.record(job.id, "unlock_failed", reason=exc.code)
                            raise
                        finally:
                            pin = ""
                        self._audit.record(job.id, "unlocked")
                        job.outputs.append(message)
                        continue
                    message = power.schedule(step.content or "")
                    self._audit.record(job.id, "power", kind=step.content)
                    job.outputs.append(message)
                    continue
                real = self._policy.resolve(
                    self._base / step.path, for_write=step.action.changes_files
                )
                if step.action is Action.READ and step.editor and step.browse:
                    if not real.is_dir():
                        raise FileNotFoundError(f"{real} is not a folder")
                    job.open, job.open_mode = str(real), "folder"
                    job.outputs.append(f"Opening {real.name or real} in the file browser.")
                elif step.action is Action.READ and step.editor and step.gallery:
                    if not real.is_dir():
                        raise FileNotFoundError(f"{real} is not a folder")
                    count = sum(1 for e in os.scandir(real) if e.is_file() and imaging.is_image(e.name))
                    job.open, job.open_mode = str(real), "gallery"
                    job.outputs.append(f"Showing the {count} picture{'s' if count != 1 else ''} in {real.name or real}." if count else f"There are no pictures in {real.name or real}.")
                elif step.action is Action.READ and step.editor:
                    if not real.is_file():
                        raise FileNotFoundError(f"{real} is not a file")
                    job.open, job.open_mode = str(real), "view" if step.view else "edit"
                    job.outputs.append(f"Opening {real.name} full screen." if step.view else f"Opening {real.name} in the editor.")
                elif step.action is Action.READ:
                    if step.pattern:
                        text = tools.find_files(real, step.pattern).render()
                    elif real.is_dir():
                        text = "\n".join(tools.list_dir(real)) or "(empty folder)"
                    elif step.lines:
                        text = tools.read_lines(real, step.lines, step.tail)
                    else:
                        text = tools.read_file(real)
                    if len(text) > MAX_OUTPUT_CHARS:
                        text = text[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
                    job.outputs.append(text)
                elif step.action is Action.SEND:
                    self._policy.authorize(Action.SEND, real, approved)
                    if real.is_dir():  # a folder goes as one zip
                        real = self._zip_folder(real, job.id, token)
                    elif not real.is_file():
                        raise FileNotFoundError(f"{real} is not a file")
                    self._check_size(real)
                    self._offered.add(real)
                    job.files.append(str(real))
                    job.outputs.append(f"{real.name} is ready to download ({real.stat().st_size:,} bytes).")
                    self._audit.record(job.id, "send_file", path=real)
                elif step.action in (Action.MOVE, Action.COPY):
                    transfer = tools.move_file if step.action is Action.MOVE else tools.copy_file
                    done = transfer(real, self._base / (step.target or ""), approved)
                    self._offered.add(done)
                    job.changed.append(step.target or "")
                elif step.action is Action.RUN:
                    command = (step.content or "").strip()
                    result = self._runner.run(job.id, real, command, approved, token)
                    job.outputs.append(result.render(command))
                    if result.stopped:
                        raise Cancelled()
                elif step.action is Action.WRITE:
                    tools.write_file(real, step.content or "", approved)
                    self._offered.add(real)
                    job.changed.append(step.path)
                elif step.action is Action.DELETE:
                    tools.delete_file(real, approved)
                    job.changed.append(step.path)
                else:
                    raise PlanError(f"{step.action.value} is not supported yet")
        except Cancelled:
            return  # stop() already marked it; what it changed so far is kept, and can be undone
        except Exception as exc:  # roll back anything this job already changed
            if token.cancelled:
                return
            if job.changed:
                tools.undo()
                job.changed = []
            self._fail(job, self._explain(exc) if isinstance(exc, PathDenied) else str(exc) if isinstance(exc, UnlockError) else f"{type(exc).__name__}: {exc}")
            return
        finally:
            self._unlock_pins.pop(job.id, None)  # whatever happened, the PIN is not kept
        if token.cancelled:
            return
        job.status = Status.DONE
        self._audit.record(job.id, "done", changed=len(job.changed))
        self._announce(job, "done")
        self._settle(job)
