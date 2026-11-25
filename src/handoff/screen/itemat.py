"""Which file or folder is under a point on this PC's screen? For a phone that is looking: long-press a file on the picture, get "Download to phone".

Windows can say what is at a screen position (UI Automation: the name Explorer shows under it) and which folder an Explorer window is showing. Put together, that
is a real path. It works for File Explorer windows and for the desktop; a file drawn inside any other program has no name Windows can give, so there the answer is "not found".

Nothing here changes anything on the PC: it only looks. The path it finds is still checked against the allowed folders by the caller, exactly like a tap on Download in My files.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MAX_CANDIDATES = 6      # more than this under one name (hidden extensions) is not a useful list


@dataclass
class Spot:
    """What the screen showed at one point: the name under it (if it was a list item) and the folders that name could be in."""
    label: str | None = None
    folders: list[str] = field(default_factory=list)
    where: str = ""     # "explorer", "desktop" or ""


class ItemFinder(Protocol):
    def find(self, px: int, py: int) -> Spot: ...


class FinderError(Exception):
    """Windows could not be asked (too slow, or no PowerShell)."""


# Runs in a PowerShell of its own, so a hang or a crash in UI Automation can never take this app down. The point comes in through the environment, never pasted into the script.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -Namespace Afp -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[void][Afp.Dpi]::SetProcessDPIAware()
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, WindowsBase
$px = [int]$env:AFP_X; $py = [int]$env:AFP_Y
$AE = [System.Windows.Automation.AutomationElement]
$CT = [System.Windows.Automation.ControlType]
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$cur = $AE::FromPoint((New-Object System.Windows.Point($px, $py)))
$label = $null; $cls = ''; $hwnd = 0; $depth = 0
while ($cur -ne $null -and $depth -lt 24) {
  $t = $cur.Current.ControlType
  if (-not $label -and ($t -eq $CT::ListItem -or $t -eq $CT::DataItem)) { $label = $cur.Current.Name }
  $h = $cur.Current.NativeWindowHandle
  if ($h -ne 0) {
    $c = $cur.Current.ClassName
    if ($c -eq 'CabinetWClass' -or $c -eq 'Progman' -or $c -eq 'WorkerW') { $cls = $c; $hwnd = $h; break }
  }
  try { $cur = $walker.GetParent($cur) } catch { break }
  $depth++
}
$folders = @()
$where = ''
if ($cls -eq 'CabinetWClass') {
  $where = 'explorer'
  $sh = New-Object -ComObject Shell.Application
  foreach ($w in $sh.Windows()) {
    try { if ([int64]$w.HWND -eq [int64]$hwnd) { $p = $w.Document.Folder.Self.Path; if ($p) { $folders += [string]$p } } } catch { }
  }
} elseif ($cls) {
  $where = 'desktop'
  $folders += [Environment]::GetFolderPath('Desktop')
  $folders += [Environment]::GetFolderPath('CommonDesktopDirectory')
}
$out = [ordered]@{ label = $label; where = $where; folders = @($folders | Select-Object -Unique) }
[Console]::Out.Write(($out | ConvertTo-Json -Compress))
"""


class WindowsItemFinder:
    TIMEOUT = 12.0   # the first run loads UI Automation and can take several seconds

    def __init__(self) -> None:
        self._encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
        self._one = threading.Lock()   # one question at a time: each one starts a program

    def find(self, px: int, py: int) -> Spot:
        if not self._one.acquire(blocking=False):
            raise FinderError("Still looking at the last touch. Try again in a moment.")
        try:
            env = dict(os.environ, AFP_X=str(int(px)), AFP_Y=str(int(py)))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", self._encoded],
                                      capture_output=True, timeout=self.TIMEOUT, env=env, creationflags=flags)
            except (OSError, subprocess.TimeoutExpired):
                raise FinderError("Windows took too long to say what is there. Try again.") from None
            if done.returncode != 0:
                raise FinderError("Windows couldn't say what is there.")
            return parse_spot(done.stdout.decode("utf-8", "replace"))
        finally:
            self._one.release()


def parse_spot(text: str) -> Spot:
    """The helper's answer, whatever shape it came in. Anything unexpected is simply "nothing found"."""
    try:
        data = json.loads(text.strip() or "{}")
    except ValueError:
        return Spot()
    if not isinstance(data, dict):
        return Spot()
    label = data.get("label")
    folders = data.get("folders") or []
    if isinstance(folders, str):
        folders = [folders]
    if not isinstance(folders, list):
        folders = []
    return Spot(label=label.strip() if isinstance(label, str) and label.strip() else None,
                folders=[f for f in folders if isinstance(f, str)], where=data.get("where") if isinstance(data.get("where"), str) else "")


def matches(folders: list[str], label: str | None) -> list[Path]:
    """The real files and folders named `label` in those folders. Explorer may hide extensions, so "Report" also finds Report.docx; a name that is exact is listed first."""
    if not label:
        return []
    want = label.casefold()
    exact: list[Path] = []
    loose: list[Path] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            if not folder or folder.startswith("::") or not os.path.isdir(folder):   # "::{...}" is a virtual place such as This PC
                continue
            with os.scandir(folder) as it:
                for entry in it:
                    name = entry.name.casefold()
                    stem = os.path.splitext(name)[0]
                    if name == want:
                        bucket = exact
                    elif stem == want:
                        bucket = loose
                    else:
                        continue
                    real = str(Path(entry.path).resolve())
                    if real not in seen:
                        seen.add(real)
                        bucket.append(Path(real))
        except OSError:
            continue
    return (exact + loose)[:MAX_CANDIDATES]


def default_finder() -> ItemFinder | None:
    return WindowsItemFinder() if sys.platform == "win32" else None
