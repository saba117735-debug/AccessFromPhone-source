"""The window for setting up unlocking, opened by the "Set up unlocking" button on the PC's page (no terminal, no commands).

It runs with administrator rights (Windows asks first), asks for the Windows sign-in to save and the unlock PIN for the phone, and then does
exactly what `python -m handoff.unlock.setup install` does. It is drawn to look like the app: dark, three clear steps, live checks as you type.
The form's rules are a plain function (`check_form`) so they are tested on their own.
"""
from __future__ import annotations

import getpass
import os
import threading
from typing import Callable

from .store import PIN_MAX, PIN_MIN

RISK = ("This PC will keep your Windows sign-in, encrypted so it opens on this PC only. Anyone with your unlock PIN and a phone already paired "
        "with this PC could unlock it. A Windows Hello PIN is the safer one to keep: it works on this one PC and nothing else.")

# the app's own colours
BG, CARD, FIELD, LINE = "#060b16", "#0f1a30", "#0a1428", "#243455"
TEXT, MUTED, FAINT = "#e6f1ff", "#8392ad", "#4b5a74"
ACCENT, ACCENT2, OK, BAD, WARN = "#22d3ee", "#3b82f6", "#34d399", "#f87171", "#fbbf24"
FONT = "Segoe UI"


def check_form(kind: str, secret: str, secret_again: str, pin: str, pin_again: str) -> str | None:
    """What is wrong with the form, in words for the person, or None when it is fine."""
    name = "Windows Hello PIN" if kind == "pin" else "Windows password"
    if not secret:
        return f"Type your {name}."
    if secret != secret_again:
        return f"The two {name} entries don't match."
    if len(secret) > 128:
        return f"That {name} is too long."
    if not (PIN_MIN <= len(pin) <= PIN_MAX):
        return f"The unlock PIN needs {PIN_MIN} to {PIN_MAX} characters."
    if pin != pin_again:
        return "The two unlock PIN entries don't match."
    if pin == secret:
        return "The unlock PIN must be different from your Windows sign-in: it protects it."
    return None


def _mix(a: str, b: str, t: float) -> str:
    """A colour between two hex colours (t = 0 is a, 1 is b)."""
    pa, pb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def run(perform: Callable[[str, str, str], None] | None = None, check_password: Callable[[str, str], bool] | None = None,
        default_pin: bool | None = None, root=None, mainloop: bool = True) -> dict:
    """Shows the window. `perform(kind, secret, pin)` does the setup (raises on failure). Returns the widgets, for the tests."""
    import tkinter as tk

    from . import setup

    perform = perform or (lambda kind, secret, pin: setup.perform_install(kind, secret, pin))
    check_password = check_password or setup.password_is_right
    use_pin = setup.sign_in_looks_like_pin() if default_pin is None else default_pin

    if root is None:
        try:  # crisp text on a high-resolution screen
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    window = root or tk.Tk()
    window.title("Unlock your PC from your phone")
    window.configure(bg=BG)
    window.resizable(False, False)
    kind = tk.StringVar(value="pin" if use_pin else "password")

    def label(parent, text, size=10, color=TEXT, bold=False, wrap=0, **grid):
        item = tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"), justify="left", anchor="w",
                        wraplength=wrap)
        if grid:
            item.grid(**grid)
        return item

    page = tk.Frame(window, bg=BG, padx=20, pady=14)
    page.pack(fill="both", expand=True)
    WIDTH = 452

    # ---- header
    head = tk.Frame(page, bg=BG)
    head.pack(fill="x")
    badge = tk.Canvas(head, width=46, height=46, bg=BG, highlightthickness=0)
    badge.create_oval(2, 2, 44, 44, fill=_mix(BG, ACCENT, 0.16), outline=_mix(BG, ACCENT, 0.5))
    badge.create_rectangle(15, 22, 31, 33, fill=ACCENT, outline=ACCENT)           # a padlock
    badge.create_arc(17, 12, 29, 28, start=0, extent=180, style="arc", outline=ACCENT, width=3)
    badge.pack(side="left")
    titles = tk.Frame(head, bg=BG)
    titles.pack(side="left", padx=(12, 0))
    label(titles, "Unlock your PC from your phone", 15, TEXT, True).pack(anchor="w")
    label(titles, "One-time setup. It takes a minute.", 9, MUTED).pack(anchor="w")

    # ---- the risk, said plainly
    note = tk.Frame(page, bg=_mix(BG, WARN, 0.10), highlightbackground=_mix(BG, WARN, 0.45), highlightthickness=1)
    note.pack(fill="x", pady=(10, 6))
    label(note, RISK, 9, _mix(TEXT, WARN, 0.35), wrap=WIDTH - 24).pack(padx=12, pady=7, anchor="w")

    def card(number: str, title: str) -> tk.Frame:
        frame = tk.Frame(page, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=12, pady=8)
        frame.pack(fill="x", pady=4)
        top = tk.Frame(frame, bg=CARD)
        top.pack(fill="x", pady=(0, 4))
        bubble = tk.Canvas(top, width=22, height=22, bg=CARD, highlightthickness=0)
        bubble.create_oval(1, 1, 21, 21, fill=_mix(CARD, ACCENT, 0.2), outline="")
        bubble.create_text(11, 11, text=number, fill=ACCENT, font=(FONT, 9, "bold"))
        bubble.pack(side="left")
        label(top, title, 11, TEXT, True).pack(side="left", padx=(8, 0))
        return frame

    # ---- step 1: which sign-in
    step1 = card("1", "What do you sign in with at the lock screen?")
    choices = tk.Frame(step1, bg=CARD)
    choices.pack(fill="x")
    pills: dict[str, tk.Label] = {}

    def pick(value: str) -> None:
        kind.set(value)
        for key, pill in pills.items():
            on = key == value
            pill.config(bg=_mix(CARD, ACCENT, 0.18) if on else FIELD, fg=ACCENT if on else MUTED,
                        highlightbackground=ACCENT if on else LINE)
        step2_title.config(text="Your Windows Hello PIN" if value == "pin" else "Your Windows password")
        refresh()

    for key, text in (("pin", "Windows Hello PIN   ·   recommended"), ("password", "Windows password")):
        pill = tk.Label(choices, text=text, bg=FIELD, fg=MUTED, font=(FONT, 10, "bold"), padx=12, pady=5, cursor="hand2", highlightthickness=1,
                        highlightbackground=LINE)
        pill.pack(fill="x", pady=1)
        pill.bind("<Button-1>", lambda event, value=key: pick(value))
        pills[key] = pill

    # ---- the entries
    fields: dict[str, tk.StringVar] = {}
    boxes: dict[str, tk.Entry] = {}
    hints: dict[str, tk.Label] = {}

    def pair(parent) -> tk.Frame:
        """Two entries side by side, with one line under them for what the live check has to say."""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1, uniform="pair")
        row.columnconfigure(1, weight=1, uniform="pair")
        return row

    def entry(parent, key: str, caption: str, hint_key: str | None = None, column: int = 0) -> tk.Entry:
        row = tk.Frame(parent, bg=CARD)
        row.grid(row=0, column=column, sticky="ew", padx=(0, 6) if column == 0 else (6, 0))
        label(row, caption, 9, MUTED).pack(anchor="w")
        line = tk.Frame(row, bg=FIELD, highlightbackground=LINE, highlightcolor=ACCENT, highlightthickness=1)
        line.pack(fill="x", pady=(2, 0))
        fields[key] = tk.StringVar()
        box = tk.Entry(line, textvariable=fields[key], show="•", bg=FIELD, fg=TEXT, insertbackground=ACCENT, relief="flat", font=(FONT, 11),
                       highlightthickness=0, disabledbackground=FIELD)
        box.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=5)
        box.config(width=8)
        eye = tk.Label(line, text="Show", bg=FIELD, fg=FAINT, font=(FONT, 8), cursor="hand2", padx=6)
        eye.pack(side="right")

        def flip(event=None) -> None:
            hidden = box.cget("show") != ""
            box.config(show="" if hidden else "•")
            eye.config(text="Hide" if hidden else "Show")

        eye.bind("<Button-1>", flip)
        box.bind("<FocusIn>", lambda event: line.config(highlightbackground=ACCENT))
        box.bind("<FocusOut>", lambda event: line.config(highlightbackground=LINE))
        boxes[key] = box
        return box

    def hint(parent, key: str) -> None:
        hints[key] = label(parent, "", 9, FAINT)
        hints[key].pack(anchor="w", pady=(3, 0))

    step2 = card("2", "")
    step2_title = step2.winfo_children()[0].winfo_children()[1]
    both = pair(step2)
    first = entry(both, "secret", "Type it")
    entry(both, "secret_again", "Type it again", column=1)
    hint(step2, "secret")
    step3 = card("3", "Choose an unlock PIN for your phone")
    label(step3, f"You will type this on the phone each time. {PIN_MIN} to {PIN_MAX} characters, and different from your Windows sign-in.", 9, MUTED,
          wrap=WIDTH - 30).pack(anchor="w", pady=(0, 2))
    both = pair(step3)
    entry(both, "pin", "Unlock PIN")
    entry(both, "pin_again", "Type it again", column=1)
    hint(step3, "length")
    hints["pin"] = hints["length"]  # one line serves both: the length first, then whether the two match

    message = tk.Label(page, text="", bg=BG, fg=BAD, font=(FONT, 9), justify="left", anchor="w", wraplength=WIDTH)
    message.pack(fill="x", pady=(4, 0))

    # ---- the button: rounded, with the app's gradient
    class Button(tk.Canvas):
        def __init__(self, parent, text: str, command: Callable[[], None]):
            super().__init__(parent, height=46, bg=BG, highlightthickness=0, cursor="hand2")
            self._text, self._command, self._off = text, command, False
            self.bind("<Configure>", lambda event: self._paint())
            self.bind("<Button-1>", lambda event: None if self._off else self._command())

        def cget(self, key):  # (the tests read the label back)
            return self._text if key == "text" else super().cget(key)

        def config(self, **kw):
            if "text" in kw:
                self._text = kw.pop("text")
            if "command" in kw:
                self._command = kw.pop("command")
            self._paint()
            return super().config(**kw) if kw else None

        configure = config

        def state(self, flags):
            for flag in flags:
                if flag == "disabled":
                    self._off = True
                elif flag == "!disabled":
                    self._off = False
            self._paint()
            return []

        def _paint(self) -> None:
            self.delete("all")
            width, height, radius = max(self.winfo_width(), 60), 46, 12
            for x in range(width):  # a 1-pixel strip at a time, with the corners cut round
                edge = min(x, width - 1 - x)
                inset = 0 if edge >= radius else round(radius - (radius * radius - (radius - edge) ** 2) ** 0.5)
                tone = ACCENT  # one flat colour
                self.create_line(x, inset, x, height - inset, fill=_mix(CARD, tone, 0.35 if self._off else 1))
            self.create_text(width / 2, height / 2, text=self._text, fill="#04121a" if not self._off else _mix(CARD, TEXT, 0.5), font=(FONT, 11, "bold"))

    button = Button(page, "Set up unlocking", lambda: None)
    button.pack(fill="x", pady=(8, 0))

    state = {"done": False, "error": None}

    def refresh(*_):
        """Live checks while typing: do the two entries match, is the unlock PIN long enough. The button only looks ready when the form is fine."""
        values = {k: v.get() for k, v in fields.items()}

        def show(key: str, text: str, colour: str) -> None:
            hints[key].config(text=text, fg=colour)

        if values["secret_again"]:
            same = values["secret"] == values["secret_again"]
            show("secret", "✓ They match" if same else "They don't match yet", OK if same else WARN)
        else:
            show("secret", "", FAINT)
        count = len(values["pin"])
        if values["pin_again"] and PIN_MIN <= count <= PIN_MAX:
            same = values["pin"] == values["pin_again"]
            show("length", f"{count} characters · " + ("✓ they match" if same else "they don't match yet"), OK if same else WARN)
        elif count:
            enough = PIN_MIN <= count <= PIN_MAX
            show("length", f"{count} characters" + ("" if enough else f" · needs {PIN_MIN} to {PIN_MAX}"), OK if enough else WARN)
        else:
            show("length", "", FAINT)
        ready = check_form(kind.get(), values["secret"], values["secret_again"], values["pin"], values["pin_again"]) is None
        if not state["done"]:
            button.state(["!disabled"] if ready else ["disabled"])

    for variable in fields.values():
        variable.trace_add("write", refresh)

    def finish(error: str | None) -> None:
        if error:
            message.config(text=error, fg=BAD)
            button.config(text="Set up unlocking")
            refresh()
            return
        state["done"] = True
        for child in page.winfo_children():
            child.destroy()
        done = tk.Canvas(page, width=WIDTH, height=190, bg=BG, highlightthickness=0)
        done.create_oval(WIDTH / 2 - 38, 20, WIDTH / 2 + 38, 96, fill=_mix(BG, OK, 0.18), outline=_mix(BG, OK, 0.6), width=2)
        done.create_line(WIDTH / 2 - 16, 58, WIDTH / 2 - 4, 70, WIDTH / 2 + 18, 44, fill=OK, width=5, capstyle="round", joinstyle="round")
        done.create_text(WIDTH / 2, 124, text="Unlocking is set up", fill=TEXT, font=(FONT, 15, "bold"))
        done.create_text(WIDTH / 2, 156, text='Lock this PC, then write "unlock my PC" on your phone.', fill=MUTED, font=(FONT, 10))
        done.pack(pady=(10, 0))
        message_after = tk.Label(page, text="You approve each unlock and type your unlock PIN. You can switch it off any time on this PC's page.", bg=BG, fg=MUTED,
                                 font=(FONT, 9), wraplength=WIDTH)
        message_after.pack(pady=(0, 14))
        close = Button(page, "Done", window.destroy)
        close.pack(fill="x")
        close.update_idletasks()
        state["close"] = close

    def submit() -> None:
        values = {k: v.get() for k, v in fields.items()}
        problem = check_form(kind.get(), values["secret"], values["secret_again"], values["pin"], values["pin_again"])
        if problem:
            message.config(text=problem, fg=BAD)
            return
        if kind.get() == "password" and not check_password(os.environ.get("USERDOMAIN", ".") + "\\" + getpass.getuser(), values["secret"]):
            message.config(text="Windows says that password isn't right, so nothing was saved.", fg=BAD)
            return
        message.config(text="", fg=MUTED)
        button.config(text="Setting up…")
        button.state(["disabled"])

        def work() -> None:
            try:
                perform(kind.get(), values["secret"], values["pin"])
                window.after(0, finish, None)
            except Exception as exc:  # noqa: BLE001  (shown to the person; nothing secret is in these messages)
                window.after(0, finish, f"It didn't work: {exc}")

        if root is not None:  # tests run it at once
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    button.config(command=submit)
    window.bind("<Return>", lambda event: submit() if not state["done"] else None)
    pick(kind.get())
    first.focus_set()
    if root is None:  # in the middle of the screen
        window.update_idletasks()
        x, y = (window.winfo_screenwidth() - window.winfo_reqwidth()) // 2, (window.winfo_screenheight() - window.winfo_reqheight()) // 3
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    if mainloop and root is None:
        window.mainloop()
    return {"window": window, "kind": kind, "fields": fields, "message": message, "button": button, "submit": submit, "state": state, "boxes": boxes,
            "hints": hints, "pick": pick}
