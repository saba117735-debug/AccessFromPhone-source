"""What this PC is playing, as sound for the phone that is looking.

The PC's own "loopback" (what goes to the speakers) is captured and sent as plain 16-bit mono PCM. The default is 48,000 samples a second ("Clear": about 96 KB a second, the rate
nearly every PC and phone already uses, so nothing is re-sampled and nothing is lost between the speakers and the phone); "Light" is 16,000 (about 32 KB a second) for a slow
connection. It is only sent to the phone that is looking, only while it looks, and one stream at a time.
Needs the optional PyAudioWPatch package (installed with the app on Windows); without it the Sound switch simply says it is not available.
"""
from __future__ import annotations

import queue
import sys
import time
from typing import Callable, Iterator, Protocol

RATE = 16000                   # the old fixed rate, still what a source that can't choose one sends
RATES = (16000, 24000, 48000)  # what the phone may ask for
DEFAULT_RATE = 48000
CHUNK_SECONDS = 0.04           # the sound is passed on in pieces of 40 ms (it was 100 ms: coarser, and each late piece was patched with silence)


def clean_rate(value) -> int:
    """The rate the phone asked for, or the default when it asked for nothing sensible."""
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RATE
    return rate if rate in RATES else DEFAULT_RATE


class AudioUnavailable(Exception):
    """This PC cannot share its sound (no audio device, or the audio package is missing)."""


class AudioSource(Protocol):
    def available(self) -> bool: ...
    def chunks(self, keep_going: Callable[[], bool], rate: int = RATE) -> Iterator[bytes]: ...


def to_phone_format(data: bytes, channels: int, rate: int, state=None, out_rate: int = RATE):
    """Raw device samples (16-bit, `channels`, `rate`) to mono at `out_rate`. Returns (bytes, state) so a stream can carry the resampler on.
    Nothing is re-sampled when the device already runs at `out_rate` (most PCs run at 48 kHz): that is the clearest sound there is."""
    import audioop  # noqa: PLC0415 - standard library up to Python 3.12

    if channels == 2:
        data = audioop.tomono(data, 2, 0.5, 0.5)
    elif channels > 2:   # keep the first two (front left and right) of a surround device
        frames = len(data) // (2 * channels)
        data = audioop.tomono(b"".join(data[i * 2 * channels:i * 2 * channels + 4] for i in range(frames)), 2, 0.5, 0.5)
    if rate != out_rate:
        data, state = audioop.ratecv(data, 2, 1, rate, out_rate, state)
    return data, state


class LoopbackAudio:
    """Windows WASAPI loopback through PyAudioWPatch."""

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import pyaudiowpatch as pa  # noqa: PLC0415

            p = pa.PyAudio()
            try:
                p.get_default_wasapi_loopback()
                return True
            finally:
                p.terminate()
        except Exception:  # noqa: BLE001 - no package, no device, no WASAPI
            return False

    def chunks(self, keep_going: Callable[[], bool], rate: int = DEFAULT_RATE) -> Iterator[bytes]:
        try:
            import pyaudiowpatch as pa  # noqa: PLC0415

            p = pa.PyAudio()
            device = p.get_default_wasapi_loopback()
        except Exception as exc:  # noqa: BLE001
            raise AudioUnavailable("This PC cannot share its sound (no audio device, or the audio package is not installed).") from exc
        channels, device_rate = int(device["maxInputChannels"]) or 2, int(device["defaultSampleRate"])
        heard: "queue.Queue[bytes]" = queue.Queue(maxsize=200)

        def on_audio(data, frames, info, status):   # PortAudio's thread
            try:
                heard.put_nowait(data)
            except queue.Full:
                pass   # the phone is far behind: drop, the stream carries on live
            return (None, pa.paContinue)

        try:
            stream = p.open(format=pa.paInt16, channels=channels, rate=device_rate, input=True, input_device_index=device["index"],
                            frames_per_buffer=int(device_rate * CHUNK_SECONDS), stream_callback=on_audio)
        except Exception as exc:  # noqa: BLE001
            p.terminate()
            raise AudioUnavailable("This PC's sound could not be opened.") from exc
        state = None
        began, sent = time.monotonic(), 0   # `sent`: samples handed on so far. The stream is kept in step with the clock, not with the chunks:
        try:
            while keep_going():
                try:
                    raw = heard.get(timeout=0.25)   # (waiting less than a chunk's length, as before, meant a chunk that was a moment late got silence put in front of it: a gap, then a pile-up, then a skip)
                except queue.Empty:
                    # the PC plays nothing, and loopback then sends nothing: hand on the silence that has passed, so the phone's clock stays right
                    owed = int((time.monotonic() - began) * rate) - sent
                    if owed >= rate // 20:
                        sent += owed
                        yield b"\0" * (owed * 2)
                    continue
                out, state = to_phone_format(raw, channels, device_rate, state, rate)
                sent += len(out) // 2
                yield out
        finally:
            try:
                stream.stop_stream(); stream.close()
            finally:
                p.terminate()
