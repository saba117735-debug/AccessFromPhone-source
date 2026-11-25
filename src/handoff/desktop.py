"""The installed app: it lives in the tray, opens the PC page in its own window, and can start with Windows.

    AccessFromPhone.exe                 open the app (and its window)
    AccessFromPhone.exe --background    start quietly in the tray (what "start with Windows" uses)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP = "AccessFromPhone"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

CONFIG_TEMPLATE = """# AccessFromPhone settings. Change what you need, then quit the app from its tray icon and open it again.
# Everything here is optional; the defaults are safe.

server:
  host: 0.0.0.0
  port: 8765
  public_url: auto                 # this PC's Wi-Fi address, found again at every start
  https: true                      # encrypted link (needed for voice input on the phone)

accounts:
  enabled: true
  sign_in: auto                    # on Windows: sign in with your Windows user name and password

agent:
  allowed_folders:
    - '{workspace}'                # the only folders the agent may change. Add more, one per line
  ask_for_access: true             # other folders: you are asked on the phone, once or always

llm:
  provider: auto                   # auto = a local model if there is one. Or anthropic (set ANTHROPIC_API_KEY)

relay:                             # reach this PC from any network through your own server
  enabled: {relay_enabled}
  url: {relay_url}

push:                              # a notice on the phone when a plan is ready or a job finished (see "Notifications" in the README)
  enabled: false                   # true once the push keys below are in place
  show_details: false              # false = a notice never says what you asked, only that something needs you
  # credentials_file: C:\\path\\to\\.env   # an existing file holding VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT, FIREBASE_SERVICE_ACCOUNT_PATH (read, never copied)

power:
  remote_actions: [lock, screen_off, sleep]    # what the phone may ask this PC to do (each one still needs your approval)
  keep_awake: true

storage:
  results_dir: data/results
  snapshots_dir: data/snapshots

logging:
  audit_file: data/audit.log
"""


# ---------------------------------------------------------------------------------------------- where things live

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def home_dir() -> Path:
    """Settings, the database and logs. Not the install folder, so updates and uninstalls never touch a customer's data."""
    if override := os.environ.get("ACCESSFROMPHONE_HOME"):
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP


def default_workspace() -> Path:
    return Path.home() / "Documents" / APP


def build_value(name: str, default: str = "") -> str:
    """Values fixed when the installer was built (the relay address, the version). Missing in a development checkout."""
    try:
        from . import _build

        return str(getattr(_build, name, default))
    except ImportError:
        return default


def ensure_home(home: Path | None = None) -> Path:
    """Make the data folder and a first config.yaml if there isn't one. Returns the config's path. Never overwrites."""
    home = home or home_dir()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.yaml"
    if not config.exists():
        workspace = default_workspace() if not os.environ.get("ACCESSFROMPHONE_HOME") else home / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        note = workspace / "Welcome.txt"
        if not note.exists():
            note.write_text("This is the folder your phone can work in.\nAsk it to list, read, search or change files here, and you approve every change.\n", encoding="utf-8")
        relay = build_value("RELAY_URL")
        config.write_text(CONFIG_TEMPLATE.format(workspace=workspace.as_posix(), relay_enabled="true" if relay else "false", relay_url=relay or "http://localhost:9000"), encoding="utf-8")
    return config


# ---------------------------------------------------------------------------------------------- one copy at a time

class SingleInstance:
    """A second launch should not start a second server. It asks the first to show its window and quits."""

    def __init__(self, name: str = APP):
        self._keep = None
        self.already = False
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{name}-{os.environ.get('USERNAME', 'user')}")
            self.already = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
            self._keep = handle
        else:  # another system: whoever binds this local port first is the running copy
            sock = socket.socket()
            try:
                sock.bind(("127.0.0.1", 47821))
                self._keep = sock
            except OSError:
                self.already = True


# ---------------------------------------------------------------------------------------------- start with Windows

def _winreg():
    import winreg

    return winreg


def autostart_command() -> str:
    if is_frozen():
        return f'"{sys.executable}" --background'
    return f'"{sys.executable}" -m handoff.desktop --background'


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_READ) as key:
            wr.QueryValueEx(key, APP)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> bool:
    if os.name != "nt":
        return False
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_SET_VALUE) as key:
            if on:
                wr.SetValueEx(key, APP, 0, wr.REG_SZ, autostart_command())
            else:
                try:
                    wr.DeleteValue(key, APP)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------- the window

def find_browser() -> Path | None:
    """Edge ships with Windows 10/11; Chrome is the usual second choice. Either can show a page as a plain app window."""
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
    for relative in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for root in roots:
            if root and (candidate := Path(root) / relative).is_file():
                return candidate
    return None


def screen_size() -> tuple[int, int]:
    if os.name == "nt":
        import ctypes

        return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    return 1920, 1080


def window_box(screen: tuple[int, int], want: tuple[int, int] = (1180, 880)) -> tuple[int, int, int, int]:
    """(width, height, x, y): the size we'd like, kept inside the screen, in the middle of it."""
    width, height = min(want[0], screen[0] - 80), min(want[1], screen[1] - 120)
    return width, height, (screen[0] - width) // 2, max(10, (screen[1] - height) // 2 - 20)


def window_command(browser: Path, url: str, profile: Path, screen: tuple[int, int] | None = None) -> list[str]:
    width, height, x, y = window_box(screen or screen_size())
    return [
        str(browser), f"--app={url}", f"--window-size={width},{height}", f"--window-position={x},{y}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        # Edge offers to "sync your browsing data" with the Windows work account the first time. Not in this window.
        "--disable-sync", "--disable-signin-promo", "--disable-session-crashed-bubble",
        "--disable-features=Translate,msEdgeShopping,msImplicitSignin,msEdgeOnRampFRE,msAutoSignin,msSyncPromo",
    ]


def open_window(url: str, home: Path | None = None) -> None:
    """The PC page in its own window, with no address bar or tabs. Falls back to the normal browser."""
    browser = find_browser() if os.name == "nt" else None
    if browser:
        try:
            subprocess.Popen(window_command(browser, url, (home or home_dir()) / "window"), close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        except OSError:
            pass
    webbrowser.open(url)


# ---------------------------------------------------------------------------------------------- what the tray says

def status_line(owner_ready: bool, phones_online: int, phones_paired: int) -> str:
    if not owner_ready:
        return "Open the app to set it up"
    if phones_online:
        return f"{phones_online} phone{'s' if phones_online != 1 else ''} connected"
    if phones_paired:
        return f"Running · {phones_paired} phone{'s' if phones_paired != 1 else ''} paired"
    return "Running · no phone yet"


def tray_state(owner_ready: bool, phones_online: int, relay_active: bool) -> tuple[str, bool]:
    """(icon colour, show the green dot): alert until set up, bright when something is connected, quiet otherwise."""
    if not owner_ready:
        return "alert", False
    return ("ok", True) if phones_online or relay_active else ("idle", False)


def new_phone_names(known: set[str], devices: list[dict]) -> list[str]:
    return [d["name"] for d in devices if d["id"] not in known and not d.get("blocked")]


class Tray:
    def __init__(self, running, home: Path, quit_app):
        import pystray  # imported here: a missing display must not break the rest of the app

        self._pystray, self._running, self._home, self._quit = pystray, running, home, quit_app
        self._url = running.admin_url
        self._known = {d["id"] for d in running.app.state.accounts.devices()} if running.app.state.accounts else set()
        self._last = None
        self._stop = threading.Event()
        from .branding import tray_icon

        self._make = tray_icon
        self.icon = pystray.Icon(APP, tray_icon("idle"), APP, self._menu())

    # ---- menu
    def _menu(self):
        item, Menu = self._pystray.MenuItem, self._pystray.Menu
        r = self._running
        return Menu(
            item(APP, None, enabled=False),
            item(lambda _: status_line(r.owner_ready(), r.phones_online(), r.phones_paired()), None, enabled=False),
            Menu.SEPARATOR,
            item("Open", self.open, default=True),
            item("Connect a phone…", self.open),
            item(lambda _: self._anywhere_text(), None, enabled=False, visible=lambda _: bool(self._anywhere_text())),
            Menu.SEPARATOR,
            item("Start with Windows", self._toggle_autostart, checked=lambda _: autostart_enabled(), visible=os.name == "nt"),
            item("Open my data folder", self._open_data),
            Menu.SEPARATOR,
            item("Quit", self.quit),
        )

    def _anywhere_text(self) -> str:
        s = self._running.anywhere_status()
        if not s.get("enabled") or s.get("provider") != "relay":
            return ""
        return f"Anywhere: connected to {s.get('name')}" if s.get("active") else f"Anywhere: connecting to {s.get('name')}…"

    # ---- actions
    def open(self, *_):
        open_window(self._url, self._home)

    def _toggle_autostart(self, *_):
        set_autostart(not autostart_enabled())

    def _open_data(self, *_):
        if os.name == "nt":
            os.startfile(self._home)  # noqa: S606 - the person's own folder

    def quit(self, *_):
        self._stop.set()
        self.icon.stop()
        self._quit()

    # ---- keep the icon honest
    def _watch(self):
        r = self._running
        while not self._stop.wait(3):
            try:
                state, online = tray_state(r.owner_ready(), r.phones_online(), bool(r.anywhere_status().get("active")))
                online_count, paired = r.phones_online(), r.phones_paired()
                key = (state, online, online_count, paired, r.owner_ready())
                if key != self._last:
                    self._last = key
                    self.icon.icon = self._make(state, online)
                    self.icon.title = f"{APP}: {status_line(r.owner_ready(), online_count, paired)}"
                    self.icon.update_menu()
                accounts = r.app.state.accounts
                if accounts is not None:
                    devices = accounts.devices()
                    for name in new_phone_names(self._known, devices):
                        self.icon.notify(f"{name} is connected. You can use your PC from it now.", "New phone connected")
                    self._known = {d["id"] for d in devices}
            except Exception:
                logging.getLogger(APP).exception("tray refresh failed")

    def run(self):
        threading.Thread(target=self._watch, name="tray-watch", daemon=True).start()
        self.icon.run()


# ---------------------------------------------------------------------------------------------- the program

def _message(text: str, title: str = APP) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)


def _setup_logging(home: Path) -> None:
    (home / "logs").mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(home / "logs" / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    if sys.stdout is None or sys.stderr is None:  # a windowed program has no console: keep print() and library output from crashing
        log = open(home / "logs" / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP)
    parser.add_argument("--background", action="store_true", help="start quietly in the tray, without opening a window")
    parser.add_argument("--home", help="where settings and data are kept (default: your app-data folder)")
    parser.add_argument("--no-tray", action="store_true", help="run without the tray icon (for testing)")
    args = parser.parse_args(argv)
    if args.home:
        os.environ["ACCESSFROMPHONE_HOME"] = args.home

    home = home_dir()
    config = ensure_home(home)
    _setup_logging(home)
    log = logging.getLogger(APP)

    from .config import load_settings
    from .runtime import start, wait_until_listening

    settings = load_settings(config)
    admin_url = f"http://127.0.0.1:{settings.accounts.admin_port}/"
    instance = SingleInstance()
    if instance.already:  # the app is already running: show its window instead of starting another
        log.info("already running; showing its window")
        open_window(admin_url, home)
        return 0

    try:
        running = start(settings, quiet=True)
    except Exception as exc:
        log.exception("could not start")
        _message(f"{APP} could not start.\n\n{exc}\n\nDetails are in {home / 'logs' / 'app.log'}")
        return 1
    if not wait_until_listening(running):
        running.stop()
        log.error("a port is already in use")
        _message(f"{APP} could not start because port {settings.server.port} or {settings.accounts.admin_port} is already in use.\n\nIs another copy running?")
        return 1
    log.info("started; the PC page is at %s", admin_url)

    first_time = not running.owner_ready()
    if not args.background or first_time:  # started by hand, or nobody has set it up yet
        open_window(running.admin_url, home)

    def finish():
        running.stop()

    if args.no_tray:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            finish()
        return 0
    try:
        tray = Tray(running, home, finish)
    except Exception:
        log.exception("no tray icon available; running without it")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            finish()
        return 0
    if args.background and not first_time:
        threading.Timer(2.5, lambda: tray.icon.notify("It's running in the tray. Click the icon to open it.", APP)).start()
    tray.run()
    finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
