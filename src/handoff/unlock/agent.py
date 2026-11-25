"""The unlock helper: a small program that runs as the system (a scheduled task started at boot) and is the only thing that can type at the lock screen.

It listens on this PC only (127.0.0.1) for the phone app's requests. It does not trust the app: it checks the unlock PIN itself, counts wrong
ones itself, and holds the saved sign-in itself. The app can ask it to unlock, to say how things stand, or to switch itself off. It can never
be asked to hand the sign-in over: nothing in this program sends the saved sign-in anywhere except to the keyboard of the lock screen.

    python -m handoff.unlock.agent            (installed and started by:  python -m handoff.unlock.setup install)
"""
from __future__ import annotations

import collections
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import lockstate
from .errors import NotLocked, StillLocked, UnlockError
from .store import UnlockStore

DEFAULT_PORT = 8769
MAX_REQUEST = 4096


def data_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AccessFromPhone"


def source_stamps(folder: Path | None = None) -> dict[str, float]:
    """When each of the helper's own files was last changed."""
    folder = folder or Path(__file__).resolve().parent
    return {p.name: p.stat().st_mtime for p in folder.glob("*.py")}


def changed_since(before: dict[str, float], now: dict[str, float]) -> list[str]:
    return sorted(name for name in set(before) | set(now) if before.get(name) != now.get(name))


def restart_when_updated(log: Callable[[str], None], interval: float = 5.0, stamps: Callable[[], dict[str, float]] = source_stamps,
                         leave: Callable[[int], None] = os._exit, sleep: Callable[[float], None] = time.sleep, rounds: int | None = None) -> None:
    """When the app's files change (an update), the helper would go on running the old code. It leaves instead, with a failure code, and the
    scheduled task starts it again a minute later (the task is set to restart a helper that dies), so an update needs nothing done by hand."""
    before, count = stamps(), 0
    while rounds is None or count < rounds:
        sleep(interval)
        count += 1
        try:
            now = stamps()
        except OSError:
            continue
        moved = changed_since(before, now)
        if moved:
            log(f"the helper's files changed ({', '.join(moved[:4])}): restarting to use them")
            leave(3)
            return


def _where(exc: BaseException) -> str:
    """The file and line an error came from (and nothing about its message, which could in theory hold something private)."""
    tb = exc.__traceback__
    while tb and tb.tb_next:
        tb = tb.tb_next
    return f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}" if tb else "?"


class Agent:
    def __init__(self, store: UnlockStore, locked: Callable[[], bool | None] = lockstate.is_locked, type_it: Callable[[dict], int] | None = None,
                 log: Callable[[str], None] = lambda line: None, wait_seconds: float = 10.0, sleep: Callable[[float], None] = time.sleep):
        self.store, self._locked, self._type = store, locked, type_it
        self.recent: collections.deque = collections.deque(maxlen=25)  # the last lines of the log, so they can be read without administrator rights

        def remember(line: str) -> None:
            self.recent.append(time.strftime("%H:%M:%S ") + line)
            log(line)

        self._log = remember
        store.notice = remember   # so the log says when it restarted itself
        self._wait, self._sleep = wait_seconds, sleep
        self._busy = threading.Lock()

    # ------------------------------------------------------------------ what the app may ask
    def handle(self, request: dict) -> dict:
        command = request.get("cmd")
        try:
            if command == "status":
                return {"ok": True, **self.store.status(), "locked": self._locked(), "log": list(self.recent)[-12:]}  # (log lines never hold a PIN or the sign-in)
            if command == "check":  # is this PIN right? (the app asks before it approves anything, so a wrong PIN leaves the request waiting)
                self.store.verify_pin(str(request.get("pin", "")))
                return {"ok": True}
            if command == "unlock":
                return self._unlock(str(request.get("pin", "")))
            if command == "resume":  # after it stopped itself: the owner has looked, and wants it to try again
                self.store.clear_attention()
                self._log("started trying again at the owner's request")
                return {"ok": True}
            if command == "enable":  # switching it back on: it still needs the unlock PIN for every unlock
                self.store.set_enabled(True)
                self._log("unlock switched on")
                return {"ok": True}
            if command == "disable":  # turning it off is always safe, so the app may ask
                self.store.set_enabled(False)
                self._log("unlock switched off")
                return {"ok": True}
            if command == "forget":
                self.store.forget()
                self._log("saved sign-in removed")
                return {"ok": True}
            return {"ok": False, "code": "bad_request", "message": "Unknown request."}
        except UnlockError as exc:
            self._log(f"{command}: refused ({exc.code})")
            return exc.to_dict()
        except Exception as exc:  # noqa: BLE001  (never take the helper down, and never put a secret in the message)
            self._log(f"{command}: failed ({type(exc).__name__}) at {_where(exc)}")
            return {"ok": False, "code": "failed", "message": "Something went wrong in the unlock helper. See its log on the PC."}

    def _unlock(self, pin: str) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "code": "busy", "message": "An unlock is already being tried."}
        try:
            self.store.verify_pin(pin)  # counts a wrong PIN, pauses after too many
            locked = self._locked()
            if locked is False:
                raise NotLocked("This PC isn't locked, so there is nothing to unlock.")
            kind, secret = self.store.reveal()
            self._log(f"typing the saved {kind} at the lock screen")
            try:
                code = self._type({"secret": secret, "kind": kind, "wake": True, "submit": True}) if self._type else 1
            except OSError as exc:  # Windows refused a step: say which (these messages are written in winsession.py and never hold the sign-in)
                self._log(f"could not start typing: {exc}")
                self.store.note_result(False)
                raise StillLocked(f"I couldn't type at the lock screen: {exc}") from None
            finally:
                secret = ""
            detail = ""
            if hasattr(code, "code"):  # the real helper reports what it said too
                detail, code = code.output, code.code
            if code != 0:
                self._log(f"the typing helper stopped with code {code}: {detail}")
                self.store.note_result(False)
                raise StillLocked(f"I couldn't type at the lock screen (the typing helper stopped with code {code}). {detail}".strip())
            deadline = time.monotonic() + self._wait
            while time.monotonic() < deadline:
                if self._locked() is False:
                    self.store.note_result(True)
                    self._log("the PC is unlocked")
                    return {"ok": True, "message": "The PC is unlocked."}
                self._sleep(0.5)
            if self._locked() is None:  # can't tell: the keys were sent
                return {"ok": True, "message": "I sent your sign-in to the lock screen."}
            self.store.note_result(False)
            self._log("typed the sign-in but the PC is still locked")
            raise StillLocked("I typed the sign-in, but the PC is still locked. The saved sign-in may be out of date, or the lock screen was in a different state.")
        finally:
            self._busy.release()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(30)
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if not line or len(line) > MAX_REQUEST:
                return
            request = json.loads(line.decode("utf-8"))
            answer = self.server.agent.handle(request if isinstance(request, dict) else {})  # type: ignore[attr-defined]
        except (ValueError, OSError):
            answer = {"ok": False, "code": "bad_request", "message": "Unreadable request."}
        try:
            self.wfile.write((json.dumps(answer) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(agent: Agent, port: int = DEFAULT_PORT) -> _Server:
    """Listens on this PC only. The caller runs `serve_forever()`; tests use port 0."""
    server = _Server(("127.0.0.1", port), _Handler)
    server.agent = agent  # type: ignore[attr-defined]
    return server


def _logger(path: Path) -> Callable[[str], None]:
    def write(line: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 200_000:
                path.replace(path.with_suffix(".old"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except OSError:
            pass
    return write


def main(argv: list[str] | None = None) -> int:
    from . import dpapi, winsession

    args = argv if argv is not None else sys.argv[1:]
    folder = Path(args[args.index("--data") + 1]) if "--data" in args else data_dir()
    port = int(args[args.index("--port") + 1]) if "--port" in args else DEFAULT_PORT
    store = UnlockStore(folder / "unlock.json", dpapi.protect, dpapi.unprotect)
    log = _logger(folder / "unlock.log")
    log(f"unlock helper starting (python {sys.version.split()[0]}, user {os.environ.get('USERNAME', '?')})")
    agent = Agent(store, type_it=winsession.type_at_lock_screen, log=log)
    server = serve(agent, port)
    log(f"unlock helper listening on 127.0.0.1:{port}")
    threading.Thread(target=restart_when_updated, args=(log,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        log(f"unlock helper stopped: {type(exc).__name__} at {_where(exc)}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
