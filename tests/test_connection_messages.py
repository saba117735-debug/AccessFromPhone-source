"""The page and the phone app must say the same thing when the PC can't be reached, and the files they share must really be shared."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
ANDROID = ROOT / "android" / "app"
APP_JS = (WEB / "app.js").read_text(encoding="utf-8")
INDEX = (WEB / "index.html").read_text(encoding="utf-8")

# page reason -> the app's string names (title, text)
SHARED = {
    "phone_offline": ("p_phone_offline_title", "p_phone_offline_text"),
    "other_network": ("p_other_network_title", "p_other_network_text"),
    "app_not_running": ("p_app_not_running_title", "p_app_not_running_text"),
    "pc_unreachable": ("p_pc_unreachable_title", "p_pc_unreachable_text"),
    "pc_offline": ("p_pc_offline_title", "p_pc_offline_text"),
    "name_not_found": ("p_name_not_found_title", "p_name_not_found_text"),
    "address_changed": ("p_address_changed_title", "p_address_changed_text"),
    "pc_error": ("p_pc_error_title", "p_pc_error_text"),
    "certificate": ("problem_certificate", "problem_certificate_hint"),
}


def page_reasons() -> dict:
    return {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r'^\s{4}(\w+): \["([^"]*)", "([^"]*)"', APP_JS, re.M)}


def app_strings() -> dict:
    xml = (ANDROID / "src/main/res/values/strings.xml").read_text(encoding="utf-8")
    out = {}
    for name, value in re.findall(r'<string name="([^"]+)">(.*?)</string>', xml):
        out[name] = value.replace("\\'", "'").replace("&amp;", "&").replace("&lt;", "<")
    return out


def test_the_page_and_the_app_use_the_same_words():
    reasons, strings = page_reasons(), app_strings()
    for reason, (title_name, text_name) in SHARED.items():
        assert reason in reasons, f"the page has no '{reason}'"
        assert strings[title_name] == reasons[reason][0], f"{reason}: title differs"
        assert strings[text_name] == reasons[reason][1], f"{reason}: text differs"


def test_every_reason_the_app_can_report_has_a_page_message():
    kinds = re.findall(r"^\s{8}([A-Z_]+),", (ANDROID / "src/main/java/app/accessfromphone/Diagnosis.kt").read_text(encoding="utf-8"), re.M)
    assert kinds, "could not read the app's reasons"
    mapped = set(re.findall(r'(\w+): "(\w+)"', APP_JS[APP_JS.index("const NATIVE_CAUSE"):APP_JS.index("// A notice from the PC only")]))
    assert {k for k, _ in mapped} == set(kinds), "the page does not understand every reason the app reports"
    for _, cause in mapped:
        assert cause in page_reasons(), cause


def test_every_notice_the_pc_can_send_has_a_page_message():
    from handoff.push import Notifier

    reasons = page_reasons()
    informational = {"pc_plugged", "pc_unplugged"}  # they say what the charger did; they never explain a silence, so the popup has no words for them
    for kind in Notifier.PC_TEXT:
        if kind in informational:
            assert kind not in reasons, f"{kind} would be remembered as the reason for an outage"
            continue
        assert kind in reasons, f"the PC can send {kind} but the popup has no words for it"


def test_the_popup_never_offers_to_dismiss_itself():
    start = APP_JS.index("function buildLost()")
    block = APP_JS[start:APP_JS.index("function paintLost", start)]
    assert "Escape" not in APP_JS[start:start + 6000]
    for word in ("Dismiss", "Close", "Cancel", "Later", "Ignore"):
        assert word not in block, f"the popup offers '{word}'"
    assert 'role: "alertdialog"' in block and '"aria-modal": "true"' in block


def test_storage_helpers_used_by_the_page_all_exist():
    defined = set(re.findall(r"^\s{4}(\w+)\(", APP_JS[APP_JS.index("const store = {"):APP_JS.index("const DEFAULTS")], re.M))
    used = set(re.findall(r"\bstore\.(\w+)\(", APP_JS))
    assert used <= defined, f"the page calls store.{sorted(used - defined)}, which does not exist"


def test_every_script_and_style_the_page_loads_is_served_inside_the_app_too():
    shipped = set(re.findall(r'"static/([\w.]+)" to', (ANDROID / "src/main/java/app/accessfromphone/LocalPage.kt").read_text(encoding="utf-8")))
    needed = set(re.findall(r'<script src="/static/([\w.]+)"', INDEX)) | set(re.findall(r'<link rel="stylesheet" href="/static/([\w.]+)"', INDEX))
    assert needed <= shipped, f"the app's built-in page would be missing {sorted(needed - shipped)}"
    gradle = (ANDROID / "build.gradle.kts").read_text(encoding="utf-8")
    copied = set(re.findall(r'"([\w.]+)"', gradle[gradle.index("copyPage"):gradle.index("copyPage") + 300]))
    assert (needed | {"index.html"}) <= copied, f"the app build would not copy {sorted((needed | {'index.html'}) - copied)}"


def test_the_connection_monitor_loads_before_the_page_uses_it():
    assert INDEX.index("/static/conn.js") < INDEX.index("/static/app.js")
    assert "/static/conn.js" in (WEB / "sw.js").read_text(encoding="utf-8")  # and works offline too


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_connection_monitor_timing_cases():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "conn_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
