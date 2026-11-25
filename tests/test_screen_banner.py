"""The real window on this PC's desktop. It briefly appears on screen, so it only runs when asked for:  AFP_DESKTOP_TESTS=1 python -m pytest tests/test_screen_banner.py"""
import os
import time

import pytest

from handoff.db import Database
from handoff.screen import ScreenManager, Settings
from handoff.screen.banner import DesktopBanner

pytestmark = pytest.mark.skipif(os.environ.get("AFP_DESKTOP_TESTS") != "1", reason="shows a real window: set AFP_DESKTOP_TESTS=1 to run")


def wait_for(check, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


def test_the_window_asks_the_owner_and_shows_who_is_looking_with_a_working_stop(tmp_path):
    banner = DesktopBanner()
    screen = ScreenManager(Database(tmp_path / "app.db"), Settings(), grab=lambda w, q: b"x", banner=banner)
    screen.request("a", "Asha's phone", "fixing the printer")
    assert wait_for(lambda: "wants to see your screen" in banner.title), banner.title
    banner.press("Allow 15 min")  # the Owner taps Allow on the little window
    assert wait_for(lambda: screen.allowed("a")) and wait_for(lambda: banner.title == "")
    screen.start("a", "Asha's phone")
    assert wait_for(lambda: "is viewing this PC's screen" in banner.title), banner.title
    banner.press("Stop")  # and Stop
    assert wait_for(lambda: screen.viewing() is None) and wait_for(lambda: banner.title == "")


def test_denying_from_the_window_gives_nothing(tmp_path):
    banner = DesktopBanner()
    screen = ScreenManager(Database(tmp_path / "app.db"), Settings(), banner=banner)
    screen.request("a", "Ben's phone")
    assert wait_for(lambda: "wants to see your screen" in banner.title)
    banner.press("Deny")
    assert wait_for(lambda: banner.title == "") and not screen.allowed("a") and screen.pending() == []


def test_the_window_turns_amber_when_the_phone_goes_quiet_and_back_when_it_returns(tmp_path):
    banner = DesktopBanner()
    screen = ScreenManager(Database(tmp_path / "app.db"), Settings(), grab=lambda w, q: b"x", banner=banner)
    screen.request("a", "Asha's phone")
    assert wait_for(lambda: "wants to see your screen" in banner.title)
    banner.press("Allow 15 min")
    assert wait_for(lambda: screen.allowed("a"))
    screen.start("a", "Asha's phone")
    assert wait_for(lambda: "is viewing this PC's screen" in banner.title), banner.title
    banner.waiting("Asha's phone", 12)   # the phone's app was swiped away: nothing asks for pictures
    assert wait_for(lambda: "isn't responding" in banner.title and "12 s" in banner.title), banner.title
    banner.waiting("Asha's phone", 11)
    assert wait_for(lambda: "11 s" in banner.title), banner.title
    banner.active("Asha's phone", lambda: None, False)   # it came back
    assert wait_for(lambda: "is viewing this PC's screen" in banner.title), banner.title
    banner.press("Stop")
    assert wait_for(lambda: banner.title == "")
