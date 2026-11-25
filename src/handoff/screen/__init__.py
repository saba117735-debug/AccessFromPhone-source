"""Looking at this PC's screen from a phone (view only, one viewer at a time, only with the Owner's yes, always visible on the PC).

  capture.py   a picture of the screen (Pillow)
  manager.py   who may look, who is looking, and the rules that end it (pure logic, tested with a fake clock and a fake screen)
  banner.py    the small always-on-top window on this PC: "Ben's phone wants to see your screen [Allow] [Deny]" and "Ben's phone is viewing [Stop]"
"""
from __future__ import annotations

from .capture import ScreenUnavailable
from .inject import InputError, Injector
from .manager import NullBanner, ScreenBusy, ScreenManager, ScreenNotAllowed, Settings

__all__ = ["InputError", "Injector", "NullBanner", "ScreenBusy", "ScreenManager", "ScreenNotAllowed", "ScreenUnavailable", "Settings"]
