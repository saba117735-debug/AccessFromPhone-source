"""Where Windows keeps the person's usual folders (Downloads, Desktop, ...), wherever they were moved to."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_IDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
}
NAMES = tuple(_IDS)
TITLES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures", "videos": "Videos", "music": "Music"}


def _from_windows(guid: str) -> Path | None:
    import ctypes
    from ctypes import wintypes

    raw = ctypes.create_string_buffer(uuid.UUID(guid).bytes_le, 16)
    out = ctypes.c_wchar_p()
    shell = ctypes.windll.shell32
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    try:
        if shell.SHGetKnownFolderPath(raw, 0, None, ctypes.byref(out)) != 0 or not out.value:
            return None
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def known_folder(name: str) -> Path | None:
    """The folder called `name` (downloads, desktop, documents, pictures, videos, music), or None when this PC has no such folder."""
    key = name.lower()
    if key not in _IDS:
        return None
    found = None
    if sys.platform == "win32":
        try:
            found = _from_windows(_IDS[key])
        except Exception:  # noqa: BLE001 - fall back to the usual place below
            found = None
    if found is None:
        found = Path(os.path.expanduser("~")) / TITLES[key]
    return found if found.is_dir() else None


def all_places() -> list[dict]:
    """The usual folders that exist on this PC, for pinning and for quick opening."""
    out = []
    for key in NAMES:
        folder = known_folder(key)
        if folder is not None:
            out.append({"key": key, "name": TITLES[key], "path": str(folder)})
    return out
