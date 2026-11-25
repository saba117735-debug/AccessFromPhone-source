"""Taking a picture of this PC's screen. Pillow only (the app already uses it), no extra program to install."""
from __future__ import annotations

import io
import threading


def _dpi_aware() -> None:
    """Without this, a PC set to 125% or 150% scaling tells programs a smaller screen than it really has, and the pointer would be drawn in the wrong place."""
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - not Windows, or already set
        pass


_dpi_aware()


def pointer_position() -> tuple[int, int] | None:
    """Where the mouse pointer is on the main display, or None when it cannot be known."""
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return point.x, point.y
    except Exception:  # noqa: BLE001
        pass
    return None


def pointer_fraction() -> tuple[float, float] | None:
    """Where the pointer is, as fractions of the main display (0 to 1), so a phone can place its own pointer exactly where the PC's is."""
    where = pointer_position()
    if where is None:
        return None
    try:
        import ctypes

        width, height = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        if width > 0 and height > 0:
            return min(1.0, max(0.0, where[0] / width)), min(1.0, max(0.0, where[1] / height))
    except Exception:  # noqa: BLE001
        pass
    return None


def screen_size() -> tuple[int, int] | None:
    """The main display's size in real pixels, or None when it cannot be known."""
    try:
        import ctypes

        width, height = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        if width > 0 and height > 0:
            return width, height
    except Exception:  # noqa: BLE001
        pass
    return None


# ---- the pointer's real shape ------------------------------------------------------------------------------------------------------------------
# The pointer on the PC changes with what is under it (arrow, text bar, hand, resize arrows, busy ring, an app's own shapes). Windows says which one is showing
# (GetCursorInfo) and can draw it out of its handle (DrawIconEx), so any shape can be copied exactly, not just the standard ones. Each shape is drawn once and kept.

CURSOR_ARROW = "arrow"      # what is said when the shape can't be read (the phone then uses its ordinary arrow)
_shapes: dict[int, tuple] = {}   # handle -> (RGBA picture, hot spot x, hot spot y); only handles this PC has actually shown ever get in
_shapes_lock = threading.Lock()


def cursor_handle() -> int | None | str:
    """The handle of the cursor showing now; None when the cursor is hidden (the PC hides it while typing or in a video); CURSOR_ARROW when it can't be read."""
    try:
        import ctypes
        from ctypes import wintypes

        class Info(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("flags", wintypes.DWORD), ("cursor", wintypes.HANDLE), ("x", ctypes.c_long), ("y", ctypes.c_long)]

        info = Info(ctypes.sizeof(Info), 0, None, 0, 0)
        if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)):
            return CURSOR_ARROW
        if not (info.flags & 1) or not info.cursor:   # CURSOR_SHOWING
            return None
        return int(info.cursor)
    except Exception:  # noqa: BLE001 - not Windows
        return CURSOR_ARROW


def _pixels(picture) -> list:
    """All the pixels of a picture (Pillow renamed this call; both names work here)."""
    return list(picture.get_flattened_data()) if hasattr(picture, "get_flattened_data") else list(picture.getdata())


def render_cursor(handle: int):
    """(RGBA picture, hot spot x, hot spot y) for a cursor handle, or None. Drawn twice, on black and on white: the difference is the transparency (this copes with cursors that
    have a soft edge and with the old kind that are a mask), and a pixel that INVERTS what is under it (the text bar) is drawn white with a dark edge so it shows on anything."""
    import ctypes
    from ctypes import wintypes

    from PIL import Image, ImageChops, ImageFilter

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    class IconInfo(ctypes.Structure):
        _fields_ = [("icon", wintypes.BOOL), ("hx", wintypes.DWORD), ("hy", wintypes.DWORD), ("mask", wintypes.HBITMAP), ("color", wintypes.HBITMAP)]

    class Bitmap(ctypes.Structure):
        _fields_ = [("type", ctypes.c_long), ("width", ctypes.c_long), ("height", ctypes.c_long), ("stride", ctypes.c_long), ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("data", ctypes.c_void_p)]

    class Header(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", ctypes.c_long), ("height", ctypes.c_long), ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                    ("image", wintypes.DWORD), ("xppm", ctypes.c_long), ("yppm", ctypes.c_long), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]

    user32.GetIconInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(IconInfo))
    user32.DrawIconEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT)
    gdi32.GetObjectW.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p)
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = (wintypes.HDC,), wintypes.HDC
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = (wintypes.HDC, wintypes.HGDIOBJ), wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP

    hcursor = wintypes.HANDLE(handle)
    info = IconInfo()
    if not user32.GetIconInfo(hcursor, ctypes.byref(info)):
        return None
    try:
        measure = Bitmap()
        gdi32.GetObjectW(info.mask, ctypes.sizeof(Bitmap), ctypes.byref(measure))
        width = int(measure.width)
        height = int(measure.height) if info.color else int(measure.height) // 2   # an old black-and-white cursor keeps two pictures (and and xor) in one tall bitmap
        if not (4 <= width <= 256 and 4 <= height <= 256):
            return None
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        dc = gdi32.CreateCompatibleDC(None)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            return None
        old = gdi32.SelectObject(dc, bitmap)
        try:
            renders = []
            for fill in (0x00, 0xFF):
                ctypes.memset(bits, fill, width * height * 4)
                user32.DrawIconEx(dc, 0, 0, hcursor, width, height, 0, None, 0x0003)   # DI_NORMAL
                data = ctypes.string_at(bits, width * height * 4)
                renders.append(Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1).copy())
        finally:
            gdi32.SelectObject(dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)
        on_black, on_white = renders
        difference = ImageChops.subtract(on_white, on_black).convert("L")          # how much of the background shows through (clipped at 0 where the pixel inverts)
        inverted = ImageChops.subtract(on_black, on_white).convert("L").point(lambda v: 255 if v > 60 else 0)
        alpha = difference.point(lambda v: 255 - v)
        alpha = ImageChops.lighter(alpha, inverted)                                   # an inverting pixel is solid
        pixels, shown, mark, dark = _pixels(on_black), _pixels(alpha), _pixels(inverted), []
        for rgb, a, inv in zip(pixels, shown, mark):
            if inv:
                dark.append((255, 255, 255, 255))                                    # the text bar: white, and the edge below makes it show on a light page too
            elif a <= 0:
                dark.append((0, 0, 0, 0))
            else:
                k = 255.0 / a
                dark.append((min(255, int(rgb[0] * k)), min(255, int(rgb[1] * k)), min(255, int(rgb[2] * k)), a))
        shape = Image.new("RGBA", (width, height))
        shape.putdata(dark)
        # a one-pixel dark edge round the whole shape, so it can be seen on any background (white arrows and inverted text bars especially)
        edge = shape.getchannel("A").filter(ImageFilter.MaxFilter(3))
        framed = Image.new("RGBA", (width + 2, height + 2), (0, 0, 0, 0))
        outline = Image.new("RGBA", (width + 2, height + 2), (0, 0, 0, 0))
        outline.paste((20, 20, 20, 255), (1, 1), edge)
        framed = Image.alpha_composite(framed, outline)
        framed.alpha_composite(shape, (1, 1))
        return framed, int(info.hx) + 1, int(info.hy) + 1
    finally:
        if info.mask:
            gdi32.DeleteObject(info.mask)
        if info.color:
            gdi32.DeleteObject(info.color)


def cursor_shape(handle):
    """The shape for a handle this PC has shown (kept for next time), or None. A handle that was never shown here is refused: the phone can't ask for anything it was not told about."""
    if not isinstance(handle, int):
        return None
    with _shapes_lock:
        return _shapes.get(handle)


def remember_cursor(handle) -> bool:
    """Make sure the shape of this (currently showing) handle is drawn and kept. False when it can't be."""
    if not isinstance(handle, int):
        return False
    with _shapes_lock:
        if handle in _shapes:
            return True
    try:
        made = render_cursor(handle)
    except Exception:  # noqa: BLE001
        made = None
    if made is None:
        return False
    with _shapes_lock:
        if len(_shapes) >= 96:   # an animated busy cursor makes a new handle for every step: keep the newest ones only
            _shapes.pop(next(iter(_shapes)))
        _shapes[handle] = made
    return True


def draw_cursor(image, x: float, y: float, shape, size: int) -> None:
    """The real pointer pasted into the picture with its tip (its hot spot) at (x, y). `size` is the arrow size setting in pixels (22 = normal)."""
    picture, hx, hy = shape
    scale = max(0.4, size / 22.0)
    if abs(scale - 1.0) > 0.02:
        from PIL import Image

        picture = picture.resize((max(1, round(picture.width * scale)), max(1, round(picture.height * scale))), Image.BILINEAR)
        hx, hy = hx * scale, hy * scale
    image.paste(picture, (int(round(x - hx)), int(round(y - hy))), picture)


def draw_pointer(image, x: float, y: float, size: int = 22) -> None:
    """The classic arrow, white with a dark edge, with its tip at (x, y). A screenshot has no pointer, and controlling by touch needs to see where it is."""
    from PIL import ImageDraw

    shape = [(0, 0), (0, 0.84), (0.2, 0.68), (0.33, 1.0), (0.46, 0.94), (0.33, 0.63), (0.58, 0.63)]
    points = [(x + px * size, y + py * size) for px, py in shape]
    ImageDraw.Draw(image).polygon(points, fill=(255, 255, 255), outline=(0, 0, 0))


class ScreenUnavailable(Exception):
    """The screen cannot be captured right now (for example the PC is locked, or no one is signed in)."""


MAX_WIDTH, MIN_WIDTH = 1920, 320
MAX_QUALITY, MIN_QUALITY = 92, 20
SHARP_FROM = 75   # from this quality up, colour is kept at full detail (4:4:4): coloured text and thin lines stay clean instead of fringed

# Is the screen the same as the last picture handed out? The phone uses it to send one extra-sharp picture while nothing moves, and to rest between
# checks instead of re-sending the same pixels many times a second. Compared on a small averaged copy, so it costs about a millisecond.
_still_lock = threading.Lock()
_still_prev: bytes | None = None
_still_now = False


def _screen_is_still(image) -> bool:
    global _still_prev, _still_now
    try:
        small = image.reduce(8).convert("L").tobytes()      # 8x8 blocks averaged: a typed letter or a moving window changes some block
    except Exception:  # noqa: BLE001 - never let this break a picture
        return False
    with _still_lock:
        same = _still_prev is not None and small == _still_prev
        _still_prev, _still_now = small, same
    return same


def last_still() -> bool:
    """Whether the picture just handed out was identical (to the eye of `_screen_is_still`) to the one before."""
    with _still_lock:
        return _still_now


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def pointer_pixels(scale: float) -> int:
    """The drawn pointer's size in pixels for a size setting (1 = normal), kept to a sensible range."""
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    return int(round(22 * max(0.5, min(3.0, scale))))


MIN_REGION = 0.05  # no smaller than a twentieth of the screen across: far past anything useful


def clamp_region(region) -> tuple[float, float, float, float] | None:
    """The part of the screen a phone zoomed in on, as fractions (left, top, width, height), made safe. None means the whole screen."""
    if not region:
        return None
    try:
        x, y, w, h = (float(v) for v in region)
    except (TypeError, ValueError):
        return None
    if not all(v == v and abs(v) != float("inf") for v in (x, y, w, h)):  # NaN or infinite
        return None
    w, h = min(1.0, max(MIN_REGION, w)), min(1.0, max(MIN_REGION, h))
    x, y = min(1.0 - w, max(0.0, x)), min(1.0 - h, max(0.0, y))
    if w >= 0.999 and h >= 0.999:
        return None
    return x, y, w, h


class _FastGrab:
    """The whole main screen with one BitBlt into a buffer that is kept between pictures. ImageGrab does the same copy but then converts and copies it again, which on a 1080p screen
    took about 75 ms a picture here against about 20 ms this way: the first thing that capped the picture rate (about 9 a second, whatever the network). If anything about it fails
    (a locked screen, an odd graphics driver) the caller falls back to ImageGrab, so this can only make things faster, never break them."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._c = ctypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetDC.argtypes, user32.GetDC.restype = (wintypes.HWND,), wintypes.HDC
        user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = (wintypes.HWND, wintypes.HDC), ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = (wintypes.HDC,), wintypes.HDC
        gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = (wintypes.HDC, wintypes.HGDIOBJ), wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = (wintypes.HGDIOBJ,), wintypes.BOOL
        gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = (wintypes.HDC,), wintypes.BOOL
        gdi32.BitBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
        gdi32.BitBlt.restype = wintypes.BOOL
        self._user32, self._gdi32 = user32, gdi32
        self._lock = threading.Lock()
        self._size: tuple[int, int] | None = None
        self._dc = self._bitmap = self._old = None
        self._buffer = None

    def _free(self) -> None:
        if self._dc:
            if self._old:
                self._gdi32.SelectObject(self._dc, self._old)
            if self._bitmap:
                self._gdi32.DeleteObject(self._bitmap)
            self._gdi32.DeleteDC(self._dc)
        self._dc = self._bitmap = self._old = self._buffer = self._size = None

    def _make(self, width: int, height: int) -> None:
        ctypes = self._c
        from ctypes import wintypes

        class Header(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("width", ctypes.c_long), ("height", ctypes.c_long), ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                        ("image", wintypes.DWORD), ("xppm", ctypes.c_long), ("yppm", ctypes.c_long), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]

        self._free()
        screen_dc = self._user32.GetDC(None)
        try:
            self._dc = self._gdi32.CreateCompatibleDC(screen_dc)
            header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)   # a negative height: the rows come top-down, as an image wants them
            bits = ctypes.c_void_p()
            create = self._gdi32.CreateDIBSection
            create.argtypes = (wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
            create.restype = wintypes.HBITMAP
            self._bitmap = create(screen_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
            if not self._bitmap or not bits.value:
                raise OSError("no bitmap")
            self._old = self._gdi32.SelectObject(self._dc, self._bitmap)
            self._buffer = (ctypes.c_char * (width * height * 4)).from_address(bits.value)
            self._size = (width, height)
        finally:
            self._user32.ReleaseDC(None, screen_dc)

    def grab(self):
        from PIL import Image

        with self._lock:
            width, height = self._user32.GetSystemMetrics(0), self._user32.GetSystemMetrics(1)
            if width <= 0 or height <= 0:
                raise OSError("no screen")
            if self._size != (width, height):
                self._make(width, height)
            screen_dc = self._user32.GetDC(None)
            try:
                if not self._gdi32.BitBlt(self._dc, 0, 0, width, height, screen_dc, 0, 0, 0x00CC0020):   # SRCCOPY (no layered windows: the screen-edge light is not in the picture)
                    raise OSError("the screen could not be copied")
            finally:
                self._user32.ReleaseDC(None, screen_dc)
            return Image.frombuffer("RGB", (width, height), self._buffer, "raw", "BGRX", 0, 1).copy()   # a copy: the next picture reuses the buffer


_fast: _FastGrab | None = None
_fast_failed = False


def grab_screen():
    """The main display as an RGB picture: the fast copy when it works, ImageGrab otherwise (and for good, once the fast one has failed)."""
    global _fast, _fast_failed
    if not _fast_failed:
        try:
            if _fast is None:
                _fast = _FastGrab()
            return _fast.grab()
        except Exception:  # noqa: BLE001 - not Windows, a locked screen, an odd driver: the ordinary way does it (and says so if that fails too)
            _fast_failed = True
    from PIL import ImageGrab

    return ImageGrab.grab(all_screens=False).convert("RGB")   # the main display: choosing another is a later step


class _Source:
    """Pictures of the screen taken ahead of the requests. Asking for a picture used to mean: capture it (about 50-80 ms on this PC), compress it, send it, and only then capture
    the next one, so the screen was idle while the network worked and the network while the screen worked. Now one thread captures the next picture while the last one is being
    compressed and sent, and a picture is only ever handed out once, so two requests in flight at the same moment get two different pictures. It only runs while someone is asking
    (it stops about three seconds after the last request), and it never captures more than one picture ahead of what is being used."""

    HOLD = 3.0       # seconds it keeps going after the last request
    STALE = 0.25     # an unused picture is refreshed after this long (so a slow viewer still gets a current one)

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame = None
        self._id = self._served = 0
        self._at = 0.0
        self._until = 0.0
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def _run(self) -> None:
        import time

        while True:
            with self._cond:
                if time.monotonic() > self._until:
                    self._thread = None
                    return
                fresh = self._id > self._served and time.monotonic() - self._at < self.STALE   # a picture nobody has taken yet: no need for another
            if fresh:
                time.sleep(0.004)
                continue
            try:
                frame = grab_screen()
            except Exception as exc:  # noqa: BLE001 - a locked screen, a driver hiccup: say so to whoever waits, and try again in a moment
                with self._cond:
                    self._error = exc
                    self._cond.notify_all()
                time.sleep(0.3)
                continue
            with self._cond:
                self._frame, self._id, self._at, self._error = frame, self._id + 1, time.monotonic(), None
                self._cond.notify_all()

    def take(self, timeout: float = 1.5):
        """A picture that has not been handed out yet (waiting for the next one if need be)."""
        import time

        with self._cond:
            self._until = time.monotonic() + self.HOLD
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="screen-capture", daemon=True)
                self._thread.start()
            end = time.monotonic() + timeout
            while self._id <= self._served:
                left = end - time.monotonic()
                if left <= 0:
                    raise self._error or OSError("no picture of the screen")
                self._cond.wait(left)
            self._served = self._id
            return self._frame   # shared and never changed: every use makes its own copy (crop, resize)


FRAME_SOURCE = True   # False: capture on demand, one picture per call (tests, and anything that wants exactly the screen as it is right now)
_source = _Source()


def grab_jpeg(width: int = 1280, quality: int = 55, pointer: bool = True, pointer_size: float = 1.0, region=None) -> bytes:
    """The screen as a JPEG no wider than `width`, with the mouse pointer drawn in unless `pointer` is False (a phone that draws its own pointer
    asks for none). `region` is the part of the screen the phone zoomed in on: it is cut out of the full-detail picture BEFORE shrinking, so
    zooming shows real pixels instead of an enlarged small picture. Raises ScreenUnavailable instead of anything else."""
    width, quality = clamp(width, MIN_WIDTH, MAX_WIDTH), clamp(quality, MIN_QUALITY, MAX_QUALITY)
    region = clamp_region(region)
    try:
        from PIL import Image

        image = _source.take() if FRAME_SOURCE else grab_screen()
        _screen_is_still(image)    # of the whole screen, before anything is cut out of it
        left = top = 0
        if region is not None:
            left, top = int(round(region[0] * image.width)), int(round(region[1] * image.height))
            right, bottom = min(image.width, left + max(1, int(round(region[2] * image.width)))), min(image.height, top + max(1, int(round(region[3] * image.height))))
            image = image.crop((left, top, right, bottom))
        ratio = 1.0
        if image.width > width:  # never enlarged: a small crop is sent as it is, and the phone scales it
            ratio = width / image.width
            image = image.resize((width, max(1, round(image.height * ratio))), Image.LANCZOS if region is not None else Image.BILINEAR, reducing_gap=None if region is not None else 2.0)  # zoomed pictures are mostly text: worth the sharper filter; the whole screen is shrunk in two quick steps (about twice as fast, no visible difference)
        where = pointer_position() if pointer else None
        if where is not None:
            px, py = where[0] - left, where[1] - top  # in the cut-out picture's own pixels
            if 0 <= px < image.width / ratio and 0 <= py < image.height / ratio:
                handle = cursor_handle()
                if handle is None:
                    pass   # the PC has hidden its pointer (typing, a video): so is it here
                elif isinstance(handle, int) and remember_cursor(handle):
                    draw_cursor(image, px * ratio, py * ratio, cursor_shape(handle), pointer_pixels(pointer_size))   # the pointer as it really is: a text bar over text, a hand over a link...
                else:
                    draw_pointer(image, px * ratio, py * ratio, pointer_pixels(pointer_size))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=quality, subsampling=0 if quality >= SHARP_FROM else 2)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - the screen being unavailable has many causes; the phone gets one clear sentence
        raise ScreenUnavailable("This PC's screen can't be captured right now. It may be locked, or nobody is signed in.") from exc
