"""The phone's side of the everyday helpers: every route is under /api/daily and passes the same checks as the rest of /api
(a paired phone, not paused, an access level that allows it, and for a change, the phone whose turn it is)."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..access import levels
from ..policy import OutsideAllowed, PathDenied
from ..push import Message
from ..screen import ScreenBusy, ScreenNotAllowed, ScreenUnavailable
from . import controls, places, printing
from .inbox import InboxError
from .recents import FavError
from .schedule import REPEATS, ScheduleError
from .watch import WatchError, list_processes


class ControlIn(BaseModel):
    action: str = Field(min_length=1, max_length=30)
    steps: int = Field(default=1, ge=1, le=10)


class PlaceIn(BaseModel):
    key: str = Field(min_length=1, max_length=20)


class TextIn(BaseModel):
    text: str = Field(default="", max_length=30_000)


class NoteIn(BaseModel):
    text: str = Field(default="", max_length=6000)
    todo: bool = False


class TaskIn(BaseModel):
    kind: Literal["remind", "power"]
    due: float  # seconds since 1970, from the phone's own clock
    repeat: str = Field(default="", max_length=10)
    action: str = Field(default="", max_length=20)
    text: str = Field(default="", max_length=300)


class WatchIn(BaseModel):
    kind: Literal["process", "folder", "quiet"]
    target: str = Field(default="", max_length=1000)
    then: str = Field(default="", max_length=20)


class DismissIn(BaseModel):
    id: int | None = None


class PathIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class VolumeIn(BaseModel):
    level: int | None = Field(default=None, ge=0, le=100)
    muted: bool | None = None


class BrightnessIn(BaseModel):
    level: int = Field(ge=0, le=100)


class PinIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    label: str = Field(default="", max_length=60)


def mount(api: APIRouter, *, daily, settings, manager, access, screen, screen_dark) -> None:
    cfg = daily.cfg

    def device_of(request: Request):
        return getattr(request.state, "device", None)

    def level_of(request: Request) -> str:
        device = device_of(request)
        if device is None or not settings.access.enabled:
            return "admin"  # one shared link or access rules switched off: every phone acts freely, as it does everywhere else
        return access.store.level(device.id)

    def need(request: Request, level: str) -> None:
        have = level_of(request)
        if not levels.at_least(have, level):
            raise HTTPException(status_code=403, detail={"code": "level", "message": levels.message_for(have, level), "have": have, "need": level})

    def need_power_level(request: Request, action: str) -> None:
        need(request, "admin" if action in levels.POWER_ADMIN_ONLY else "operator")

    def who(request: Request) -> tuple[str, str]:
        device = device_of(request)
        return (device.id, device.name) if device is not None else ("", "this phone")

    def bad(exc: Exception, status: int = 422) -> HTTPException:
        return HTTPException(status_code=status, detail={"code": "bad_input", "message": str(exc)})

    def on() -> None:
        if not cfg.enabled:
            raise HTTPException(status_code=404, detail={"code": "daily_off", "message": "The everyday helpers are switched off on this PC (daily: enabled in config.yaml)."})

    def file_error(exc: Exception) -> HTTPException:
        if isinstance(exc, OutsideAllowed):
            folder = exc.path if exc.path.is_dir() else exc.path.parent
            return HTTPException(status_code=403, detail={"code": "needs_access", "folder": str(folder), "message": str(exc)})
        if isinstance(exc, PathDenied):
            return HTTPException(status_code=403, detail=str(exc))
        if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            return HTTPException(status_code=404, detail=str(exc))
        raise exc

    # -- overview and alerts ---------------------------------------------------------------------------------------------

    @api.get("/daily/state")
    def state(request: Request) -> dict:
        on()
        return {**daily.features(), "counts": {**daily.inbox.counts(), "tasks": len(daily.scheduler.list()), "watchers": len(daily.watchers.list())},
                "alert_id": max((a["id"] for a in daily.alerts()), default=0), "level": level_of(request)}

    @api.get("/daily/alerts")
    def alerts(after: int = 0) -> dict:
        on()
        return {"alerts": daily.alerts(after)}

    @api.get("/daily/briefing")
    def briefing(request: Request) -> dict:
        on()
        return daily.briefing()

    # -- quick controls ----------------------------------------------------------------------------------------------------

    @api.get("/daily/places")
    def usual_places() -> dict:
        on()
        return {"places": places.all_places()}

    @api.post("/daily/control")
    def control(body: ControlIn, request: Request) -> dict:
        on()
        need(request, "operator")
        try:
            message = controls.perform(body.action, body.steps, screen_dark=bool(screen_dark()))
        except controls.ControlError as exc:
            raise bad(exc) from None
        manager.note("quick_control", action=body.action, by=who(request)[1])
        return {"ok": True, "message": message}

    @api.post("/daily/open-place")
    def open_place(body: PlaceIn, request: Request) -> dict:
        on()
        need(request, "operator")
        try:
            message = controls.open_place(body.key)
        except controls.ControlError as exc:
            raise bad(exc) from None
        manager.note("quick_open_place", place=body.key, by=who(request)[1])
        return {"ok": True, "message": message}

    # -- live levels, for the sliders: volume (Windows' own audio service) and a laptop screen's brightness -----------------------

    @api.get("/daily/volume")
    def volume_state() -> dict:
        on()
        try:
            return daily.volume.get()
        except controls.ControlError as exc:
            return {"available": False, "level": 0, "muted": False, "why": str(exc)}

    @api.post("/daily/volume")
    def volume_set(body: VolumeIn, request: Request) -> dict:
        on()
        need(request, "operator")
        if body.level is None and body.muted is None:
            raise bad(ValueError("Say a level or whether to mute."))
        try:
            state = daily.volume.get()
            if body.level is not None:
                state = daily.volume.set_level(body.level)
            if body.muted is not None:
                state = daily.volume.set_muted(body.muted)
        except controls.ControlError as exc:
            raise bad(exc) from None
        manager.note("quick_control", action="volume_set" if body.level is not None else "mute_set", level=state["level"], muted=state["muted"], by=who(request)[1])
        return state

    @api.get("/daily/brightness")
    async def brightness_state() -> dict:
        on()
        return await run_in_threadpool(daily.brightness_state)

    @api.post("/daily/brightness")
    async def brightness_set(body: BrightnessIn, request: Request) -> dict:
        on()
        need(request, "operator")
        try:
            state = await run_in_threadpool(daily.brightness_set, body.level, bool(screen_dark()))
        except controls.ControlError as exc:
            raise bad(exc) from None
        manager.note("quick_control", action="brightness_set", level=state["level"], by=who(request)[1])
        return state

    # -- the inbox ---------------------------------------------------------------------------------------------------------

    @api.get("/daily/inbox")
    def inbox_list() -> dict:
        on()
        return {"to_pc": daily.inbox.items(), "to_phone": daily.inbox.to_phone(), "folders": {"inbox": str(daily.inbox.to_pc_dir), "to_phone": str(daily.inbox.to_phone_dir)}}

    @api.post("/daily/inbox/text")
    def inbox_text(body: TextIn, request: Request) -> dict:
        on()
        need(request, "editor")
        try:
            item = daily.inbox.add_text(body.text, who(request)[1])
        except InboxError as exc:
            raise bad(exc) from None
        manager.note("inbox_received", kind=item["kind"], by=who(request)[1])
        return item

    @api.put("/daily/inbox/file")
    async def inbox_file(request: Request, name: str) -> dict:
        on()
        need(request, "editor")
        limit = daily.inbox.max_bytes
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail={"code": "too_big", "message": f"That file is larger than the {limit // (1024 * 1024)} MB limit."})
        upload = daily.inbox.open_upload(name[:200], who(request)[1])
        try:
            async for chunk in request.stream():
                upload.write(chunk)
            item = upload.commit()
        except InboxError as exc:
            upload.abort()
            raise HTTPException(status_code=413 if "larger" in str(exc) else 422, detail={"code": "bad_input", "message": str(exc)}) from None
        except BaseException:
            upload.abort()
            raise
        manager.note("inbox_received", kind="file", name=item["name"], size=item["size"], by=who(request)[1])
        return item

    @api.get("/daily/inbox/{item_id}/file")
    def inbox_download(item_id: str) -> FileResponse:
        on()
        try:
            path, name = daily.inbox.file_of(item_id)
        except InboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return FileResponse(path, filename=name, headers={"X-Content-Type-Options": "nosniff"})

    @api.get("/daily/inbox/{item_id}/text")
    def inbox_text_of(item_id: str) -> dict:
        on()
        try:
            return {"text": daily.inbox.text_of(item_id)}
        except InboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @api.delete("/daily/inbox/{item_id}")
    def inbox_remove(item_id: str, request: Request) -> dict:
        on()
        need(request, "editor")
        try:
            daily.inbox.remove(item_id)
        except InboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return {"ok": True}

    @api.post("/daily/inbox/{item_id}/clipboard")
    def inbox_clipboard(item_id: str, request: Request) -> dict:
        on()
        need(request, "operator")
        try:
            count = daily.inbox.copy_to_clipboard(item_id)
        except InboxError as exc:
            raise bad(exc) from None
        manager.note("inbox_to_clipboard", characters=count, by=who(request)[1])  # never the text itself
        return {"ok": True, "characters": count}

    @api.post("/daily/inbox/{item_id}/open")
    def inbox_open(item_id: str, request: Request) -> dict:
        on()
        need(request, "operator")
        try:
            url = daily.inbox.open_link(item_id)
        except InboxError as exc:
            raise bad(exc) from None
        manager.note("inbox_link_opened", host=url.split("/")[2][:80], by=who(request)[1])
        return {"ok": True}

    @api.get("/daily/tophone/file")
    def tophone_download(name: str, request: Request) -> FileResponse:
        on()
        try:
            path = daily.inbox.to_phone_file(name)
        except InboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        limit = settings.agent.max_download_mb
        if limit and path.stat().st_size > limit * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{path.name} is larger than the {limit} MB download limit")
        manager.note("tophone_download", name=path.name, by=who(request)[1])
        return FileResponse(path, filename=path.name, headers={"X-Content-Type-Options": "nosniff"})

    # -- notes ---------------------------------------------------------------------------------------------------------------

    @api.get("/daily/notes")
    def notes_list() -> dict:
        on()
        return {"notes": daily.notes.recent(), "folder": str(daily.notes.dir)}

    @api.post("/daily/notes")
    def notes_add(body: NoteIn, request: Request) -> dict:
        on()
        need(request, "editor")
        try:
            note = daily.notes.add(body.text, body.todo)
        except InboxError as exc:
            raise bad(exc) from None
        manager.note("note_saved", todo=body.todo, characters=len(note["text"]), by=who(request)[1])
        return note

    # -- reminders and scheduled actions -------------------------------------------------------------------------------------

    @api.get("/daily/tasks")
    def tasks_list() -> dict:
        on()
        return {"tasks": daily.scheduler.list(), "now": time.time(), "power": sorted(daily.allowed_power()), "repeats": [r for r in REPEATS if r]}

    @api.post("/daily/tasks")
    def tasks_add(body: TaskIn, request: Request) -> dict:
        on()
        need(request, "operator")
        if body.kind == "power":
            need_power_level(request, body.action)
        device_id, name = who(request)
        try:
            task = daily.scheduler.add(kind=body.kind, due=body.due, repeat=body.repeat, action=body.action, text=body.text, device=device_id, device_name=name)
        except ScheduleError as exc:
            raise bad(exc) from None
        manager.note("task_scheduled", kind=task["kind"], action=task["action"], when=datetime.fromtimestamp(task["due"]).isoformat(timespec="minutes"), repeat=task["repeat"], by=name)
        return task

    @api.delete("/daily/tasks/{task_id}")
    def tasks_remove(task_id: str, request: Request) -> dict:
        on()
        need(request, "operator")
        if not daily.scheduler.remove(task_id):
            raise HTTPException(status_code=404, detail="That task is already gone.")
        manager.note("task_removed", by=who(request)[1])
        return {"ok": True}

    # -- watchers ------------------------------------------------------------------------------------------------------------

    @api.get("/daily/watch")
    def watch_list() -> dict:
        on()
        return {"watchers": daily.watchers.list(), "power": sorted(daily.allowed_power()), "places": places.all_places()}

    @api.get("/daily/processes")
    def processes(request: Request) -> dict:
        on()
        need(request, "operator")
        return {"processes": list_processes()}

    @api.post("/daily/watch")
    def watch_add(body: WatchIn, request: Request) -> dict:
        on()
        need(request, "operator")
        if body.then:
            need_power_level(request, body.then)
        device_id, name = who(request)
        try:
            watcher = daily.watchers.add(kind=body.kind, target=body.target, then=body.then, device=device_id, device_name=name)
        except WatchError as exc:
            raise bad(exc) from None
        except (OutsideAllowed, PathDenied, FileNotFoundError, NotADirectoryError) as exc:
            raise file_error(exc) from None
        manager.note("watch_added", kind=watcher["kind"], then=watcher["then"], by=name)
        return watcher

    @api.delete("/daily/watch/{watch_id}")
    def watch_remove(watch_id: str, request: Request) -> dict:
        on()
        need(request, "operator")
        if not daily.watchers.remove(watch_id):
            raise HTTPException(status_code=404, detail="That watcher is already gone.")
        return {"ok": True}

    # -- the PC's own notifications ------------------------------------------------------------------------------------------

    @api.get("/daily/notifications")
    def notifications_list() -> dict:
        on()
        if not cfg.notifications:
            return {"enabled": False, "items": [], "problem": ""}
        daily.mirror.poll()
        return {"enabled": True, "available": daily.mirror.available, "problem": daily.mirror.problem, "items": daily.mirror.items()}

    @api.post("/daily/notifications/dismiss")
    def notifications_dismiss(body: DismissIn, request: Request) -> dict:
        on()
        need(request, "editor")
        if not cfg.notifications:
            raise HTTPException(status_code=404, detail="PC notifications are switched off (daily: notifications in config.yaml).")
        return {"hidden": daily.mirror.dismiss(body.id)}

    # -- recent files and pinned places ----------------------------------------------------------------------------------------

    @api.get("/daily/recent")
    def recent_files() -> dict:
        on()
        return {"files": daily.recents.recent_files()}

    @api.get("/daily/favourites")
    def favourites() -> dict:
        on()
        return {"favourites": daily.recents.favourites(), "suggestions": daily.recents.suggestions()}

    @api.post("/daily/favourites")
    def favourite_add(body: PinIn, request: Request) -> dict:
        on()
        need(request, "editor")
        try:
            return daily.recents.pin(body.path, body.label)
        except FavError as exc:
            raise bad(exc) from None

    @api.delete("/daily/favourites")
    def favourite_remove(path: str, request: Request) -> dict:
        on()
        need(request, "editor")
        return {"removed": daily.recents.unpin(path)}

    # -- printing ------------------------------------------------------------------------------------------------------------

    @api.get("/daily/print/info")
    def print_info() -> dict:
        on()
        if not cfg.printing:
            return {"enabled": False}
        printer = printing.default_printer()
        return {"enabled": True, "printer": printer["name"] if printer else "", "offline": bool(printer and printer["offline"]), "types": sorted(printing.PRINTABLE)}

    @api.post("/daily/print")
    def print_it(body: PathIn, request: Request) -> dict:
        on()
        need(request, "operator")
        if not cfg.printing:
            raise HTTPException(status_code=404, detail={"code": "daily_off", "message": "Printing from the phone is switched off (daily: printing in config.yaml)."})
        try:
            real = manager.resolve_file(body.path)
        except (OutsideAllowed, PathDenied, FileNotFoundError, NotADirectoryError) as exc:
            raise file_error(exc) from None
        try:
            printer = printing.print_file(real)
        except printing.PrintError as exc:
            raise bad(exc) from None
        manager.note("print", path=str(real), printer=printer, by=who(request)[1])
        return {"ok": True, "printer": printer, "name": real.name}

    # -- one picture of the screen, or of the webcam -----------------------------------------------------------------------------

    def keep(data: bytes, prefix: str) -> str:
        folder = Path(cfg.folder) / "Screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.jpg"
        path.write_bytes(data)
        return path.name

    @api.post("/daily/snap/screen")
    def snap_screen(request: Request) -> Response:
        """One full-size picture of the screen. It goes through the same permission as looking at the screen: the Owner's yes, on this PC."""
        on()
        need(request, "operator")
        device = device_of(request)
        if device is None or not settings.access.enabled:
            raise HTTPException(status_code=404, detail={"code": "screen_unavailable", "message": "A screenshot needs a paired phone."})
        started = False
        try:
            state = screen.view(device.id)["state"]
            if state == "granted":
                screen.start(device.id, device.name)
                started = True
            elif state != "viewing":
                raise HTTPException(status_code=403, detail={"code": "screen_not_allowed", "state": state, "message": "Ask to look at this PC's screen first. The Owner says yes on the PC."})
            data = screen.frame(device.id, 2560, 85, False)
        except (ScreenNotAllowed, ScreenBusy) as exc:
            raise HTTPException(status_code=403 if isinstance(exc, ScreenNotAllowed) else 409, detail={"code": "screen_not_allowed", "message": str(exc)}) from None
        except ScreenUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": "screen_unavailable", "message": str(exc)}) from None
        finally:
            if started:
                screen.stop(device.id)
        saved = keep(data, "screenshot")
        manager.note("snap_screen", file=saved, by=device.name)
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Saved-As": saved, "X-Content-Type-Options": "nosniff"})

    @api.post("/daily/snap/webcam")
    async def snap_webcam(request: Request) -> Response:
        """One photo from the PC's webcam. Off unless config.yaml allows it, Admin phone only, logged, and every phone is told."""
        on()
        need(request, "admin")
        if not cfg.webcam:
            raise HTTPException(status_code=403, detail={"code": "webcam_off", "message": "The webcam is switched off. To allow it, set daily: webcam: true in config.yaml on the PC."})
        name = who(request)[1]
        manager.note("snap_webcam", by=name)  # written first: even a photo that fails was asked for
        try:
            data = await run_in_threadpool(printing.webcam_photo)
        except printing.PrintError as exc:
            raise HTTPException(status_code=503, detail={"code": "webcam_unavailable", "message": str(exc)}) from None
        daily.tell(Message("Webcam photo taken", f"{name} took a photo with this PC's camera.", "", "security"))
        saved = keep(data, "webcam")
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Saved-As": saved, "X-Content-Type-Options": "nosniff"})
