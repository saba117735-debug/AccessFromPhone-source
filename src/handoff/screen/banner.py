"""What shows on this PC so screen viewing is never secret:

  asking:   "Ben's phone wants to see your screen"  [Allow 15 min] [Allow 1 hour] [Deny]     (the Owner answers right here, no page needed)
  looking:  a soft light runs round the edge of the screen, and a small tab with a down arrow sits at the top middle.
            Tap the tab and a card says "Ben's phone is viewing this PC" with [Stop]; tap it again and the card folds away.
            The light and the tab stay up the whole time, so viewing is never hidden and never in the way.

The question card and the tab sit at the top middle of the main display, so they are also in the picture the phone sees. Everything runs on its own thread (Tk wants one),
fed through a queue; if there is no screen to draw on, it quietly does nothing and the PC page still shows the same request and the same Stop.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Callable

from .manager import NullBanner

log = logging.getLogger("handoff.screen")

BG, FG, MUTED, ACCENT, DANGER = "#0f1b2d", "#e6f1ff", "#9fb3c8", "#22d3ee", "#f87171"
WARN = "#fbbf24"   # the phone has gone quiet
KEY = "#010203"      # the colour of the see-through part of the light window
RINGS = 6            # how many lines thick the glowing edge is
TAIL = 16            # pieces in the light that runs round the edge


def _mix(a: str, b: str, t: float) -> str:
    """A colour t of the way from a to b (both "#rrggbb")."""
    t = max(0.0, min(1.0, t))
    ca, cb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


class DesktopBanner(NullBanner):
    def __init__(self) -> None:
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._broken = False
        self._lock = threading.Lock()
        self.hint = ""   # said under the title while someone looks ("Panic key: Ctrl+Alt+Shift+X stops this at once")
        self.title = ""  # what the window says right now ("" when hidden): for tests and for anyone curious

    # -- called from anywhere (the server's threads)
    def request(self, name: str, note: str, decide: Callable[[bool, int], None]) -> None:
        self._send(("request", name, note, decide))

    def request_control(self, name: str, decide: Callable[[bool, int], None]) -> None:
        self._send(("control", name, decide))

    def active(self, name: str, stop: Callable[[], None], controlling: bool = False) -> None:
        self._send(("active", name, stop, controlling))

    def waiting(self, name: str, left: int) -> None:
        """The phone has stopped asking for pictures (its app was closed, or it lost the signal). Amber, with the seconds left before this ends by itself."""
        self._send(("waiting", name, int(left)))

    def clear(self) -> None:
        if self._thread is not None:
            self._send(("clear",))

    def press(self, label: str) -> None:
        """Tap a button of the window by its label, or "Show who" for the tab (for tests: there is no one to click it)."""
        self._send(("press", label))

    def _send(self, message: tuple) -> None:
        if self._broken:
            return
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="screen-banner", daemon=True)
                self._thread.start()
        self._q.put(message)

    # -- the Tk thread
    def _run(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg=BG, highlightthickness=1, highlightbackground="#2a3b55")
            body = tk.Frame(root, bg=BG, padx=16, pady=12)
            body.pack()
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()

            # a soft light round the edge of the screen: a see-through window on top that clicks pass straight through
            glow = canvas = None
            rings: list[int] = []
            tail: list[int] = []
            try:
                glow = tk.Toplevel(root)
                glow.withdraw()
                glow.overrideredirect(True)
                glow.attributes("-topmost", True)
                glow.configure(bg=KEY)
                glow.attributes("-transparentcolor", KEY)
                glow.geometry(f"{sw}x{sh}+0+0")
                canvas = tk.Canvas(glow, width=sw, height=sh, bg=KEY, highlightthickness=0, bd=0)
                canvas.pack()
                rings = [canvas.create_rectangle(i, i, sw - 1 - i, sh - 1 - i, outline=ACCENT, width=1) for i in range(RINGS)]
                tail = [canvas.create_line(0, 0, 0, 0, fill=ACCENT, width=3, capstyle="round") for _ in range(TAIL)]
                glow.update_idletasks()
                import ctypes

                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes, user32.GetAncestor.restype = (ctypes.c_void_p, ctypes.c_uint), ctypes.c_void_p
                user32.GetWindowLongW.argtypes, user32.GetWindowLongW.restype = (ctypes.c_void_p, ctypes.c_int), ctypes.c_long
                user32.SetWindowLongW.argtypes, user32.SetWindowLongW.restype = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long), ctypes.c_long
                hwnd = user32.GetAncestor(glow.winfo_id(), 2)   # GA_ROOT
                user32.SetWindowLongW(hwnd, -20, user32.GetWindowLongW(hwnd, -20) | 0x80000 | 0x20 | 0x80)   # layered, click-through, not in Alt+Tab
            except Exception:  # noqa: BLE001 - no see-through windows here (not Windows): the tab still says it
                log.info("the screen edge light is not available here", exc_info=True)
                glow = None

            # the small tab at the top middle: a down arrow that opens the card saying who is looking
            tab = tk.Toplevel(root)
            tab.withdraw()
            tab.overrideredirect(True)
            tab.attributes("-topmost", True)
            tab.configure(bg=BG, highlightthickness=1, highlightbackground=ACCENT)
            tab_label = tk.Label(tab, text="●  ▾", bg=BG, fg=ACCENT, font=("Segoe UI", 12, "bold"), padx=18, pady=2, cursor="hand2")
            tab_label.pack()

            state = {"active": None, "open": False, "card": None, "colour": ACCENT, "beat": None, "quiet": 0}   # active: (name, stop, controlling); card: "ask" | "active" | None

            def place(top: int = 10) -> None:
                root.update_idletasks()
                w, h = root.winfo_reqwidth(), root.winfo_reqheight()
                root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{top}")

            def place_tab() -> None:
                tab.update_idletasks()
                w = tab.winfo_reqwidth()
                tab.geometry(f"{w}x{tab.winfo_reqheight()}+{max(0, (sw - w) // 2)}+0")

            buttons_now: dict[str, "tk.Button"] = {}

            def show(title: str, sub: str, buttons: list[tuple[str, str, Callable[[], None]]], top: int = 10) -> None:
                self.title = title
                buttons_now.clear()
                for child in body.winfo_children():
                    child.destroy()
                tk.Label(body, text=title, bg=BG, fg=FG, font=("Segoe UI", 11, "bold"), wraplength=520, justify="left").pack(anchor="w")
                if sub:
                    tk.Label(body, text=sub, bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=520, justify="left").pack(anchor="w", pady=(2, 0))
                row = tk.Frame(body, bg=BG)
                row.pack(anchor="w", pady=(10, 0))
                for text, colour, action in buttons:
                    button = tk.Button(row, text=text, command=action, bg=colour, fg="#06121f" if colour != DANGER else "#2a0a0a", activebackground=colour, relief="flat",
                                       font=("Segoe UI", 9, "bold"), padx=12, pady=5, cursor="hand2")
                    button.pack(side="left", padx=(0, 8))
                    buttons_now[text] = button
                place(top)
                root.deiconify()
                root.lift()
                root.attributes("-topmost", True)

            def active_title() -> str:
                name, _stop, controlling = state["active"]
                if state["quiet"]:
                    return f"●  {name} isn't responding. Ending in {state['quiet']} s"
                return f"●  {name} is {'viewing and CONTROLLING' if controlling else 'viewing'} this PC's screen"

            def paint_tab() -> None:
                colour = state["colour"]
                tab_label.configure(text="●  ▴" if state["open"] else "●  ▾", fg=colour)
                tab.configure(highlightbackground=colour)
                place_tab()

            def hide_card() -> None:
                buttons_now.clear()
                root.withdraw()
                state["card"] = None

            def open_card() -> None:
                """The card that says who is looking and has Stop, just under the tab."""
                if state["active"] is None or state["card"] == "ask":
                    return
                _name, stop, controlling = state["active"]
                state["card"] = "active"
                if state["quiet"]:
                    said = "The phone stopped asking for pictures (its app may have been closed, or it lost the signal). If it doesn't come back, this ends by itself."
                else:
                    said = ("They can move the mouse and type on this PC." if controlling else "Everything on this screen can be seen.") + (f"  {self.hint}" if self.hint else "")
                show(active_title(), said,
                     [("Stop", DANGER, lambda: (stop(), end_active()))], top=tab.winfo_reqheight() + 4)

            def toggle() -> None:
                if state["active"] is None or state["card"] == "ask":
                    return
                state["open"] = not state["open"]
                paint_tab()
                if state["open"]:
                    open_card()
                else:
                    hide_card()
                    self.title = active_title()

            tab.bind("<Button-1>", lambda _e: toggle())   # once only: a click on the label also reaches the tab behind it, and a second binding would open and shut the card in the same click

            def frame() -> None:
                """One step of the light: the edge breathes, and a brighter spot runs round it."""
                state["beat"] = None
                if glow is None or state["active"] is None:
                    return
                now = time.time()
                colour = state["colour"]
                breathe = 0.5 + 0.5 * math.sin(now * 2.4)
                for i, item in enumerate(rings):
                    canvas.itemconfigure(item, outline=_mix("#06121f", colour, (0.30 + 0.70 * breathe) * (1 - i / RINGS) ** 1.6))
                perimeter = 2 * (sw + sh)
                head = (now * 0.10 % 1.0) * perimeter
                step = 46

                def at(s_: float) -> tuple[float, float]:
                    s_ %= perimeter
                    if s_ < sw:
                        return s_, 2
                    s_ -= sw
                    if s_ < sh:
                        return sw - 3, s_
                    s_ -= sh
                    if s_ < sw:
                        return sw - s_, sh - 3
                    return 2, sh - (s_ - sw)

                for k, item in enumerate(tail):
                    (x1, y1), (x2, y2) = at(head - k * step), at(head - (k + 1) * step)
                    canvas.coords(item, x1, y1, x2, y2)
                    canvas.itemconfigure(item, fill=_mix("#06121f", "#ffffff" if k < 3 else colour, (1 - k / TAIL) ** 1.3))
                state["beat"] = root.after(90, frame)

            def start_waiting(name: str, left: int) -> None:
                """The phone went quiet: amber, and the seconds left. Looking carries on being shown until it ends."""
                if state["active"] is None:
                    return
                state["quiet"] = max(1, left)
                state["colour"] = WARN
                if state["card"] != "ask":
                    self.title = active_title()
                paint_tab()
                if state["open"] and state["card"] != "ask":
                    open_card()

            def start_active(name: str, stop: Callable[[], None], controlling: bool) -> None:
                state["active"] = (name, stop, controlling)
                state["quiet"] = 0
                state["colour"] = DANGER if controlling else ACCENT
                if state["card"] != "ask":
                    self.title = active_title()
                paint_tab()
                tab.deiconify()
                tab.lift()
                tab.attributes("-topmost", True)
                if glow is not None:
                    glow.deiconify()
                    glow.lift()
                    glow.attributes("-topmost", True)
                    if state["beat"] is None:
                        frame()
                if state["open"] and state["card"] != "ask":
                    open_card()   # the card was open and something changed (control went on or off): it says so

            def end_active() -> None:
                state["active"], state["open"], state["quiet"] = None, False, 0
                if state["beat"] is not None:
                    root.after_cancel(state["beat"])
                    state["beat"] = None
                tab.withdraw()
                if glow is not None:
                    glow.withdraw()
                if state["card"] != "ask":
                    hide_card()
                    self.title = ""

            def answered() -> None:
                """The Owner answered a question: the card goes, and the tab and light (if someone is looking) carry on."""
                hide_card()
                self.title = active_title() if state["active"] is not None else ""
                if state["active"] is not None and state["open"]:
                    open_card()

            def ask_card(title: str, sub: str, buttons) -> None:
                state["card"] = "ask"
                show(title, sub, buttons)

            def poll() -> None:
                try:
                    while True:
                        kind, *args = self._q.get_nowait()
                        if kind == "request":
                            name, note, decide = args
                            answer = lambda allow, minutes=0: (decide(allow, minutes), answered())  # noqa: E731
                            ask_card(f"{name} wants to see your screen", (f"“{note}”. " if note else "") + "They can only look. Nothing on this PC can be changed from it. \"Always\" lasts until you take it back on the PC page.",
                                     [("Allow 15 min", ACCENT, lambda: answer(True, 15)), ("Allow 1 hour", ACCENT, lambda: answer(True, 60)), ("Always", ACCENT, lambda: answer(True, 0)), ("Deny", "#2a3b55", lambda: answer(False))])
                        elif kind == "control":
                            name, decide = args
                            answer = lambda allow, minutes=0: (decide(allow, minutes), answered())  # noqa: E731
                            ask_card(f"{name} wants to CONTROL your PC", "They already see your screen. This lets them move the mouse and type. You can stop it any time.",
                                     [("Allow 15 min", ACCENT, lambda: answer(True, 15)), ("Allow 1 hour", ACCENT, lambda: answer(True, 60)), ("Deny", "#2a3b55", lambda: answer(False))])
                        elif kind == "active":
                            start_active(*args)
                        elif kind == "waiting":
                            start_waiting(*args)
                        elif kind == "clear":
                            end_active()
                            hide_card()
                            self.title = ""
                        elif kind == "press":
                            label = args[0]
                            if label == "Show who":   # the tab
                                toggle()
                                continue
                            if buttons_now.get(label) is None and label == "Stop" and state["active"] is not None:
                                state["open"] = True
                                paint_tab()
                                open_card()
                            button = buttons_now.get(label)
                            if button is not None:
                                button.invoke()
                except queue.Empty:
                    pass
                root.after(200, poll)

            root.after(200, poll)
            root.mainloop()
        except Exception:  # noqa: BLE001 - no screen to draw on, or Tk missing: the PC page still shows everything this window would
            self._broken = True
            log.warning("the desktop banner could not start; the PC page still shows screen requests and viewers", exc_info=True)
