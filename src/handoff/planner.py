"""Turns a prompt into a plan of steps.

`DemoPlanner` understands a few plain commands, so the whole flow (plan,
approve, act, undo) works with no AI model. `OllamaPlanner` asks a model that
runs on this PC (through Ollama) to choose the steps. Both sit behind the same
`Planner` interface, so approvals, undo and folder limits never change.

The model may only *propose* writes and deletes. Reads and searches run at
once, inside the configured folders, so it can look before it proposes.
"""
from __future__ import annotations

import functools
import http.client
import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

from .cancel import CURRENT, Cancelled
from .config import Action, LlmConfig
from . import imaging, sysinfo
from .power import ACTIONS as POWER_ACTIONS
from .policy import OutsideAllowed, PolicyError
from .titles import TITLE_SYSTEM, clean_title

MAX_READ_CHARS = 8_000


class PlanError(Exception):
    """The prompt could not be turned into a plan."""


def _brief(text: str, limit: int = 60) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


@dataclass(frozen=True)
class Step:
    action: Action
    path: str  # relative to the first writable folder, or absolute
    content: str | None = None
    pattern: str | None = None  # READ of a folder: find files whose name matches this
    target: str | None = None  # MOVE and COPY: where the file goes
    lines: int | None = None  # READ of a file: only this many lines
    tail: bool = False  # ... from the end (the newest lines of a log) instead of the start
    edit: tuple[str, str] | None = None  # WRITE that replaces one piece of text: (old, new). `content` is the whole new file
    editor: bool = False  # READ that opens the file in the phone's viewer or editor
    view: bool = False  # ... in the viewer (read only to start with) rather than straight into editing
    gallery: bool = False  # ... a FOLDER, shown as a grid of its pictures
    browse: bool = False  # ... a FOLDER, opened in the file browser to walk through
    app_kind: str = ""  # APP: "open", "teams_unread", "teams_chat" or "teams_reply". `pattern` is the app or the person, `content` the words to send

    def detail(self) -> str:
        if self.action is Action.WRITE and self.edit:
            old, new = self.edit
            return f"replace “{_brief(old)}” with “{_brief(new)}”"
        if self.action is Action.READ and self.editor and self.browse:
            return "open in the file browser"
        if self.action is Action.READ and self.editor:
            return "open full screen" if self.view else "open in the editor"
        if self.action is Action.READ and self.lines:
            return f"{'last' if self.tail else 'first'} {self.lines} lines"
        if self.action is Action.WRITE and self.content is not None:
            return f"{len(self.content)} characters"
        if self.action in (Action.MOVE, Action.COPY):
            return f"to {self.target}"
        if self.action is Action.RUN:
            return f"$ {(self.content or '').strip()} (cannot be undone)"  # the WHOLE command: an approval must never hide the end of what it allows
        if self.action is Action.APP:
            return _app_detail(self)
        if self.action is Action.POWER:
            return POWER_ACTIONS[self.content].detail if self.content in POWER_ACTIONS else "change this PC's power state"
        if self.pattern:
            return f"find {self.pattern}"
        return ""


def _app_detail(step: "Step") -> str:
    who, kind = step.pattern or "", step.app_kind
    if kind == "open":
        return f"open {who} on this PC"
    if kind == "teams_unread":
        return "bring Teams forward and read your unread chats"
    if kind == "teams_chat":
        return f"bring Teams forward and read your latest messages with {who}"
    if kind == "teams_reply":
        return f"send to {who} in Teams, exactly as written: “{_brief(step.content or '', 300)}”"
    return "use an app on this PC"


@dataclass(frozen=True)
class Plan:
    summary: str
    steps: list[Step] = field(default_factory=list)
    reply: str | None = None  # a plain answer, when there is nothing to change


class ReadContext(Protocol):
    """What a planner may look at. `roots`, `history` and `commands_enabled` are optional extras."""

    def list_dir(self, path: str) -> list[str]: ...
    def read_file(self, path: str) -> str: ...
    def find_files(self, path: str, pattern: str) -> str: ...


class Planner(Protocol):
    is_slow: bool

    def plan(self, prompt: str, context: ReadContext | None = None) -> Plan: ...


# ---------------------------------------------------------------- demo planner

DEMO_HELP = (
    "The demo planner understands:\n"
    "  list [folder]\n"
    "  read <file>\n"
    "  find <name pattern> [in <folder>]\n"
    "  create <file> with <text>\n"
    "  delete <file>\n"
    "  move <file> to <new path>\n"
    "  copy <file> to <new path>\n"
    "  run <command>   (only when commands.enabled is true)\n"
    "  download <file> [from <folder>]"
)


def _clean(path: str) -> str:
    return path.strip().strip("\"'")


_REQUEST = re.compile(
    r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
    r"(?P<verb>download|fetch|read|show|open|view|display|cat|print)\s+(?:me\s+)?(?:the\s+)?(?:file\s+)?"
    r"(?P<rest>.+?)\s*[.?!]*$",
    re.I | re.S,
)
_QUOTED = re.compile(r"\"([^\"]+)\"|'([^']+)'")
_DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'?!,;]*")
_DRIVE_START = re.compile(r"[A-Za-z]:[\\/]")
_MORE_PATH = re.compile(r"[ \t]+([^\s\"'?!,;]+)")  # one more word after a space, which may still belong to the path


def _path_spans(text: str) -> list[tuple[int, int]]:
    """Where the full drive paths are in `text`, spaces inside them included: C:\\Users\\Me\\OneDrive - Work\\Pictures is ONE path.

    A plain pattern stops at the first space ("C:\\Users\\Me\\OneDrive"), which is a folder that does not exist. So after the first word, the
    following words are added only as long as the longer text is a path that really exists. "list C:\\Docs please" therefore stays "C:\\Docs"
    (there is no "C:\\Docs please"), and a path that is not there at all is read as before, up to the first space.
    """
    spans, taken = [], 0
    for first in _DRIVE_PATH.finditer(text):
        if first.start() < taken:
            continue
        end = best = first.end()
        for _ in range(8):  # a name with spaces in it rarely has more than a few words
            more = _MORE_PATH.match(text, end)
            if not more or _DRIVE_START.match(more.group(1)):
                break
            end = more.end()
            if os.path.exists(text[first.start():end].rstrip(".,;")):
                best = end
        spans.append((first.start(), best))
        taken = best
    return spans


def drive_paths(text: str) -> list[str]:
    """Every full drive path typed in `text` (see _path_spans), without a sentence's trailing . , ;"""
    return [text[a:b].rstrip(".,;") for a, b in _path_spans(text)]


def strip_drive_paths(text: str) -> str:
    """`text` with every drive path replaced by a space."""
    out, at = [], 0
    for a, b in _path_spans(text):
        out += [text[at:a], " "]
        at = b
    return "".join(out) + text[at:]
_NOT_A_FILE = {
    "it", "this", "that", "a", "an", "my", "me", "file", "files", "something", "anything", "everything", "all",
    "what", "whats", "which", "contents", "content", "of", "folder", "folders", "directory", "path", "inside",
    "details", "info", "text", "code", "lines", "list", "the", "in", "from", "at",
}
_DOTTED = re.compile(r"\.[A-Za-z]\w*$")


def _quick_target(rest: str, need_folder: bool, hint: str | None = None) -> str | None:
    """The file a short request names, or None when it is not plain enough to act on without a model."""
    if len(rest.split()) > 14:
        return None
    if need_folder and re.search(r"\b\d+\s*lines?\b", rest, re.I):
        return None  # "the first 20 lines": the model should trim it, not a plain dump
    quoted = [a or b for a, b in _QUOTED.findall(rest)]
    unquoted = _QUOTED.sub(" ", rest)
    drive = drive_paths(unquoted)
    drive += [q for q in quoted if _DRIVE_START.match(q)]
    plain = [q for q in quoted if not _DRIVE_START.match(q)]
    tokens = strip_drive_paths(unquoted).split()
    if re.match(r"""^["']?[A-Za-z]:[\\/]""", rest) and drive:
        path = drive[0]  # the whole request is one full path
    else:
        words = [t.rstrip(".,;?!") for t in tokens]
        dotted = [t for t in words if _DOTTED.search(t)]  # "show me contents of x.ts in C:\p": x.ts, not "contents"
        name = (plain[0] if plain else (dotted[0] if dotted else (words[0] if words else ""))).strip()
        if not name or name.lower() in _NOT_A_FILE or any(c in name for c in "*?"):
            return None
        if not drive and need_folder:
            if not hint or "." not in name:
                return None  # "read AskCore.tsx": which folder? Only the chat's last folder can answer that
            return os.path.join(hint, name)
        # no rstrip: join("C:", "x") means "x in the current folder of drive C", but join("C:\\", "x") is C:\x
        path = os.path.join(drive[-1], name) if drive else name
    return path


_LINES = re.compile(
    r"\b(?:(?P<which>first|top|head|last|bottom|tail|latest)\s+)?(?P<n>\d{1,5})\s*lines?\b(?:\s+(?:of|from|in))?", re.I
)
_BARE_LINES = re.compile(
    r"^(?:(?:ok|okay|now|and|also|then|please|can you|could you)[,\s]+)*(?:show\s+(?:me\s+)?)?(?:the\s+)?"
    r"(?P<body>(?:first|last|top|bottom|head|tail|latest)\s+\d{1,5}\s*lines?(?:\s+(?:of|from)\s+\S+(?:\s+file)?)?)\s*[.?!]*$",
    re.I,
)
_PRONOUNS = {
    "it", "this", "that", "file", "the", "same", "one", "again", "above", "previous", "please", "now", "also",
    "completely", "fully", "entire", "entirely", "whole", "all", "everything", "full", "complete", "thing", "me", "to", "us",
}


def quick_request(prompt: str, folder_hint: str | None = None, last_file: str | None = None) -> Plan | None:
    """Understand 'download <file> [from <folder>]' and 'read <file> in <folder>' without asking a model.

    A model can answer these wrongly (it may guess instead of looking), so plain ones skip it. They are also
    instant. `folder_hint` is the last folder mentioned in this chat, for "read AskCore.tsx" follow-ups, and
    `last_file` is the file the chat was last about, for "read 50 lines from it". "last 100 lines" means the end
    of the file (a log's newest lines). Anything else returns None, so the model gets it.
    """
    text = prompt.strip()
    if bare := _BARE_LINES.match(text):
        text = "read " + bare.group("body")  # "now the last 2 lines of it"
    m = _REQUEST.match(text)
    if not m:
        return None
    sending = m.group("verb").lower() in ("download", "fetch")
    rest, count, tail = m.group("rest"), None, False
    if not sending and (lines := _LINES.search(rest)):
        count = int(lines.group("n"))
        tail = (lines.group("which") or "").lower() in ("last", "bottom", "tail", "latest")
        rest = _LINES.sub(" ", rest, count=1)
    path = _quick_target(rest, need_folder=not sending, hint=folder_hint)
    if path is None and not sending and last_file:
        words = re.findall(r"[A-Za-z']+", rest.lower())
        if all(w in _PRONOUNS for w in words) and (words or count):  # "read it again", "50 lines from it"
            path = last_file
    if path is None or (count is not None and count < 1):
        return None
    if sending:
        return Plan(f"Send {os.path.basename(path)} to your phone", [Step(Action.SEND, path)])
    name = os.path.basename(path)
    if path.rsplit(".", 1)[-1].lower() in VIEWER_ONLY and "." in name:  # a picture or a PDF has no text to print: show it
        return Plan(f"Open {name} full screen", [Step(Action.READ, path, editor=True, view=True)])
    if count:
        return Plan(f"Read the {'last' if tail else 'first'} {count} lines of {name}", [Step(Action.READ, path, lines=count, tail=tail)])
    return Plan(f"Read {name}", [Step(Action.READ, path)])


def check_target(path: str, context: ReadContext | None) -> dict | None:
    """Ask the context whether a file exists and, if not, what was probably meant. None if it cannot tell."""
    look = getattr(context, "look_for", None)
    if look is None:
        return None
    try:
        return look(path)
    except (PolicyError, OSError, ValueError):
        return None


def correct_plan(plan: Plan, context: ReadContext | None) -> Plan:
    """A direct plan that names a file which is not there: fix an obvious typo, or explain what is missing."""
    step = plan.steps[0] if plan.steps else None
    if step is None or step.action not in (Action.READ, Action.SEND):
        return plan
    found = check_target(step.path, context)
    if not found or found.get("exists"):
        return plan
    wanted, folder = os.path.basename(step.path), found.get("folder", "")
    if found.get("folder_missing"):
        return Plan("", [], reply=f"I can't find the folder {folder}. Check the path, including the drive letter.")
    if found.get("auto"):
        fixed = os.path.join(folder, found["auto"])
        verb = "Send" if step.action is Action.SEND else "Open" if step.editor else "Read"
        target = " to your phone" if step.action is Action.SEND else " in the editor" if step.editor else ""
        return Plan(f"{verb} {found['auto']}{target} (you typed {wanted})", [replace(step, path=fixed)])
    if found.get("maybe"):
        return Plan("", [], reply=f"There is no file called {wanted} in {folder}. Did you mean: {', '.join(found['maybe'])}?")
    return Plan("", [], reply=f"There is no file called {wanted} in {folder}.")


def suggestion(path: str, context: ReadContext | None) -> str:
    """' Did you mean ...?' for a file that was not found, or an empty string."""
    found = check_target(path, context)
    if not found or found.get("exists") or found.get("folder_missing"):
        return ""
    names = [found["auto"]] if found.get("auto") else found.get("maybe") or []
    return f" Did you mean: {', '.join(names)}?" if names else ""


def quick_download(prompt: str) -> Plan | None:
    """Just the download half of `quick_request`."""
    plan = quick_request(prompt)
    return plan if plan and plan.steps[0].action is Action.SEND else None


# A question about files must be answered from the files. Small models sometimes answer from the chat instead.
_ABOUT_FILES = re.compile(r"(?:[A-Za-z]:[\\/])|\b(?:files?|folders?|directory|directories|path)\b|(?:^|\s)[\w-]*\.[A-Za-z]\w{0,6}\b", re.I)
NUDGE = (
    "You answered without using a tool, so you may be guessing from earlier messages. "
    "Call list_files, find_files or read_file now to look at the real files, then answer only from what they return."
)
UNVERIFIED = "\n\n(I didn't open your files to answer this, so it may be wrong. Try: read <file> in <folder>.)"


def needs_look(prompt: str) -> bool:
    return bool(_ABOUT_FILES.search(prompt))


def last_folder(history: list[dict]) -> str | None:
    """The last folder the user named earlier in this chat, so "summarise AskCore.tsx" knows where to look."""
    for message in reversed(history):
        if message.get("role") != "user":
            continue
        found = drive_paths(message.get("content") or "")
        if found:
            path = found[-1]
            return os.path.dirname(path) if os.path.splitext(path)[1] else path
    return None


_CHANGING_WORDS = re.compile(r"\b(create|write|save|delete|remove|move|rename|run|execute|edit|replace|change)\b", re.I)


def mentioned_paths(prompt: str) -> list[str]:
    """Full paths the user typed, when the request only reads. Used to ask for access before any model is started."""
    if _CHANGING_WORDS.search(prompt):
        return []
    return drive_paths(prompt)


_THAT_FILE = re.compile(r"\b(?:that|this|the same|the|my)\s+(?:file|one|script|log|document|config)\b", re.I)
_CHANGES_OR_SEARCHES = re.compile(r"\b(create|write|save|delete|remove|move|copy|rename|run|execute|find|search|download|fetch)\b", re.I)
_FILE_NAME = re.compile(r"[\w.-]*\.[A-Za-z]\w{0,7}")


def _file_names(prompt: str) -> list[str]:
    without_paths = strip_drive_paths(prompt)
    names = []
    for token in _FILE_NAME.findall(without_paths):
        if token.lower() in ("e.g", "i.e") or token.endswith("."):
            continue
        if token.startswith(".") and re.search(re.escape(token) + r"\s+files\b", prompt, re.I):
            continue  # ".bak files" is a kind of file, not one file
        if token not in names:
            names.append(token)
    return names[:2]


class Facts(str):
    """Text added to the model's prompt. It also keeps the raw files, so a timeout can still show them."""

    raw: list

    def __new__(cls, text: str, raw=()):
        obj = str.__new__(cls, text)
        obj.raw = list(raw)
        return obj


_EDIT = re.compile(
    r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:replace|change)\s+"
    r"(?P<old>\"[^\"]+\"|'[^']+'|\S+)\s+(?:with|to|by)\s+(?P<new>\"[^\"]*\"|'[^']*'|\S+)\s+in\s+(?P<rest>.+?)\s*[.?!]*$",
    re.I | re.S,
)


def _unquote(text: str) -> str:
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'" else text


def quick_edit(prompt: str, context: ReadContext | None, hint: str | None, last_file: str | None) -> Plan | None:
    """'replace X with Y in <file>' without a model: the app makes the change itself and shows it for approval."""
    m = _EDIT.match(prompt.strip())
    if not m:
        return None
    old, new, rest = _unquote(m.group("old")), _unquote(m.group("new")), m.group("rest")
    path = _quick_target(rest, need_folder=True, hint=hint)
    if path is None and last_file:
        words = re.findall(r"[A-Za-z']+", rest.lower())
        if words and all(w in _PRONOUNS for w in words):
            path = last_file  # "... in it", "... in that file"
    if path is None:
        return None
    found = check_target(path, context)
    if found and not found.get("exists"):
        if not found.get("auto"):
            # no close match: let the same code that explains a missing file say so ("There is no file called ...")
            return correct_plan(Plan(f"Read {os.path.basename(path)}", [Step(Action.READ, path)]), context)
        path = os.path.join(found["folder"], found["auto"])  # a typo in the file name: use the real one
    made = make_edit(path, old, new, context)
    if isinstance(made, str):
        reason = made.removeprefix("error: ")
        return Plan("", [], reply=reason[:1].upper() + reason[1:])
    return Plan(f"Replace “{_brief(old)}” with “{_brief(new)}” in {os.path.basename(path)}", [made])


_OPEN_EDITOR = re.compile(
    r"^(?:(?:ok|okay|now|so|and|then|please)[,\s]+)*"
    r"(?:(?:i\s+(?:want|need|would\s+like|wanna)\s+to|let\s+me|can\s+i|could\s+i|lets|let's|i\s+will|i'll)\s+)?"
    r"(?:edit|modify|update|change)\s+(?:(?:the|this|that|my)\s+)?(?:file\s+)?(?P<rest>.+?)"
    r"(?:\s+(?:live|manually|myself|by\s+hand|here|on\s+(?:my\s+)?phone))*\s*[.?!]*$",
    re.I | re.S,
)
_EDIT_FILLER = re.compile(r"\b(?:live|manually|myself|by\s+hand|here|on\s+(?:my\s+|the\s+)?(?:phone|mobile))\b", re.I)
_INSTRUCTION_WORDS = re.compile(r"\b(with|to|so|by|because|then|into|from|add|remove|replace|set|make)\b", re.I)


def quick_open_editor(prompt: str, hint: str | None, last_file: str | None) -> Plan | None:
    """'edit that file', 'let me edit app.cfg in C:\\x': open the phone's editor. A request that says WHAT to change goes to the AI."""
    m = _OPEN_EDITOR.match(prompt.strip())
    if not m:
        return None
    rest = _EDIT_FILLER.sub(" ", m.group("rest")).strip()  # "on my phone", "manually": how, not which file
    words = re.findall(r"[A-Za-z']+", rest.lower())
    path = None
    if not words or all(w in _PRONOUNS or w in ("file", "it") for w in words):
        path = last_file  # "that file", "it", or nothing left but filler: the file the chat was about
    elif not _INSTRUCTION_WORDS.search(rest):
        path = _quick_target(rest, need_folder=True, hint=hint)
    if path is None:
        return None
    return Plan(f"Open {os.path.basename(path)} in the editor", [Step(Action.READ, path, editor=True)])


_OPEN_VIEW = re.compile(
    r"^(?:(?:ok|okay|now|so|and|then|please|can you|could you)[,\s]+)*(?:open|view|show|display)\s+(?:me\s+)?"
    r"(?:(?:the|this|that|my)\s+)?(?:file\s+)?(?P<rest>.+?)\s+(?:(?:in|on|as|into|with)\s+)?(?:a\s+|the\s+|my\s+)?"
    r"(?:new\s+(?:screen|window|tab|page)|full[\s-]?screen|canvas|viewer|separate\s+(?:screen|window))"
    r"(?:\s+(?:in|from|at|under|inside)\s+(?P<tail>.+?))?\s*[.?!]*$",  # "... in a new screen in C:\folder"
    re.I | re.S,
)
VIEWER_ONLY = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg", "pdf"}  # not text: they can only be looked at


def quick_open_view(prompt: str, hint: str | None, last_file: str | None) -> Plan | None:
    """'open it full screen', 'view app.cfg in a new screen': the phone's full-screen viewer, where it can also be edited."""
    m = _OPEN_VIEW.match(prompt.strip())
    if not m:
        return None
    rest = m.group("rest")
    words = re.findall(r"[A-Za-z']+", rest.lower())
    if m.group("tail"):
        rest = f"{rest} in {m.group('tail')}"  # the folder came after the words about the screen
    glue = ("file", "it", "in", "on", "as", "into", "with")  # "view that file in the viewer": nothing here names a file
    path = last_file if not words or all(w in _PRONOUNS or w in glue for w in words) else _quick_target(rest, need_folder=True, hint=hint)
    if path is None:
        return None
    return Plan(f"Open {os.path.basename(path)} full screen", [Step(Action.READ, path, editor=True, view=True)])


_PICTURES = r"(?:images?|imgs?|photos?|pictures?|pics?|screen\s*shots?)"
_ASK = (r"^(?:(?:ok|okay|now|so|and|then|please|hey)[,\s]+)*"
        r"(?:(?:i\s+(?:want|need|would\s+like|wanna)\s+to|let\s+me|can\s+i|could\s+i|can\s+you|could\s+you|lets|let's)\s+)?")
_GALLERY = re.compile(
    _ASK + r"(?:show|open|view|see|display|browse|preview|look\s+at|look\s+through|go\s+through)(?:\s+me)?\s+"
    r"(?:(?:all|any|some|the|my|these|those|every|of)\s+)*" + _PICTURES + r"(?:\s+files?)?"
    r"(?:\s+(?:in|from|of|inside|under|at|within)\s+(?:(?:the|this|that|my)\s+)?(?:(?:folder|directory|path|location)\s+)?(?P<rest>.+?))?\s*[.?!]*$",
    re.I | re.S,
)
_HERE = {"it", "this", "that", "there", "here", "folder", "directory", "same", "the", "last", "previous", "one", "again"}
_SCREEN_WORDS = re.compile(r"\b(?:new\s+(?:screen|window|tab|page)|full[\s-]?screen|viewer|canvas|separate\s+(?:screen|window))\b", re.I)


def _folder_label(folder: str) -> str:
    """A folder as a person says it: its own name, or "the workspace" for the writable folder itself."""
    return "the workspace" if folder in (".", "") else os.path.basename(folder.rstrip("\\/")) or folder


def _gallery_folder(rest: str, hint: str | None, last_file: str | None) -> str:
    """The folder a picture request means: the one named, else the one the chat was last in, else the writable workspace."""
    rest = rest.strip(" .,;")
    words = re.findall(r"[A-Za-z']+", rest.lower())
    if rest and not all(w in _HERE for w in words):
        named = drive_paths(rest) or [a or b for a, b in _QUOTED.findall(rest)]
        if named:
            return named[0]
        if hint and os.path.isdir(os.path.join(hint, rest)):
            return os.path.join(hint, rest)  # "in Screenshots", when the chat was already in the folder that holds it
        return rest
    if hint:
        return hint
    return os.path.dirname(last_file) if last_file else "."


def quick_gallery(prompt: str, hint: str | None, last_file: str | None) -> Plan | None:
    """'show me the photos in a folder', 'open any photo file': a grid of the folder's pictures, each one a tap from the viewer and editor."""
    m = _GALLERY.match(prompt.strip())
    if not m:
        return None
    rest = m.group("rest") or ""
    if _SCREEN_WORDS.search(rest):
        return None  # "open the image in a new screen": that is about HOW to show a picture, not which folder
    folder = _gallery_folder(rest, hint, last_file)
    return Plan(f"Show the pictures in {_folder_label(folder)}", [Step(Action.READ, folder, editor=True, gallery=True)])


_BROWSE = re.compile(
    _ASK + r"(?:(?:open|browse|explore|walk\s+through|go\s+(?:in)?to|dig\s+into|look\s+(?:in|inside|through))\s+(?:me\s+)?"
    r"(?:(?:the|my|this|that)\s+)?(?:(?:folder|directory|dir)\s+)?|(?:browse|explore)\s+)(?P<rest>.*?)\s*[.?!]*$",
    re.I | re.S,
)
_FOLDER_WORD = re.compile(r"\b(?:folders?|directory|directories|dir)\b", re.I)
_BROWSE_ONLY = {"files", "folders", "my files", "my folders", "all files", "all folders", "my pc", "the pc", "my computer", "this pc"}


def quick_browse(prompt: str, hint: str | None, last_file: str | None) -> Plan | None:
    """'open the folder Reports on drive C', 'browse my folders', 'open <a folder path>': the file browser, to walk through it by tapping."""
    text = prompt.strip()
    m = _BROWSE.match(text)
    if not m:
        return None
    rest = m.group("rest").strip(" .,;")
    if _SCREEN_WORDS.search(rest) or _IMAGE_EXT.search(rest):
        return None
    said_folder = bool(_FOLDER_WORD.search(text)) or text.lower().startswith(("browse", "explore"))
    named = drive_paths(rest) or [a or b for a, b in _QUOTED.findall(rest)]
    if rest.lower() in _BROWSE_ONLY or (not rest and said_folder):
        folder = "."
    elif named:
        folder = named[0]
        only_the_path = rest.strip(" \"'") == folder
        inside = re.match(r"(?P<name>.+?)\s+(?:in|inside|under|from)\s+", rest, re.I)
        if inside and not only_the_path:  # "Reports in C:/Work": the folder called Reports inside it
            child = os.path.join(folder, inside.group("name").strip(" \"'"))
            if os.path.isdir(child):
                folder = child
            elif not said_folder:
                return None  # "open README in <folder>": a file inside it, for the other requests
        elif not only_the_path and not said_folder:
            return None
        if not said_folder and not os.path.isdir(folder):
            return None  # a path that is not a folder (a file, say) is for the other requests to handle
    elif said_folder and rest:
        folder = _gallery_folder(rest, hint, last_file)
    else:
        return None
    return Plan(f"Browse {_folder_label(folder)}", [Step(Action.READ, folder, editor=True, browse=True)])


_IMAGE_EDIT = re.compile(
    _ASK + r"(?:edit|retouch|photoshop|annotate|crop|markup|mark\s+up|touch\s+up|draw\s+on|blur|redact)\s+(?:(?:the|this|that|my|a|an|some|any)\s+)?"
    r"(?:one\s+of\s+(?:the\s+|my\s+)?)?" + _PICTURES + r"s?(?:\s+file)?"
    r"(?:\s+(?:in|from|of|inside|under|at)\s+(?:(?:the|this|that|my)\s+)?(?:(?:folder|directory|path|location)\s+)?(?P<rest>.+?))?\s*[.?!]*$",
    re.I | re.S,
)


def quick_image_edit(prompt: str, hint: str | None, last_file: str | None) -> Plan | None:
    """'edit that photo', 'photoshop an image in a folder': straight into the picture editor when the chat already has a picture, else pick one."""
    m = _IMAGE_EDIT.match(prompt.strip())
    if not m:
        return None
    rest = (m.group("rest") or "").strip()
    if not rest and last_file and imaging.is_image(last_file):
        return Plan(f"Open {os.path.basename(last_file)} in the picture editor", [Step(Action.READ, last_file, editor=True)])
    folder = _gallery_folder(rest, hint, last_file)
    return Plan(f"Pick a picture to edit in {_folder_label(folder)}", [Step(Action.READ, folder, editor=True, gallery=True)])


_OPEN_IMAGE = re.compile(
    _ASK + r"(?:open|view|show|display|see|preview|look\s+at)\s+(?:me\s+)?(?:(?:the|this|that|my)\s+)?(?:(?:image|photo|picture|pic|img|screenshot|file)\s+)?(?P<rest>.+?)\s*[.?!]*$",
    re.I | re.S,
)
_IMAGE_EXT = re.compile(r"\.(?:png|jpe?g|gif|webp|bmp|ico|svg)\b", re.I)


def quick_open_image(prompt: str, hint: str | None, last_file: str | None) -> Plan | None:
    """'open photo.png', 'show me Screenshot (4).png in a folder': the picture, in the viewer (where it can be edited)."""
    m = _OPEN_IMAGE.match(prompt.strip())
    if not m or not _IMAGE_EXT.search(m.group("rest")):
        return None
    # In the folder the chat was last in; failing that, in the writable workspace (a name that is not there is explained, with close matches).
    path = _quick_target(m.group("rest"), need_folder=True, hint=hint) or _quick_target(m.group("rest"), need_folder=False)
    if not path or not _IMAGE_EXT.search(path):
        return None
    return Plan(f"Open {os.path.basename(path)} full screen", [Step(Action.READ, path, editor=True, view=True)])


_POLITE = r"(?:(?:please|pls|kindly|hey|ok(?:ay)?|now)[ ,]+)*(?:(?:can|could|would|will) you[ ,]+(?:please[ ,]+)?|i (?:want|need|would like|'d like) (?:you )?to[ ,]+|let'?s[ ,]+|just[ ,]+|go ahead and[ ,]+)?"
_NOUN = r"pc|computer|laptop|machine|desktop|workstation|system"
_PC = rf"(?:(?:my|the|this|our)\s+)?(?:{_NOUN})"
_POWER = [
    ("screen_on", re.compile(rf"^{_POLITE}(?:(?:turn|switch|put|bring)\s+(?:on|back\s+on)|wake|(?:bring|put)\s+back|restore|un-?black(?:\s*out)?)\s+(?:(?:my|the|this|our)\s+)?(?:screen|monitor|display)(?:\s+(?:on|back(?:\s+on)?))?\W*$|^{_POLITE}(?:(?:turn|switch)\s+(?:my|the|this|our)?\s*(?:screen|monitor|display)\s+(?:back\s+)?on)\W*$", re.I)),
    ("screen_off", re.compile(rf"^{_POLITE}(?:(?:turn|switch|black|blank)\s+off|black\s*out|blank|dark(?:en)?)\s+(?:(?:my|the|this|our)\s+)?(?:screen|monitor|display)\W*$|^{_POLITE}(?:(?:turn|switch)\s+(?:my|the|this|our)?\s*(?:screen|monitor|display)\s+off)\W*$", re.I)),
    ("unlock", re.compile(rf"^{_POLITE}unlock\s+(?:{_PC}|(?:(?:my|the|this)\s+)?(?:lock\s*screen|screen|windows))\W*$", re.I)),
    ("hibernate", re.compile(rf"^{_POLITE}hibernate\s+{_PC}\W*$", re.I)),
    ("restart", re.compile(rf"^{_POLITE}(?:restart|reboot)\s+{_PC}\W*$", re.I)),
    ("shutdown", re.compile(rf"^{_POLITE}(?:shut\s*down|power\s*off|turn\s*off|switch\s*off)\s+{_PC}\W*$", re.I)),
    ("sleep", re.compile(rf"^{_POLITE}(?:(?:put|send|set|make|let)\s+{_PC}\s+(?:(?:to|into)\s+)?(?:sleep|standby)|(?:sleep|suspend)\s+{_PC})\W*$", re.I)),
    ("lock", re.compile(rf"^{_POLITE}lock\s+(?:(?:my|the|this)\s+)?(?:{_NOUN}|screen)\W*$", re.I)),
]


def quick_power(prompt: str) -> Plan | None:
    """"put my PC to sleep", "lock the computer", "restart my laptop": a plan with one step the person approves.

    Only plain commands count. A question such as "how do I put my PC to sleep?" goes to the model like any other.
    """
    text = prompt.strip()
    for key, pattern in _POWER:
        if pattern.match(text):
            return Plan(POWER_ACTIONS[key].summary, [Step(Action.POWER, ".", content=key)])
    return None


_STATUS_WORDS = re.compile(
    r"\b(battery|charging|charge level|how much charge|network speed|internet speed|wi-?fi speed|connection speed|download speed|upload speed|"
    r"pc status|system status|computer status|laptop status|cpu usage|processor usage|memory usage|ram usage|disk space|storage left|free space|"
    r"how(?:'s| is) my (?:pc|laptop|computer) doing)\b", re.I)
_NOT_STATUS = re.compile(r"^(how (?:do|can|to|should)|why|write|create|make|edit|delete|read|open|find|explain|what (?:is|does) (?:a|an|the)\b|set|change|fix|improve|extend)", re.I)


# ---------------------------------------------------------------------------------------------------------------- apps and Teams

_OPEN_APP = re.compile(
    rf"^{_POLITE}(?:open|launch|start|run|bring up|switch to|go to)\s+(?:the\s+|my\s+)?(?P<name>[\w .+#&'-]{{1,40}}?)(?:\s+(?:app|application|program|software))?(?:\s+(?:on|in)\s+(?:my\s+)?(?:{_NOUN}))?\W*$", re.I)
_TEAMS_WORD = re.compile(r"\bteams\b", re.I)
_MSG_WORDS = re.compile(r"\b(messages?|chats?|pings?|pinged|unread|notifications?|mentions?|said|say|replied)\b", re.I)
_SEPARATOR = re.compile(r"\s*(?::|—|–|\s-\s|,|\bsaying\b|\bthat\b|\bwith\b)\s*", re.I)
_TEAMS_QUOTED = re.compile(r"[\"“”'‘’](?P<msg>.+)[\"“”'‘’]\W*$")
_AND_SEND = re.compile(r"\s*(?:,|\band\b|\bthen\b|&)\s*(?:send|reply|post)(?:\s+(?:it|that|this|the message))?\W*$", re.I)
_ON_TEAMS = re.compile(r"\s+(?:on|in|via|through|over)\s+(?:microsoft\s+)?teams\b", re.I)
_DOING_SOMETHING = re.compile(r"^(?:summari[sz]e|read|show|find|open|create|write|delete|copy|move|download|list|check|email|mail|upload|zip|rename|run|fix|make|edit|save|search|print|share|forward)\b", re.I)
_REPLY_VERBS = r"reply|respond|answer"
_TELL_VERBS = r"send|text|ping|message|tell|dm|write|chat"


def _clean_message(text: str) -> str:
    text = _AND_SEND.sub("", text.strip())
    quoted = _TEAMS_QUOTED.match(text)
    if quoted:
        text = quoted.group("msg")
    return text.strip()


def _clean_person(text: str) -> str:
    text = _ON_TEAMS.sub("", text)
    text = re.sub(r"^(?:to|for)\s+", "", text.strip(), flags=re.I)
    text = re.sub(r"\s+(?:a|the)?\s*(?:message|reply)$", "", text, flags=re.I)
    return text.strip(" ,:-")


def _split_person_message(rest: str, need_separator: bool) -> tuple[str, str] | None:
    """"vinod: I'm coming" / "vinod saying on my way" / "vinod "on my way"" / (no separator allowed) "vinod on my way" -> ("vinod", "I'm coming")."""
    rest = _ON_TEAMS.sub("", rest.strip(), count=1)
    sep = _SEPARATOR.search(rest)
    quoted = re.search(r"[\"“‘](?P<msg>.+)[\"”’]\W*$", rest)
    if sep and sep.start() > 0:
        who, msg = _clean_person(rest[:sep.start()]), _clean_message(rest[sep.end():])
    elif quoted and quoted.start() > 0:
        who, msg = _clean_person(rest[:quoted.start()]), _clean_message(quoted.group("msg"))
    elif need_separator:
        return None
    else:
        parts = rest.split(None, 1)
        if len(parts) < 2:
            return None
        who, msg = _clean_person(parts[0]), _clean_message(parts[1])
    if not who or not msg or len(who.split()) > 4 or re.match(r"(?:the|a|an|my|this|that|his|her|their|email|e-mail|mail|message|it)\b", who, re.I):
        return None  # "reply to the email" is not a person
    return who, msg


def _teams_plan(kind: str, who: str = "", text: str | None = None) -> Plan:
    if kind == "teams_unread":
        return Plan("Read your unread Teams messages", [Step(Action.APP, ".", app_kind=kind)])
    if kind == "teams_chat":
        return Plan(f"Read your Teams messages with {who}", [Step(Action.APP, ".", pattern=who, app_kind=kind)])
    return Plan(f"Reply to {who} in Teams", [Step(Action.APP, ".", pattern=who, content=text, app_kind="teams_reply")])


def quick_teams(prompt: str, context: ReadContext | None) -> Plan | None:
    """"show my unread Teams messages", "what did Vinod say on Teams", "reply to Vinod: I'm coming" and, straight after reading a chat, "reply: I'm coming"."""
    text = " ".join(prompt.strip().split())
    if not text or len(text) > 1600:
        return None
    last = getattr(context, "last_teams", None)
    last = last() if last else None
    on_teams = bool(_TEAMS_WORD.search(text))
    low = text.lower()

    reply = re.match(rf"^{_POLITE}(?:{_REPLY_VERBS})\b[ ,:-]*(?P<rest>.*)$", text, re.I)
    if reply:
        rest = reply.group("rest").strip()
        named = re.match(r"^to\s+(?P<r>.+)$", rest, re.I)
        if named:  # "reply to Vinod: I'm coming"
            pronoun = re.match(r"^(?:him|her|them|that|it)\b[ ,:-]*(?P<m>.+)$", named.group("r"), re.I)
            if last and pronoun and (msg := _clean_message(pronoun.group("m"))):  # "reply to him: I'm coming", straight after reading a chat
                return _teams_plan("teams_reply", last, msg)
            if split := _split_person_message(named.group("r"), need_separator=not on_teams):
                return _teams_plan("teams_reply", *split)
            return None
        if last and rest:  # "reply: I'm coming" / "reply saying I'm coming", straight after reading a chat
            if msg := _clean_message(re.sub(r"^(?:saying|that|with)\s+", "", rest, flags=re.I)):
                return _teams_plan("teams_reply", last, msg)
        if rest and (split := _split_person_message(rest, need_separator=not on_teams)):  # "reply Vinod I'm coming on Teams"
            return _teams_plan("teams_reply", *split)
        return None

    tell = re.match(rf"^{_POLITE}(?:{_TELL_VERBS})\b(?P<rest>.*)$", text, re.I)
    if tell and (on_teams or last):
        rest = re.sub(r"^(?:a\s+)?(?:message\s+)?(?:to\s+)?", "", tell.group("rest").strip(), flags=re.I)
        verb = text.split()[0].lower() if text.split() else ""
        if split := _split_person_message(rest, need_separator=verb in ("send", "write", "chat")):
            return _teams_plan("teams_reply", *split)

    if last and (m := re.match(r"^(?P<msg>.{1,200}?)\s*(?:,|\band\b|\bthen\b|&)\s*(?:send|reply)(?:\s+(?:it|that|this))?\W*$", text, re.I)) \
            and not _DOING_SOMETHING.match(m.group("msg")) and not re.search(r"[\\/]|\.\w{2,5}\b", m.group("msg")):  # "I'm coming, send it": a message. "summarise the file and send": a different job
        return _teams_plan("teams_reply", last, m.group("msg").strip())

    if on_teams and _MSG_WORDS.search(text) and not re.search(r"\b(how|why|install|download|update|fix|set ?up|disable|enable|turn|mute)\b", low):
        who = None
        for pattern in (r"\b(?:from|with|of|by)\s+(?P<who>[\w.'-]+(?:\s+[\w.'-]+){0,2}?)(?:\s+(?:on|in)\s+(?:microsoft\s+)?teams)?\W*$",
                        r"\b(?:what\s+did|did)\s+(?P<who>[\w.'-]+(?:\s+[\w.'-]+){0,2}?)\s+(?:say|send|write|ping|reply)",
                        r"\b(?P<who>[\w.-]+)'s\s+(?:latest\s+|last\s+|new\s+)?(?:messages?|chat)"):
            m = re.search(pattern, text, re.I)
            if m and m.group("who").lower() not in ("teams", "microsoft", "my", "the", "me", "you", "all", "unread", "new", "latest", "recent"):
                who = _clean_person(m.group("who"))
                break
        return _teams_plan("teams_chat", who) if who else _teams_plan("teams_unread")
    return None


def quick_app_open(prompt: str, context: ReadContext | None) -> Plan | None:
    """"open Teams", "launch Excel", "open Chrome": anything in this PC's Start menu, found by name."""
    match = _OPEN_APP.match(prompt.strip())
    if not match:
        return None
    name = " ".join(match.group("name").split())
    apps = getattr(context, "start_apps", None)
    if not apps or re.search(r"[\\/:]|\.\w{1,5}$|\b(?:file|folder|photo|picture|pictures|image|document|documents|downloads?|desktop|drive)\b", name, re.I):
        return None
    from .appctl import find_app

    found = find_app(name, apps())
    if found is None:
        return None
    return Plan(f"Open {found.name}", [Step(Action.APP, ".", pattern=found.name, app_kind="open")])


def quick_status(prompt: str) -> Plan | None:
    """"what's my battery?", "network speed", "how is my PC doing": answered from the PC's own readings, not guessed by a model."""
    text = prompt.strip()
    if len(text) > 70 or _NOT_STATUS.match(text) or not _STATUS_WORDS.search(text):
        return None
    return Plan("PC status", [], reply=sysinfo.describe(sysinfo.current()))


def quick_for(prompt: str, context: ReadContext | None) -> Plan | None:
    """The requests the app can do itself, using what the chat knows: the last folder, and the last file (for "it")."""
    last = getattr(context, "last_file", None)
    hint, last_file = last_folder(history_for(context)), (last() if last else None)
    return (quick_power(prompt) or quick_teams(prompt, context) or quick_status(prompt) or quick_edit(prompt, context, hint, last_file) or quick_gallery(prompt, hint, last_file)
            or quick_browse(prompt, hint, last_file) or quick_image_edit(prompt, hint, last_file) or quick_open_image(prompt, hint, last_file) or quick_open_view(prompt, hint, last_file)
            or quick_open_editor(prompt, hint, last_file) or quick_app_open(prompt, context) or quick_request(prompt, hint, last_file))


def check_cancel() -> None:
    token = CURRENT.get()
    if token:
        token.check()


def timeout_fallback(exc: Exception, facts: str) -> Plan | None:
    """The model ran out of time but the app already has the file: show the file instead of a bare error."""
    raw = getattr(facts, "raw", None)
    if "did not answer within" not in str(exc) or not raw:
        return None
    path, text = raw[0]
    rows = text.splitlines()
    shown, rest = chr(10).join(rows[:40]), len(rows) - 40
    more = f"{chr(10)}... ({rest} more lines)" if rest > 0 else ""
    gap = chr(10) * 2
    return Plan("", [], reply=(
        f"The AI didn't answer in time, so here is the start of {os.path.basename(path)} instead:{gap}{shown}{more}{gap}"
        'Try again, or ask for a smaller piece, such as "read 50 lines from it".'
    ))


def ground(prompt: str, context: ReadContext | None, hint: str | None, limit: int = MAX_READ_CHARS) -> "Facts":
    """Look at the file (or folder) a request names, and hand the model the real text.

    A small model asked to "summarise X" often lists the folder, or answers from the chat, instead of reading X.
    So when the request plainly names a file and a folder, the app reads it. The model is told what was found
    (or that nothing was), and can only answer from that. Changes and searches are left to the model's tools.
    """
    if context is None or _CHANGES_OR_SEARCHES.search(prompt):
        return Facts("")
    folders = drive_paths(prompt)
    names = _file_names(prompt)
    folder = None
    if folders:
        folder = os.path.dirname(folders[-1]) if os.path.splitext(folders[-1])[1] else folders[-1]
    elif hint:
        folder = hint
    last = getattr(context, "last_file", None)
    last = last() if last else None
    if last and not names and _THAT_FILE.search(prompt):  # "I want to edit that file"
        folder, names = os.path.dirname(last), [os.path.basename(last)]
    if not folder:
        return Facts("")
    facts, raw = [], []
    for name in names:
        path = os.path.join(folder, name)
        found = check_target(path, context)
        if found and not found.get("exists") and not found.get("folder_missing"):
            if found.get("auto"):  # "actionClient.ts" when the real file is actionsClient.ts
                path = os.path.join(found["folder"], found["auto"])
                facts.append(f'NOTE: the user wrote "{name}" but the real file is "{found["auto"]}". The app read that one. '
                             "Mention the corrected name in your answer.")
            elif found.get("maybe"):
                facts.append(f'NOTE: there is no file named "{name}" in {found["folder"]}. Close names: {", ".join(found["maybe"])}. '
                             "Ask which one they meant. Do not list the whole folder.")
                continue
            else:
                facts.append(f'NOTE: there is no file named "{name}" in {found["folder"]}. Say so briefly. Do not list the whole folder.')
                continue
        try:
            text = context.read_file(path)
            raw.append((path, text))
            if len(text) > limit:
                text = text[:limit] + "\n... (truncated)"
            facts.append(f"FILE {path}:\n{text}")
        except (PolicyError, OSError, ValueError) as exc:
            facts.append(f"FILE {path} could not be read: {exc}")
    if not facts and folders and not os.path.splitext(folders[-1])[1]:
        try:
            names = context.list_dir(folder)
            facts.append(f"FOLDER {folder} contains:\n" + "\n".join(names[:200]))
        except (PolicyError, OSError, ValueError) as exc:
            facts.append(f"FOLDER {folder} could not be read: {exc}")
    if not facts:
        return Facts("")
    return Facts(
        "\n\n[The app already looked at this for you. Answer only from it; do not guess.]\n"
        + "\n\n".join(facts)
        + "\n\n[That is all the text. Now do exactly what my request above asks, using only that text. "
        "If I asked for a summary or an explanation, write 3 to 5 plain sentences about what the file does. "
        "Do not just say where the file is.]",
        raw,
    )


class DemoPlanner:
    is_slow = False

    def __init__(self, reason: str | None = None):
        self._reason = reason  # why no AI model is connected, shown when a request needs one

    _LIST = re.compile(r"^(?:list|ls)(?:\s+(?P<path>.+))?$", re.I | re.S)
    _FIND = re.compile(r"^(?:find|search)\s+(?P<pattern>\S+)(?:\s+in\s+(?P<path>.+))?$", re.I | re.S)
    _WRITE = re.compile(
        r"^(?:create|write|save)\s+(?P<path>\S+)\s+(?:with|containing)\s+(?P<content>.+)$",
        re.I | re.S,
    )
    _READ = re.compile(r"^(?:read|show|open)\s+(?P<path>.+)$", re.I | re.S)
    _DELETE = re.compile(r"^(?:delete|remove)\s+(?P<path>.+)$", re.I | re.S)
    _MOVE = re.compile(r"^(?P<verb>move|rename|copy)\s+(?P<src>.+?)\s+to\s+(?P<dst>.+)$", re.I | re.S)
    _RUN = re.compile(r"^(?:run|exec)\s+(?P<command>.+)$", re.I | re.S)

    def plan(self, prompt: str, context: ReadContext | None = None) -> Plan:
        text = prompt.strip()
        if quick := quick_for(text, context):
            return correct_plan(quick, context)
        if m := self._LIST.match(text):
            path = _clean(m.group("path") or ".")
            return Plan(f"List {path}", [Step(Action.READ, path)])
        if m := self._FIND.match(text):
            path = _clean(m.group("path") or ".")
            pattern = _clean(m.group("pattern"))
            return Plan(f"Find {pattern} in {path}", [Step(Action.READ, path, pattern=pattern)])
        if m := self._WRITE.match(text):
            path = _clean(m.group("path"))
            return Plan(
                f"Create or replace {path}",
                [Step(Action.WRITE, path, m.group("content").strip())],
            )
        if m := self._READ.match(text):
            path = _clean(m.group("path"))
            return Plan(f"Read {path}", [Step(Action.READ, path)])
        if m := self._DELETE.match(text):
            path = _clean(m.group("path"))
            return Plan(f"Delete {path}", [Step(Action.DELETE, path)])
        if m := self._MOVE.match(text):
            src, dst = _clean(m.group("src")), _clean(m.group("dst"))
            copy = m.group("verb").lower() == "copy"
            action = Action.COPY if copy else Action.MOVE
            return Plan(f"{'Copy' if copy else 'Move'} {src} to {dst}", [Step(action, src, target=dst)])
        if m := self._RUN.match(text):
            command = m.group("command").strip()
            return Plan("Run a command", [Step(Action.RUN, ".", command)])
        if self._reason:
            typed = DEMO_HELP.split(chr(10), 1)[1]
            raise PlanError(
                "I didn't understand that, because no AI model is connected right now, "
                "so I only understand typed commands.\n"
                f"Why: {self._reason}\n\nThese work without an AI:\n{typed}"
            )
        raise PlanError(f"I did not understand that.\n{DEMO_HELP}")


# -------------------------------------------------------------- Ollama planner

# transport(url, payload, timeout_seconds) -> parsed JSON; payload None means GET.
Transport = Callable[[str, "dict | None", float], dict]

SYSTEM_PROMPT = """You manage files on the user's PC, from their phone. Use only the folders listed below; relative paths start in the first one.
Reading tools (list_files, read_file, find_files) run now. Every other tool only PROPOSES: the user must approve it first.
Rules:
- Look with a tool before answering about a file or folder. Never guess, and never answer from earlier messages: they may be out of date.
- To change part of a file use edit_file (old text, new text). Use write_file only for a new or tiny file. Never rewrite a whole big file.
- If the user wants to change a file but did not say how, ask what change they want.
- To give the user a file use download_file. Read-only folders are never changed.
- run_command (if offered) shows the exact command for approval, cannot be undone, and you will not see its output.
- Reply in the language the user writes in (English unless they use another). One or two plain sentences. If they are only chatting, answer without tools.
- The user can open any file in the app's own editor to change it by hand. Never retype a whole file into your answer."""


# Short on purpose: the model re-reads these every time it is started, and this PC reads about 25 words a second.
def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_S = {"type": "string"}
_PATH = {"path": _S}

TOOLS = [
    _tool("list_files", "List a folder.", _PATH, []),
    _tool("read_file", "Read a text file.", _PATH, ["path"]),
    _tool("find_files", "Find files by name in a folder and below. Pattern like *.bak.", {"pattern": _S, "path": _S}, ["pattern", "path"]),
    _tool("write_file", "Propose writing a whole new or tiny file.", {"path": _S, "content": _S}, ["path", "content"]),
    _tool("edit_file", "Propose replacing text in a file. old must appear exactly once.", {"path": _S, "old": _S, "new": _S}, ["path", "old", "new"]),
    _tool("delete_file", "Propose deleting a file.", _PATH, ["path"]),
    _tool("download_file", "Propose sending a file to the user's phone.", _PATH, ["path"]),
    _tool("move_file", "Propose moving or renaming one file.", {"path": _S, "destination": _S}, ["path", "destination"]),
    _tool("copy_file", "Propose copying one file.", {"path": _S, "destination": _S}, ["path", "destination"]),
]

RUN_TOOL = _tool("run_command", "Propose one shell command (PowerShell) in a writable folder.", {"command": _S, "path": _S}, ["command", "path"])


def tools_for(context: ReadContext | None) -> list[dict]:
    """The tool list this request may use. Running commands is offered only when it is turned on."""
    enabled = getattr(context, "commands_enabled", None)
    return [*TOOLS, RUN_TOOL] if enabled and enabled() else list(TOOLS)


def history_for(context: ReadContext | None) -> list[dict]:
    """Earlier turns of this conversation as plain user/assistant messages."""
    history = getattr(context, "history", None)
    return list(history()) if history else []


def _hang_up(conn: http.client.HTTPConnection) -> None:
    """Drop a connection from another thread, even while it is blocked waiting for the model.

    A plain close() is not enough: Python keeps the socket open while a reader is attached to it, so the
    blocked read never wakes up. `_real_close` closes it for real, which makes that read fail at once. It is
    tested (test_stop_hangs_up_on_a_model_that_is_still_thinking), so a Python change that breaks it shows up.
    """
    try:
        sock = conn.sock
        if sock is not None:
            getattr(sock, "_real_close", sock.close)()
    except OSError:
        pass
    try:
        conn.close()
    except Exception:
        pass


def _http_json(
    url: str,
    payload: dict | None,
    timeout: float,
    headers: dict | None = None,
    service: str = "Ollama",
    hint: str = "Is it installed and running? (https://ollama.com)",
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    parts = urllib.parse.urlsplit(url)
    connect = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = connect(parts.hostname, parts.port, timeout=timeout)
    token = CURRENT.get()
    # Stop hangs up on the model: a local model notices and stops working, so the PC is free again.
    forget = token.on_cancel(lambda: _hang_up(conn)) if token else None
    base = url.split("/api/")[0].split("/v1/")[0]
    try:
        conn.connect()  # failing here means nobody is listening, which is different from a slow answer
    except Exception:
        if forget:
            forget()
        conn.close()
        if token and token.cancelled:
            raise Cancelled() from None
        raise PlanError(f"Cannot reach {service} at {base}. {hint}") from None
    if token and token.cancelled:  # Stop was pressed while connecting
        forget()
        conn.close()
        raise Cancelled()
    try:
        conn.request(
            "GET" if payload is None else "POST",
            parts.path + (f"?{parts.query}" if parts.query else ""),
            body=data,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        response = conn.getresponse()
        body = response.read()
        status = response.status
    except TimeoutError:
        raise PlanError(
            f"The model did not answer within {timeout:g} seconds. A PC without a graphics card can be slow: "
            "try again, ask for less, or raise llm.timeout_seconds in config.yaml."
        ) from None
    except Exception:
        if token and token.cancelled:
            raise Cancelled() from None
        raise PlanError(f"Cannot reach {service} at {base}. {hint}") from None
    finally:
        if forget:
            forget()
        conn.close()
    if status >= 400:
        raise PlanError(f"{service} returned HTTP {status}: {body.decode('utf-8', 'replace')[:300]}")
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise PlanError(f"{service} sent a reply this app does not understand.") from None


def _folders_note(context: ReadContext | None) -> str:
    roots = getattr(context, "roots", None)
    if roots is None:
        return ""
    writable, read_only = roots()
    lines = ["", "", "Folders you may use:"]
    for i, folder in enumerate(writable):
        lines.append(f"- {folder} (read and write{'; relative paths start here' if i == 0 else ''})")
    lines += [f"- {folder} (READ-ONLY)" for folder in read_only]
    lines.append("- Any other folder the user names: use it anyway. If it is not allowed yet, the app asks the user for permission.")
    return "\n".join(lines)


def _read_error(exc: Exception, path: str, context: ReadContext | None) -> str:
    if isinstance(exc, OutsideAllowed):
        return (f"error: {exc}. The user is being asked for permission right now. "
                "Do not try other paths; say you are waiting for their permission.")
    return f"error: {exc}" + (suggestion(path, context) if isinstance(exc, FileNotFoundError) else "")


def make_edit(path: str, old: str, new: str, context: ReadContext | None) -> Step | str:
    """A change that replaces one piece of text in a file, or a sentence saying why that cannot be done.

    The model only has to write the few words that change. The app builds the whole new file from the real one,
    so nothing else in it can be altered by a slip, and a slow model never has to retype a big file.
    """
    if context is None:
        return "error: reading files is not available"
    if not old:
        return "error: 'old' must be the exact text to replace"
    try:
        text = context.read_file(path)
    except (PolicyError, OSError, ValueError) as exc:
        return _read_error(exc, path, context)
    count = text.count(old)
    if count == 0:
        return "error: that text is not in the file. Copy it exactly as it appears in the file."
    if count > 1:
        return f"error: that text appears {count} times. Include more of the text around it so it is unique."
    return Step(Action.WRITE, path, text.replace(old, new, 1), edit=(old, new))


def run_tool(name: str, arguments: object, context: ReadContext | None, steps: list[Step], limit: int = MAX_READ_CHARS) -> str:
    """Run one tool call from a model. Reads happen now; changes are queued as steps."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return "error: arguments must be a JSON object"
    args = arguments if isinstance(arguments, dict) else {}
    path = args.get("path", "." if name == "list_files" else None)
    if not isinstance(path, str) or not path.strip():
        return "error: 'path' is required. Use the folder or file the user named."
    path = path.strip()

    if name in ("list_files", "read_file", "find_files"):
        if context is None:
            return "error: reading files is not available"
        try:
            if name == "list_files":
                return "\n".join(context.list_dir(path)) or "(empty folder)"
            if name == "find_files":
                pattern = args.get("pattern")
                if not isinstance(pattern, str) or not pattern.strip():
                    return "error: 'pattern' is required, for example *.bak"
                return context.find_files(path, pattern)
            text = context.read_file(path)
        except (PolicyError, OSError, ValueError) as exc:
            return _read_error(exc, path, context)
        if len(text) > limit:
            text = text[:limit] + "\n... (truncated)"
        return text
    if name == "edit_file":
        old, new = args.get("old"), args.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            return "error: 'old' and 'new' must be text"
        made = make_edit(path, old, new, context)
        if isinstance(made, str):
            return made
        steps.append(made)
        return f"queued: edit {path}. The user must approve it."
    if name == "write_file":
        content = args.get("content")
        if not isinstance(content, str):
            return "error: 'content' must be text"
        steps.append(Step(Action.WRITE, path, content))
        return f"queued: write {path}. The user must approve it."
    if name == "delete_file":
        steps.append(Step(Action.DELETE, path))
        return f"queued: delete {path}. The user must approve it."
    if name == "download_file":
        steps.append(Step(Action.SEND, path))
        return f"queued: send {path} to the phone. The user must approve it."
    if name in ("move_file", "copy_file"):
        destination = args.get("destination")
        if not isinstance(destination, str) or not destination.strip():
            return "error: 'destination' is required"
        action = Action.MOVE if name == "move_file" else Action.COPY
        steps.append(Step(action, path, target=destination.strip()))
        return f"queued: {action.value} {path} to {destination.strip()}. The user must approve it."
    if name == "run_command":
        enabled = getattr(context, "commands_enabled", None)
        if not (enabled and enabled()):
            return "error: running commands is turned off"
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return "error: 'command' is required"
        steps.append(Step(Action.RUN, path, command.strip()))
        return "queued: the command. The user must approve it. You will not see its output."
    return f"error: unknown tool '{name}'"


class OllamaPlanner:
    is_slow = True  # a local model can take a while; the manager plans in the background

    def __init__(self, llm: LlmConfig, transport: Transport | None = None):
        self._llm = llm
        self._transport = transport or _http_json

    def plan(self, prompt: str, context: ReadContext | None = None) -> Plan:
        hint = last_folder(history_for(context))
        if quick := quick_for(prompt, context):  # a plain request needs no model, so it is instant and cannot be guessed
            return correct_plan(quick, context)
        facts = ground(prompt, context, hint, self._llm.file_chars)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT + _folders_note(context)},
            *history_for(context),
            {"role": "user", "content": prompt + facts},
        ]
        tools = tools_for(context)
        steps: list[Step] = []
        looked, nudged = bool(facts), False
        for _ in range(self._llm.max_rounds):
            check_cancel()
            try:
                message = self._chat(messages, tools)
            except PlanError as exc:
                if fallback := timeout_fallback(exc, facts):
                    return fallback
                raise
            calls = message.get("tool_calls") or []
            text = (message.get("content") or "").strip()
            if message.get("_cut"):
                if calls or text.lstrip().startswith(("{", "[", "<tool_call>")):
                    raise PlanError(CUT_OFF)  # never act on (or show) a half-written change
                text += " … (the answer was cut off because it was too long)"
            if not calls:
                if steps:
                    return Plan(text or f"{len(steps)} proposed change(s)", steps)
                if not looked and needs_look(prompt):
                    if not nudged:  # about files, but it never looked: send it back to look
                        nudged = True
                        messages += [{"role": "assistant", "content": text or "(no answer)"}, {"role": "user", "content": NUDGE}]
                        continue
                    text = (text or "(the model gave no reply)") + UNVERIFIED
                return Plan("", [], reply=text or "(the model gave no reply)")
            looked = True
            messages.append({"role": "assistant", "content": text, "tool_calls": calls})
            for call in calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                result = run_tool(name, function.get("arguments"), context, steps, self._llm.file_chars)
                messages.append({"role": "tool", "tool_name": name, "content": result})
        raise PlanError(
            f"The model did not finish within {self._llm.max_rounds} steps. Try a simpler request."
        )

    def _threads(self) -> int | None:
        setting = self._llm.num_thread
        if setting == "auto":
            return max(1, (os.cpu_count() or 4) - 2)
        return setting or None

    def title(self, prompt: str) -> str | None:
        """A short name for a chat that starts with this request, from the local model. None when it has nothing usable (never an error:
        the phone already has a name made from the words)."""
        payload = {
            "model": self._llm.model,
            "messages": [{"role": "system", "content": TITLE_SYSTEM}, {"role": "user", "content": " ".join(prompt.split())[:600]}],
            "stream": False,
            "keep_alive": self._llm.keep_alive,
            "options": {
                "temperature": 0.2,
                "num_ctx": self._llm.num_ctx,  # the same size the planner uses, so naming a chat never makes Ollama reload the model
                "num_predict": 24,
                **({"num_thread": threads} if (threads := self._threads()) else {}),
            },
        }
        try:
            data = self._transport(f"{self._llm.base_url}/api/chat", payload, min(self._llm.timeout_seconds, 25))
        except PlanError:
            return None
        message = data.get("message") if isinstance(data, dict) else None
        return clean_title(message.get("content") if isinstance(message, dict) else None)

    def _chat(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {
            "model": self._llm.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": self._llm.keep_alive,
            "options": {
                "temperature": self._llm.temperature,
                "num_ctx": self._llm.num_ctx,  # set on purpose: a too-small default silently cuts the prompt
                "num_predict": self._llm.max_output_tokens,  # a runaway answer stops here
                **({"num_thread": threads} if (threads := self._threads()) else {}),
            },
        }
        try:
            data = self._transport(
                f"{self._llm.base_url}/api/chat", payload, self._llm.timeout_seconds
            )
        except PlanError as exc:
            if "not found" in str(exc).lower():
                raise PlanError(
                    f"The model '{self._llm.model}' is not installed. "
                    f"Run:  ollama pull {self._llm.model}"
                ) from None
            raise
        message = data.get("message")
        if not isinstance(message, dict):
            raise PlanError("Ollama sent a reply this app does not understand.")
        if data.get("done_reason") == "length":  # it hit max_output_tokens and was cut off mid-answer
            message = {**message, "_cut": True}
        return message


CUT_OFF = (
    "The AI's answer was too long to finish, so I did not use it: half a change is worse than none. "
    "Ask for a smaller change (for example \"replace X with Y in the file\"), or use the hosted model for big rewrites."
)


class AnthropicPlanner:
    """The same planner, backed by a hosted Claude model. Requests and file text leave the PC."""

    is_slow = True

    def __init__(self, llm: LlmConfig, api_key: str, transport: Transport | None = None):
        self._llm = llm
        self._transport = transport or functools.partial(
            _http_json,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            service="Anthropic",
            hint="Check the internet connection and llm.hosted_url.",
        )

    def plan(self, prompt: str, context: ReadContext | None = None) -> Plan:
        hint = last_folder(history_for(context))
        if quick := quick_for(prompt, context):
            return correct_plan(quick, context)
        facts = ground(prompt, context, hint, self._llm.file_chars)
        system = SYSTEM_PROMPT + _folders_note(context)
        tools = [
            {"name": t["function"]["name"], "description": t["function"]["description"],
             "input_schema": t["function"]["parameters"]}
            for t in tools_for(context)
        ]
        messages: list[dict] = [*history_for(context), {"role": "user", "content": prompt + facts}]
        steps: list[Step] = []
        looked, nudged = bool(facts), False
        for _ in range(self._llm.max_rounds):
            check_cancel()
            try:
                blocks = self._chat(system, messages, tools)
            except PlanError as exc:
                if fallback := timeout_fallback(exc, facts):
                    return fallback
                raise
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses:
                if steps:
                    return Plan(text or f"{len(steps)} proposed change(s)", steps)
                if not looked and needs_look(prompt):
                    if not nudged:
                        nudged = True
                        messages += [{"role": "assistant", "content": text or "(no answer)"}, {"role": "user", "content": NUDGE}]
                        continue
                    text = (text or "(the model gave no reply)") + UNVERIFIED
                return Plan("", [], reply=text or "(the model gave no reply)")
            looked = True
            messages.append({"role": "assistant", "content": blocks})
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": use.get("id"),
                    "content": run_tool(use.get("name", ""), use.get("input"), context, steps, self._llm.file_chars),
                }
                for use in uses
            ]
            messages.append({"role": "user", "content": results})
        raise PlanError(
            f"The model did not finish within {self._llm.max_rounds} steps. Try a simpler request."
        )

    def _chat(self, system: str, messages: list[dict], tools: list[dict]) -> list[dict]:
        payload = {
            "model": self._llm.hosted_model,
            "max_tokens": self._llm.max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        try:
            data = self._transport(
                f"{self._llm.hosted_url}/v1/messages", payload, self._llm.timeout_seconds
            )
        except PlanError as exc:
            if "HTTP 401" in str(exc):
                raise PlanError(
                    f"The hosted model rejected the API key. Check the {self._llm.api_key_env} environment variable."
                ) from None
            raise
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise PlanError("The hosted model sent a reply this app does not understand.")
        return blocks


def ollama_ready(llm: LlmConfig, transport: Transport | None = None) -> tuple[bool, str]:
    """Is Ollama reachable and does it have the configured model?"""
    send = transport or _http_json
    try:
        data = send(f"{llm.base_url}/api/tags", None, 3)
    except PlanError as exc:
        return False, str(exc)
    names = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
    if any(n == llm.model or n.split(":")[0] == llm.model for n in names):
        return True, ""
    return False, f"model '{llm.model}' is not installed (run: ollama pull {llm.model})"


def read_key(name: str) -> str:
    """The API key from the environment. On Windows also the copy saved with `setx`.

    A key saved with setx is invisible to any window opened before it was saved. Reading the saved copy means the
    app works however it was started, with no need to open a fresh window first.
    """
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""


def build_planner(llm: LlmConfig, transport: Transport | None = None) -> tuple[Planner, str]:
    """Pick the planner named in the config. Returns it with a one-line note."""
    if llm.provider == "demo":
        return DemoPlanner("llm.provider is 'demo' in config.yaml."), "AI model: none (llm.provider is 'demo'). Demo commands only."
    if llm.provider == "anthropic":
        key = read_key(llm.api_key_env) if llm.api_key_env.isidentifier() else ""
        if not llm.api_key_env.isidentifier():  # someone pasted the key itself: never echo it
            return DemoPlanner("llm.api_key_env in config.yaml must be a variable NAME, not the key."), (
                "AI model: none (llm.api_key_env must be the NAME of an environment variable, "
                "such as ANTHROPIC_API_KEY, not the key itself). Demo commands only."
            )
        if not key:
            return DemoPlanner(f'no API key was found. Save it with: setx {llm.api_key_env} "your-key", then restart me.'), (
                f"AI model: none (set the {llm.api_key_env} environment variable to your API key). "
                "Demo commands only."
            )
        return AnthropicPlanner(llm, key, transport), (
            f"AI model: {llm.hosted_model} (HOSTED). Your requests and any file text it reads are "
            "sent to the provider, not kept on this PC."
        )
    ollama = OllamaPlanner(llm, transport)
    if llm.provider == "ollama":
        return ollama, f"AI model: {llm.model} via Ollama at {llm.base_url}"
    ready, reason = ollama_ready(llm, transport)  # provider == "auto"
    if ready:
        return ollama, f"AI model: {llm.model} via Ollama at {llm.base_url}"
    return DemoPlanner(f"{reason[:1].upper()}{reason[1:]}"), f"AI model: none ({reason}). Demo commands only."
