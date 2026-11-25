"""Who may look at this PC's screen, and who is looking right now.

The rules (the PC enforces all of them; a phone only asks):
* Nobody sees the screen without the Owner's yes. A phone asks ("knocks"); the Owner answers on this PC (its page, or the little window on the
  desktop) with a time limit, or pre-approves a phone on the PC page. An unanswered request is dropped after `ask` seconds.
* One viewer at a time. The viewing is visible on the PC the whole time, with a Stop button.
* It ends by itself when the phone stops asking for pictures (`idle` seconds), when the permission runs out, on Pause, or when the Owner stops it.
Time and the capture function are passed in, so every rule is tested without waiting and without a real screen.
"""
from __future__ import annotations

import inspect
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..db import Database
from . import audio as audio_mod
from . import capture
from . import clipboard as clipboard_mod
from . import itemat
from .inject import InputError, Injector, clean_events


class ScreenNotAllowed(Exception):
    """This phone has no permission (yet) to see the screen."""


class ScreenBusy(Exception):
    """Somebody else is already looking."""

    def __init__(self, who: str):
        super().__init__(f"{who} is already viewing this PC's screen.")
        self.who = who


@dataclass
class Settings:
    enabled: bool = True
    control: bool = True  # false: no phone can ever move the mouse or type on this PC, whatever it is allowed
    idle: float = 20.0   # no picture asked for this long: the viewing ends
    quiet: float = 6.0   # no picture asked for this long: the PC says the phone isn't responding (and how long is left)
    ask: float = 120.0   # an unanswered request is dropped after this long
    release_after: float = 5.0   # a button or key still held with no word from the phone this long is let go
    audio: bool = True       # a phone that looks may also listen
    clipboard: bool = True   # a phone that controls may paste text and read the clipboard
    panic_block: float = 120.0   # after the panic key, control cannot be taken again for this long


@dataclass
class _Request:
    device_id: str
    name: str
    note: str
    at: float


@dataclass
class _Viewing:
    device_id: str
    name: str
    since: float
    last: float
    controlling: bool = False
    sent: float = 0.0          # when the current input window began
    count: int = 0             # events in it
    last_input: float = 0.0    # the last time input came from the phone: a button held longer than `release_after` without any is let go
    listening: bool = False    # the phone is receiving this PC's sound
    quiet: bool = False        # the phone has gone quiet (no picture asked for a while): the PC says so, until it is back or the viewing ends


class AudioStream:
    """The PC's sound as an iterator of pieces, that also knows its sample rate."""

    def __init__(self, pieces, rate: int):
        self._pieces, self.rate = pieces, rate

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        return next(self._pieces)

    def close(self) -> None:
        self._pieces.close()


class NullBanner:
    """What shows on this PC while someone looks or asks. The real one is a small window (banner.py); tests use this empty one."""

    def request(self, name: str, note: str, decide: Callable[[bool, int], None]) -> None: ...
    def request_control(self, name: str, decide: Callable[[bool, int], None]) -> None: ...
    def active(self, name: str, stop: Callable[[], None], controlling: bool = False) -> None: ...
    def waiting(self, name: str, left: int) -> None: ...   # the phone has gone quiet; `left` seconds until the viewing ends by itself
    def clear(self) -> None: ...


class ScreenManager:
    def __init__(self, db: Database, settings: Settings | None = None, clock: Callable[[], float] = time.time,
                 grab: Callable[[int, int], bytes] = capture.grab_jpeg, on_event: Callable[..., None] | None = None, banner: NullBanner | None = None):
        self._db, self.cfg, self._now, self._grab = db, settings or Settings(), clock, grab
        self._emit = on_event or (lambda *a, **k: None)
        self.banner = banner or NullBanner()
        self.trusted: Callable[[str], bool] = lambda device_id: False  # phones that may look without a yes each time (the Admin phone, unless the Owner chose otherwise)
        self._lock = threading.RLock()
        self._pending: dict[str, _Request] = {}
        self._control_pending: dict[str, _Request] = {}  # phones that are looking and ask to take control too
        self._viewing: _Viewing | None = None
        self.injector = Injector()
        self.clipboard = clipboard_mod.default_clipboard()
        self.items = itemat.default_finder()   # which file or folder is under a point (Windows only; replaced in tests)
        self.audio: audio_mod.AudioSource = audio_mod.LoopbackAudio()
        self.panic_hint = ""        # the key combination, said on the banner and to the phone ("Ctrl+Alt+Shift+X"); set by whoever registers it
        self._panic_until = 0.0     # control cannot be taken before this time (after the panic key)
        self._audio_epoch = 0       # a newer sound stream replaces an older one
        self._real_clock = clock is time.time   # only a real clock gets a watchdog thread; a test's fake clock is driven by hand (watch_once)
        self._watch: threading.Thread | None = None
        self._closed = threading.Event()
        try:  # a database made before control existed gets the new column (a no-op on a fresh one)
            self._db.run("ALTER TABLE screen_grants ADD COLUMN control INTEGER NOT NULL DEFAULT 0")
        except Exception:  # noqa: BLE001 - "duplicate column": already there
            pass

    # ------------------------------------------------------------------ permissions (kept in the PC's database)

    def _until(self, device_id: str) -> float | None:
        row = self._db.one("SELECT until FROM screen_grants WHERE device_id=?", (device_id,))
        return None if row is None else row["until"]

    def allowed(self, device_id: str) -> bool:
        if self.trusted(device_id):
            return True
        until = self._until(device_id)
        return until is not None and (until == 0 or until > self._now())

    def grant(self, device_id: str, minutes: int, control: bool = False) -> None:
        """Allow this phone to look (and, only if `control`, to move the mouse and type). `minutes` of 0 means until the Owner takes it back."""
        until = 0.0 if minutes <= 0 else self._now() + minutes * 60
        self._db.run("INSERT INTO screen_grants (device_id, until, control) VALUES (?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET until=excluded.until, control=excluded.control",
                     (device_id, until, 1 if control else 0))
        self._pending.pop(device_id, None)

    def grant_control(self, device_id: str, minutes: int) -> None:
        """The Owner lets a phone that already looks also control, for `minutes` (0 = until taken back). It never shortens what the phone already had."""
        row = self._db.one("SELECT until FROM screen_grants WHERE device_id=?", (device_id,))
        wanted = 0.0 if minutes <= 0 else self._now() + minutes * 60
        if row is None:
            until = wanted
        elif row["until"] == 0 or wanted == 0:
            until = 0.0
        else:
            until = max(row["until"], wanted)
        self._db.run("INSERT INTO screen_grants (device_id, until, control) VALUES (?, ?, 1) ON CONFLICT(device_id) DO UPDATE SET until=excluded.until, control=1", (device_id, until))
        self._control_pending.pop(device_id, None)

    def control_allowed(self, device_id: str) -> bool:
        """May this phone take control (not only look)? The Admin phone, or a phone the Owner allowed to control. Never when control is switched off."""
        if not self.cfg.control or not self.allowed(device_id) or self._now() < self._panic_until:
            return False
        if self.trusted(device_id):
            return True
        row = self._db.one("SELECT control FROM screen_grants WHERE device_id=?", (device_id,))
        return bool(row and row["control"])

    def revoke(self, device_id: str) -> None:
        self._db.run("DELETE FROM screen_grants WHERE device_id=?", (device_id,))
        with self._lock:
            if self._viewing is not None and self._viewing.device_id == device_id:
                self._stop("permission removed")

    # ------------------------------------------------------------------ the asking

    def _expire(self) -> None:
        now, v = self._now(), self._viewing
        if v is not None:
            if not self.allowed(v.device_id):
                self._stop("permission ran out")
            elif now - v.last > self.cfg.idle:
                self._stop("the phone stopped looking")
            elif v.controlling and now - v.last_input > self.cfg.release_after and self.injector.holding():
                self.injector.release_all()   # the phone went quiet with a button or key down (a dropped connection mid-drag): let go, so nothing stays pressed
                self._emit("screen_released", device=v.name, device_id=v.device_id)
        dropped = False
        for table in (self._pending, self._control_pending):
            for key, req in list(table.items()):
                if now - req.at > self.cfg.ask:
                    del table[key]
                    dropped = True
                    self._emit("screen_unanswered", device=req.name, device_id=req.device_id)
        if dropped and not self._pending and self._viewing is None:
            self.banner.clear()

    # ------------------------------------------------------------------ the watchdog: a phone that simply vanishes

    def watch_once(self) -> None:
        """One look at whether the phone is still there. A phone can vanish without a word (the app swiped away from the recent apps, no signal, a flat battery), and then nothing
        calls this manager any more: without this, the PC would go on saying "viewing" (and keep any button held down) until something happened to ask. It runs by itself every
        second while anyone is looking or asking. After `quiet` seconds without a picture asked for, the PC says the phone isn't responding and how long is left; after `idle`
        seconds the viewing ends by itself, everything is let go, and the PC says nothing is viewing any more. If the phone comes back first, it carries on as if nothing happened."""
        with self._lock:
            self._expire()
            v = self._viewing
            if v is None:
                return
            silent = self._now() - v.last
            quiet = min(self.cfg.quiet, self.cfg.idle * 0.4)
            if silent >= quiet:
                v.quiet = True
                waiting = getattr(self.banner, "waiting", None)
                if waiting is not None:
                    waiting(v.name, max(1, int(self.cfg.idle - silent)))
            elif v.quiet:
                v.quiet = False
                self.banner.active(v.name, lambda: self.stop_all("stopped on this PC"), v.controlling)

    def _ensure_watchdog(self) -> None:
        """Called (holding the lock) whenever someone starts looking or asking."""
        if not self._real_clock or (self._watch is not None and self._watch.is_alive()):
            return
        self._closed.clear()
        self._watch = threading.Thread(target=self._watch_loop, name="screen-watchdog", daemon=True)
        self._watch.start()

    def _watch_loop(self) -> None:
        while not self._closed.wait(1.0):
            self.watch_once()
            with self._lock:
                if self._viewing is None and not self._pending and not self._control_pending:
                    self._watch = None   # nobody to watch: it ends, and starts again with the next viewing
                    return

    def close(self) -> None:
        """Stop the watchdog (the app is shutting down)."""
        self._closed.set()

    def request(self, device_id: str, name: str, note: str = "") -> str:
        """A phone asks to look. Returns "granted" (it already may), "pending" (the Owner must answer), or "busy"."""
        with self._lock:
            self._expire()
            if not self.cfg.enabled:
                raise ScreenNotAllowed("Screen viewing is switched off on this PC.")
            if self.allowed(device_id):
                return "granted"
            if device_id not in self._pending:
                req = _Request(device_id, name, " ".join(note.split())[:120], self._now())
                self._pending[device_id] = req
                self._emit("screen_requested", device=name, device_id=device_id)
                if self._viewing is None:
                    self.banner.request(name, req.note, lambda allow, minutes, d=device_id: self.decide(d, allow, minutes))
                self._ensure_watchdog()
            return "pending"

    def cancel(self, device_id: str) -> None:
        with self._lock:
            gone = self._pending.pop(device_id, None) is not None or self._control_pending.pop(device_id, None) is not None
            if gone and not self._pending and not self._control_pending and self._viewing is None:
                self.banner.clear()

    def request_control(self, device_id: str, name: str) -> str:
        """A phone that is looking asks to control too. Returns "granted" (it already may) or "pending" (the Owner must answer)."""
        with self._lock:
            self._expire()
            v = self._viewing
            if not self.cfg.control:
                raise ScreenNotAllowed("Controlling this PC is switched off in its settings.")
            if v is None or v.device_id != device_id:
                raise ScreenNotAllowed("Start viewing the screen first.")
            if self.control_allowed(device_id):
                return "granted"
            if device_id not in self._control_pending:
                self._control_pending[device_id] = _Request(device_id, name, "", self._now())
                self._emit("screen_control_requested", device=name, device_id=device_id)
                self.banner.request_control(name, lambda allow, minutes, d=device_id: self.decide(d, allow, minutes, kind="control"))
                self._ensure_watchdog()
            return "pending"

    def pending_control(self, device_id: str) -> bool:
        with self._lock:
            self._expire()
            return device_id in self._control_pending

    def decide(self, device_id: str, allow: bool, minutes: int = 15, kind: str = "look") -> bool:
        """The Owner's answer to a waiting request (to look, or to control). False when there was nothing waiting (someone else answered, or it timed out)."""
        with self._lock:
            if kind == "control":
                req = self._control_pending.pop(device_id, None)
                if req is None:
                    return False
                if allow:
                    self.grant_control(device_id, minutes)
                self._emit("screen_control_allowed" if allow else "screen_control_denied", device=req.name, device_id=device_id, minutes=minutes if allow else 0)
                if not self._pending and not self._control_pending and self._viewing is None:
                    self.banner.clear()
                return True
            req = self._pending.pop(device_id, None)
            if req is None:
                return False
            if allow:
                self.grant(device_id, minutes)
            self._emit("screen_allowed" if allow else "screen_denied", device=req.name, device_id=device_id, minutes=minutes if allow else 0)
            if not self._pending and self._viewing is None:
                self.banner.clear()
            return True

    def pending(self) -> list[dict]:
        with self._lock:
            self._expire()
            now = self._now()
            looks = [{"device_id": r.device_id, "name": r.name, "note": r.note, "waiting": int(now - r.at), "kind": "look"} for r in self._pending.values()]
            controls = [{"device_id": r.device_id, "name": r.name, "note": "", "waiting": int(now - r.at), "kind": "control"} for r in self._control_pending.values()]
            return looks + controls

    # ------------------------------------------------------------------ the looking

    def start(self, device_id: str, name: str) -> None:
        with self._lock:
            self._expire()
            if not self.allowed(device_id):
                raise ScreenNotAllowed("You don't have permission to see this PC's screen yet. Ask for it first.")
            v = self._viewing
            if v is not None and v.device_id != device_id:
                raise ScreenBusy(v.name)
            if v is None:
                now = self._now()
                self._viewing = _Viewing(device_id, name, now, now)
                self._emit("screen_started", device=name, device_id=device_id)
                self.banner.active(name, lambda: self.stop_all("stopped on this PC"), False)
            self._ensure_watchdog()

    def stop(self, device_id: str) -> bool:
        with self._lock:
            if self._viewing is not None and self._viewing.device_id == device_id:
                self._stop("stopped by the phone")
                return True
            return False

    def stop_all(self, reason: str) -> None:
        with self._lock:
            if reason == "paused":
                self._pending.clear()  # nobody is waiting for an answer any more
            if self._viewing is not None:
                self._stop(reason)
            elif not self._pending:
                self.banner.clear()

    def _stop(self, reason: str) -> None:
        v, self._viewing = self._viewing, None
        self.injector.release_all()   # whatever happened, nothing is left held down on this PC
        if v is not None:
            self._control_pending.pop(v.device_id, None)
            self._emit("screen_stopped", device=v.name, device_id=v.device_id, reason=reason)
        if not self._pending and not self._control_pending:
            self.banner.clear()

    def set_control(self, device_id: str, on: bool) -> None:
        """Take control (mouse and keyboard) while looking, or hand it back. Looking alone is the default every time."""
        with self._lock:
            self._expire()
            v = self._viewing
            if v is None or v.device_id != device_id:
                raise ScreenNotAllowed("Start viewing the screen first.")
            if on and self._now() < self._panic_until:
                raise ScreenNotAllowed("Control was cut with the panic key on the PC. It can be asked for again in about %d seconds." % max(1, int(self._panic_until - self._now())))
            if on and not self.control_allowed(device_id):
                raise ScreenNotAllowed("This phone may look at the screen but not control it. The PC owner can allow control on the PC page.")
            if v.controlling != on:
                if not on:
                    self.injector.release_all()
                v.controlling, v.count, v.sent, v.last_input = on, 0, self._now(), self._now()
                self._emit("screen_control_started" if on else "screen_control_stopped", device=v.name, device_id=device_id)
                self.banner.active(v.name, lambda: self.stop_all("stopped on this PC"), on)  # the window on the PC says it, too

    def send_input(self, device_id: str, raw_events: object) -> int:
        """Mouse and keyboard from the phone that has taken control. Every event is checked; too many too fast are refused."""
        with self._lock:
            self._expire()
            v = self._viewing
            if v is None or v.device_id != device_id or not v.controlling:
                raise ScreenNotAllowed("You are not controlling this PC. Turn control on first.")
            if not self.control_allowed(device_id):
                v.controlling = False
                raise ScreenNotAllowed("Control is no longer allowed for this phone.")
            events = clean_events(raw_events)
            now = self._now()
            if now - v.sent > 2.0:
                v.sent, v.count = now, 0
            v.count += len(events)
            if v.count > 500:  # far more than a hand can do in two seconds
                raise InputError("Too many events at once. Slow down.")
            v.last = v.last_input = now  # controlling counts as looking: it keeps the viewing alive
        return self.injector.apply(events)  # outside the lock, so the Owner's Stop is never held up

    def panic(self) -> bool:
        """The panic key, pressed on this PC: stop whoever is looking, let go of everything, take back every phone's permission to control, and refuse control for a while.
        Looking can be allowed again from this PC; control has to be asked for and answered again. Returns whether anyone was looking."""
        with self._lock:
            self._panic_until = self._now() + self.cfg.panic_block
            self._control_pending.clear()
            self._db.run("UPDATE screen_grants SET control=0")
            was = self._viewing
            if was is not None:
                self._stop("the panic key was pressed on this PC")
            else:
                self.injector.release_all()
                if not self._pending:
                    self.banner.clear()
            self._emit("screen_panic", device=was.name if was else "", device_id=was.device_id if was else "")
            return was is not None

    def item_at(self, device_id: str, x: float, y: float) -> tuple[str | None, list[Path]]:
        """The file or folder under a point of the picture (x and y are fractions of the screen), for the phone that is looking. Only looks: it needs no control.
        Returns the name that was under the finger and the real paths that name could be (empty when it was not a file or folder in an Explorer window or on the desktop)."""
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            raise InputError("That touch was not on the screen.") from None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise InputError("That touch was not on the screen.")
        with self._lock:
            self._expire()
            v = self._viewing
            if v is None or v.device_id != device_id:
                raise ScreenNotAllowed("You are not viewing this PC's screen. Start viewing first.")
            v.last = self._now()
        size = capture.screen_size()
        if self.items is None or size is None:
            raise ScreenNotAllowed("Picking a file from the screen only works on a Windows PC.")
        try:
            spot = self.items.find(int(x * (size[0] - 1)), int(y * (size[1] - 1)))   # outside the lock: it takes a moment and must not hold up the Owner's Stop
        except itemat.FinderError as exc:
            raise InputError(str(exc)) from None
        found = itemat.matches(spot.folders, spot.label)
        self._emit("screen_item_lookup", device_id=device_id, found=len(found))
        return spot.label, found

    def clipboard_get(self, device_id: str) -> str:
        """The PC's clipboard text, for the phone that controls it."""
        self._need_control(device_id, "clipboard")
        try:
            text = self.clipboard.get_text()
        except clipboard_mod.ClipboardError as exc:
            raise InputError(str(exc)) from None
        self._emit("screen_clipboard_read", device_id=device_id, characters=len(text))
        return text[: clipboard_mod.MAX_CLIP]

    def clipboard_set(self, device_id: str, text: str) -> int:
        """Put text on the PC's clipboard (then the phone sends Ctrl+V to paste it)."""
        self._need_control(device_id, "clipboard")
        if not isinstance(text, str) or len(text) > clipboard_mod.MAX_CLIP:
            raise InputError("That text is too long (up to %d characters)." % clipboard_mod.MAX_CLIP)
        try:
            self.clipboard.set_text(text)
        except clipboard_mod.ClipboardError as exc:
            raise InputError(str(exc)) from None
        self._emit("screen_clipboard_written", device_id=device_id, characters=len(text))
        return len(text)

    def _need_control(self, device_id: str, what: str) -> None:
        with self._lock:
            self._expire()
            v = self._viewing
            if what == "clipboard" and not self.cfg.clipboard:
                raise ScreenNotAllowed("The clipboard is switched off in this PC's settings.")
            if v is None or v.device_id != device_id or not v.controlling or not self.control_allowed(device_id):
                raise ScreenNotAllowed("You are not controlling this PC. Turn control on first.")
            if self.clipboard is None:
                raise ScreenNotAllowed("This PC's clipboard can't be reached from here (Windows only).")
            v.last = v.last_input = self._now()

    def audio_available(self) -> bool:
        return bool(self.cfg.audio and self.audio is not None and self.audio.available())

    def audio_stream(self, device_id: str, rate=None):
        """The PC's sound for the phone that is looking: plain 16-bit mono PCM at the rate it asked for (48 kHz unless it chose "Light"). The stream says which rate it really has
        (`.rate`), because a source that can't choose one still sends 16 kHz. It ends when the looking ends, or when a newer stream replaces it."""
        with self._lock:
            self._expire()
            v = self._viewing
            if not self.cfg.audio:
                raise ScreenNotAllowed("Sound is switched off in this PC's settings.")
            if v is None or v.device_id != device_id:
                raise ScreenNotAllowed("You are not viewing this PC's screen. Start viewing first.")
            self._audio_epoch += 1
            mine = self._audio_epoch
            v.listening = True
            self._emit("screen_audio_started", device=v.name, device_id=device_id)

        def keep_going() -> bool:
            with self._lock:
                now = self._viewing
                return now is not None and now.device_id == device_id and self._audio_epoch == mine

        wanted = audio_mod.clean_rate(rate)
        try:
            chooses = "rate" in inspect.signature(self.audio.chunks).parameters
        except (TypeError, ValueError):
            chooses = False
        actual = wanted if chooses else audio_mod.RATE

        def run():
            try:
                yield from (self.audio.chunks(keep_going, wanted) if chooses else self.audio.chunks(keep_going))
            finally:
                with self._lock:
                    now = self._viewing
                    if now is not None and now.device_id == device_id and self._audio_epoch == mine:
                        now.listening = False

        return AudioStream(run(), actual)

    def audio_stop(self, device_id: str) -> None:
        """The phone switched its sound off: end the stream now (its capture on this PC stops with it)."""
        with self._lock:
            v = self._viewing
            if v is not None and v.device_id == device_id:
                self._audio_epoch += 1
                v.listening = False

    def cursor_id(self) -> str:
        """Which pointer shape the PC is showing: an id the phone can ask the picture of ("c<hex>"), "none" when the pointer is hidden, "" when it can't be told (the phone keeps its arrow)."""
        handle = capture.cursor_handle()
        if handle is None:
            return "none"
        if isinstance(handle, int) and capture.remember_cursor(handle):
            return "c%x" % handle
        return ""

    def cursor_picture(self, device_id: str, cursor_id: str):
        """The picture of a pointer shape the PC has shown, as (PNG bytes, hot spot x, hot spot y, width, height); only for the phone that is looking."""
        with self._lock:
            self._expire()
            v = self._viewing
            if v is None or v.device_id != device_id:
                raise ScreenNotAllowed("You are not viewing this PC's screen. Start viewing first.")
            v.last = self._now()
        try:
            handle = int(str(cursor_id).lstrip("c"), 16) if str(cursor_id).startswith("c") else None
        except ValueError:
            handle = None
        shape = capture.cursor_shape(handle) if handle is not None else None
        if shape is None:
            return None
        import io

        picture, hx, hy = shape
        out = io.BytesIO()
        picture.save(out, "PNG")
        return out.getvalue(), hx, hy, picture.width, picture.height

    def pointer_at(self) -> tuple[float, float] | None:
        """Where the PC's own pointer is, as fractions of the screen (so a phone can draw its pointer in the same place)."""
        return capture.pointer_fraction()

    def _take_picture(self, width: int, quality: int, pointer: bool, pointer_size: float = 1.0, region=None) -> bytes:
        """The grab function is replaced in tests by a plain two-argument one; it is only told about the options it knows."""
        try:
            names = inspect.signature(self._grab).parameters
        except (TypeError, ValueError):
            names = {}
        extra = {}
        if "pointer" in names:
            extra["pointer"] = pointer
        if "pointer_size" in names:
            extra["pointer_size"] = pointer_size
        if "region" in names and region is not None:
            extra["region"] = region
        return self._grab(width, quality, **extra)

    def frame(self, device_id: str, width: int = 1280, quality: int = 55, pointer: bool = True, pointer_size: float = 1.0, region=None) -> bytes:
        """One picture of the screen for the phone that is looking. Asking for it is what keeps the viewing alive."""
        with self._lock:
            self._expire()
            v = self._viewing
            if v is None or v.device_id != device_id:
                raise ScreenNotAllowed("You are not viewing this PC's screen. Start viewing first.")
            v.last = self._now()
        return self._take_picture(width, quality, pointer, pointer_size, region)  # outside the lock: capturing takes a moment and must not hold up the Owner's Stop

    # ------------------------------------------------------------------ what the phone is shown

    def view(self, device_id: str) -> dict:
        with self._lock:
            self._expire()
            v, now = self._viewing, self._now()
            until = self._until(device_id)
            if not self.cfg.enabled:
                state = "off"
            elif v is not None and v.device_id == device_id:
                state = "viewing"
            elif v is not None:
                state = "busy"
            elif self.allowed(device_id):
                state = "granted"
            elif device_id in self._pending:
                state = "pending"
            else:
                state = "none"
            trusted = bool(self.trusted(device_id))
            blocked = max(0, int(self._panic_until - now)) if self.cfg.control else 0
            control = {"allowed": self.control_allowed(device_id), "on": bool(v is not None and v.device_id == device_id and v.controlling), "pending": device_id in self._control_pending,
                       "blocked": blocked, "clipboard": bool(self.cfg.clipboard and self.clipboard is not None)}
            left = None if until in (None, 0) or until <= now or trusted else int(until - now)
            return {"state": state, "left": left, "forever": until == 0 or trusted, "trusted": trusted, "control": control, "viewer": v.name if v is not None and v.device_id != device_id else None,
                    "audio": bool(self.cfg.audio), "panic": self.panic_hint}

    def viewing(self) -> dict | None:
        with self._lock:
            self._expire()
            v = self._viewing
            return None if v is None else {"device_id": v.device_id, "name": v.name, "since": int(self._now() - v.since), "controlling": v.controlling, "listening": v.listening, "quiet": v.quiet}

    def grants(self) -> list[dict]:
        now = self._now()
        out = []
        for row in self._db.rows("SELECT device_id, until, control FROM screen_grants"):
            if row["until"] == 0 or row["until"] > now:
                out.append({"device_id": row["device_id"], "left": None if row["until"] == 0 else int(row["until"] - now), "control": bool(row["control"])})
        return out
