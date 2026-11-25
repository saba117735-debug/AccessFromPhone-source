"""The page's design layer and the everyday tools' files: served, loaded in the right order and on demand, styled, listed for offline use, safe, and in step with the
routes and icons they use."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
read = lambda name: (WEB / name).read_text(encoding="utf-8")  # noqa: E731
APP, DAILY, DAILY_CSS, UX, GLASS, APP_CSS, INDEX, SW = (read(n) for n in ("app.js", "daily.js", "daily.css", "ux.js", "glass.css", "app.css", "index.html", "sw.js"))
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
NODE = shutil.which("node") is not None


# ---- serving and loading ----------------------------------------------------------------------------------------------------

def test_the_files_are_served_and_always_re_checked(settings):
    client = TestClient(create_app(settings))
    for name, kind in (("daily.js", "javascript"), ("daily.css", "css"), ("ux.js", "javascript"), ("glass.css", "css")):
        res = client.get(f"/static/{name}")
        assert res.status_code == 200 and len(res.content) > 1500 and kind in res.headers["content-type"] and res.headers["cache-control"] == "no-cache", name


def test_the_files_are_clean_utf_8_with_no_byte_order_mark_and_no_scrambled_characters():
    for name in ("daily.js", "daily.css", "ux.js", "glass.css", "app.js", "index.html"):
        raw = (WEB / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), name
        text = raw.decode("utf-8")
        assert not any(marker in text for marker in ("\u00c2\u00b7", "\u00e2\u20ac\u00a6", "\u00c3\u2014")), name   # "Â·", "â€¦", "Ã—": UTF-8 read as Windows-1252 and saved again


def test_the_design_layer_comes_after_the_page_style_and_the_helpers_before_the_page_script():
    assert INDEX.index("/static/app.css") < INDEX.index("/static/glass.css")      # it overrides app.css, so it must come second
    assert INDEX.index("/static/ux.js") < INDEX.index("/static/app.js")            # the page asks for AfpUx when it starts
    assert "ux.js" in INDEX and INDEX.count("glass.css") == 1


def test_the_tools_are_fetched_when_home_has_the_pcs_answer_not_at_start_up():
    assert "daily.js" not in INDEX and "daily.css" not in INDEX   # a PC with the helpers off never downloads them
    loader = APP[APP.index("function loadDaily()"):APP.index("const dailyOn")]
    assert '"/static/daily.css"' in loader and '"/static/daily.js"' in loader and "AfpDaily.create" in loader and "token: () => S.token" in APP
    assert "dailyOn()" in APP[APP.index("S.homeSig = sig;"):APP.index("S.homeSig = sig;") + 200]
    assert "Daily.homeCard()" in APP and "S.info.daily" in APP


def test_the_design_layer_works_offline_and_in_the_phone_app():
    for name in ("glass.css", "ux.js"):
        assert f'"/static/{name}"' in SW, name          # a browser that opens the page with the PC off still has it
        assert f'"{name}"' in GRADLE, name              # and the app's built-in copy of the page
        assert f'"static/{name}" to "{name}"' in (ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "accessfromphone" / "LocalPage.kt").read_text(encoding="utf-8"), name
    assert "daily.js" not in GRADLE and "daily.css" not in GRADLE   # fetched from the PC like the folder browser; the built-in page only has the connect screen


# ---- safety -----------------------------------------------------------------------------------------------------------------

def test_nothing_from_the_pc_is_written_into_the_page_as_markup():
    for source in (DAILY, DAILY_CSS):
        assert "innerHTML" not in source and "eval(" not in source and "document.write" not in source and "insertAdjacentHTML" not in source
    uses = [line for line in UX.splitlines() if "innerHTML" in line]
    assert len(uses) == 2 and all("ICON[name]" in line or "ART[name]" in line for line in uses), uses   # only the fixed icon and picture strings written in that file
    assert "eval(" not in UX and "document.write" not in UX and "insertAdjacentHTML" not in UX


# ---- in step with the rest of the page ------------------------------------------------------------------------------------------

def test_every_route_the_tools_call_exists():
    routes_source = (ROOT / "src" / "handoff" / "daily" / "routes.py").read_text(encoding="utf-8")
    routes = set(re.findall(r'@api\.(?:get|post|put|delete)\("(/daily[^"]*)"', routes_source))
    called = set(re.findall(r'call\("(/[a-z/-]+)', DAILY)) | {p for p in re.findall(r'd\.api\("/daily(/[a-z/-]+)', DAILY)} | {p for p in re.findall(r'"/api/daily(/[a-z/-]+)', DAILY)}
    assert len(called) >= 18 and len(routes) >= 29
    for path in called:
        stem = "/daily" + path.rstrip("/")
        assert any(r == stem or r.startswith(stem + "/") for r in routes), path


def test_every_style_the_tools_use_is_defined():
    used = set(re.findall(r'"(dy-[a-z]+)', DAILY)) | set(re.findall(r'class: "(?:[a-z-]+ )*(dy-[a-z]+)', DAILY)) | set(re.findall(r'classList\.\w+\("(dy-[a-z]+)"', DAILY))
    assert len(used) >= 40
    for cls in sorted(used):
        assert "." + cls in DAILY_CSS, cls
    helper_classes = set(re.findall(r'el\("\w+", "([a-z][a-z0-9 -]+)"', UX)) | set(re.findall(r'classList\.add\("([a-z-]+)"\)', UX))
    for group in helper_classes:
        for cls in group.split():
            assert "." + cls in GLASS or cls in ("shim", "dot", "ring"), cls


def test_opening_chat_is_wired_to_the_paint_and_every_part_of_it_is_styled():
    tab = APP[APP.index("function tabTo(key)"):APP.index("function paintTabs()")]
    assert "AfpUx.paint" in tab and 'onCovered: () => setTab("chat")' in tab and "motionLevel()" in tab   # the page switches only once the paint covers it
    assert "!calmMotion()" in tab                                                                       # no paint for reduce-motion or Motion: Off
    for cls in ("pnt", "pnt-cv", "pnt-sheen", "pnt-title", "pnt-ic", "is-in", "is-titled", "is-wiping", "is-fading", "is-gone", "is-flat"):
        assert "." + cls in GLASS, cls
    assert "perspective" in GLASS[GLASS.index(".pnt {"):GLASS.index(".pnt {") + 400]                    # the 3D part
    assert "safety" in UX and "pointerdown" in UX[UX.index("function paint("):]                        # a hidden tab and a tap both let go of the page


def test_every_tab_that_opens_a_screen_has_its_own_paint_and_every_part_of_it_is_styled():
    tab = APP[APP.index("function tabTo(key)"):APP.index("function paintTabs()")]
    for theme, key in (("pc", "access"), ("files", "files"), ("settings", "settings")):
        assert f'{key}: ["{theme}"' in APP[APP.index("const TAB_PAINT"):APP.index("function tabTo(key)")], key
    assert "onCovered: opener" in tab and "!calmMotion()" in tab         # the screen opens only once the paint covers the page; no paint for reduce-motion or Motion: Off
    for style in ("scan", "stack", "swirl"):
        assert f"function {style}Plan(" in UX and f".pnt.k-{style} .pnt-cv" in GLASS, style
        assert f'style: "{style}"' in UX, style
    for cls in ("is-top",):
        assert "." + cls in GLASS
    assert re.search(r"\.pnt\.is-top \{ z-index: (\d+)", GLASS) and int(re.search(r"\.pnt\.is-top \{ z-index: (\d+)", GLASS).group(1)) > 60   # over the sheets (60) that Settings and the PC open into
    assert "if (active) active.skip();" in UX                                                         # a second tap finishes the first paint (its screen opens) instead of being lost


def test_the_swipe_row_does_not_collide_with_the_accent_swatch_class():
    assert re.search(r"(?m)^\.sw[ .:{+\[]", APP_CSS)                       # the Settings accent swatch is a 40px circle called .sw
    assert not re.search(r"(?m)^\s*\.sw[ .:{+\[,]", GLASS) and not re.search(r'"sw[ "-]', UX)
    assert ".swp" in GLASS and '"swp"' in UX


def test_the_aurora_is_switched_back_on_because_the_page_style_hides_it():
    assert "body::before { display: none; }" in APP_CSS   # the old grid was removed this way
    assert re.search(r"body::before\s*\{\s*display:\s*block", GLASS)


def test_the_design_layer_has_a_fallback_for_every_limit():
    assert "@supports not ((backdrop-filter: blur(1px))" in GLASS      # a phone that cannot blur gets solid panels
    assert 'data-motion="simple"' in GLASS and 'data-motion="off"' in GLASS and "prefers-reduced-motion" in GLASS
    assert 'data-theme="light"' in GLASS
    for source in (DAILY_CSS,):
        assert "prefers-reduced-motion" in source and 'data-motion="off"' in source
    assert "prefers-reduced-motion" in UX and "motion === \"off\"" in UX     # the gesture helpers calm down too


def test_only_cheap_properties_animate():
    for keyframes in re.findall(r"@keyframes [\w-]+ \{(.*?)\n?\}\s*(?=\n|@|\.|:)", GLASS + DAILY_CSS, re.S):
        for forbidden in ("width:", "height:", "top:", "left:", "margin", "padding", "box-shadow:", "filter:", "background"):
            assert forbidden not in keyframes, (forbidden, keyframes[:120])


def test_controls_are_comfortable_to_touch():
    assert re.search(r"\.dy-slider \{[^}]*height: 60px", DAILY_CSS) and re.search(r"\.dy-media \{[^}]*width: 52px", DAILY_CSS)
    assert re.search(r"\.dy-chip \{[^}]*min-height: 40px", DAILY_CSS) and re.search(r"\.btn \{[^}]*min-height: 46px", GLASS)
    assert re.search(r"\.dy-tic \{[^}]*width: 54px", DAILY_CSS)


def test_colours_in_the_tools_come_from_the_pages_theme_not_fixed_values():
    stripped = re.sub(r"rgba?\([^)]*\)", "", DAILY_CSS.replace("background: #000", "").replace("color: #fff", "").replace("#fff;", ""))
    stripped = re.sub(r"--c[12]: #[0-9a-fA-F]{3,6}", "", stripped)
    assert "var(--g-fill)" in DAILY_CSS and "var(--text)" in DAILY_CSS and "var(--a1)" in DAILY_CSS
    assert not re.findall(r"(?<![\w-])#[0-9a-fA-F]{3,6}\b", stripped.replace("#94a3b8", "").replace("#64748b", "").replace("#fb7185", "").replace("#ef4444", "").replace("#fbbf24", "").replace("#f472b6", "").replace("#a78bfa", "").replace("#22d3ee", "")), "a fixed colour outside the tile tints"


# ---- their own tests ----------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not NODE, reason="needs Node.js")
@pytest.mark.parametrize("name", ["daily_test.js", "ux_test.js"])
def test_the_wording_gestures_and_home_rules_pass_their_own_tests(name):
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / name)], capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stdout + done.stderr
