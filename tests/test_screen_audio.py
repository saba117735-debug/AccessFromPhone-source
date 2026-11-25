"""The PC's sound for the phone, and the pictures: clear (48 kHz) by default, kept in step with the clock, and never padded with silence just because a chunk was a moment late."""
import sys
import threading
import time
import types

import pytest

from handoff.screen import audio, capture
from handoff.screen.audio import DEFAULT_RATE, LoopbackAudio, clean_rate, to_phone_format


# ---- which rate

@pytest.mark.parametrize("asked,expected", [(None, 48000), ("48000", 48000), (16000, 16000), ("24000", 24000), ("44100", 48000), ("junk", 48000), ("", 48000), (-1, 48000)])
def test_the_rate_asked_for_is_one_of_the_offered_ones_or_the_clear_default(asked, expected):
    assert clean_rate(asked) == expected


def stereo(frames, value=1000):
    return b"".join(value.to_bytes(2, "little", signed=True) * 2 for _ in range(frames))


def test_a_pc_that_already_plays_at_48k_is_not_re_sampled():
    out, _ = to_phone_format(stereo(1920), 2, 48000, None, 48000)
    assert len(out) == 1920 * 2                                    # stereo to mono: half the bytes, the same number of samples
    assert out[:2] == (1000).to_bytes(2, "little", signed=True)     # and the samples are untouched


def test_a_44k_pc_is_brought_to_48k_and_the_light_choice_is_16k():
    out, _ = to_phone_format(stereo(4410), 2, 44100, None, 48000)
    assert abs(len(out) // 2 - 4800) <= 2
    out16, _ = to_phone_format(stereo(4800), 2, 48000, None, 16000)
    assert abs(len(out16) // 2 - 1600) <= 2


# ---- the stream itself, against a stand-in for the PC's sound card

class FakeSoundCard:
    """pyaudiowpatch: a loopback device at 48 kHz stereo, and a callback fed on a schedule (so the test controls when each 40 ms of sound 'arrives')."""

    def __init__(self, schedule):
        self.schedule = schedule
        self.paInt16, self.paContinue = 8, 0

    def PyAudio(self):
        card = self

        class Device:
            def get_default_wasapi_loopback(self):
                return {"maxInputChannels": 2, "defaultSampleRate": 48000, "index": 1}

            def open(self, **kw):
                callback, frames = kw["stream_callback"], kw["frames_per_buffer"]

                class Stream:
                    stopped = False

                    def feed(self):
                        began = time.monotonic()
                        for at in card.schedule:
                            wait = began + at - time.monotonic()
                            if wait > 0:
                                time.sleep(wait)
                            if self.stopped:
                                return
                            callback(stereo(frames, 1234), frames, None, None)

                    def stop_stream(self):
                        self.stopped = True

                    def close(self):
                        pass

                stream = Stream()
                threading.Thread(target=stream.feed, daemon=True).start()
                return stream

            def terminate(self):
                pass

        return Device()


def run_stream(monkeypatch, schedule, seconds):
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", FakeSoundCard(schedule))
    got, end = [], time.monotonic() + seconds
    for piece in LoopbackAudio().chunks(lambda: time.monotonic() < end, DEFAULT_RATE):
        got.append((time.monotonic(), piece))
    return got


def test_a_chunk_that_is_a_moment_late_is_played_as_it_is_with_no_silence_put_in(monkeypatch):
    """The old stream waited only 0.1 s for each 0.1 s chunk; one a hair late got a whole chunk of silence in front of it, which is the stutter people heard."""
    frames = int(DEFAULT_RATE * audio.CHUNK_SECONDS)
    # twelve chunks, a steady 40 ms apart with a few arriving up to 25 ms late (as a real sound card, a busy PC or a Wi-Fi hiccup does)
    schedule = [0.04 * (i + 1) + (0.025 if i in (3, 6, 9) else 0) for i in range(12)]
    got = [p for _, p in run_stream(monkeypatch, schedule, 0.9)]
    loud = [i for i, p in enumerate(got) if any(p[:200])]
    assert sum(len(got[i]) for i in loud) == 12 * frames * 2, "sound was lost or cut"
    assert loud == list(range(loud[0], loud[-1] + 1)), "silence was put in between the pieces of sound (a late chunk was patched with silence)"
    assert all(got[i][:2] == (1234).to_bytes(2, "little", signed=True) for i in loud)   # every piece is the real sound


def test_a_quiet_pc_gets_silence_for_the_time_that_passed_so_the_phones_clock_stays_right(monkeypatch):
    frames = int(DEFAULT_RATE * audio.CHUNK_SECONDS)
    schedule = [0.04 * (i + 1) for i in range(5)] + [1.3]   # sound, then the PC plays nothing for about a second (loopback then sends nothing at all), then sound again
    got = run_stream(monkeypatch, schedule, 1.6)
    data = b"".join(p for _, p in got)
    silence = sum(len(p) for _, p in got if not any(p[:200]))   # the pieces that are all zeros
    sound = len(data) - silence
    assert sound == 6 * frames * 2                                     # all six real chunks arrived, whole
    seconds_of_silence = silence / 2 / DEFAULT_RATE
    assert 1.0 <= seconds_of_silence <= 1.75, seconds_of_silence       # the 1.1 s of quiet (and a little at the end of the run): not nothing, and not doubled


def test_the_stream_as_a_whole_keeps_up_with_real_time(monkeypatch):
    schedule = [0.04 * (i + 1) for i in range(20)]
    got = run_stream(monkeypatch, schedule, 1.0)
    seconds_of_sound = sum(len(p) for _, p in got) / 2 / DEFAULT_RATE
    assert 0.7 <= seconds_of_sound <= 1.1, seconds_of_sound


# ---- the pictures: captured ahead, each handed out once

class FakePicture:
    pass


def test_each_picture_is_handed_out_once_and_the_next_is_captured_while_it_is_used(monkeypatch):
    taken = []

    def fake_grab():
        time.sleep(0.01)
        picture = FakePicture()
        taken.append(picture)
        return picture

    monkeypatch.setattr(capture, "grab_screen", fake_grab)
    source = capture._Source()
    source.HOLD = 0.3
    first, second, third = source.take(), source.take(), source.take()
    assert len({id(first), id(second), id(third)}) == 3        # three different pictures (two requests in flight at once never get the same one)
    assert len(taken) >= 3
    time.sleep(0.8)                                             # let its thread finish before the next test borrows the stand-in
    assert source._thread is None


def test_capturing_stops_a_few_seconds_after_the_last_request(monkeypatch):
    calls = []
    monkeypatch.setattr(capture, "grab_screen", lambda: calls.append(1) or FakePicture())
    source = capture._Source()
    source.HOLD = 0.4
    source.take()
    time.sleep(1.0)
    settled = len(calls)
    time.sleep(0.6)
    assert len(calls) == settled and source._thread is None    # nothing keeps capturing when nobody is looking


def test_when_the_fast_copy_fails_the_ordinary_capture_does_it(monkeypatch):
    from PIL import Image

    monkeypatch.setattr(capture, "_fast_failed", False)

    class Broken:
        def grab(self):
            raise OSError("no")

    monkeypatch.setattr(capture, "_fast", Broken())
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda all_screens=False: Image.new("RGB", (64, 36)))
    assert capture.grab_screen().size == (64, 36)
    assert capture._fast_failed is True                     # and it does not keep trying the fast way
    monkeypatch.setattr(capture, "_fast_failed", False)
