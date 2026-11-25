"""Start everything the app needs, and hand back one object to look at and stop it.

Both the command line (`python -m handoff`) and the installed tray app use this, so they behave the same.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import uvicorn

from .admin import create_admin_app
from .config import Settings
from . import netfix
from .connectivity import Anywhere, advertise, host_label, keep_awake, lan_ip
from .screen.blackout import blackout, recover
from .secure import restrict_to_current_user
from .server import create_app
from .tunnel import RelayLink


@dataclass
class Running:
    settings: Settings
    app: object
    anywhere: object
    admin_url: str = ""
    notes: list[str] = field(default_factory=list)  # one line per thing that was switched on, for the console
    servers: list[uvicorn.Server] = field(default_factory=list)
    _stops: list = field(default_factory=list)

    # ---- what the tray shows
    def phones_online(self, within_seconds: int = 90) -> int:
        accounts = self.app.state.accounts
        if accounts is None:
            return 0
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        count = 0
        for d in accounts.devices():
            try:
                seen = datetime.fromisoformat(d["last_seen"])
            except ValueError:
                continue
            if not d["blocked"] and (now - seen).total_seconds() <= within_seconds:
                count += 1
        return count

    def phones_paired(self) -> int:
        accounts = self.app.state.accounts
        return len(accounts.devices()) if accounts is not None else 0

    def owner_ready(self) -> bool:
        accounts = self.app.state.accounts
        return True if accounts is None else accounts.owner_exists

    def anywhere_status(self) -> dict:
        try:
            return self.anywhere.status()
        except Exception:
            return {}

    def stop(self) -> None:
        blackout.close()  # nobody is left with a black screen, a dark laptop, or an app that is gone
        for server in self.servers:
            server.should_exit = True
        for stop in self._stops:
            try:
                stop()
            except Exception:
                pass
        link = getattr(self.anywhere, "_client", None)
        if link is not None:
            link.stop()
        try:
            self.app.state.manager.flush()
        except Exception:
            pass


def _serve(app, host: str, port: int, name: str, quiet: bool, **extra) -> uvicorn.Server:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", log_config=None if quiet else uvicorn.config.LOGGING_CONFIG, **extra)
    server = uvicorn.Server(config)
    server.thread = threading.Thread(target=server.run, name=name, daemon=True)
    server.thread.start()
    return server


def start(settings: Settings, quiet: bool = False) -> Running:
    """Build the app and start its servers in the background. Returns at once; `Running.stop()` ends them."""
    blackout.state_file = settings.server.token_file.parent / "blackout.json"
    recover(blackout.state_file)  # a laptop left dark by a crash gets its brightness back
    netfix.install()  # a phone that drops while connecting must not stop the server listening (Windows)
    app = create_app(settings)
    if settings.screen.banner:  # the real app shows the small window on this PC; tests and servers without a desktop skip it
        from .screen.banner import DesktopBanner

        app.state.screen.banner = DesktopBanner()
    panic = None
    if settings.screen.panic_key:  # a key on THIS PC's keyboard that cuts a phone's control at once (a phone cannot press it)
        from .screen.hotkey import PanicKey, describe

        panic = PanicKey(settings.screen.panic_key, lambda: (app.state.screen.panic(), blackout.off()))
        if panic.start():
            app.state.screen.panic_hint = f"Panic key: {describe(settings.screen.panic_key)} stops this at once."
            if hasattr(app.state.screen.banner, "hint"):
                app.state.screen.banner.hint = app.state.screen.panic_hint
        else:
            panic = None
    server = settings.server
    notes: list[str] = []
    tls: dict = {}
    if server.https:
        from .tls import ensure_certificate, hosts_for

        cert, key = ensure_certificate(server.tls_dir, hosts_for(server.public_url_now))
        tls = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

    stops = []
    if settings.storage.lock_data_folder:
        locked = {settings.storage.database.parent, server.token_file.parent}  # where the database, the sign-in token and the saved certificates live
        if any([restrict_to_current_user(folder) for folder in locked]):
            notes.append("Closed the data folder to other accounts on this PC.")
    if settings.power.keep_awake and keep_awake(True):
        notes.append("Keeping this PC awake while the app runs, so your phone can always reach it.")
    if settings.connectivity.mdns:
        stop_mdns = advertise(server.port, server.https, host_label(), lan_ip())
        if stop_mdns:
            stops.append(stop_mdns)
            notes.append(f"Also reachable by name on the Wi-Fi: {host_label()}.local")

    if settings.relay.enabled:  # your own server: nothing for the customer to install
        anywhere = RelayLink(app, app.state.manager.db, settings.relay.url, settings.relay.register_key)
    else:
        anywhere = Anywhere(app, settings.connectivity.tailscale_path, settings.connectivity.anywhere_port, server.tls_dir / "tailscale",
                            enabled=settings.connectivity.anywhere == "auto")
    app.state.anywhere = anywhere
    threading.Thread(target=anywhere.enable, name="anywhere-check", daemon=True).start()  # it may take a few seconds: don't hold up the start

    if panic is not None:
        stops.append(panic.stop)
    running = Running(settings=settings, app=app, anywhere=anywhere, notes=notes, _stops=stops)
    accounts = app.state.accounts
    if accounts is not None:
        try:  # the old "connect" page held a link that signs in without a password; it must not linger
            (server.token_file.parent / "connect.html").unlink(missing_ok=True)
        except OSError:
            pass
        running.admin_url = f"http://127.0.0.1:{settings.accounts.admin_port}/"
        running.servers.append(_serve(create_admin_app(settings, accounts, app.state.manager, anywhere), "127.0.0.1", settings.accounts.admin_port, "pc-page", quiet))
    running.servers.append(_serve(app, server.host, server.port, "phone-server", quiet, **tls))
    return running


def wait_until_listening(running: Running, seconds: float = 15.0) -> bool:
    """True once every server is accepting connections; False if one stopped (usually because its port is taken)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if all(s.started for s in running.servers):
            return True
        if any(not s.thread.is_alive() for s in running.servers):
            return False
        time.sleep(0.05)
    return False
