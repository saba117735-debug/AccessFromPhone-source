"""How the app talks to the unlock helper: one short line each way over a connection that never leaves this PC."""
from __future__ import annotations

import json
import socket

from .agent import DEFAULT_PORT
from .errors import (AgentDown, Disabled, Locked, NeedsAttention, NotLocked, NotSetUp, StillLocked, UnlockError, WrongPin)

SETUP_HINT = "Run   python -m handoff.unlock.setup install   on the PC (it asks for administrator rights once)."


class AgentClient:
    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout: float = 30.0):
        self.host, self.port, self.timeout = host, port, timeout

    def _ask(self, request: dict, timeout: float | None = None) -> dict:
        try:
            with socket.create_connection((self.host, self.port), timeout=3) as conn:
                conn.settimeout(timeout or self.timeout)
                conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            answer = json.loads(data.decode("utf-8"))
        except (OSError, ValueError):
            raise AgentDown(f"The unlock helper isn't running on this PC. {SETUP_HINT}") from None
        return answer if isinstance(answer, dict) else {}

    def status(self) -> dict:
        """What the helper says, or {"configured": False, "running": False} when it isn't there."""
        try:
            answer = self._ask({"cmd": "status"}, timeout=5)
        except AgentDown:
            return {"running": False, "configured": False, "enabled": False, "kind": "", "attention": False, "locked": None}
        answer["running"] = True
        return answer

    def unlock(self, pin: str) -> str:
        """The message to show when it worked. Raises an UnlockError that says why when it did not."""
        answer = self._ask({"cmd": "unlock", "pin": pin}, timeout=self.timeout)
        if answer.get("ok"):
            return str(answer.get("message") or "The PC is unlocked.")
        raise _error(answer)

    def check_pin(self, pin: str) -> None:
        """Returns if the PIN is right; raises why not. A wrong PIN is counted, as with unlock."""
        answer = self._ask({"cmd": "check", "pin": pin}, timeout=10)
        if not answer.get("ok"):
            raise _error(answer)

    def resume(self) -> None:
        self._ask({"cmd": "resume"}, timeout=5)

    def enable(self) -> None:
        self._ask({"cmd": "enable"}, timeout=5)

    def disable(self) -> None:
        self._ask({"cmd": "disable"}, timeout=5)

    def forget(self) -> None:
        self._ask({"cmd": "forget"}, timeout=5)


def _error(answer: dict) -> UnlockError:
    code, message = answer.get("code"), str(answer.get("message") or "The PC could not be unlocked.")
    if code == "wrong_pin":
        return WrongPin(int(answer.get("tries_left", 0)))
    if code == "locked_out":
        return Locked(int(answer.get("seconds", 600)))
    for cls in (NotSetUp, Disabled, NeedsAttention, NotLocked, StillLocked):
        if code == cls.code:
            return cls(message)
    return UnlockError(message)
