import os
import sys
import types

import pytest

from handoff import branding, desktop
from handoff.config import load_settings


# ---------------------------------------------------------------- where things live

def test_the_data_folder_can_be_chosen_and_is_separate_from_the_install(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESSFROMPHONE_HOME", str(tmp_path / "mine"))
    assert desktop.home_dir() == tmp_path / "mine"
    monkeypatch.delenv("ACCESSFROMPHONE_HOME")
    assert desktop.home_dir().name == "AccessFromPhone"
    assert str(desktop.home_dir()) != os.path.dirname(sys.executable)


def test_a_first_run_makes_a_working_config_and_a_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESSFROMPHONE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")  # the test suite turns accounts off; a real first run has them on
    config = desktop.ensure_home(tmp_path / "home")
    assert config == tmp_path / "home" / "config.yaml"
    settings = load_settings(config)  # it must be a config the app accepts, with nothing to edit first
    folder = settings.agent.allowed_folders[0]
    assert folder.is_dir() and (folder / "Welcome.txt").is_file()
    assert settings.accounts.enabled and settings.server.https and settings.relay.enabled is False
    assert settings.storage.database == (tmp_path / "home" / "data" / "app.db").resolve()  # data lands beside the config, not in the install folder


def test_an_existing_config_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESSFROMPHONE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.yaml").write_text("# mine\n", encoding="utf-8")
    desktop.ensure_home(tmp_path / "home")
    assert (tmp_path / "home" / "config.yaml").read_text(encoding="utf-8") == "# mine\n"


def test_a_relay_address_baked_in_at_build_time_switches_it_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESSFROMPHONE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(desktop, "build_value", lambda name, default="": "https://example.com" if name == "RELAY_URL" else default)
    settings = load_settings(desktop.ensure_home(tmp_path / "home"))
    assert settings.relay.enabled is True and settings.relay.url == "https://example.com"


def test_a_workspace_path_with_spaces_and_backslashes_survives_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESSFROMPHONE_HOME", str(tmp_path / "home with spaces"))
    settings = load_settings(desktop.ensure_home(tmp_path / "home with spaces"))
    assert settings.agent.allowed_folders[0].is_dir()


# ---------------------------------------------------------------- one copy at a time

@pytest.mark.skipif(os.name != "nt", reason="Windows mutex")
def test_a_second_copy_notices_the_first(monkeypatch):
    monkeypatch.setenv("USERNAME", "test-user-for-the-mutex")
    first = desktop.SingleInstance("AccessFromPhoneTest")
    second = desktop.SingleInstance("AccessFromPhoneTest")
    assert not first.already and second.already


# ---------------------------------------------------------------- start with Windows

class FakeRegistry(types.SimpleNamespace):
    """Just enough of winreg to see what the app writes."""

    def __init__(self):
        super().__init__(HKEY_CURRENT_USER=1, KEY_READ=1, KEY_SET_VALUE=2, REG_SZ=1, values={})

    def OpenKey(self, root, sub, reserved, access):
        outer = self

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        assert sub == desktop.RUN_KEY and root == 1
        return Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.mark.skipif(os.name != "nt", reason="Windows registry")
def test_start_with_windows_adds_and_removes_one_run_entry(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(desktop, "_winreg", lambda: fake)
    assert not desktop.autostart_enabled()
    assert desktop.set_autostart(True) and desktop.autostart_enabled()
    assert fake.values[desktop.APP].endswith("--background")  # logs in quietly, no window
    assert desktop.set_autostart(False) and not desktop.autostart_enabled()
    assert desktop.set_autostart(False)  # turning it off twice is fine


def test_the_autostart_command_quotes_the_program_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\Jo Doe\AppData\Local\Programs\AccessFromPhone\AccessFromPhone.exe")
    assert desktop.autostart_command() == '"C:\\Users\\Jo Doe\\AppData\\Local\\Programs\\AccessFromPhone\\AccessFromPhone.exe" --background'


# ---------------------------------------------------------------- the window

def test_the_window_is_a_plain_app_window_with_its_own_clean_profile(tmp_path):
    cmd = desktop.window_command(tmp_path / "msedge.exe", "http://127.0.0.1:8766/", tmp_path / "window", screen=(1920, 1080))
    assert cmd[0].endswith("msedge.exe") and "--app=http://127.0.0.1:8766/" in cmd
    assert f"--user-data-dir={tmp_path / 'window'}" in cmd and "--no-first-run" in cmd
    assert "--disable-sync" in cmd and any(a.startswith("--disable-features=") and "msImplicitSignin" in a for a in cmd)  # no "sync your data" pop-up
    assert "--window-size=1180,880" in cmd and "--window-position=370,80" in cmd  # in the middle of a 1080p screen


def test_the_window_always_fits_the_screen():
    assert desktop.window_box((1366, 768)) == (1180, 648, 93, 40)
    assert desktop.window_box((1024, 600))[:2] == (944, 480)
    w, h, x, y = desktop.window_box((800, 500))
    assert w <= 720 and h <= 380 and x >= 0 and y >= 0


# ---------------------------------------------------------------- what the tray says

@pytest.mark.parametrize("owner, online, paired, text", [
    (False, 0, 0, "Open the app to set it up"),
    (True, 0, 0, "Running · no phone yet"),
    (True, 0, 1, "Running · 1 phone paired"),
    (True, 0, 3, "Running · 3 phones paired"),
    (True, 1, 3, "1 phone connected"),
    (True, 2, 3, "2 phones connected"),
])
def test_the_tray_line(owner, online, paired, text):
    assert desktop.status_line(owner, online, paired) == text


def test_the_icon_colour_follows_what_is_happening():
    assert desktop.tray_state(False, 0, False) == ("alert", False)  # not set up
    assert desktop.tray_state(True, 0, False) == ("idle", False)  # running, nobody there
    assert desktop.tray_state(True, 1, False) == ("ok", True)  # a phone is connected
    assert desktop.tray_state(True, 0, True) == ("ok", True)  # or the relay is


def test_a_new_phone_is_announced_once_and_a_blocked_one_never():
    seen = {"a"}
    devices = [{"id": "a", "name": "Old", "blocked": False}, {"id": "b", "name": "Afza's iPhone", "blocked": False}, {"id": "c", "name": "Bad", "blocked": True}]
    assert desktop.new_phone_names(seen, devices) == ["Afza's iPhone"]


# ---------------------------------------------------------------- the pictures

@pytest.mark.parametrize("state", ["ok", "idle", "alert"])
def test_every_icon_state_draws_at_every_size(state):
    for size in (16, 32, 64, 256):
        image = branding.logo(size, state, dot="green")
        assert image.size == (size, size) and image.getbbox() is not None  # something was drawn
    assert branding.app_icon(256).size == (256, 256) and branding.tray_icon("ok", True).size == (64, 64)


# ---------------------------------------------------------------- one name everywhere

def test_the_product_is_called_accessfromphone_everywhere_a_person_looks():
    from pathlib import Path
    web = Path(desktop.__file__).parent / "web"
    for name in ("index.html", "admin.html", "manifest.webmanifest", "app.js"):
        text = (web / name).read_text(encoding="utf-8")
        assert "Afdani" not in text and "AccessFromPhone" in text, name
    assert desktop.APP == "AccessFromPhone"