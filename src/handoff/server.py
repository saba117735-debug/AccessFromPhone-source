"""HTTP server for the phone page. Every /api call needs a paired phone's token (or, with accounts off, the shared token)."""
from __future__ import annotations

import asyncio
import getpass
import json
import mimetypes
import platform
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from importlib import metadata
from pathlib import Path

from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .accounts import AccountError, AccountStore, Device, DeviceLimit, Locked
from .config import Settings
from . import sysinfo
from .jobs import FileChanged, FileTooBig, JobManager, JobNotFound, JobStateError, Status
from .pause import Burst
from .folders import folders_overview, parse_targets, requests_overview
from .policy import set_viewer
from .screen.blackout import blackout
from .screen.capture import last_still as capture_still
from .screen import InputError, ScreenBusy, ScreenManager, ScreenNotAllowed, ScreenUnavailable, Settings as ScreenSettings
from .access import Access, AccessDenied, SessionHeld, SessionRefused, Settings as AccessSettings, levels as access_levels
from .unlock import UnlockError
from . import pcwatch
from .daily import Daily, routes as daily_routes
from .push import Message, Notifier
from .planner import AnthropicPlanner, DemoPlanner, OllamaPlanner, Planner, build_planner
from .policy import OutsideAllowed, PathDenied, ReadOnlyPath

_WEB = Path(__file__).parent / "web"
_PAGE = _WEB / "index.html"
_CONVERSATION = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

mimetypes.add_type("application/manifest+json", ".webmanifest")


def quiet_disconnects(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Windows prints a long traceback each time a phone drops its connection (the screen locks, the app closes).

    That is normal and harmless, so those few errors are ignored. Anything else is reported as usual.
    """
    if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    asyncio.get_running_loop().set_exception_handler(quiet_disconnects)
    sysinfo.DEFAULT.start()  # so the network speed is already known when the phone first asks
    app.state.daily.start()  # reminders, watchers and the PC's notifications
    stop_watching = pcwatch.start(app.state.notifier)  # tells the phone before this PC sleeps, shuts down or runs out of battery
    yield
    app.state.daily.stop()
    app.state.notifier.notify_pc("pc_closing", wait=True)  # the app is stopping on purpose: say so while there is still a network
    stop_watching()
    app.state.manager.flush()  # the last few seconds of every request are written before the PC stops


class _Assets(StaticFiles):
    """The page's own files. They change when the app is updated, so the browser must re-check them."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def load_or_create_token(token_file: Path) -> str:
    if token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    token_file.write_text(token, encoding="utf-8")
    return token


class AccessIn(BaseModel):
    decision: Literal["always", "once", "deny"]


class PathIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class RevokeIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=40)  # take it back from this one phone (without it: from everyone)


class ShareTarget(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    change: bool = False  # may this phone change files there too (never more than the owner was allowed)


class ShareIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=40)  # the phone the folder belongs to (without it: the folder as every phone that has it, shared the same way)
    targets: list[ShareTarget] = Field(default_factory=list, max_length=30)  # who may use it (empty: no one)


class GrantIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    change: bool = False  # also allow editing files in the folder


class FileIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    text: str = Field(max_length=1_200_000)
    base_sha: str | None = Field(default=None, max_length=64)  # the file as the editor opened it; None = save over whatever is there
    conversation: str | None = Field(default=None, max_length=40)


class ApproveIn(BaseModel):
    pin: str | None = Field(default=None, max_length=64)  # the unlock PIN, only for a request to unlock this PC; never stored or logged


class ClipIn(BaseModel):
    text: str = Field(default="", max_length=100_000)


class ClaimIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=60)
    install: str = Field(default="", max_length=80)  # an id this app install keeps for itself, even after signing out: the same phone gets its own entry back
    previous: str = Field(default="", max_length=200)  # this phone's old token, if it still has one: it then replaces its own entry


class DeviceNameIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class DeviceRemoveIn(BaseModel):
    username: str = Field("", max_length=120)
    password: str = Field(min_length=1, max_length=256)


class PushSubIn(BaseModel):
    kind: Literal["web", "fcm"]
    endpoint: str = Field(default="", max_length=1000)  # web: the browser's push address
    keys: dict[str, str] = Field(default_factory=dict)  # web: p256dh and auth
    token: str = Field(default="", max_length=4096)  # fcm: the app's token


class TitleIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class ControlIn(BaseModel):
    on: bool


class InputIn(BaseModel):
    events: list[dict] = Field(min_length=1, max_length=40)  # each one is checked again by screen.inject.clean_event


class ItemIn(BaseModel):
    x: float = Field(ge=0.0, le=1.0)   # where on the PC's screen the finger was, as fractions of it
    y: float = Field(ge=0.0, le=1.0)


class ScreenAskIn(BaseModel):
    note: str = Field(default="", max_length=120)


class TakeoverIn(BaseModel):
    message: str = Field(default="", max_length=120)


class AnswerIn(BaseModel):
    decision: Literal["end", "continue"]


class LevelIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    level: str = Field(min_length=1, max_length=20)


class PrefsIn(BaseModel):
    """What this phone wants to hear about. Checked again by push.clean_prefs: anything unknown or out of range is ignored."""
    types: dict[str, bool] | None = None
    quiet: dict[str, bool | int | str] | None = None


class PushOffIn(BaseModel):
    kind: Literal["web", "fcm"] | None = None


class PromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    conversation: str | None = Field(default=None, max_length=40)
    file: str | None = Field(default=None, max_length=1000)  # the file the phone was showing: what "it" means
    client_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")  # a request held on the phone: sending it twice (a dropped connection) must not run it twice


def _folder_of(path) -> str:
    return str(path if path.is_dir() else path.parent)


def _version() -> str:
    try:
        return metadata.version("handoff")
    except metadata.PackageNotFoundError:  # the installed app has no package metadata: the installer stamped the version instead
        from . import _build

        return _build.VERSION


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user name on some service accounts
        return "You"


_SECURITY = {
    "device_paired": ("A phone was paired", "warn"), "pair_failed": ("A wrong pairing code was tried", "warn"),
    "pair_refused_limit": ("A phone was turned away: the PC has its maximum number of phones", "warn"),
    "owner_check_refused": ("A wrong owner password was tried", "bad"), "device_remove_refused": ("A wrong owner password was tried to remove a phone", "bad"),
    "device_removed_from_phone": ("A phone was removed", "warn"), "device_signed_out": ("A phone signed out", "ok"),
    "suspicious": ("Several wrong tries in a few minutes", "bad"), "paused": ("Phone access was paused", "bad"), "resumed": ("Phone access was resumed on the PC", "ok"),
}


def security_line(event: dict) -> dict | None:
    """One audit entry as a line for the security list, or None when it is not about who can reach the PC."""
    kind = event.get("event", "")
    if kind not in _SECURITY:
        return None
    text, tone = _SECURITY[kind]
    who = event.get("device") or event.get("by") or ""
    return {"time": event.get("time", ""), "kind": kind, "text": text, "who": str(who)[:60], "tone": tone}


def create_app(settings: Settings, planner: Planner | None = None) -> FastAPI:
    token = load_or_create_token(settings.server.token_file)
    if planner is None:
        planner, note = build_planner(settings.llm)
    else:
        note = "AI model: custom planner"
    manager = JobManager(settings, planner)

    app = FastAPI(title="AccessFromPhone", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
    accounts = AccountStore(manager.db, settings.accounts.pairing_minutes, settings.accounts.max_devices, legacy_file=settings.accounts.file, mode=settings.accounts.sign_in, forget_after_days=settings.accounts.forget_after_days) if settings.accounts.enabled else None
    app.state.token = token
    app.state.manager = manager
    app.state.planner_note = note
    app.state.accounts = accounts
    notifier = Notifier(settings.push, manager.db)
    manager.on_event = notifier.on_job_event  # a request that needs you, or is over, becomes a notification
    manager.on_pc_event = notifier.notify_pc  # the phone asked this PC to sleep or shut down: every phone hears it first
    app.state.notifier = notifier

    # -- who may do what, and one person at a time (see the access package)
    handoff = {"last": None}  # the phone that held the session last

    def cancel_waiting_plans() -> None:
        """Plans and folder questions still waiting belong to the person who just finished: they must not be answered by whoever is next."""
        for job in manager.list():
            try:
                if job.status is Status.AWAITING:
                    manager.reject(job.id)
                elif job.status is Status.NEEDS_ACCESS:
                    manager.decide_access(job.id, "deny")
            except (JobNotFound, JobStateError):
                pass

    def on_access_event(kind: str, **details) -> None:
        try:
            manager.note(kind, **details)
            if kind == "session_started":
                if handoff["last"] not in (None, details.get("device_id")):
                    cancel_waiting_plans()
                handoff["last"] = details.get("device_id")
            elif kind == "session_ended" and details.get("reason") not in ("idle", "lost"):
                cancel_waiting_plans()
            elif kind == "takeover_requested":  # the holder and the Admin phone are told at once, even with the page closed
                text = f"{details.get('device')} would like a turn on this PC."
                for target in {details.get("holder_id"), access.store.admin_id()} - {None, details.get("device_id")}:
                    threading.Thread(target=lambda t=target: notifier.send(Message("Someone wants a turn", text, "", "awaiting"), t), daemon=True).start()
        except Exception:  # noqa: BLE001 - recording or telling must never break the session itself
            pass

    ac = settings.access
    access = Access(manager.db, AccessSettings(ac.lease_seconds, ac.idle_minutes * 60, ac.ask_seconds, ac.cooldown_seconds), on_event=on_access_event)
    manager.access = access
    app.state.access = access
    sc = settings.screen
    screen = ScreenManager(manager.db, ScreenSettings(sc.enabled, sc.control, sc.idle_seconds, sc.ask_seconds, release_after=sc.release_after_seconds, audio=sc.audio, clipboard=sc.clipboard), on_event=lambda kind, **details: manager.note(kind, **details))
    screen.trusted = lambda device_id: not sc.admin_needs_yes and access.store.role(device_id) == "admin"
    manager.screen = screen  # the PC page and the desktop window reach it through the manager
    app.state.screen = screen
    daily = Daily(settings, manager, notifier, clipboard=screen.clipboard)  # everyday helpers: quick controls, inbox, notes, reminders, watchers, briefing...
    app.state.daily = daily
    if accounts is not None:
        accounts.on_signed_out = lambda gone: (access.sessions.end(gone.id, "signed_out"), screen.revoke(gone.id))
        accounts.on_removed = lambda gone: (access.sessions.end(gone.id, "removed"), access.store.forget(gone.id), screen.revoke(gone.id), manager.forget_phone(gone.id))

    def deny(exc: AccessDenied) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=exc.detail())

    def require_token(request: Request) -> None:
        supplied = request.headers.get("x-token", "")
        if accounts is not None:
            device = accounts.authenticate(supplied, request.client.host if request.client else "")
            if device is None:
                raise HTTPException(status_code=401, detail="missing or wrong access token")
            request.state.device = device
            if manager.pause_switch.paused:  # the owner (or any phone) pressed Pause: nothing gets through until the PC owner resumes
                raise HTTPException(status_code=423, detail="paused")
            if settings.access.enabled:
                try:
                    access.gate.check(request.method, request.url.path, device.id, device.name)  # may this level do it, and does this phone hold the session
                except AccessDenied as exc:
                    raise deny(exc) from None
            return
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="missing or wrong access token")
        if manager.pause_switch.paused:
            raise HTTPException(status_code=423, detail="paused")

    def tell_phones(message: Message) -> None:
        """A notification to every phone, off the request's own thread (sending can be slow, and must never delay or break the answer)."""
        threading.Thread(target=lambda: notifier.send(message), daemon=True).start()

    guesses = Burst(limit=3, window=600, quiet=600)  # wrong pairing codes and wrong owner passwords, together

    def guess_alert(kind: str) -> None:
        """Three wrong tries within ten minutes look like guessing, not a slip: say so once, on every phone."""
        if guesses.hit():
            manager.note("suspicious", reason=f"several wrong {kind} tries")
            tell_phones(Message("Security alert", "Several wrong codes or passwords were tried on this PC in a few minutes. If this wasn't you, press Pause.", kind="security"))

    @app.get("/pair/info")
    def pair_info() -> dict:
        """What the sign-in screen may know before anyone is signed in. Not the PC's name or anything else about it:
        that is shown only after a phone has paired."""
        return {"accounts": accounts is not None, "ready": accounts is None or accounts.owner_exists}

    @app.post("/pair/claim")
    def pair_claim(body: ClaimIn, request: Request) -> dict:
        """Trade a one-time pairing code (from the QR code on the PC) for this phone's own token."""
        if accounts is None:
            raise HTTPException(status_code=404, detail="pairing is off on this PC")
        ip = request.client.host if request.client else ""
        try:
            device, device_token = accounts.pair(body.code, body.name, ip, body.previous, body.install)
        except Locked as exc:
            guess_alert("pair")
            raise HTTPException(status_code=429, detail=str(exc)) from None
        except DeviceLimit as exc:
            manager.note("pair_refused_limit", ip=ip)
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except AccountError as exc:
            manager.note("pair_failed", ip=ip)
            guess_alert("pair")
            raise HTTPException(status_code=400, detail=str(exc)) from None
        manager.note("device_paired", device=device.name, device_id=device.id, ip=ip)
        if len(accounts.devices()) > 1:  # not the very first phone: tell the others, so an unknown one cannot join quietly
            tell_phones(Message("A new phone was paired", f"{device.name} can now reach this PC. If this wasn't you, remove it and press Pause.", kind="security"))
        return {"token": device_token, "device": device.public(), "pc": platform.node()}

    async def bind_viewer(request: Request) -> None:
        """Say who is asking for the rest of this request: folders a phone allowed are its own, so each phone sees only its own (and those shared with it); the Admin phone sees all."""
        device = getattr(request.state, "device", None)
        if device is None:
            set_viewer(None)
        else:
            set_viewer(device.id, bool(settings.access.enabled and access.store.role(device.id) == "admin"))

    manager.is_admin = lambda device_id: bool(settings.access.enabled and access.store.role(device_id) == "admin")
    api = APIRouter(prefix="/api", dependencies=[Depends(require_token), Depends(bind_viewer)])
    seen_requests: dict[str, tuple[str, str]] = {}  # (phone, client_id) -> (the job it became, its prompt); the newest 500, kept in memory
    seen_lock = threading.Lock()

    def phone_id(request: Request) -> str:
        device = getattr(request.state, "device", None)
        return device.id if device is not None else ""

    def own_job(request: Request, job_id: str) -> None:
        """Every phone has its own requests: another phone's request is "no such job" to this one."""
        who = phone_id(request)
        if who:
            try:
                if manager.get(job_id).owner != who:
                    raise HTTPException(status_code=404, detail="no such job")
            except JobNotFound:
                raise HTTPException(status_code=404, detail="no such job") from None

    def job_or_error(action, job_id: str, request: Request | None = None) -> dict:
        if request is not None:
            own_job(request, job_id)
        try:
            return action(job_id).to_dict()
        except JobNotFound:
            raise HTTPException(status_code=404, detail="no such job") from None
        except JobStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @api.post("/jobs")
    def create_job(body: PromptIn, request: Request) -> dict:
        if body.conversation is not None and not _CONVERSATION.match(body.conversation):
            raise HTTPException(status_code=422, detail="conversation must be letters, digits, - or _")
        if body.client_id:
            device = getattr(request.state, "device", None)
            key = f"{device.id if device is not None else ''}:{body.client_id}"  # one phone's ids never touch another phone's requests
            with seen_lock:
                known = seen_requests.get(key)
                if known is not None and known[1] == body.prompt:  # the same request again: hand back the first one instead of starting a second
                    try:
                        return manager.get(known[0]).to_dict()
                    except JobNotFound:
                        pass
                job = manager.submit(body.prompt, body.conversation, body.file, phone_id(request))
                seen_requests[key] = (job.id, body.prompt)
                while len(seen_requests) > 500:
                    seen_requests.pop(next(iter(seen_requests)))
                return job.to_dict()
        return manager.submit(body.prompt, body.conversation, body.file, phone_id(request)).to_dict()

    @api.get("/jobs")
    def list_jobs(request: Request) -> list[dict]:
        who = phone_id(request)
        return [j.to_dict() for j in manager.list() if not who or j.owner == who]

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str, request: Request) -> dict:
        return job_or_error(manager.get, job_id, request)

    @api.post("/jobs/{job_id}/approve")
    def approve(job_id: str, request: Request, body: ApproveIn | None = None) -> dict:
        own_job(request, job_id)
        device = getattr(request.state, "device", None)
        if device is not None and settings.access.enabled:
            try:  # what the plan does decides who may approve it: a view-only phone can read it, never approve a change
                access.gate.check_plan(manager.get(job_id).to_dict()["steps"], device.id)
            except AccessDenied as exc:
                raise deny(exc) from None
            except JobNotFound:
                raise HTTPException(status_code=404, detail="no such job") from None
        try:
            return manager.approve(job_id, body.pin if body else None).to_dict()
        except UnlockError as exc:  # a missing or wrong unlock PIN: the request stays waiting, and the card says why
            raise HTTPException(status_code=422 if exc.code == "pin_required" else 403, detail={"code": exc.code, "message": str(exc), **exc.extra}) from None
        except JobNotFound:
            raise HTTPException(status_code=404, detail="no such job") from None
        except JobStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @api.post("/jobs/{job_id}/reject")
    def reject(job_id: str, request: Request) -> dict:
        return job_or_error(manager.reject, job_id, request)

    @api.post("/jobs/{job_id}/undo")
    def undo(job_id: str, request: Request) -> dict:
        return job_or_error(manager.undo, job_id, request)

    @api.post("/jobs/{job_id}/access")
    def decide_folder_access(job_id: str, body: AccessIn, request: Request) -> dict:
        """Your answer to "may I open this folder?"."""
        return job_or_error(lambda jid: manager.decide_access(jid, body.decision), job_id, request)

    @api.post("/access/revoke")
    def revoke(body: PathIn) -> dict:
        """A phone takes back a folder it allowed (only its own: one shared with it is not its to remove)."""
        return {"granted": manager.revoke_access(body.path), "granted_details": manager.granted_details()}

    @api.post("/folders/revoke")
    def folders_revoke(body: RevokeIn, request: Request) -> dict:
        """The Admin phone takes a folder back from everyone, or from one phone (`owner`)."""
        need_admin(request)
        manager.revoke_access(body.path, body.owner)
        return {"admin": True, **folders_overview(manager, accounts)}

    @api.get("/folders")
    def folders_list(request: Request) -> dict:
        """The folders this phone can use. For the Admin phone: every phone's, with who owns each and who it is shared with, and the phones to share with."""
        device = getattr(request.state, "device", None)
        admin = bool(device is not None and settings.access.enabled and access.store.role(device.id) == "admin")
        if admin:
            return {"admin": True, **folders_overview(manager, accounts)}
        return {"admin": False, "folders": manager.granted_details(), "phones": []}

    @api.post("/folders/share")
    def folders_share(body: ShareIn, request: Request) -> dict:
        """The Admin chooses which other phones may use a phone's folder, and whether they may also change files there."""
        need_admin(request)
        real = str(Path(body.path).resolve())
        owners = [body.owner] if body.owner is not None else [e["owner"] for e in manager.folder_entries() if e["path"] == real]
        if not owners:
            raise HTTPException(status_code=404, detail="That folder is not on the list any more.")
        try:
            chosen = {o: parse_targets(body.targets, accounts, o) for o in owners}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if not all([manager.share_folder(body.path, o, chosen[o]) for o in owners]):
            raise HTTPException(status_code=404, detail="That folder is not on the list any more.")
        return {"admin": True, **folders_overview(manager, accounts)}

    @api.get("/overview")
    def requests_list(request: Request) -> dict:
        """Who asked what, and when, across every phone: read-only, for the Admin phone. The conversations themselves stay private to each phone."""
        need_admin(request)
        return {"requests": requests_overview(manager, accounts)}

    @api.post("/jobs/{job_id}/stop")
    def stop(job_id: str, request: Request) -> dict:
        return job_or_error(manager.stop, job_id, request)

    @api.get("/audit")
    def audit(request: Request) -> list[dict]:
        """This phone's own record: its requests, and what happened to it. Not another phone's. (The Admin phone manages access, so it sees the whole record.)"""
        who = phone_id(request)
        events = manager.audit_events()
        if not who or (settings.access.enabled and access.store.role(who) == "admin"):
            return events
        mine = {j.id for j in manager.list() if j.owner == who}
        return [e for e in events if e.get("job") in mine or e.get("device_id") == who]

    @api.post("/pause")
    def pause_access(request: Request) -> dict:
        """The panic button. Every phone, this one included, is shut out until the owner taps Resume on the PC itself (never from a phone).
        Anything that is running is stopped; nothing is deleted or undone."""
        device = getattr(request.state, "device", None)
        by = device.name if device is not None else "a phone"
        manager.pause_switch.pause(by)
        manager.note("paused", by=by)
        access.sessions.end_all("paused")
        screen.stop_all("paused")
        for job in manager.list():
            if job.status in (Status.PLANNING, Status.RUNNING):
                try:
                    manager.stop(job.id)
                except (JobNotFound, JobStateError):
                    pass  # it finished a moment ago
        tell_phones(Message("Phone access paused", f"{by} paused all phone access to this PC. Resume it on the PC.", kind="security"))
        return {"ok": True}

    @api.get("/security")
    def security() -> dict:
        """For the "Your data and control" screen: who is paired, and the recent things worth a second look (newest first)."""
        alerts = []
        for event in reversed(manager.audit_events(1500)):
            item = security_line(event)
            if item:
                alerts.append(item)
            if len(alerts) >= 20:
                break
        return {"devices": len(accounts.devices()) if accounts is not None else 0, "alerts": alerts}

    @api.post("/device/rename")
    def device_rename(body: DeviceNameIn, request: Request) -> dict:
        if accounts is None:
            raise HTTPException(status_code=404, detail="no accounts")
        device = accounts.rename(request.state.device.id, body.name)
        manager.note("device_renamed", device=device.name, device_id=device.id)
        return device.public()

    @api.delete("/device")
    def device_sign_out(request: Request) -> dict:
        """This phone signs itself out: its token stops working for good."""
        if accounts is not None:
            device: Device = request.state.device
            accounts.sign_out(device.id)
            manager.note("device_signed_out", device=device.name, device_id=device.id)
        return {"ok": True}

    def owner_ok(body: DeviceRemoveIn, request: Request) -> bool:
        """The PC owner's password (or Windows sign-in) typed on a phone. Too many wrong tries wait, like at the PC."""
        ip = request.client.host if request.client else ""
        try:
            return accounts.windows_sign_in(body.username, body.password) if accounts.mode == "windows" else accounts.verify_owner(body.password, "owner:" + ip)
        except Locked as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None

    def owner_refusal() -> str:
        return "That user name or Windows password is not right." if accounts.mode == "windows" else "That password is not right."

    @api.post("/owner/check")
    def owner_check(body: DeviceRemoveIn, request: Request) -> dict:
        """Used to reset a forgotten app PIN: the phone proves the PC owner's password instead of signing out and losing its chats."""
        if accounts is None:
            raise HTTPException(status_code=404, detail="no accounts")
        if not owner_ok(body, request):
            manager.note("owner_check_refused", by=request.state.device.name)
            guess_alert("password")
            raise HTTPException(status_code=403, detail=owner_refusal())
        manager.note("app_pin_reset", by=request.state.device.name)
        return {"ok": True}

    @api.get("/devices")
    def devices_list(request: Request) -> dict:
        """Who is connected, so a full PC can be sorted out from the phone (removing one still needs the PC owner's password)."""
        if accounts is None:
            raise HTTPException(status_code=404, detail="no accounts")
        me = request.state.device.id
        return {"limit": settings.accounts.max_devices, "mode": accounts.mode, "owner": accounts.owner_name,
                "devices": [{**d, "current": d["id"] == me} for d in accounts.devices()]}

    @api.post("/devices/{device_id}/remove")
    def device_remove(device_id: str, body: DeviceRemoveIn, request: Request) -> dict:
        """Take another phone off this PC. Any paired phone can ask, but only the PC owner's password lets it happen."""
        if accounts is None:
            raise HTTPException(status_code=404, detail="no accounts")
        me: Device = request.state.device
        if device_id == me.id:
            raise HTTPException(status_code=400, detail="That is this phone. Use Sign out instead.")
        if not owner_ok(body, request):
            manager.note("device_remove_refused", by=me.name, device_id=device_id)
            guess_alert("password")
            raise HTTPException(status_code=403, detail=owner_refusal())
        try:
            gone = accounts.remove(device_id)
        except AccountError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        manager.note("device_removed_from_phone", device=gone.name, device_id=gone.id, by=me.name)
        return {"ok": True}

    # -- the session: who is using this PC, asking for a turn, handing it over
    def need_device(request: Request) -> Device:
        device = getattr(request.state, "device", None)
        if device is None or not settings.access.enabled:
            raise HTTPException(status_code=404, detail="sessions are not on")
        return device

    def need_admin(request: Request) -> Device:
        device = need_device(request)
        if access.store.role(device.id) != "admin":
            raise HTTPException(status_code=403, detail={"code": "admin_only", "message": "Only the Admin phone can do this."})
        return device

    def no_turn_for_viewers(device: Device) -> None:
        if access.store.level(device.id) == "viewer":
            raise HTTPException(status_code=403, detail={"code": "level", "message": "A view-only phone doesn't take a turn: it can look and read at any time.", "have": "viewer", "need": "editor"})

    @api.get("/session")
    def session_state(request: Request) -> dict:
        return access.view(need_device(request).id)

    @api.post("/session/start")
    def session_start(request: Request) -> dict:
        device = need_device(request)
        no_turn_for_viewers(device)
        try:
            access.sessions.start(device.id, device.name, access.store.level(device.id))
        except SessionHeld as held:
            raise HTTPException(status_code=409, detail={"code": "session_held", "message": f"{held.holder} is using this PC. Ask them for a turn.", "holder": held.holder}) from None
        return access.view(device.id)

    @api.post("/session/end")
    def session_end(request: Request) -> dict:
        device = need_device(request)
        access.sessions.end(device.id, "ended")
        return access.view(device.id)

    @api.post("/session/takeover")
    def session_takeover(body: TakeoverIn, request: Request) -> dict:
        device = need_device(request)
        no_turn_for_viewers(device)
        try:
            result = access.sessions.request_takeover(device.id, device.name, access.store.level(device.id), body.message)
        except SessionRefused as exc:
            raise HTTPException(status_code=429, detail={"code": "wait", "message": str(exc)}) from None
        return {"result": result, **access.view(device.id)}

    @api.post("/session/cancel")
    def session_cancel(request: Request) -> dict:
        device = need_device(request)
        access.sessions.cancel_request(device.id)
        return access.view(device.id)

    @api.post("/session/answer")
    def session_answer(body: AnswerIn, request: Request) -> dict:
        device = need_device(request)
        result = access.sessions.answer(device.id, body.decision == "end")
        return {"result": result, **access.view(device.id)}

    @api.post("/session/release")
    def session_release(request: Request) -> dict:
        device = need_admin(request)
        access.sessions.release(device.name)
        return access.view(device.id)

    @api.post("/session/take")
    def session_take(request: Request) -> dict:
        device = need_admin(request)
        access.sessions.take_over(device.id, device.name, "admin", device.name)
        return access.view(device.id)

    @api.get("/access/people")
    def access_people(request: Request) -> dict:
        me = need_admin(request)
        return {"people": [{**p, "current": p["id"] == me.id} for p in access.store.people()], "levels": [{"id": k, "name": access_levels.NAMES[k]} for k in access_levels.GRANTABLE]}

    @api.post("/access/level")
    def access_set_level(body: LevelIn, request: Request) -> dict:
        me = need_admin(request)
        if body.device_id == me.id or access.store.role(body.device_id) == "admin":
            raise HTTPException(status_code=400, detail="The Admin phone's own access can only be changed on the PC.")
        if accounts is None or accounts.get(body.device_id) is None:
            raise HTTPException(status_code=404, detail="That phone is not paired any more.")
        try:
            access.store.set_level(body.device_id, body.level)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        manager.note("level_changed", device=accounts.get(body.device_id).name, device_id=body.device_id, level=body.level, by=me.name)
        return {"ok": True}

    # -- looking at the PC's screen: view only, one viewer, only with the Owner's yes (given on this PC)
    def screen_error(exc: Exception) -> HTTPException:
        if isinstance(exc, ScreenBusy):
            return HTTPException(status_code=409, detail={"code": "screen_busy", "message": str(exc), "viewer": exc.who})
        if isinstance(exc, ScreenUnavailable):
            return HTTPException(status_code=503, detail={"code": "screen_unavailable", "message": str(exc)})
        return HTTPException(status_code=403, detail={"code": "screen_not_allowed", "message": str(exc)})

    @api.get("/screen")
    def screen_state(request: Request) -> dict:
        return screen.view(need_device(request).id)

    @api.post("/screen/request")
    def screen_request(body: ScreenAskIn, request: Request) -> dict:
        device = need_device(request)
        try:
            screen.request(device.id, device.name, body.note)
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        return screen.view(device.id)

    @api.post("/screen/cancel")
    def screen_cancel(request: Request) -> dict:
        device = need_device(request)
        screen.cancel(device.id)
        return screen.view(device.id)

    @api.post("/screen/start")
    def screen_start(request: Request) -> dict:
        device = need_device(request)
        try:
            screen.start(device.id, device.name)
        except (ScreenNotAllowed, ScreenBusy) as exc:
            raise screen_error(exc) from None
        return screen.view(device.id)

    @api.post("/screen/stop")
    def screen_stop(request: Request) -> dict:
        device = need_device(request)
        screen.stop(device.id)
        return screen.view(device.id)

    def need_control_rights(device: Device) -> None:
        """Moving the mouse or typing changes the PC, so it needs Operate level AND the phone's own turn (taking control starts the turn if the PC is free)."""
        level = access.store.level(device.id)
        if not access_levels.at_least(level, "operator"):
            raise HTTPException(status_code=403, detail={"code": "level", "message": access_levels.message_for(level, "operator"), "have": level, "need": "operator"})
        try:
            access.sessions.act(device.id, device.name, level)
        except SessionHeld as held:
            try:
                screen.set_control(device.id, False)  # the turn is someone else's now: control is handed back at once
            except ScreenNotAllowed:
                pass
            raise HTTPException(status_code=409, detail={"code": "session_held", "message": f"{held.holder} is using this PC. Ask them for a turn.", "holder": held.holder}) from None

    @api.post("/screen/control/request")
    def screen_control_request(request: Request) -> dict:
        """A phone that is looking asks the Owner (on the PC) to let it control too."""
        device = need_device(request)
        level = access.store.level(device.id)
        if not access_levels.at_least(level, "operator"):
            raise HTTPException(status_code=403, detail={"code": "level", "message": access_levels.message_for(level, "operator"), "have": level, "need": "operator"})
        try:
            screen.request_control(device.id, device.name)
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        return screen.view(device.id)

    @api.post("/screen/control")
    def screen_control(body: ControlIn, request: Request) -> dict:
        device = need_device(request)
        if body.on:
            need_control_rights(device)
        try:
            screen.set_control(device.id, body.on)
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        return screen.view(device.id)

    @api.post("/screen/input")
    def screen_input(body: InputIn, request: Request) -> dict:
        device = need_device(request)
        need_control_rights(device)
        try:
            done = screen.send_input(device.id, body.events)
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        except InputError as exc:
            raise HTTPException(status_code=422, detail={"code": "bad_input", "message": str(exc)}) from None
        return {"ok": True, "done": done}

    @app.websocket("/api/screen/ws")
    async def screen_socket(ws: WebSocket) -> None:
        """A fast lane for the mouse and keyboard. One open connection instead of a new web request for every batch, so a move does not queue behind a picture. It
        checks exactly what /api/screen/input checks (this phone's token, Operate level, its own turn, the Owner's yes) on every message. The phone falls back to
        the ordinary request by itself wherever this cannot be opened."""
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 8))
            supplied = str(hello.get("token", ""))[:300] if isinstance(hello, dict) else ""
            device = accounts.authenticate(supplied, ws.client.host if ws.client else "") if accounts is not None else None
            if device is None or not settings.access.enabled:
                await ws.close(code=1008)
                return
            await ws.send_text('{"ready":true}')
            while True:
                text = await ws.receive_text()
                try:
                    msg = json.loads(text)
                    batch = InputIn(events=msg.get("events"))
                    stamp = msg.get("ts")
                except (ValueError, TypeError, AttributeError, ValidationError):
                    await ws.send_text(json.dumps({"error": {"code": "bad_input", "message": "That was not a batch of events."}, "status": 422}))
                    continue

                def work(device=device, events=batch.events):
                    if manager.pause_switch.paused:
                        raise HTTPException(status_code=423, detail={"code": "paused", "message": "Phone access is paused."})
                    need_control_rights(device)
                    return screen.send_input(device.id, events)

                try:
                    done = await run_in_threadpool(work)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    await ws.send_text(json.dumps({"error": detail, "status": exc.status_code}))
                    continue
                except ScreenNotAllowed as exc:
                    await ws.send_text(json.dumps({"error": {"code": "screen_not_allowed", "message": str(exc)}, "status": 403}))
                    continue
                except InputError as exc:
                    await ws.send_text(json.dumps({"error": {"code": "bad_input", "message": str(exc)}, "status": 422}))
                    continue
                await ws.send_text(json.dumps({"ok": True, "done": done, "ts": stamp}))
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
            pass
        except Exception:  # noqa: BLE001 - a broken lane is not worth a crash: the phone uses the ordinary request
            try:
                await ws.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    @api.get("/screen/clipboard")
    def screen_clipboard_read(request: Request) -> dict:
        """The PC's clipboard text, for the phone that controls this PC (Operate level, and its own turn)."""
        device = need_device(request)
        need_control_rights(device)
        try:
            return {"text": screen.clipboard_get(device.id)}
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        except InputError as exc:
            raise HTTPException(status_code=422, detail={"code": "bad_input", "message": str(exc)}) from None

    @api.post("/screen/clipboard")
    def screen_clipboard_write(body: ClipIn, request: Request) -> dict:
        """Put text on the PC's clipboard (the phone then presses Ctrl+V on the PC)."""
        device = need_device(request)
        need_control_rights(device)
        try:
            return {"ok": True, "characters": screen.clipboard_set(device.id, body.text)}
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        except InputError as exc:
            raise HTTPException(status_code=422, detail={"code": "bad_input", "message": str(exc)}) from None

    @api.post("/screen/audio/stop")
    def screen_audio_stop(request: Request) -> dict:
        """The phone switched its sound off: the PC stops capturing at once."""
        screen.audio_stop(need_device(request).id)
        return {"ok": True}

    @api.get("/screen/audio")
    def screen_audio(request: Request) -> StreamingResponse:
        """What this PC is playing, for the phone that is looking: raw 16-bit mono PCM at 16,000 samples a second, for as long as it keeps listening."""
        device = need_device(request)
        if not screen.audio_available():
            raise HTTPException(status_code=503, detail={"code": "audio_unavailable", "message": "This PC can't share its sound (it is switched off, has no audio device, or the audio package is missing)."})
        try:
            stream = screen.audio_stream(device.id, request.query_params.get("rate"))
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        async def body():
            """Piece by piece, looking at the phone's connection each time: when the phone stops listening, the capture on this PC stops with it."""
            try:
                while not await request.is_disconnected():
                    chunk = await run_in_threadpool(next, stream, None)
                    if chunk is None:
                        break
                    yield chunk
            finally:
                await run_in_threadpool(stream.close)

        return StreamingResponse(body(), media_type="application/octet-stream", headers={"Cache-Control": "no-store", "X-Audio-Rate": str(stream.rate), "X-Content-Type-Options": "nosniff"})

    @api.get("/screen/frame")
    def screen_frame(request: Request, w: int = 1280, q: int = 55, pointer: int = 1, ps: float = 1.0,
                     rx: float | None = None, ry: float | None = None, rw: float | None = None, rh: float | None = None) -> Response:
        """One picture. `rx, ry, rw, rh` (fractions of the screen) ask for just the part the phone zoomed in on, cut from the full-detail picture."""
        device = need_device(request)
        region = (rx, ry, rw, rh) if None not in (rx, ry, rw, rh) else None
        try:
            data = screen.frame(device.id, w, q, bool(pointer), ps, region)
        except (ScreenNotAllowed, ScreenUnavailable) as exc:
            raise screen_error(exc) from None
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        at = screen.pointer_at()
        if at is not None:  # where the PC's own pointer is, so the phone can start its own pointer in the same place
            headers["X-Pointer"] = f"{at[0]:.4f},{at[1]:.4f}"
        if capture_still():  # nothing on the screen changed since the last picture: the phone sharpens it and rests
            headers["X-Still"] = "1"
        shape = screen.cursor_id()
        if shape:  # which shape the PC's pointer has right now, so the phone's own pointer can look the same ("none": the PC has hidden it)
            headers["X-Cursor"] = shape
        return Response(content=data, media_type="image/jpeg", headers=headers)

    @api.post("/screen/item")
    def screen_item(body: ItemIn, request: Request) -> dict:
        """Long-press on the picture of the PC's screen: which file or folder is under the finger? Only for the phone that is looking; it needs no control.
        Each answer says whether it can be downloaded now, or which folder has to be allowed first."""
        device = need_device(request)
        try:
            label, found = screen.item_at(device.id, body.x, body.y)
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        except InputError as exc:
            raise HTTPException(status_code=422, detail={"code": "bad_input", "message": str(exc)}) from None
        return {"label": label, "items": [manager.item_info(p) for p in found]}

    @api.get("/screen/cursor")
    def screen_cursor(request: Request, id: str = "") -> Response:
        """The picture of a pointer shape the PC has shown (PNG with the hot spot in X-Hotspot), so the phone's pointer can look like the PC's. Only shapes it was told about."""
        device = need_device(request)
        try:
            found = screen.cursor_picture(device.id, id)
        except ScreenNotAllowed as exc:
            raise screen_error(exc) from None
        if found is None:
            raise HTTPException(status_code=404, detail="That pointer shape is not known.")
        png, hx, hy, w, h = found
        return Response(content=png, media_type="image/png", headers={"X-Hotspot": f"{hx},{hy}", "X-Size": f"{w},{h}", "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @api.get("/ping")
    def ping() -> dict:
        """The phone's heartbeat: cheap, and it needs the phone's token, so a phone this PC removed finds out here (401).
        `v` lets a phone notice the PC was updated while it was away."""
        return {"ok": True, "v": _version()}

    unlock_seen: dict = {"at": 0.0, "value": {}}

    def unlock_summary() -> dict:
        """Whether unlocking is set up and whether the PC is locked, for the phone's Home screen. Asked of the helper at most every few seconds."""
        if time.monotonic() - unlock_seen["at"] < 4:
            return unlock_seen["value"]
        info = manager.unlocker.status()
        unlock_seen.update(at=time.monotonic(), value={k: info.get(k) for k in ("running", "configured", "enabled", "attention", "locked")})
        return unlock_seen["value"]

    @api.get("/status")
    def status(request: Request) -> dict:
        """How this PC is doing right now: battery, network speed, processor, memory, disk, and whether it is locked.
        Also who is using the PC (the phone's banner and the takeover question ride along with its regular check)."""
        device = getattr(request.state, "device", None)
        return {**sysinfo.current(), "unlock": unlock_summary(), "session": access.view(device.id) if device is not None and settings.access.enabled else None}

    @api.get("/info")
    def info(request: Request) -> dict:
        """What this PC is set up to do. Read-only: settings are changed in config.yaml, never from the phone."""
        if isinstance(planner, AnthropicPlanner):
            ai, model = "hosted", settings.llm.hosted_model
        elif isinstance(planner, OllamaPlanner):
            ai, model = "local", settings.llm.model
        elif isinstance(planner, DemoPlanner):
            ai, model = "none", ""
        else:
            ai, model = "custom", ""
        agent = settings.agent
        return {
            "app": "AccessFromPhone",
            "version": _version(),
            "pc": platform.node(),
            "user": _user(),
            "accounts": accounts is not None,
            "device": request.state.device.public() if accounts is not None else None,
            "owner": accounts.owner_name if accounts is not None else "",
            "ai": {"kind": ai, "model": model, "note": note},
            "commands_enabled": settings.commands.enabled,
            "command_timeout_seconds": settings.commands.timeout_seconds,
            "folders": {
                "writable": [str(f) for f in agent.allowed_folders],
                "read_only": [str(f) for f in agent.read_only_folders],
                "granted": manager.granted_folders(),
                "granted_details": manager.granted_details(),
            },
            "max_edit_kb": agent.max_edit_kb,
            "max_view_kb": agent.max_view_kb,
            "allow_edit_grants": agent.allow_edit_grants,
            "ask_for_access": agent.ask_for_access,
            "approval": {
                "required_for": list(settings.approval.require_for),
                "timeout_minutes": settings.approval.timeout_minutes,
            },
            "max_download_mb": agent.max_download_mb,
            "power_actions": list(settings.power.remote_actions), "screen_dark": blackout.active,  # what the Home screen may offer (lock, sleep, ...)
            "daily": daily.features(),
            "memory_turns": settings.llm.memory_turns,
            "https": settings.server.https,
            "links": {"privacy": settings.links.privacy_url, "support": settings.links.support_url},
        }

    @api.get("/file")
    def open_for_editing(path: str) -> dict:
        """A file's text and whether it can be changed, for the phone's editor."""
        try:
            return manager.open_file(path)
        except OutsideAllowed as exc:
            raise HTTPException(status_code=403, detail={"code": "needs_access", "folder": _folder_of(exc.path), "message": str(exc)}) from None
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @api.get("/file/raw")
    def picture_or_pdf(path: str):
        """A picture or PDF for the viewer. Reading only, and only these types."""
        try:
            real, mime = manager.raw_file(path)
        except OutsideAllowed as exc:
            raise HTTPException(status_code=403, detail={"code": "needs_access", "folder": _folder_of(exc.path), "message": str(exc)}) from None
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except FileTooBig as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from None
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return FileResponse(real, media_type=mime, headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    def _browse_errors(exc: Exception) -> HTTPException:
        if isinstance(exc, OutsideAllowed):
            return HTTPException(status_code=403, detail={"code": "needs_access", "folder": _folder_of(exc.path), "message": str(exc)})
        if isinstance(exc, PathDenied):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, FileTooBig):
            return HTTPException(status_code=413, detail=str(exc))
        if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, PermissionError):
            return HTTPException(status_code=403, detail="Windows doesn't let this app open that folder.")
        raise exc

    def _label_places(data: dict, request: Request) -> None:
        """The list of places: on the Admin phone, who allowed each folder and who it is shared with (the folder can be shared from here);
        on another phone, which ones were shared with it."""
        device = getattr(request.state, "device", None)
        if device is not None and settings.access.enabled and access.store.role(device.id) == "admin":
            by_path: dict[str, dict] = {}
            for f in folders_overview(manager, accounts)["folders"]:
                row = by_path.setdefault(f["path"], {"owners": [], "shared": {}})
                row["owners"].append(f["owner_name"])
                for s in f["shared"]:
                    row["shared"][s["id"]] = s["name"]
            for place in data["entries"]:
                row = by_path.get(place["path"])
                if row:
                    place.update(shareable=True, owners=row["owners"], shared_with=sorted(row["shared"].values()))
        else:
            mine = {f["path"]: f for f in manager.granted_details()}
            for place in data["entries"]:
                row = mine.get(place["path"])
                if row and row["shared"] and not row["mine"]:
                    place["access"] = "shared_change" if row["change"] else "shared"

    @api.get("/browse")
    def browse_folder(request: Request, path: str = "", q: str = "", sort: str = "name", order: str = "asc", hidden: bool = False, offset: int = 0, limit: int = 200) -> dict:
        """One folder's contents for the file browser; with no path, the places it may start from."""
        try:
            data = manager.browse(path, q=q[:100], sort=sort, order="desc" if order == "desc" else "asc", hidden=hidden, offset=offset, limit=limit)
            if data.get("root"):
                _label_places(data, request)
            return data
        except (PathDenied, FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            raise _browse_errors(exc) from None

    @api.post("/browse/stream")
    def browse_stream(path: str) -> dict:
        """A short-lived link for playing a video or audio file."""
        try:
            return manager.stream_link(path)
        except (PathDenied, FileNotFoundError, PermissionError) as exc:
            raise _browse_errors(exc) from None

    @api.post("/browse/offer")
    def browse_offer(path: str) -> dict:
        """Tapping Download in the browser makes that one file fetchable through /api/files."""
        try:
            return manager.offer_file(path)
        except (PathDenied, FileTooBig, FileNotFoundError, PermissionError) as exc:
            raise _browse_errors(exc) from None

    @api.post("/browse/offer_folder")
    def browse_offer_folder(path: str) -> dict:
        """Download a whole folder: it is packed into one zip, and that zip is what the phone then fetches."""
        try:
            return manager.offer_folder(path)
        except (PathDenied, FileTooBig, FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            raise _browse_errors(exc) from None

    @app.get("/dl/{key}")
    def download_stream(key: str):
        """The file behind a download link, as an attachment. Ranges work, so the phone can fetch a huge file piece by piece."""
        if manager.pause_switch.paused:
            raise HTTPException(status_code=423, detail="paused")
        try:
            real = manager.downloadable(key)
        except PathDenied:
            raise HTTPException(status_code=404, detail="This link has run out.") from None
        return FileResponse(real, filename=real.name, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.get("/stream/{key}")
    def stream(key: str):
        """Video or audio for the player. No header can be sent from a player, so the unguessable link is the key. Ranges work, so it seeks."""
        if manager.pause_switch.paused:
            raise HTTPException(status_code=423, detail="paused")
        try:
            real, mime = manager.stream_file(key)
        except (PathDenied, FileNotFoundError):
            raise HTTPException(status_code=404, detail="This link has run out.") from None
        return FileResponse(real, media_type=mime, headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @api.get("/images")
    def pictures_in_folder(path: str = "") -> dict:
        """The pictures directly inside a folder, newest first, for the gallery."""
        try:
            return manager.list_images(path)
        except OutsideAllowed as exc:
            raise HTTPException(status_code=403, detail={"code": "needs_access", "folder": _folder_of(exc.path), "message": str(exc)}) from None
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @api.get("/thumb")
    def picture_thumbnail(path: str, size: int = 256) -> Response:
        """A small copy of one picture, for the gallery. Made once, then kept."""
        try:
            data, mime = manager.thumb(path, size)
        except OutsideAllowed as exc:
            raise HTTPException(status_code=403, detail={"code": "needs_access", "folder": _folder_of(exc.path), "message": str(exc)}) from None
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except FileTooBig as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from None
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except Exception as exc:  # a damaged picture, or no Pillow: the gallery shows a plain tile instead
            raise HTTPException(status_code=415, detail="No thumbnail for this picture.") from exc
        return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @api.put("/image")
    async def save_picture(request: Request, path: str, base_sha: str | None = None, unique: bool = False, conversation: str | None = None, note: str = "") -> dict:
        """A picture the user edited on the phone, sent as the file's own bytes. Their tap on Save is the approval; it can be undone."""
        if conversation is not None and not _CONVERSATION.match(conversation):
            raise HTTPException(status_code=422, detail="conversation must be letters, digits, - or _")
        limit = settings.agent.max_image_mb * 1024 * 1024
        if int(request.headers.get("content-length") or 0) > limit + 4096:
            raise HTTPException(status_code=413, detail=f"That picture is over the {settings.agent.max_image_mb} MB the editor saves.")
        data = await request.body()
        try:
            return await run_in_threadpool(manager.save_image, path, data, base_sha, conversation, unique, note, phone_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except FileChanged as exc:
            raise HTTPException(status_code=409, detail={"code": "changed", "message": str(exc)}) from None
        except (ReadOnlyPath, OutsideAllowed) as exc:
            folder = _folder_of(exc.path) if isinstance(exc, OutsideAllowed) else str(Path(path).resolve().parent)
            raise HTTPException(status_code=403, detail={"code": "needs_change_access", "folder": folder, "message": str(exc)}) from None
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @api.put("/file")
    def save_from_editor(body: FileIn, request: Request) -> dict:
        """The user's own edits. Their tap on Save is the approval."""
        if body.conversation is not None and not _CONVERSATION.match(body.conversation):
            raise HTTPException(status_code=422, detail="conversation must be letters, digits, - or _")
        try:
            return manager.save_file(body.path, body.text, body.base_sha, body.conversation, phone_id(request))
        except FileChanged as exc:
            raise HTTPException(status_code=409, detail={"code": "changed", "message": str(exc)}) from None
        except (ReadOnlyPath, OutsideAllowed) as exc:
            folder = _folder_of(exc.path) if isinstance(exc, OutsideAllowed) else str(Path(body.path).resolve().parent)
            raise HTTPException(status_code=403, detail={"code": "needs_change_access", "folder": folder, "message": str(exc)}) from None
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except FileTooBig as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from None

    @api.post("/access/grant")
    def grant(body: GrantIn) -> dict:
        """Allow a folder from the editor: reading it, and with `change`, editing files in it. Kept until removed."""
        try:
            return {"granted": manager.grant_folder(body.path, body.change)}
        except PathDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    @api.post("/files/link")
    def download_link(path: str) -> dict:
        """A streaming link for an offered file, so a file of any size reaches the phone without being held in memory."""
        try:
            return manager.download_link(path)
        except PathDenied:
            raise HTTPException(status_code=403, detail="that file is not available for download") from None
        except FileTooBig as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="no such file") from None

    @api.get("/files")
    def download(path: str):
        try:
            real = manager.file_for_download(path)
        except PathDenied:
            raise HTTPException(status_code=403, detail="that file is not available for download") from None
        except FileTooBig as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from None
        if not real.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        return FileResponse(real, filename=real.name)

    # -- notifications ---------------------------------------------------------
    # Push belongs to a paired phone, so it needs accounts: with them off (testing only) there is nobody to address.

    def push_device(request: Request) -> Device:
        device = getattr(request.state, "device", None)
        if device is None or not notifier.enabled:
            raise HTTPException(status_code=404, detail="notifications are not set up on this PC")
        return device

    titles_seen: dict[str, str | None] = {}  # request -> its name (newest 200): naming the same request twice asks the model once

    @api.post("/title")
    async def chat_title(body: TitleIn) -> dict:
        """A name for a new chat, from this PC's own AI model. Only a LOCAL model is asked: a hosted one would be sent requests that never
        otherwise leave this PC. {"title": null} means "use the one made from the words", which the phone already has."""
        if not isinstance(planner, OllamaPlanner):
            return {"title": None}
        key = " ".join(body.prompt.split())[:300]
        if key not in titles_seen:
            titles_seen[key] = await run_in_threadpool(planner.title, body.prompt)
            while len(titles_seen) > 200:
                titles_seen.pop(next(iter(titles_seen)))
        return {"title": titles_seen[key]}

    @api.get("/push")
    def push_info(request: Request) -> dict:
        device = getattr(request.state, "device", None)
        on = notifier.enabled and device is not None
        why = notifier.why_off() or ("this phone is not signed in to the PC" if device is None else "")
        return {"enabled": on, "why": why, "web": notifier.web.public_key if on and notifier.web else None, "fcm": bool(on and notifier.fcm),
                "subscribed": notifier.kinds(device.id) if on else []}

    @api.post("/push/subscribe")
    def push_subscribe(body: PushSubIn, request: Request) -> dict:
        device = push_device(request)
        try:
            if body.kind == "web":
                notifier.subscribe_web(device.id, body.endpoint, body.keys.get("p256dh", ""), body.keys.get("auth", ""))
            else:
                notifier.subscribe_fcm(device.id, body.token)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        manager.note("push_on", device=device.name, kind=body.kind)
        return {"ok": True, "subscribed": notifier.kinds(device.id)}

    @api.post("/push/unsubscribe")
    def push_unsubscribe(body: PushOffIn, request: Request) -> dict:
        device = push_device(request)
        notifier.unsubscribe(device.id, body.kind)
        manager.note("push_off", device=device.name, kind=body.kind or "all")
        return {"ok": True, "subscribed": notifier.kinds(device.id)}

    @api.get("/push/prefs")
    def push_prefs_get(request: Request) -> dict:
        return notifier.prefs(push_device(request).id)

    @api.post("/push/prefs")
    def push_prefs_set(body: PrefsIn, request: Request) -> dict:
        return notifier.set_prefs(push_device(request).id, body.model_dump(exclude_none=True))

    @api.post("/push/test")
    def push_test(request: Request) -> dict:
        device = push_device(request)
        return notifier.send(Message("Notifications are on", "This is a test from your PC.", "", "test"), device.id).public()

    daily_routes.mount(api, daily=daily, settings=settings, manager=manager, access=access, screen=screen, screen_dark=lambda: blackout.active)
    app.include_router(api)

    @app.get("/sw.js")
    def service_worker() -> Response:
        """The service worker (it shows notifications and keeps the page's shell for offline). It must be served from the root to cover the whole page."""
        script = (_WEB / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", _version())
        return Response(script, media_type="text/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/healthz")
    def health() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    app.mount("/static", _Assets(directory=_WEB), name="static")
    return app
