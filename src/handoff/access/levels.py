"""What each access level may do. Pure rules, no storage and no web, so they are easy to test and to read in one place.

Levels, lowest to highest:
  viewer    looks and reads; changes nothing
  editor    plus changing files (every change is still saved first, so Undo works)
  operator  plus approved commands, opening apps, lock and sleep
  admin     plus unlock, restart, shut down; manages people (only the Admin phone has this level)
"""
from __future__ import annotations

import re

LEVELS = ("viewer", "editor", "operator", "admin")
GRANTABLE = ("viewer", "editor", "operator")  # what the Admin phone may hand out; never "admin"
DEFAULT_LEVEL = "operator"  # a paired phone that nobody has set yet
NAMES = {"viewer": "View only", "editor": "Read and write", "operator": "Operator", "admin": "Admin"}

# A plan step's action -> the lowest level allowed to approve it.
STEP_LEVEL = {"read": "viewer", "write": "editor", "move": "editor", "copy": "editor", "delete": "editor", "send": "editor",
              "deploy": "operator", "run": "operator", "app": "operator", "power": "operator"}
POWER_ADMIN_ONLY = {"unlock", "restart", "shutdown", "hibernate"}  # power actions that need more than operator

# Requests that are not a plan step but still change something: (method, path pattern) -> level.
ROUTE_LEVEL: list[tuple[str, "re.Pattern[str]", str]] = [
    ("POST", re.compile(r"^/api/jobs$"), "viewer"),
    ("POST", re.compile(r"^/api/jobs/[^/]+/(reject|stop)$"), "viewer"),
    ("POST", re.compile(r"^/api/jobs/[^/]+/approve$"), "viewer"),  # the real check is by what the plan does (see level_for_steps)
    ("POST", re.compile(r"^/api/jobs/[^/]+/undo$"), "editor"),
    ("POST", re.compile(r"^/api/jobs/[^/]+/access$"), "editor"),
    ("POST", re.compile(r"^/api/access/(grant|revoke)$"), "editor"),
    ("PUT", re.compile(r"^/api/(file|image)$"), "editor"),
    # the everyday helpers: the level is checked before a turn is taken, so a phone that may not do it never takes one by asking
    ("POST", re.compile(r"^/api/daily/(control|open-place|print|tasks|watch|snap/screen|volume|brightness)$"), "operator"),
    ("DELETE", re.compile(r"^/api/daily/(tasks|watch)/[^/]+$"), "operator"),
    ("POST", re.compile(r"^/api/daily/inbox/[^/]+/(clipboard|open)$"), "operator"),
    ("POST", re.compile(r"^/api/daily/snap/webcam$"), "admin"),
    ("POST", re.compile(r"^/api/daily/(inbox/text|notes|favourites|notifications/dismiss)$"), "editor"),
    ("PUT", re.compile(r"^/api/daily/inbox/file$"), "editor"),
    ("DELETE", re.compile(r"^/api/daily/(inbox/[^/]+|favourites)$"), "editor"),
]

# Requests that change nothing about the PC and need no session (they may come from any paired phone).
SESSION_EXEMPT = re.compile(
    r"^/api/(session(/.*)?|pause|screen(/.*)?|device(/.*)?|devices(/.*)?|push(/.*)?|title|owner/check|browse/(stream|offer)|files/link|access/(level|people)|folders(/.*)?|overview)$")


def rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def at_least(have: str, need: str) -> bool:
    return rank(have) >= rank(need)


def level_for_steps(steps: list[dict]) -> str:
    """The lowest level that may approve a plan with these steps (each step is the dict the phone sees: action, path, detail...)."""
    need = "viewer"
    for step in steps:
        action = str(step.get("action", ""))
        wanted = STEP_LEVEL.get(action, "operator")  # an action nobody named yet is treated as the more serious kind
        if action == "power" and _power_kind(step) in POWER_ADMIN_ONLY:
            wanted = "admin"
        if rank(wanted) > rank(need):
            need = wanted
    return need


def _power_kind(step: dict) -> str:
    text = " ".join(str(step.get(k, "")) for k in ("content", "detail", "path")).lower()
    for kind in POWER_ADMIN_ONLY:
        if kind in text:
            return kind
    return ""


def level_for_route(method: str, path: str) -> str | None:
    """The level a change needs, by where it goes. None when the route is not one of the known changing ones (it then needs a session only)."""
    for m, pattern, level in ROUTE_LEVEL:
        if m == method and pattern.match(path):
            return level
    return None


def needs_session(method: str, path: str) -> bool:
    """Does this request change something, so that only the person holding the session may send it?"""
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    return not SESSION_EXEMPT.match(path)


def message_for(have: str, need: str) -> str:
    return f"{NAMES.get(have, have)} access can't do this. It needs {NAMES.get(need, need).lower()} access. Ask the person who manages this PC."
