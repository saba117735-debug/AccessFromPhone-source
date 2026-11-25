"""Print a file on this PC's default printer, and take one picture with the PC's webcam.

Printing hands the file to the program Windows uses for that kind of file, with its "print" action, so what comes out is what that program prints.
Only ordinary documents and pictures are printed; a program or script never is.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

PRINTABLE = {".pdf", ".txt", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt", ".csv"}
MAX_PRINT_MB = 100


class PrintError(Exception):
    """The file can't be printed (or the picture taken); the message says why."""


def _powershell(script: str, timeout: float = 20) -> str:
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (done.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def printers(shell: Callable[[str], str] = _powershell) -> list[dict]:
    raw = shell("Get-CimInstance Win32_Printer | Select-Object Name, Default, WorkOffline | ConvertTo-Json -Compress")
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    data = [data] if isinstance(data, dict) else data
    return [{"name": str(p.get("Name", "")), "default": bool(p.get("Default")), "offline": bool(p.get("WorkOffline"))} for p in data if isinstance(p, dict) and p.get("Name")]


def default_printer(shell: Callable[[str], str] = _powershell) -> dict | None:
    return next((p for p in printers(shell) if p["default"]), None)


def check(path: Path) -> None:
    if path.suffix.lower() not in PRINTABLE:
        raise PrintError(f"{path.suffix or 'That kind of file'} can't be printed from here. Documents, spreadsheets, slides, text and pictures can.")
    if path.stat().st_size > MAX_PRINT_MB * 1024 * 1024:
        raise PrintError(f"That file is larger than {MAX_PRINT_MB} MB.")


def print_file(path: Path, *, shell: Callable[[str], str] = _powershell, starter: Callable[[str, str], None] | None = None) -> str:
    """Send the file to the default printer and return the printer's name."""
    path = Path(path)
    check(path)
    if starter is None:
        if sys.platform != "win32":
            raise PrintError("Printing from the phone works on Windows only.")
        starter = lambda target, verb: os.startfile(target, verb)  # noqa: E731
    printer = default_printer(shell)
    if printer is None:
        raise PrintError("This PC has no default printer. Set one in Windows first.")
    if printer["offline"]:
        raise PrintError(f"{printer['name']} is offline.")
    try:
        starter(str(path), "print")
    except OSError as exc:
        raise PrintError("Windows has no program that can print this kind of file.") from exc
    return printer["name"]


def webcam_available() -> bool:
    try:
        import cv2  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def webcam_photo() -> bytes:
    """One JPEG from the first camera. Raises PrintError (with the reason) when there is none or it can't be used."""
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        raise PrintError("The webcam needs the opencv-python package on the PC (pip install opencv-python).") from None
    camera = cv2.VideoCapture(0)
    try:
        if not camera.isOpened():
            raise PrintError("This PC's camera could not be opened (it may be in use or switched off).")
        frame = None
        for _ in range(8):  # the first frames are dark while the camera adjusts
            ok, got = camera.read()
            if ok:
                frame = got
        if frame is None:
            raise PrintError("The camera gave no picture.")
        ok, data = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise PrintError("The picture could not be saved.")
        return data.tobytes()
    finally:
        camera.release()
