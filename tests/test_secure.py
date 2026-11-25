"""The data folder is closed to other accounts on this PC, and a PC where that cannot be done is never broken by trying."""
import subprocess
import sys

import pytest

from handoff import secure

BS = chr(92)  # a backslash


def test_the_command_drops_inherited_rights_and_keeps_only_this_user_and_the_system(tmp_path):
    cmd = secure.acl_command(tmp_path, "PCNAME" + BS + "asha")
    assert cmd[0] == "icacls" and "/inheritance:r" in cmd and "/T" not in cmd
    assert "PCNAME" + BS + "asha:(OI)(CI)F" in cmd and f"{secure.SYSTEM_SID}:(OI)(CI)F" in cmd
    assert not any("Everyone" in part or "Users" in part for part in cmd[2:])  # (the folder itself, cmd[1], may be under C:\Users)


def test_a_missing_folder_or_a_failing_tool_is_not_an_error(tmp_path, monkeypatch):
    assert secure.restrict_to_current_user(tmp_path / "nope") is False
    monkeypatch.setattr(secure.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no icacls")))
    monkeypatch.setattr(secure.sys, "platform", "win32")
    assert secure.restrict_to_current_user(tmp_path) is False


def test_it_runs_the_tool_without_a_shell(tmp_path, monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(secure.subprocess, "run", fake)
    monkeypatch.setattr(secure.sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "PC")
    monkeypatch.setenv("USERNAME", "asha")
    assert secure.restrict_to_current_user(tmp_path) is True
    assert isinstance(seen["cmd"], list) and not seen["kwargs"].get("shell")  # a folder name can never become a command


@pytest.mark.skipif(sys.platform != "win32", reason="Windows access lists")
def test_on_windows_other_accounts_really_lose_access_and_this_user_keeps_it(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "app.db").write_text("x", encoding="utf-8")
    assert secure.restrict_to_current_user(folder) is True
    listing = subprocess.run(["icacls", str(folder)], capture_output=True, text=True).stdout
    assert "BUILTIN" + BS + "Users" not in listing and "Everyone" not in listing and "Authenticated Users" not in listing
    assert "NT AUTHORITY" + BS + "SYSTEM" in listing and secure._who().split(BS)[-1].lower() in listing.lower()
    assert (folder / "app.db").read_text(encoding="utf-8") == "x"  # and this user can still use it
    (folder / "new.txt").write_text("y", encoding="utf-8")
