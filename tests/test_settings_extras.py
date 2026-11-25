"""Settings extras: privacy and support links, the whole command on an approval card, and the wording about the safety filter."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.config import LinksConfig
from handoff.planner import Action, Step
from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src" / "handoff" / "web" / "app.js").read_text(encoding="utf-8")


def test_links_are_https_only_and_optional():
    assert LinksConfig().privacy_url == ""
    assert LinksConfig(privacy_url=" https://example.com/privacy ").privacy_url == "https://example.com/privacy"
    for bad in ("http://example.com", "javascript:alert(1)", "https://exa mple.com", "ftp://x"):
        with pytest.raises(ValueError):
            LinksConfig(support_url=bad)


def test_info_carries_the_links(settings):
    app = create_app(settings)
    client = TestClient(app)
    client.headers.update({"X-Token": app.state.token})
    assert client.get("/api/info").json()["links"] == {"privacy": "", "support": ""}


def test_an_approval_card_shows_the_whole_command():
    long_command = "echo start; " + "x" * 900 + "; Remove-Item evil"
    detail = Step(action=Action.RUN, path=".", content=long_command).detail()
    assert long_command in detail and detail.endswith("(cannot be undone)")  # not cut at 300: the end of a command is what matters most


def test_the_page_calls_the_filter_a_speed_bump_not_a_sandbox():
    assert "speed bump" in APP and "sandbox" in APP


def test_settings_has_help_queue_and_forget_rows_and_no_second_status_timer():
    assert "copyDiagnostics" in APP and "clearQueue" in APP and "Forget this PC" in APP and "links.privacy" in APP
    diag = APP[APP.index("function diagnosticsText("):APP.index("async function copyDiagnostics(")]
    assert "S.token" not in diag and "prompt" not in diag  # nothing secret or personal goes into what is copied for support
    assert "PC.timer" not in APP  # the status sheet shares the page's own 2-second refresh


def test_undo_says_what_it_cannot_restore():
    assert "can't take back a command, a file already sent to your phone" in APP and "undo-note" in APP


def test_the_page_reads_the_pairing_code_after_the_hash_and_still_accepts_the_old_form():
    assert "pairInHash" in APP and 'urlParams.get("pair")' in APP
    assert "pairInHash ? \"\" : location.hash" in APP  # the code leaves the address bar either way
