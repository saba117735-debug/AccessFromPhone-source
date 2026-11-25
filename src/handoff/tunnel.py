"""This PC's side of the relay: stay connected to your server and answer the phone's requests with the app itself.

The connection goes *out* from the PC, so no router setting or open port is needed. Each request the relay passes down is handed
to the same web app that serves the Wi-Fi link, in memory, so the phone gets exactly the same pages, pairing and approvals.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
from urllib.parse import urlparse

import httpx
import websockets

from .db import Database
from .relay import protocol as proto


def phone_url(relay_url: str, pc_id: str) -> str:
    """The address a phone uses for this PC: https://<pc-id>.example.com (the relay's own name with the id in front)."""
    parts = urlparse(relay_url)
    return f"{parts.scheme}://{pc_id}.{parts.netloc}"


def agent_url(relay_url: str) -> str:
    parts = urlparse(relay_url)
    return f"{'wss' if parts.scheme == 'https' else 'ws'}://{parts.netloc}/v1/agent"


class RelayClient:
    """Keeps one connection to the relay (reconnecting by itself) and serves the requests that arrive on it."""

    def __init__(self, app, relay_url: str, pc_id: str, secret: str):
        self._app, self._url, self._pc_id, self._secret = app, agent_url(relay_url), pc_id, secret
        self.connected = False
        self.last_error = ""
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="relay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: [t.cancel() for t in asyncio.all_tasks(self._loop)])

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        with contextlib.suppress(asyncio.CancelledError):
            self._loop.run_until_complete(self._forever())

    async def _forever(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the relay is down, the network dropped, the secret was refused: wait and try again
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _session(self) -> None:
        headers = {"X-PC-Id": self._pc_id, "Authorization": "Bearer " + self._secret}
        async with websockets.connect(self._url, additional_headers=headers, ping_interval=20, ping_timeout=20, max_size=proto.MAX_BODY * 2) as ws:
            self.connected, self.last_error = True, ""
            tasks: dict[int, asyncio.Task] = {}
            send_lock = asyncio.Lock()

            async def send(frame: dict) -> None:
                async with send_lock:
                    await ws.send(proto.pack(frame))

            try:
                async for raw in ws:
                    try:
                        frame = proto.unpack(raw)
                    except ValueError:
                        continue
                    if frame["t"] == "req":
                        tasks[frame["id"]] = asyncio.create_task(self._serve(frame, send, tasks))
                    elif frame["t"] == "cancel" and (task := tasks.get(frame.get("id"))):
                        task.cancel()
            finally:
                for task in tasks.values():
                    task.cancel()

    async def _serve(self, frame: dict, send, tasks: dict) -> None:
        req_id = frame["id"]
        try:
            # the app sees the phone's real address, so its "too many wrong tries" limits count that phone, not the relay
            transport = httpx.ASGITransport(app=self._app, client=(str(frame.get("ip") or "0.0.0.0"), 0), raise_app_exceptions=False)
            headers = [(k, v) for k, v in frame.get("headers", []) if k.lower() not in proto.REQUEST_DROP]
            async with httpx.AsyncClient(transport=transport, base_url="http://pc.local") as client:
                response = await client.request(frame["method"], frame["path"], headers=headers, content=proto.unb64(frame.get("body", "")))
            await send({"t": "head", "id": req_id, "status": response.status_code,
                        "headers": [[k, v] for k, v in response.headers.multi_items() if k.lower() not in proto.HOP_BY_HOP]})
            body = response.content
            for start in range(0, len(body), proto.CHUNK):
                await send({"t": "data", "id": req_id, "b64": proto.b64(body[start:start + proto.CHUNK])})
            await send({"t": "end", "id": req_id})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await send({"t": "err", "id": req_id, "status": 502, "message": f"The PC couldn't answer ({type(exc).__name__})."})
        finally:
            tasks.pop(req_id, None)


class RelayLink:
    """What the PC page shows as "Anywhere": registered with your server, and connected to it."""

    provider = "relay"

    def __init__(self, app, db: Database, relay_url: str, register_key: str = "", enabled: bool = True):
        self._app, self._db, self._url, self._key, self._enabled = app, db, relay_url.rstrip("/"), register_key, enabled
        self._client: RelayClient | None = None
        self._error = ""
        self._lock = threading.Lock()

    # ---- credentials, kept on this PC in the app's own database
    def _creds(self) -> tuple[str, str] | None:
        pc_id, secret, relay = self._db.meta("relay_pc_id"), self._db.meta("relay_secret"), self._db.meta("relay_url")
        return (pc_id, secret) if pc_id and secret and relay == self._url else None

    def _register(self) -> tuple[str, str]:
        response = httpx.post(self._url + "/v1/register", json={"key": self._key}, timeout=15)
        if response.status_code == 403:
            raise RuntimeError("The server refused this PC. The registration key is wrong or missing.")
        response.raise_for_status()
        data = response.json()
        self._db.set_meta("relay_pc_id", data["pc_id"])
        self._db.set_meta("relay_secret", data["secret"])
        self._db.set_meta("relay_url", self._url)
        return data["pc_id"], data["secret"]

    @property
    def url(self) -> str:
        creds = self._creds()
        return phone_url(self._url, creds[0]) if creds and self.connected else ""

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.connected)

    def enable(self) -> dict:
        """Register once, then stay connected. Safe to call again."""
        with self._lock:
            if self._enabled and self._client is None:
                try:
                    pc_id, secret = self._creds() or self._register()
                    self._client = RelayClient(self._app, self._url, pc_id, secret)
                    self._client.start()
                    self._error = ""
                except Exception as exc:  # no internet, the server is down, wrong key: show it, don't crash
                    self._error = str(exc)[:240]
        for _ in range(30):  # give a fresh connection a moment, so the page doesn't say "not connected" for a blink
            if self.connected or not self._client:
                break
            threading.Event().wait(0.1)
        return self.status()

    def status(self, tailnet=None) -> dict:
        creds = self._creds()
        up = self.connected
        host = urlparse(self._url).netloc
        error = self._error or (self._client.last_error if self._client and not up else "")
        return {
            "provider": "relay", "enabled": self._enabled, "installed": True, "ready": bool(creds), "active": up, "url": self.url, "name": host, "error": error,
            "steps": [
                {"key": "register", "label": f"This PC is registered with {host}", "ok": bool(creds), "hint": error or "Press Turn on to register."},
                {"key": "connect", "label": f"This PC is connected to {host}", "ok": up, "hint": error or "Connecting…"},
            ],
        }
