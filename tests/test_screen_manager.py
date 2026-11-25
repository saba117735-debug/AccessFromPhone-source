"""Looking at the screen: nobody without the Owner's yes, one viewer at a time, always stoppable, and it ends by itself. A fake clock and a fake screen: nothing waits."""
import pytest

from handoff.db import Database
from handoff.screen import ScreenBusy, ScreenManager, ScreenNotAllowed, Settings
from handoff.screen import capture


class Clock:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Banner:
    def __init__(self):
        self.log, self.decide, self.stop = [], None, None

    def request(self, name, note, decide):
        self.log.append(("request", name)); self.decide = decide

    def active(self, name, stop, controlling=False):
        self.log.append(("active", name) if not controlling else ("controlling", name)); self.stop = stop

    def clear(self):
        self.log.append(("clear",))


@pytest.fixture
def screen(tmp_path):
    clock, events, banner, grabs = Clock(), [], Banner(), []
    manager = ScreenManager(Database(tmp_path / "app.db"), Settings(idle=20, ask=120), clock=clock,
                            grab=lambda w, q: grabs.append((w, q)) or b"JPEG", on_event=lambda kind, **d: events.append((kind, d)), banner=banner)
    return manager, clock, events, banner, grabs


def test_nobody_sees_the_screen_without_a_yes(screen):
    manager, *_ = screen
    assert manager.view("a")["state"] == "none"
    with pytest.raises(ScreenNotAllowed):
        manager.start("a", "Asha")
    with pytest.raises(ScreenNotAllowed):
        manager.frame("a")


def test_asking_shows_a_prompt_on_the_pc_and_the_owner_decides(screen):
    manager, clock, events, banner, _ = screen
    assert manager.request("a", "Asha", "fixing the printer") == "pending"
    assert manager.request("a", "Asha") == "pending"  # asking twice is one request
    assert banner.log == [("request", "Asha")]
    assert manager.pending()[0]["note"] == "fixing the printer" and manager.view("a")["state"] == "pending"
    banner.decide(True, 15)  # the Owner taps Allow on the little window
    assert manager.view("a")["state"] == "granted" and manager.view("a")["left"] == 15 * 60
    assert [k for k, _ in events] == ["screen_requested", "screen_allowed"]
    assert manager.decide("a", True, 15) is False  # already answered


def test_a_denied_request_gives_nothing(screen):
    manager, *_ = screen
    manager.request("a", "Asha")
    assert manager.decide("a", False) is True
    assert manager.view("a")["state"] == "none"
    with pytest.raises(ScreenNotAllowed):
        manager.start("a", "Asha")


def test_an_unanswered_request_is_dropped_and_the_prompt_goes_away(screen):
    manager, clock, events, banner, _ = screen
    manager.request("a", "Asha")
    clock.advance(121)
    assert manager.pending() == [] and manager.view("a")["state"] == "none"
    assert banner.log[-1] == ("clear",) and events[-1][0] == "screen_unanswered"


def test_a_permission_runs_out_and_then_the_viewing_stops(screen):
    manager, clock, events, banner, _ = screen
    manager.grant("a", 15)
    manager.start("a", "Asha")
    assert manager.view("a")["state"] == "viewing" and banner.log[-1] == ("active", "Asha")
    clock.advance(15 * 60 + 1)
    with pytest.raises(ScreenNotAllowed):
        manager.frame("a")
    assert manager.viewing() is None and any(d.get("reason") == "permission ran out" for k, d in events if k == "screen_stopped")


def test_a_permission_with_no_time_limit_lasts_until_the_owner_takes_it_back(screen):
    manager, clock, *_ = screen
    manager.grant("a", 0)
    clock.advance(10 * 24 * 3600)
    assert manager.allowed("a") and manager.view("a")["forever"] is True
    manager.revoke("a")
    assert not manager.allowed("a")


def test_one_viewer_at_a_time_and_the_second_is_told_who(screen):
    manager, *_ = screen
    manager.grant("a", 60); manager.grant("b", 60)
    manager.start("a", "Asha")
    with pytest.raises(ScreenBusy) as err:
        manager.start("b", "Ben")
    assert err.value.who == "Asha" and manager.view("b")["state"] == "busy" and manager.view("b")["viewer"] == "Asha"
    manager.start("a", "Asha")  # starting your own again is harmless
    manager.stop("a")
    manager.start("b", "Ben")


def test_only_the_viewer_gets_pictures_and_asking_keeps_it_alive(screen):
    manager, clock, events, banner, grabs = screen
    manager.grant("a", 60); manager.grant("b", 60)
    manager.start("a", "Asha")
    with pytest.raises(ScreenNotAllowed):
        manager.frame("b")  # allowed to look, but not looking: no picture
    for _ in range(10):  # a picture every 5 seconds is enough to stay
        clock.advance(5)
        assert manager.frame("a", 1000, 40) == b"JPEG"
    assert manager.viewing() is not None and grabs[-1] == (1000, 40)


def test_a_phone_that_stops_asking_loses_the_viewing(screen):
    manager, clock, events, banner, _ = screen
    manager.grant("a", 60)
    manager.start("a", "Asha")
    clock.advance(21)
    assert manager.viewing() is None and banner.log[-1] == ("clear",)
    assert any(d.get("reason") == "the phone stopped looking" for k, d in events if k == "screen_stopped")


def test_the_owner_can_stop_it_from_the_pc_and_pause_ends_everything(screen):
    manager, clock, events, banner, _ = screen
    manager.grant("a", 60)
    manager.start("a", "Asha")
    banner.stop()  # the Stop button on the PC's window
    assert manager.viewing() is None
    manager.start("a", "Asha")
    manager.request("b", "Ben")
    manager.stop_all("paused")
    assert manager.viewing() is None and manager.pending() == [] and manager.allowed("a")  # the permission stays; Pause only stops what is happening


def test_taking_the_permission_away_ends_the_viewing_at_once(screen):
    manager, *_ = screen
    manager.grant("a", 60)
    manager.start("a", "Asha")
    manager.revoke("a")
    assert manager.viewing() is None


def test_it_can_be_switched_off_for_the_whole_pc(tmp_path):
    manager = ScreenManager(Database(tmp_path / "app.db"), Settings(enabled=False))
    with pytest.raises(ScreenNotAllowed):
        manager.request("a", "Asha")
    assert manager.view("a")["state"] == "off"


def test_the_picture_size_and_quality_are_kept_in_range():
    assert capture.clamp(99999, capture.MIN_WIDTH, capture.MAX_WIDTH) == capture.MAX_WIDTH
    assert capture.clamp(1, capture.MIN_QUALITY, capture.MAX_QUALITY) == capture.MIN_QUALITY


def test_a_real_picture_comes_back_as_a_jpeg_or_a_clear_error():
    try:
        data = capture.grab_jpeg(640, 40)
    except capture.ScreenUnavailable:
        return  # a PC with no screen to capture (a build machine): the error is the right answer
    assert data[:3] == b"\xff\xd8\xff"


def test_a_trusted_phone_may_look_without_a_yes_and_everyone_else_still_needs_one(screen):
    manager, clock, events, banner, _ = screen
    manager.trusted = lambda device_id: device_id == "owner"
    assert manager.allowed("owner") and manager.view("owner")["trusted"] is True and manager.view("owner")["forever"] is True
    assert manager.request("owner", "Asha") == "granted"
    manager.start("owner", "Asha")
    assert banner.log[-1] == ("active", "Asha")  # still visible on the PC
    assert not manager.allowed("other") and manager.view("other")["trusted"] is False
    manager.stop_all("stopped on this PC")
    assert manager.viewing() is None and manager.allowed("owner")  # stopping ends the viewing, not the trust


def test_the_pointer_is_drawn_where_it_is():
    from PIL import Image

    image = Image.new("RGB", (200, 120), (0, 0, 0))
    capture.draw_pointer(image, 50, 40, size=20)
    assert image.getpixel((51, 44)) == (255, 255, 255)   # the arrow's body is white
    assert image.getpixel((150, 100)) == (0, 0, 0)      # and nothing else is touched


# -- a phone that simply vanishes (the app swiped away from the recent apps, no signal, a flat battery) ----------------------------------------

class WatchingBanner(Banner):
    def waiting(self, name, left):
        self.log.append(("waiting", name, left))


class HeldInjector:
    """A mouse button is down on the PC, and the phone is gone."""

    def __init__(self):
        self.released = 0

    def holding(self):
        return self.released == 0

    def release_all(self):
        self.released += 1

    def apply(self, events):
        return len(events)


def watched(tmp_path, **settings):
    clock, banner = Clock(), WatchingBanner()
    manager = ScreenManager(Database(tmp_path / "app.db"), Settings(idle=20, ask=120, quiet=6, **settings), clock=clock, grab=lambda w, q: b"JPEG", banner=banner)
    manager.request("a", "Asha's phone")
    manager.decide("a", True, 15)
    manager.start("a", "Asha's phone")
    return manager, clock, banner


def test_a_phone_that_vanishes_is_noticed_and_ended_without_anyone_asking(tmp_path):
    manager, clock, banner = watched(tmp_path)
    clock.advance(3)
    manager.watch_once()
    assert manager.viewing()["quiet"] is False and not any(entry[0] == "waiting" for entry in banner.log)   # a short gap is nothing
    clock.advance(4)   # seven seconds with no picture asked for
    manager.watch_once()
    assert banner.log[-1] == ("waiting", "Asha's phone", 13) and manager.viewing()["quiet"] is True   # the PC says so, and how long is left
    clock.advance(14)  # past the limit
    manager.watch_once()
    assert manager.viewing() is None and banner.log[-1] == ("clear",)   # and then it is over: the PC no longer says someone is viewing


def test_the_countdown_goes_down_while_the_phone_stays_gone(tmp_path):
    manager, clock, banner = watched(tmp_path)
    clock.advance(8); manager.watch_once()
    clock.advance(5); manager.watch_once()
    lefts = [entry[2] for entry in banner.log if entry[0] == "waiting"]
    assert lefts == [12, 7]


def test_a_phone_that_comes_back_in_time_carries_on_as_if_nothing_happened(tmp_path):
    manager, clock, banner = watched(tmp_path)
    clock.advance(9); manager.watch_once()
    assert manager.viewing()["quiet"] is True
    manager.frame("a")   # the phone asks for a picture again
    manager.watch_once()
    assert manager.viewing() is not None and manager.viewing()["quiet"] is False and banner.log[-1] == ("active", "Asha's phone")


def test_a_button_held_down_when_the_phone_vanished_is_let_go_by_the_watchdog(tmp_path):
    manager, clock, banner = watched(tmp_path)
    manager.injector = HeldInjector()
    manager._viewing.controlling = True
    manager._viewing.last_input = clock()
    clock.advance(6)   # past release_after (5 s), before the viewing ends (20 s)
    manager.watch_once()
    assert manager.injector.released == 1 and manager.viewing() is not None


def test_the_quiet_warning_never_comes_later_than_the_end_even_with_a_short_limit(tmp_path):
    manager, clock, banner = watched(tmp_path, release_after=2)
    manager.cfg.idle = 5.0   # the smallest the settings allow
    clock.advance(3); manager.watch_once()   # 40% of the limit: the warning, with time to read it
    assert banner.log[-1][0] == "waiting"


def test_the_watchdog_thread_ends_a_vanished_viewing_by_itself(tmp_path):
    """The real thing, with the real clock: nothing calls the manager after the phone is gone, and the viewing still ends."""
    import time

    banner = WatchingBanner()
    manager = ScreenManager(Database(tmp_path / "app.db"), Settings(idle=1.5, ask=120, quiet=0.5), grab=lambda w, q: b"JPEG", banner=banner)
    try:
        manager.request("a", "Asha's phone"); manager.decide("a", True, 15); manager.start("a", "Asha's phone")
        assert manager._viewing is not None
        deadline = time.time() + 6
        while time.time() < deadline and manager._viewing is not None:
            time.sleep(0.1)
        assert manager._viewing is None, "the viewing went on after the phone vanished"
        assert any(entry[0] == "waiting" for entry in banner.log) and banner.log[-1] == ("clear",)
        time.sleep(1.3)
        assert manager._watch is None   # and with nobody to watch, the watchdog stops too
    finally:
        manager.close()


# -- the sound's quality: what the phone asks for, and what the stream says it is ----------------------------------------------------------------

class RatedAudio:
    def __init__(self):
        self.rates = []

    def available(self):
        return True

    def chunks(self, keep_going, rate=16000):
        self.rates.append(rate)
        yield b"\0\0" * 10


class OldAudio:
    """A source that cannot choose a rate (it always sends 16 kHz)."""

    def available(self):
        return True

    def chunks(self, keep_going):
        yield b"\0\0" * 10


def test_the_sound_is_clear_by_default_and_the_phone_may_ask_for_light(tmp_path):
    manager, clock, banner = watched(tmp_path)
    manager.audio = RatedAudio()
    clear = manager.audio_stream("a")
    assert clear.rate == 48000 and next(clear)
    light = manager.audio_stream("a", "16000")
    assert light.rate == 16000 and next(light)
    junk = manager.audio_stream("a", "bogus")
    assert junk.rate == 48000
    assert manager.audio.rates == [48000, 16000]   # (the junk one was never read)


def test_a_source_that_cannot_choose_is_honest_about_its_rate(tmp_path):
    manager, clock, banner = watched(tmp_path)
    manager.audio = OldAudio()
    stream = manager.audio_stream("a", 48000)
    assert stream.rate == 16000 and next(stream) == b"\0\0" * 10   # the phone is told 16 kHz, so it plays it correctly
