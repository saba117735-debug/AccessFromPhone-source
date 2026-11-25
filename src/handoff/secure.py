"""Keeping the app's data folder (the database, the sign-in token, saved certificates) to the person it belongs to.

On Windows a folder inside a user's profile is usually readable by that user and by administrators only, but a folder created elsewhere
(or a shared PC) can be open to every account. This closes it to this Windows user and to the system (which the unlock helper needs),
and nobody else. It never raises: a PC where it cannot be done just keeps the folder's rights as they were.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("handoff.secure")

BS = chr(92)  # a backslash, for DOMAIN-slash-user
SYSTEM_SID = "*S-1-5-18"  # the system account, by number so it works in every language of Windows


def _who() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}{BS}{user}" if domain and user else user


def acl_command(folder: Path, who: str) -> list[str]:
    """The icacls call: drop inherited rights, then give full control to this user and to the system, for the folder and everything made in it later."""
    return ["icacls", str(folder), "/inheritance:r", "/grant:r", f"{who}:(OI)(CI)F", "/grant:r", f"{SYSTEM_SID}:(OI)(CI)F"]


def restrict_to_current_user(folder: Path) -> bool:
    """True when the folder is now closed to other accounts. False (never an error) when it could not be done or is not needed."""
    folder = Path(folder)
    try:
        if not folder.is_dir():
            return False
        if sys.platform == "win32":
            who = _who()
            if not who:
                return False
            done = subprocess.run(acl_command(folder, who), capture_output=True, text=True, timeout=30, check=False)
            if done.returncode != 0:
                log.warning("could not restrict %s: %s", folder, (done.stderr or done.stdout).strip()[:200])
            return done.returncode == 0
        folder.chmod(0o700)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not restrict %s: %s", folder, exc)
        return False
