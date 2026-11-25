"""Lock, black out the screen, sleep, hibernate, restart or shut down this PC from the phone.

Each one is a plan the person approves. Sleep, restart and shut down end the connection, so the phone is told first and the
action runs a few seconds later. Restart and shut down are off unless config.yaml allows them.
"""
from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

DELAY_SECONDS = 4  # long enough for the phone to receive "done" before the PC goes away
GRACE_SECONDS = 15  # restart and shut down count down first, so a mistake can still be cancelled at the PC


@dataclass(frozen=True)
class PowerAction:
    key: str
    summary: str  # the plan's title
    detail: str  # on the approval card
    warning: str  # the line under the card
    done: str  # what the chat says afterwards
    ends_connection: bool


ACTIONS: dict[str, PowerAction] = {a.key: a for a in (
    PowerAction("lock", "Lock this PC", "lock the screen", "Anyone at the PC will need the Windows password to use it. Your phone keeps working.",
                "The PC is locked.", False),
    PowerAction("screen_off", "Black out this PC's screen", "cover the PC's screen with black (the PC keeps running)",
                "Only what is on the PC's own monitor goes black. The PC stays on, and your phone still sees and controls the real screen. Touching the PC's own mouse or keyboard brings it back.",
                "The PC's screen is black. The PC is still running, and you can still see and control it from here.", False),
    PowerAction("screen_on", "Turn this PC's screen back on", "take the black cover off the PC's screen",
                "The PC's own monitor shows the desktop again.", "The PC's screen is back on.", False),
    PowerAction("unlock", "Unlock this PC", "type the saved sign-in at the lock screen",
                "Your PC's saved Windows sign-in is typed at its lock screen. Type your unlock PIN to allow it.", "The PC is unlocked.", False),
    PowerAction("sleep", "Put this PC to sleep", "go to sleep in a few seconds",
                "The connection will drop. This phone can't wake it again unless the PC has Wake-on-LAN set up. Someone has to touch it.",
                "The PC is going to sleep in a few seconds. This phone can't reach it until it wakes.", True),
    PowerAction("hibernate", "Hibernate this PC", "hibernate in a few seconds",
                "The connection will drop. It needs a press of the power button to start again.", "The PC is hibernating in a few seconds.", True),
    PowerAction("restart", "Restart this PC", f"restart in {GRACE_SECONDS + DELAY_SECONDS} seconds",
                "Unsaved work on the PC is lost. The phone reconnects by itself once it is back.", f"The PC restarts in about {GRACE_SECONDS + DELAY_SECONDS} seconds.", True),
    PowerAction("shutdown", "Shut down this PC", f"shut down in {GRACE_SECONDS + DELAY_SECONDS} seconds",
                "Unsaved work on the PC is lost. It will stay off until someone presses its power button.", f"The PC shuts down in about {GRACE_SECONDS + DELAY_SECONDS} seconds.", True),
)}


def supported() -> bool:
    return os.name == "nt"


def _suspend(hibernate: bool) -> None:
    import ctypes

    ctypes.windll.powrprof.SetSuspendState(bool(hibernate), True, False)  # False = sleep (rundll32 would hibernate if that is enabled)


def _lock() -> None:
    import ctypes

    ctypes.windll.user32.LockWorkStation()


def _screen_off() -> None:
    """A black cover, not "monitor off": switching the display off makes a Modern Standby PC freeze its programs, this app included."""
    from .screen.blackout import blackout

    why = blackout.on()
    if why:
        raise RuntimeError(why)


def _screen_on() -> None:
    from .screen.blackout import blackout

    blackout.off()


def _shutdown(flag: str) -> None:
    subprocess.run(["shutdown", flag, "/t", str(GRACE_SECONDS), "/c", "AccessFromPhone: requested from your phone. Run `shutdown /a` to cancel."],
                   check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


DOERS: dict[str, Callable[[], None]] = {
    "lock": _lock,
    "screen_off": _screen_off,
    "screen_on": _screen_on,
    "sleep": lambda: _suspend(False),
    "hibernate": lambda: _suspend(True),
    "restart": lambda: _shutdown("/r"),
    "shutdown": lambda: _shutdown("/s"),
}


def schedule(key: str, delay: float = DELAY_SECONDS, doer: Callable[[], None] | None = None) -> str:
    """Do it soon, off this thread, and say what will happen. Raises if this system can't."""
    action = ACTIONS[key]
    if doer is None and not supported():
        raise RuntimeError("Power actions from the phone work on Windows only.")
    if key in ("screen_off", "screen_on"):  # at once, on this thread: if the cover cannot be made the person is told, not told "done"
        (doer or DOERS[key])()
        return action.done
    timer = threading.Timer(delay if action.ends_connection else 0.5, doer or DOERS[key])
    timer.daemon = True
    timer.start()
    return action.done
