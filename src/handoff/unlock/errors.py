"""Why an unlock did not happen. Every message is written to be shown to the person as it is."""
from __future__ import annotations


class UnlockError(Exception):
    """Base class. `code` is a short word the phone page can act on."""

    code = "failed"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra

    def to_dict(self) -> dict:
        return {"ok": False, "code": self.code, "message": str(self), **self.extra}


class NotSetUp(UnlockError):
    code = "not_set_up"


class PinRequired(UnlockError):
    code = "pin_required"


class Disabled(UnlockError):
    code = "disabled"


class AgentDown(UnlockError):
    code = "agent_down"


class WrongPin(UnlockError):
    code = "wrong_pin"

    def __init__(self, tries_left: int):
        s = "" if tries_left == 1 else "s"
        super().__init__(f"That unlock PIN isn't right. {tries_left} more attempt{s} before it is paused for a while.", tries_left=tries_left)


class Locked(UnlockError):
    code = "locked_out"

    def __init__(self, seconds: int):
        minutes = max(1, round(seconds / 60))
        super().__init__(f"Too many wrong PINs. Unlocking is paused for about {minutes} minute{'s' if minutes != 1 else ''}.", seconds=int(seconds))


class NeedsAttention(UnlockError):
    code = "needs_attention"


class NotLocked(UnlockError):
    code = "not_locked"


class StillLocked(UnlockError):
    code = "still_locked"
