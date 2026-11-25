"""What the relay and a PC say to each other.

A phone talks to `https://<pc-id>.<your-domain>/...`. The relay turns each web request into one frame, sends it down the PC's
open connection, and streams the PC's answer back. Frames are JSON (bodies are base64), so the format is easy to inspect.

    relay -> PC   {"t":"req","id":7,"method":"GET","path":"/api/jobs?x=1","headers":[["x-token","..."]],"ip":"203.0.113.9","body":""}
    relay -> PC   {"t":"cancel","id":7}                         the phone gave up
    PC -> relay   {"t":"head","id":7,"status":200,"headers":[["content-type","application/json"]]}
    PC -> relay   {"t":"data","id":7,"b64":"..."}               zero or more, in order
    PC -> relay   {"t":"end","id":7}
    PC -> relay   {"t":"err","id":7,"status":502,"message":"..."}   instead of head/data/end
"""
from __future__ import annotations

import base64
import json

MAX_BODY = 8 * 1024 * 1024  # largest request a phone may send through the relay (a saved file is at most ~1.2 MB)
CHUNK = 48 * 1024  # bytes of response per frame
REQUEST_TIMEOUT = 60  # seconds the relay waits for the PC to start answering
MAX_IN_FLIGHT = 32  # requests one PC may have open at once

# Headers that belong to one hop of a connection, not to the message itself.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
# Dropped on the way to the PC as well: it computes its own, and the real client address travels separately.
REQUEST_DROP = HOP_BY_HOP | {"host", "content-length", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip"}


def pack(frame: dict) -> str:
    return json.dumps(frame, separators=(",", ":"))


def unpack(text: str) -> dict:
    frame = json.loads(text)
    if not isinstance(frame, dict) or "t" not in frame:
        raise ValueError("not a frame")
    return frame


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii")) if text else b""
