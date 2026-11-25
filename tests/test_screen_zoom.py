"""Zooming asks the PC for the zoomed part at full detail (cut out BEFORE shrinking), so it stays sharp. No real screen is used."""
import io

import pytest
from PIL import Image

from handoff.screen import capture


def fake_screen(monkeypatch, width=1920, height=1080):
    """A screen whose left half is red and right half blue, with a one-pixel black and white check in the top-right corner (fine detail that blurs if enlarged)."""
    image = Image.new("RGB", (width, height), (200, 0, 0))
    for x in range(width // 2, width):
        for y in range(height):
            image.putpixel((x, y), (0, 0, 200))
    for x in range(1500, 1560):
        for y in range(40, 100):
            image.putpixel((x, y), (255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0))
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda all_screens=False: image.copy())
    monkeypatch.setattr(capture, "_fast_failed", True)      # this test supplies its own screen: the ordinary capture, one picture per call
    monkeypatch.setattr(capture, "FRAME_SOURCE", False)
    monkeypatch.setattr(capture, "pointer_position", lambda: None)
    monkeypatch.setattr(capture, "cursor_handle", lambda: capture.CURSOR_ARROW)   # not whatever the real pointer happens to look like (or be hidden) while the test runs: the plain arrow
    return image


def decode(data):
    return Image.open(io.BytesIO(data))


def test_a_region_is_cut_out_of_the_full_detail_screen_and_not_enlarged(monkeypatch):
    fake_screen(monkeypatch)
    out = decode(capture.grab_jpeg(1280, 80, pointer=False, region=(0.5, 0.0, 0.5, 0.5)))
    assert out.size == (960, 540)  # a quarter of the screen at its own pixels: the PC does not send more pixels than exist
    r, g, b = out.getpixel((10, 270))
    assert b > 150 and r < 60  # only the right (blue) half is in it


def test_a_zoomed_picture_keeps_detail_an_enlarged_whole_screen_loses(monkeypatch):
    fake_screen(monkeypatch)
    # the check pattern is 1 pixel wide. Whole screen shrunk to the phone's width: the pattern turns to grey mush.
    whole = decode(capture.grab_jpeg(640, 90, pointer=False)).convert("L")
    patch_whole = [whole.getpixel((x, y)) for x in range(500, 520) for y in range(13, 33)]
    # the same patch asked for as a region at the same width keeps real black and white
    zoom = decode(capture.grab_jpeg(640, 90, pointer=False, region=(1500 / 1920, 40 / 1080, 60 / 1920, 60 / 1080))).convert("L")
    patch_zoom = [zoom.getpixel((x, y)) for x in range(0, zoom.width) for y in range(0, zoom.height)]
    assert max(patch_whole) - min(patch_whole) < 90  # blurred
    assert max(patch_zoom) - min(patch_zoom) > 150   # sharp


def test_the_pointer_is_drawn_in_the_right_place_of_the_cut_out_picture(monkeypatch):
    fake_screen(monkeypatch)
    monkeypatch.setattr(capture, "pointer_position", lambda: (1000, 100))  # 40 px right and 100 px down of the region's corner
    out = decode(capture.grab_jpeg(1280, 85, pointer=True, region=(960 / 1920, 0.0, 0.5, 0.5))).convert("RGB")
    assert max(out.getpixel((x, y))[0] for x in range(40, 48) for y in range(104, 112)) > 200  # the arrow's white body
    outside = decode(capture.grab_jpeg(1280, 85, pointer=True, region=(0.75, 0.5, 0.25, 0.5))).convert("RGB")  # the pointer is not in this part
    assert all(outside.getpixel((x, y))[0] < 60 for x in range(0, outside.width, 40) for y in range(0, outside.height, 40))


@pytest.mark.parametrize("bad", [None, (), (0, 0, 1, 1), ("a", 0, 1, 1), (float("nan"), 0, 0.5, 0.5), (0, 0, float("inf"), 0.5)])
def test_a_missing_or_silly_region_means_the_whole_screen(bad):
    assert capture.clamp_region(bad) is None


def test_a_region_is_kept_inside_the_screen_and_no_smaller_than_a_twentieth():
    x, y, w, h = capture.clamp_region((0.9, -3, 0.5, 0.5))
    assert 0 <= x and x + w <= 1.0 + 1e-9 and y == 0 and w == 0.5
    assert capture.clamp_region((0.2, 0.2, 0.0001, 0.0001))[2:] == (capture.MIN_REGION, capture.MIN_REGION)


# ---- the pointer's real shape (Windows only: it asks Windows which cursor is showing) -------------------------------------------------------------

import sys  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the cursor's shape is read from Windows")


def system_cursor(name):
    import ctypes

    user32 = ctypes.windll.user32
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.LoadCursorW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    ids = {"arrow": 32512, "ibeam": 32513, "wait": 32514, "cross": 32515, "hand": 32649, "size_we": 32644, "move": 32646, "no": 32648}
    return user32.LoadCursorW(None, ids[name])


@windows_only
def test_every_pointer_shape_can_be_drawn_with_its_hot_spot():
    seen = set()
    for name in ("arrow", "ibeam", "wait", "cross", "hand", "size_we", "move", "no"):
        picture, hx, hy = capture.render_cursor(system_cursor(name))
        assert picture.mode == "RGBA" and picture.width >= 8 and picture.height >= 8
        assert 0 <= hx < picture.width and 0 <= hy < picture.height, name           # the hot spot (the pixel that clicks) is inside the picture
        alpha = picture.getchannel("A")
        solid = sum(1 for v in alpha.get_flattened_data() if v > 128) if hasattr(alpha, "get_flattened_data") else sum(1 for v in alpha.getdata() if v > 128)
        assert solid > 30, name                                                      # it is really drawn, not empty
        assert alpha.getpixel((0, 0)) == 0 or name == "arrow"                        # and has see-through parts (the corner of most shapes is empty)
        seen.add(picture.tobytes())
    assert len(seen) == 8                                                            # eight different shapes, not one arrow eight times


@windows_only
def test_the_text_bar_is_visible_on_a_light_and_on_a_dark_page():
    picture, _, _ = capture.render_cursor(system_cursor("ibeam"))
    colours = {px[:3] for px in (picture.get_flattened_data() if hasattr(picture, "get_flattened_data") else picture.getdata()) if px[3] > 200}
    assert (255, 255, 255) in colours and any(max(c) < 60 for c in colours)         # white with a dark edge: seen on anything (the real one inverts the page)


@windows_only
def test_the_picture_of_the_screen_has_the_pointer_the_pc_really_has(monkeypatch):
    fake_screen(monkeypatch)
    monkeypatch.setattr(capture, "pointer_position", lambda: (400, 300))
    capture._shapes.clear()

    def shot(handle):
        monkeypatch.setattr(capture, "cursor_handle", lambda: handle)
        return decode(capture.grab_jpeg(1920, 95, pointer=True)).convert("RGB").crop((380, 280, 450, 350))

    arrow, text_bar, hand = shot(system_cursor("arrow")), shot(system_cursor("ibeam")), shot(system_cursor("hand"))
    hidden = shot(None)
    nothing = decode(capture.grab_jpeg(1920, 95, pointer=False)).convert("RGB").crop((380, 280, 450, 350))
    assert arrow.tobytes() != text_bar.tobytes() != hand.tobytes() and arrow.tobytes() != hand.tobytes()   # a different pointer over different things, as on the PC
    assert hidden.tobytes() == nothing.tobytes()                                                           # a hidden pointer is not drawn


@windows_only
def test_when_the_shape_cannot_be_read_the_old_arrow_is_drawn(monkeypatch):
    fake_screen(monkeypatch)
    monkeypatch.setattr(capture, "pointer_position", lambda: (400, 300))
    monkeypatch.setattr(capture, "cursor_handle", lambda: capture.CURSOR_ARROW)
    drawn = decode(capture.grab_jpeg(1920, 95, pointer=True)).convert("RGB").crop((380, 280, 450, 350))
    bare = decode(capture.grab_jpeg(1920, 95, pointer=False)).convert("RGB").crop((380, 280, 450, 350))
    assert drawn.tobytes() != bare.tobytes()


def test_only_shapes_the_pc_has_shown_can_be_asked_for():
    capture._shapes.clear()
    assert capture.cursor_shape(123456) is None and capture.cursor_shape("c1") is None and capture.cursor_shape(None) is None
    assert capture.remember_cursor("not a handle") is False and capture.remember_cursor(None) is False


def test_high_quality_keeps_full_colour_detail_and_ordinary_quality_does_not(monkeypatch):
    from PIL import JpegImagePlugin

    fake_screen(monkeypatch)
    sharp = decode(capture.grab_jpeg(1920, capture.SHARP_FROM, pointer=False))
    ordinary = decode(capture.grab_jpeg(1920, capture.SHARP_FROM - 5, pointer=False))
    assert JpegImagePlugin.get_sampling(sharp) == 0      # 4:4:4: coloured text and thin lines stay clean
    assert JpegImagePlugin.get_sampling(ordinary) == 2   # 4:2:0: smaller, for the quicker steps


def test_the_pc_width_is_sent_as_it_is_never_shrunk_and_never_enlarged(monkeypatch):
    fake_screen(monkeypatch)
    assert decode(capture.grab_jpeg(1920, 76, pointer=False)).size == (1920, 1080)
    assert decode(capture.grab_jpeg(1600, 76, pointer=False)).size == (1600, 900)


def test_a_screen_that_did_not_change_is_reported_still_and_a_change_is_not(monkeypatch):
    image = fake_screen(monkeypatch)
    capture._still_prev = None
    capture.grab_jpeg(800, 50, pointer=False)
    assert capture.last_still() is False                  # nothing to compare with yet
    capture.grab_jpeg(800, 50, pointer=False)
    assert capture.last_still() is True                   # the same screen twice
    for x in range(200, 240):                             # something is typed
        for y in range(300, 330):
            image.putpixel((x, y), (255, 255, 0))
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda all_screens=False: image.copy())
    capture.grab_jpeg(800, 50, pointer=False)
    assert capture.last_still() is False
