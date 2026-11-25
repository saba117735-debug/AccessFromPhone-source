"""Quick controls: volume, media keys, screen brightness and opening a usual folder on this PC.

Volume and media go in as the same keys a keyboard's media row sends, so every player and Windows itself react. Windows moves the
volume 2% a press, so a "step" here is five presses (10%).
"""
from __future__ import annotations

import os
import sys
from typing import Callable

from . import places

VOLUME_STEP_PRESSES = 5
MAX_STEPS = 10
BRIGHTNESS_STEP = 10
BRIGHTNESS_FLOOR = 5  # never so low that the screen cannot be read to fix it

KEYS = {
    "volume_up": (0xAF, "Volume up."), "volume_down": (0xAE, "Volume down."), "mute": (0xAD, "Mute switched."),
    "play_pause": (0xB3, "Play or pause sent."), "next_track": (0xB0, "Next track."), "previous_track": (0xB1, "Previous track."),
}
BRIGHTNESS = ("brightness_up", "brightness_down")
ACTIONS = tuple(KEYS) + BRIGHTNESS
LABELS = {"volume_up": "Volume up", "volume_down": "Volume down", "mute": "Mute", "play_pause": "Play / pause", "next_track": "Next", "previous_track": "Previous",
          "brightness_up": "Brighter", "brightness_down": "Dimmer"}


class ControlError(Exception):
    """Something the phone asked for cannot be done on this PC; the message says why."""


def supported() -> bool:
    return sys.platform == "win32"


def press_key(vk: int, times: int = 1) -> None:
    import ctypes
    import time

    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, 1, 0)          # key down (extended key)
        user32.keybd_event(vk, 0, 1 | 2, 0)      # key up
        time.sleep(0.01)


def _brightness_now(shell: Callable[[str], str]) -> int | None:
    raw = shell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | Select-Object -First 1).CurrentBrightness")
    try:
        return int(raw)
    except ValueError:
        return None


def brightness_state(shell: Callable[[str], str] | None = None) -> dict:
    """The laptop screen's brightness for a slider: {available, level}. A desktop monitor has no software control, so it says so."""
    if shell is None:
        if not supported():
            return {"available": False, "level": 0}
        from ..screen.blackout import _powershell as shell  # noqa: PLC0415
    level = _brightness_now(shell)
    return {"available": level is not None, "level": level or 0}


def set_brightness(level: int, *, shell: Callable[[str], str] | None = None, screen_dark: bool = False) -> dict:
    """Set the laptop screen's brightness (never below the floor, so the screen can always be read to put it back)."""
    if screen_dark:
        raise ControlError("The screen is blacked out. Turn it back on first, then change the brightness.")
    if shell is None:
        if not supported():
            raise ControlError("Quick controls work on Windows only.")
        from ..screen.blackout import _powershell as shell  # noqa: PLC0415
    if _brightness_now(shell) is None:
        raise ControlError("This screen's brightness can't be changed from software (a desktop monitor has its own buttons).")
    level = max(BRIGHTNESS_FLOOR, min(100, int(level)))
    shell("Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Select-Object -First 1) "
          f"-MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness=[byte]{level}}} | Out-Null")
    return {"available": True, "level": level}


def perform(action: str, steps: int = 1, *, press: Callable[[int, int], None] | None = None, shell: Callable[[str], str] | None = None,
            screen_dark: bool = False) -> str:
    """Do one quick control and say what happened. `steps` is how many 10% steps (volume, brightness) or presses (media keys)."""
    if action not in ACTIONS:
        raise ControlError("That control is not known.")
    if not supported() and press is None and shell is None:
        raise ControlError("Quick controls work on Windows only.")
    steps = max(1, min(MAX_STEPS, int(steps)))
    if action in KEYS:
        vk, said = KEYS[action]
        presses = steps * VOLUME_STEP_PRESSES if action in ("volume_up", "volume_down") else 1
        (press or press_key)(vk, presses)
        return said
    if screen_dark:
        raise ControlError("The screen is blacked out. Turn it back on first, then change the brightness.")
    if shell is None:
        from ..screen.blackout import _powershell as shell  # noqa: PLC0415 - the one PowerShell helper the app already has
    now = _brightness_now(shell)
    if now is None:
        raise ControlError("This screen's brightness can't be changed from software (a desktop monitor has its own buttons).")
    delta = BRIGHTNESS_STEP * steps * (1 if action == "brightness_up" else -1)
    level = max(BRIGHTNESS_FLOOR, min(100, now + delta))
    shell("Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Select-Object -First 1) "
          f"-MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness=[byte]{level}}} | Out-Null")
    return f"Brightness is {level}%."


def open_place(key: str, opener: Callable[[str], None] | None = None) -> str:
    """Open one of the usual folders in File Explorer on this PC."""
    if key not in places.NAMES:
        raise ControlError("That folder is not one of the usual ones.")
    folder = places.known_folder(key)
    if folder is None:
        raise ControlError(f"This PC has no {places.TITLES[key]} folder.")
    if opener is None:
        if not supported():
            raise ControlError("Opening a folder on the PC works on Windows only.")
        opener = os.startfile  # type: ignore[attr-defined]
    opener(str(folder))
    return f"{places.TITLES[key]} is open on the PC."
