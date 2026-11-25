"""How a phone reaches this PC: the current Wi-Fi address, "anywhere" access through Tailscale, and keeping the PC awake.

Nothing here is required. If Tailscale is not installed the app works on the Wi-Fi as before; if the PC's address changes,
it is found again at the next start instead of being typed into config.yaml.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------------------------- the Wi-Fi address

def lan_ip() -> str:
    """The address other devices on this network use to reach this PC (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # a documentation address: the OS only picks the outgoing interface
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def host_label() -> str:
    """This PC's name as a .local name the phone can find without knowing the IP."""
    return socket.gethostname().split(".")[0].lower()


def lan_base(server, connectivity) -> str:
    """The address a phone on the same Wi-Fi uses: the numeric one, or (if asked) this PC's .local name."""
    if connectivity.lan_address == "name":
        return f"{'https' if server.https else 'http'}://{host_label()}.local:{server.port}"
    return server.link_base


def mac_address() -> tuple[str, bool]:
    """(address, trustworthy). Python falls back to a random number when it can't read the real one."""
    node = uuid.getnode()
    random_mac = bool(node >> 40 & 1)  # the "multicast" bit is what Python sets on a made-up address
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -8, -8)), not random_mac


# ---------------------------------------------------------------------------------------------- Tailscale

@dataclass
class Tailnet:
    installed: bool = False
    running: bool = False
    name: str = ""  # machine.tailnet.ts.net
    ip: str = ""  # 100.x.y.z
    https_enabled: bool = False
    error: str = ""
    cli: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.running and bool(self.name) and bool(self.ip) and self.https_enabled


def find_tailscale(configured: str = "") -> str:
    candidates = [configured] if configured else []
    found = shutil.which("tailscale")
    if found:
        candidates.append(found)
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidates.append(str(Path(root) / "Tailscale" / "tailscale.exe"))
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return ""


def _run(cli: str, *args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)


def read_tailnet(configured_cli: str = "", runner: Callable[..., subprocess.CompletedProcess] | None = None) -> Tailnet:
    """Ask the Tailscale app what it is doing. Never raises."""
    cli = find_tailscale(configured_cli)
    if not cli:
        return Tailnet(installed=False, error="Tailscale isn't installed on this PC.")
    try:
        done = (runner or _run)(cli, "status", "--json")
        data = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return Tailnet(installed=True, cli=cli, error=f"Couldn't read Tailscale's status ({type(exc).__name__}).")
    me = data.get("Self") or {}
    name = str(me.get("DNSName") or "").rstrip(".")
    ips = [ip for ip in (me.get("TailscaleIPs") or []) if "." in ip]  # IPv4 only: simple to bind and to type
    running = data.get("BackendState") == "Running"
    domains = [str(d).rstrip(".") for d in (data.get("CertDomains") or [])]
    return Tailnet(
        installed=True, running=running, name=name, ip=ips[0] if ips else "", https_enabled=bool(name) and name in domains, cli=cli,
        error="" if running else "Tailscale is installed but not signed in. Open it and sign in.",
    )


def issue_certificate(tailnet: Tailnet, folder: Path, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> tuple[Path, Path]:
    """Ask Tailscale for a real certificate for this PC's name. Phones trust it, so there is no "not private" warning."""
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / f"{tailnet.name}.crt", folder / f"{tailnet.name}.key"
    done = (runner or _run)(tailnet.cli, "cert", "--cert-file", str(cert), "--key-file", str(key), tailnet.name, timeout=60)
    if done.returncode != 0 or not cert.is_file() or not key.is_file():
        raise RuntimeError((done.stderr or done.stdout or "Tailscale did not issue a certificate.").strip().splitlines()[-1][:300])
    return cert, key


class Anywhere:
    """Opens a second, private listener on the Tailscale address with the trusted certificate, once everything is in place."""

    def __init__(self, app, configured_cli: str, port: int, folder: Path, enabled: bool = True,
                 reader: Callable[[], Tailnet] | None = None, issuer: Callable[[Tailnet, Path], tuple[Path, Path]] | None = None,
                 starter: Callable[[object, str, int, Path, Path], object] | None = None):
        self._app, self._port, self._folder, self._enabled = app, port, folder, enabled
        self._read = reader or (lambda: read_tailnet(configured_cli))
        self._issue = issuer or (lambda t, f: issue_certificate(t, f))
        self._start = starter or self._start_listener
        self._lock = threading.Lock()
        self._server = None
        self._url = ""
        self._error = ""

    @staticmethod
    def _start_listener(app, host: str, port: int, cert: Path, key: Path):
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, ssl_certfile=str(cert), ssl_keyfile=str(key), log_level="warning"))
        threading.Thread(target=server.run, name="anywhere", daemon=True).start()
        return server

    @property
    def url(self) -> str:
        return self._url

    def enable(self) -> dict:
        """Everything it takes to make 'anywhere' work that can be done from here. Safe to call again."""
        with self._lock:
            if not self._enabled:
                return self.status()
            tailnet = self._read()
            if self._server is None and tailnet.ready:
                try:
                    cert, key = self._issue(tailnet, self._folder)
                    self._server = self._start(self._app, tailnet.ip, self._port, cert, key)
                    self._url = f"https://{tailnet.name}:{self._port}"
                    self._error = ""
                except Exception as exc:  # a missing certificate or a busy port is something to show, not to crash on
                    self._error = str(exc)
            return self.status(tailnet)

    def status(self, tailnet: Tailnet | None = None) -> dict:
        t = tailnet or self._read()
        steps = [
            {"key": "install", "label": "Tailscale is installed on this PC", "ok": t.installed,
             "hint": "Install it from tailscale.com/download, then sign in."},
            {"key": "signin", "label": "This PC is signed in to Tailscale", "ok": t.installed and t.running,
             "hint": "Open Tailscale on this PC and sign in."},
            {"key": "https", "label": "HTTPS certificates are switched on for your Tailscale account", "ok": t.https_enabled,
             "hint": "In the Tailscale admin page open DNS and choose Enable HTTPS. It is free and takes a minute."},
            {"key": "listen", "label": "Anywhere access is running with a trusted certificate", "ok": bool(self._url),
             "hint": self._error or "Press Turn on once the steps above are done."},
        ]
        return {"provider": "tailscale", "enabled": self._enabled, "installed": t.installed, "ready": t.ready, "active": bool(self._url), "url": self._url,
                "name": t.name, "error": self._error or ("" if t.ready or not t.installed else t.error), "steps": steps}


# ---------------------------------------------------------------------------------------------- staying awake

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake(on: bool = True) -> bool:
    """Ask Windows not to sleep while the app runs (the screen may still turn off). Returns whether it was applied.

    The request lasts as long as the calling thread, so call it from the main thread, which runs for the app's whole life.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False


# ---------------------------------------------------------------------------------------------- finding the PC by name

def advertise(port: int, https: bool, name: str, ip: str) -> Callable[[], None] | None:
    """Announce this PC on the local network (mDNS), so name.local works and survives an address change. Optional."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        return None
    try:
        zc = Zeroconf()
        info = ServiceInfo(
            "_accessfromphone._tcp.local.", f"{name}._accessfromphone._tcp.local.", addresses=[socket.inet_aton(ip)], port=port,
            properties={"https": "1" if https else "0"}, server=f"{name}.local.",
        )
        zc.register_service(info)
    except Exception:
        return None

    def stop() -> None:
        try:
            zc.unregister_service(info)
            zc.close()
        except Exception:
            pass

    return stop
