"""The picture screens: their files are served, everything they refer to exists, and their maths passes its own tests."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
UI = (WEB / "imaging.js").read_text(encoding="utf-8")
CSS = (WEB / "imaging.css").read_text(encoding="utf-8")
APP_CSS = (WEB / "app.css").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")


def test_the_picture_files_are_served_to_the_page(settings):
    client = TestClient(create_app(settings))
    for name in ("imaging.js", "imaging-core.js", "imaging.css"):
        res = client.get(f"/static/{name}")
        assert res.status_code == 200 and len(res.content) > 1000, name
        assert res.headers["cache-control"] == "no-cache"  # an update reaches the phone at once


def test_the_page_loads_exactly_the_files_that_exist():
    loaded = re.findall(r'"/static/(imaging[\w.-]*\.(?:js|css))"', APP)
    assert sorted(loaded) == ["imaging-core.js", "imaging.css", "imaging.js"]
    for name in loaded:
        assert (WEB / name).is_file(), name
    assert APP.index("/static/imaging-core.js") < APP.index("/static/imaging.js")  # the maths first: the screens use it


def test_every_icon_the_picture_screens_use_is_drawn():
    block = UI[UI.index("var ICONS = {"):UI.index("function icon(")]
    drawn = set(re.findall(r"^\s{4}(\w+):", block, re.M))
    tools = UI[UI.index("var TOOLS = ["):UI.index("];", UI.index("var TOOLS = ["))]  # [id, icon, label] for each tool in the bar
    used = set(re.findall(r'\bicon\("(\w+)"\)', UI)) | set(re.findall(r'iconButton\(ctx, "(\w+)"', UI)) | {t[1] for t in re.findall(r'\["(\w+)", "(\w+)", "[\w ]+"\]', tools)}
    assert used and used <= drawn, f"icons used but not drawn: {sorted(used - drawn)}"


def test_every_style_class_the_picture_screens_use_has_a_style():
    used = set()
    for found in re.findall(r'class: "([^"]+)"', UI):
        used.update(c for c in found.split() if re.match(r"^(gl|iv|ed|im)-", c))
    used.update(re.findall(r'"(im-[\w-]+|gl-[\w-]+|iv-[\w-]+|ed-[\w-]+)"', UI))
    styles = CSS + APP_CSS
    missing = sorted(c for c in used if not re.search(r"\." + re.escape(c) + r"(?![\w-])", styles))
    assert not missing, f"classes with no style: {missing}"


def test_the_screens_never_write_into_the_page_from_a_file_name():
    # file names come from the PC's disk: they must only ever reach the page as text, never as markup
    assert "innerHTML" in UI and UI.count("innerHTML") == 1  # only the fixed icon drawings
    assert "insertAdjacentHTML" not in UI and "document.write" not in UI and "eval(" not in UI


def test_the_screens_only_talk_to_the_pc_through_the_apps_own_helpers():
    assert "fetch(" not in UI and "XMLHttpRequest" not in UI  # every request goes through ctx.get / ctx.putImage, which carry the phone's token
    assert "localStorage" not in UI


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_picture_maths_passes_its_own_tests():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "imaging_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
