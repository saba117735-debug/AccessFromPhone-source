"""The black-out of the PC's screen: when real input wakes it, and (on Windows) that it hides from capture and from the phone's own input."""
import os
import sys
import time

import pytest

from handoff.screen import blackout as bo
from handoff.screen.blackout import Blackout, wake_decision


def test_only_real_input_at_the_pc_takes_the_black_away():
    assert wake_decision("key", injected=False, moved=0, armed=True)
    assert wake_decision("button", injected=False, moved=0, armed=True)
    assert wake_decision("wheel", injected=False, moved=0, armed=True)
    assert wake_decision("move", injected=False, moved=40, armed=True)


def test_input_sent_from_a_phone_never_does():
    for kind in ("key", "button", "wheel", "move"):
        assert not wake_decision(kind, injected=True, moved=500, armed=True)


def test_a_little_jitter_or_input_just_after_it_goes_dark_does_not_count():
    assert not wake_decision("move", injected=False, moved=bo.MOVE_PIXELS - 1, armed=True)
    assert not wake_decision("key", injected=False, moved=0, armed=False)
    assert not wake_decision("move", injected=False, moved=900, armed=False)


def test_unknown_kinds_do_nothing():
    assert not wake_decision("touch", injected=False, moved=0, armed=True)


def test_without_support_it_says_so_and_leaves_the_screen_alone(monkeypatch):
    monkeypatch.setattr(bo, "supported", lambda: False)
    cover = Blackout()
    why = cover.on()
    assert "left as it is" in why and not cover.active


def test_turning_off_when_it_was_never_on_is_harmless():
    assert Blackout().off() is False


skip = pytest.mark.skipif(sys.platform != "win32" or os.environ.get("AFP_NO_DESKTOP_TESTS"), reason="draws on the real screen for about a second (set AFP_NO_DESKTOP_TESTS=1 to skip)")


@skip
def test_the_real_cover_hides_from_capture_ignores_injected_input_and_comes_off():
    import ctypes

    from handoff.screen import capture

    def brightness(image):
        small = image.convert("L").resize((48, 27))
        return sum(small.getdata()) / (48 * 27)

    cover = Blackout(dim=False)   # the backlight has its own tests; this one must not touch the real brightness
    try:
        before_pointer = capture.cursor_handle()
        before = brightness(capture.grab_screen())
        assert cover.on() == "" and cover.active
        time.sleep(bo.ARM_AFTER + 0.3)
        user32 = ctypes.windll.user32
        for _ in range(4):
            user32.mouse_event(1, 60, 0, 0, 0)
            user32.mouse_event(1, -60, 0, 0, 0)
            time.sleep(0.05)
        user32.keybd_event(0x10, 0, 0, 0)
        user32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.3)
        assert cover.active, "input sent like a phone's must not take the black away"
        if before_pointer is not None:
            assert capture.cursor_handle() is not None, "the cover hides the PC's pointer, so the phone would have none to draw"
        during = brightness(capture.grab_screen())
        assert during > 0.5 * max(before, 1), f"the phone's picture went black ({before:.1f} -> {during:.1f}): the cover is not hidden from capture"
        # ...and it really is drawn black on the monitor: take the hiding off for a moment and the same capture sees black
        from ctypes import wintypes

        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity(cover._hwnd, 0)
        time.sleep(0.6)
        drawn = brightness(capture.grab_screen())
        user32.SetWindowDisplayAffinity(cover._hwnd, bo.WDA_EXCLUDEFROMCAPTURE)
        assert drawn < 3, f"the cover is not painted black ({drawn:.1f}): the monitor would show the desktop"
        assert cover.on() == ""   # a second ask changes nothing
    finally:
        assert cover.off() in (True, False)
    assert not cover.active


# ---- the backlight: turned down while dark, given back however it ends -------------------------------------------------------

class FakePowerShell:
    def __init__(self, level="80"):
        self.level, self.calls = level, []

    def __call__(self, script, timeout=15):
        self.calls.append(script)
        return self.level if "CurrentBrightness" in script else ""


def test_dimming_turns_the_backlight_down_and_reports_what_it_was(monkeypatch):
    fake = FakePowerShell("80")
    monkeypatch.setattr(bo, "_powershell", fake)
    assert bo.dim_backlight() == 80
    assert "Brightness=[byte]0" in fake.calls[-1]


def test_a_desktop_monitor_with_no_backlight_control_is_left_alone(monkeypatch):
    fake = FakePowerShell("")
    monkeypatch.setattr(bo, "_powershell", fake)
    assert bo.dim_backlight() is None
    assert len(fake.calls) == 1   # it only asked; nothing was changed


def test_the_brightness_is_given_back_and_never_below_one(monkeypatch):
    fake = FakePowerShell()
    monkeypatch.setattr(bo, "_powershell", fake)
    bo.restore_backlight(80)
    bo.restore_backlight(0)
    assert "Brightness=[byte]80" in fake.calls[0] and "Brightness=[byte]1" in fake.calls[1]


def test_a_crash_while_dark_is_put_right_at_the_next_start(monkeypatch, tmp_path):
    fake = FakePowerShell()
    monkeypatch.setattr(bo, "_powershell", fake)
    note = tmp_path / "blackout.json"
    note.write_text('{"brightness": 65}', encoding="utf-8")
    assert bo.recover(note) is True
    assert "Brightness=[byte]65" in fake.calls[-1] and not note.exists()
    assert bo.recover(note) is False and bo.recover(None) is False   # nothing to do the second time


def test_a_damaged_note_does_not_stop_the_app_starting(monkeypatch, tmp_path):
    monkeypatch.setattr(bo, "_powershell", FakePowerShell())
    note = tmp_path / "blackout.json"
    note.write_text("not json", encoding="utf-8")
    assert bo.recover(note) is True and not note.exists()


def test_the_cover_dims_and_the_light_comes_back_when_it_ends(monkeypatch, tmp_path):
    fake = FakePowerShell("70")
    monkeypatch.setattr(bo, "_powershell", fake)
    monkeypatch.setattr(bo, "supported", lambda: True)
    note = tmp_path / "blackout.json"

    class Quick(Blackout):
        def _run(self):          # no real window: just the lifecycle
            self._hwnd = 1
            self._ready.set()
            self._stop.wait(5)
            self._hwnd = 0
            self._restore_thread = __import__("threading").Thread(target=self._restore, daemon=True)
            self._restore_thread.start()

    cover = Quick(state_file=note)
    assert cover.on() == ""
    cover._dim_thread.join(3)
    assert note.exists() and "Brightness=[byte]0" in fake.calls[-1]
    cover.close()
    assert "Brightness=[byte]70" in fake.calls[-1] and not note.exists()
