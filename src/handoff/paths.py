"""Small path helpers shared by the config and the policy engine."""
from __future__ import annotations

import os
from pathlib import Path


def is_inside(child: Path, parent: Path) -> bool:
    """True if `child` is `parent` or lies under it (case-insensitive on Windows)."""
    c = os.path.normcase(str(child))
    p = os.path.normcase(str(parent))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False
