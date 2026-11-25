"""Open apps on this PC, and read and answer Microsoft Teams chats, from the phone.

Opening an app: the Start menu is the list ("open Teams", "open Excel"), and an app is started the way the Start menu starts it.

Teams: there is no tidy way in from outside, so this drives the Teams window the way a person would, through Windows' own accessibility
interface (UI Automation): it brings Teams forward, reads the chat list and the messages on screen, and for a reply it types the exact
words into the message box and presses Enter. Nothing is sent anywhere else, and no AI is shown a message.

It can only work while the PC is unlocked (Windows keeps keyboards out of a locked screen), and it uses the keyboard for a few seconds,
so a person using the PC at that moment can get in its way. Teams can change its window in an update; `python -m handoff.appctl teams-look`
prints what this sees, so a change is quick to fix.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AppError(Exception):
    """Something the person can act on; the message says what."""


# ---------------------------------------------------------------------------------------------------------------- the Start menu


@dataclass(frozen=True)
class AppEntry:
    name: str
    appid: str


_ALIASES = {
    "chrome": "google chrome", "edge": "microsoft edge", "vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
    "cmd": "command prompt", "calc": "calculator", "explorer": "file explorer", "files": "file explorer", "teams": "microsoft teams",
    "ppt": "powerpoint", "powerpoint": "powerpoint", "word": "word", "excel": "excel", "outlook": "outlook", "terminal": "terminal", "settings": "settings",
}
_NOISE = re.compile(r"\b(the|app|application|program|software|my)\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _NOISE.sub(" ", re.sub(r"[^a-z0-9+#. ]+", " ", text.lower()))).strip()


def find_app(query: str, apps: list[AppEntry]) -> AppEntry | None:
    """The Start-menu entry the person means by `query`, or None. An exact name beats a name that merely starts with it, which beats one that contains it."""
    wanted = [q for q in {_norm(query), _norm(_ALIASES.get(_norm(query), ""))} if q]
    best: tuple[int, int, AppEntry] | None = None
    for app in apps:
        name = _norm(app.name)
        if not name or re.search(r"\b(uninstall|help|readme|release notes|documentation|website)\b", name):
            continue
        score = 0
        for q in wanted:
            if name == q:
                score = max(score, 100)
            elif name.startswith(q + " ") or name.startswith(q):
                score = max(score, 80)
            elif all(word in name.split() for word in q.split()):
                score = max(score, 60)
            elif q in name:
                score = max(score, 50)
        if score and (best is None or (score, -len(name)) > (best[0], -best[1])):
            best = (score, len(name), app)
    return best[2] if best and best[0] >= 50 else None


_apps_cache: dict = {"at": 0.0, "apps": []}
_apps_lock = threading.Lock()


def _powershell() -> str:
    return "powershell.exe"


def start_apps(run: Callable[..., "subprocess.CompletedProcess"] | None = None, max_age: float = 120.0) -> list[AppEntry]:
    """Everything in this PC's Start menu (cached for a couple of minutes: asking Windows takes about a second)."""
    with _apps_lock:
        if _apps_cache["apps"] and time.monotonic() - _apps_cache["at"] < max_age:
            return list(_apps_cache["apps"])
        if os.name != "nt" and run is None:
            return []
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
        try:
            done = (run or subprocess.run)([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=30, creationflags=NO_WINDOW)
            data = json.loads(done.stdout.decode("utf-8", "replace") or "[]")
        except (OSError, ValueError, subprocess.SubprocessError):
            return list(_apps_cache["apps"])
        if isinstance(data, dict):
            data = [data]
        apps = [AppEntry(str(d.get("Name", "")), str(d.get("AppID", ""))) for d in data if d.get("Name") and d.get("AppID")]
        _apps_cache.update(at=time.monotonic(), apps=apps)
        return list(apps)


_BAD_ID = re.compile(r"[\"'|<>%\r\n\t^]")


def launch(app: AppEntry, starter: Callable[[list[str]], None] | None = None) -> None:
    if _BAD_ID.search(app.appid):
        raise AppError(f"I won't start {app.name!r}: its Windows id has characters I can't pass on safely.")
    command = ["explorer.exe", "shell:AppsFolder\\" + app.appid]
    if starter:
        starter(command)
        return
    try:
        subprocess.Popen(command, creationflags=NO_WINDOW)  # explorer hands it to Windows, exactly as the Start menu does
    except OSError as exc:
        raise AppError(f"Windows would not start {app.name}: {exc}") from None


# ---------------------------------------------------------------------------------------------------------------- Teams: the part that looks at the window

TEAMS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class AfpWin {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@
$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]
$script:win = $null

function Out-Json($o) {
  $j = $o | ConvertTo-Json -Depth 6 -Compress
  $j = [regex]::Replace($j, '[^\x00-\x7F]', { param($m) ('\u{0:x4}' -f [int][char]$m.Value) })
  [Console]::Out.WriteLine($j)
  [Console]::Out.Flush()
}
function Fail($code, $msg) { throw ('FAIL|' + $code + '|' + $msg) }
function Prop($p, $v) { New-Object System.Windows.Automation.PropertyCondition($p, $v) }
function Squash($t) { return [regex]::Replace(([string]$t).ToLower(), '[^\p{L}\p{N}]', '') }

function Bring-Front($h) {
  $iconic = [AfpWin]::IsIconic($h)
  if (-not $iconic -and [AfpWin]::GetForegroundWindow() -eq $h) { return @{ iconic = $false; front = $true } }
  if ($iconic) { [void][AfpWin]::ShowWindow($h, 9) } else { [void][AfpWin]::ShowWindow($h, 5) }
  # Windows only lets a program that just had input take the front: a tap of the Alt key, and sharing the front window's input queue, get round that
  [AfpWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [AfpWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
  $fg = [AfpWin]::GetForegroundWindow(); $fgPid = [uint32]0
  $t1 = [AfpWin]::GetWindowThreadProcessId($fg, [ref]$fgPid)
  $t2 = [AfpWin]::GetCurrentThreadId()
  [void][AfpWin]::AttachThreadInput($t2, $t1, $true)
  [void][AfpWin]::SetForegroundWindow($h)
  [void][AfpWin]::AttachThreadInput($t2, $t1, $false)
  for ($i = 0; $i -lt 15; $i++) { if ([AfpWin]::GetForegroundWindow() -eq $h) { break }; Start-Sleep -Milliseconds 80 }
  if ([AfpWin]::GetForegroundWindow() -ne $h) {
    try { $AE::FromHandle($h).SetFocus() } catch { }
    for ($i = 0; $i -lt 8; $i++) { if ([AfpWin]::GetForegroundWindow() -eq $h) { break }; Start-Sleep -Milliseconds 80 }
  }
  if ($iconic) { Start-Sleep -Milliseconds 500 }
  return @{ iconic = $iconic; front = ([AfpWin]::GetForegroundWindow() -eq $h) }
}
function Chat-Items {
  $all = $script:win.FindAll($TS::Descendants, (Prop $AE::ControlTypeProperty $CT::TreeItem))
  $out = @()
  foreach ($e in $all) { if ($e.Current.Name -match '^(Chat|Group chat|Meeting chat) ') { $out += $e } }
  return , $out
}
function Ensure-ChatList {
  $items = Chat-Items
  if ($items.Count -gt 0) { return , $items }
  $btns = $script:win.FindAll($TS::Descendants, (Prop $AE::ControlTypeProperty $CT::Button))
  foreach ($b in $btns) {
    if ($b.Current.Name -match '^Chat( |$)') {
      $inv = $null
      if ($b.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$inv)) { $inv.Invoke(); break }
    }
  }
  for ($i = 0; $i -lt 14; $i++) { Start-Sleep -Milliseconds 250; $items = Chat-Items; if ($items.Count -gt 0) { break } }
  return , $items
}
function Read-Messages($limit) {
  $all = $script:win.FindAll($TS::Descendants, (Prop $AE::IsContentElementProperty $true))
  $bodies = @{}; $contents = @{}; $times = @{}; $control = @{}; $order = New-Object System.Collections.ArrayList; $title = ''; $header = $null
  foreach ($e in $all) {
    $id = $e.Current.AutomationId
    if ($id -like 'message-body-*') { $k = $id.Substring(13); $bodies[$k] = $e.Current.Name; [void]$order.Add($k) }
    elseif ($id -like 'content-*') { $contents[$id.Substring(8)] = $e.Current.Name }
    elseif ($id -like 'timestamp-*') { $times[$id.Substring(10)] = $e.Current.Name }
    elseif ($id -like 'control-message-*') { $control[$id.Substring(16)] = $true }
    elseif ($id -like 'chat-header-*') { $header = $e }
  }
  if ($header) {
    $t = $header.Current.Name
    if (-not $t) {
      $texts = $header.FindAll($TS::Descendants, (Prop $AE::ControlTypeProperty $CT::Text))
      foreach ($x in $texts) { $n = $x.Current.Name; if ($n -and $n -notmatch '^(Chat participants|Chat actions|Tabs)$' -and $n.Length -gt 1) { $t = $n; break } }
    }
    $title = $t
  }
  $msgs = @()
  foreach ($k in $order) {
    if ($control.ContainsKey($k)) { continue }
    $body = [string]$bodies[$k]; $content = [string]$contents[$k]; $author = ''; $text = $body
    if ($content) {
      $text = $content
      $probe = $content.Substring(0, [Math]::Min(25, $content.Length))
      $i = $body.IndexOf($probe)
      if ($i -gt 0) { $author = $body.Substring(0, $i).Trim() }
    }
    $msgs += @{ author = $author; time = ([string]$times[$k]).TrimEnd('.'); text = $text }
  }
  if ($msgs.Count -gt $limit) { $msgs = $msgs[($msgs.Count - $limit)..($msgs.Count - 1)] }
  return @{ title = $title; messages = $msgs }
}
function Compose-Box {
  $edits = $script:win.FindAll($TS::Descendants, (Prop $AE::ControlTypeProperty $CT::Edit))
  foreach ($e in $edits) { if ($e.Current.AutomationId -like 'new-message*') { return $e } }
  return $null
}
function Box-Text($box) {
  $pat = $null
  if ($box.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pat)) { return [string]$pat.DocumentRange.GetText(4000) }
  if ($box.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pat)) { return [string]$pat.Current.Value }
  $kids = $box.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  $parts = @(); foreach ($k in $kids) { if ($k.Current.Name) { $parts += $k.Current.Name } }
  return ($parts -join '')
}

function Handle($a) {
  $proc = Get-Process -Name 'ms-teams', 'Teams' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if (-not $proc) { Fail 'not_running' 'Teams is not open on this PC.' }
  $h = $proc.MainWindowHandle
  if ($a.cmd -eq 'minimize') { [void][AfpWin]::ShowWindow($h, 6); return @{ ok = $true } }
  $st = Bring-Front $h
  $script:win = $AE::FromHandle($h)
  switch ($a.cmd) {
    'list' {
      $items = Ensure-ChatList
      if ($items.Count -eq 0) { Fail 'no_chats' 'I could not find the chat list in Teams.' }
      $rows = @(); $n = 0
      foreach ($e in $items) { if ($n -ge 120) { break }; $n++; $raw = $e.Current.Name; $rows += @{ raw = $raw.Substring(0, [Math]::Min(240, $raw.Length)) } }
      return @{ ok = $true; iconic = $st.iconic; front = $st.front; chats = $rows }
    }
    'read' {
      $items = Ensure-ChatList
      $target = $null
      foreach ($e in $items) { if ($e.Current.Name -eq $a.name) { $target = $e; break } }
      if (-not $target -and $a.label) { foreach ($e in $items) { if ($e.Current.Name.Contains([string]$a.label)) { $target = $e; break } } }  # its status text (Away, Unread...) can change between the list and now
      if (-not $target) { Fail 'no_chat' 'That chat is not in the list any more.' }
      $sc = $null; if ($target.TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern, [ref]$sc)) { $sc.ScrollIntoView() }
      $sel = $null
      if ($target.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$sel)) { $sel.Select() } else { Fail 'cannot_open' 'Teams would not open that chat.' }
      $want = (Squash $a.want)
      $r = $null
      for ($i = 0; $i -lt 6; $i++) {
        Start-Sleep -Milliseconds 350
        $r = Read-Messages ([int]$a.limit)
        if (-not $want -or (Squash $r.title).Contains($want)) { break }
      }
      return @{ ok = $true; iconic = $st.iconic; front = $st.front; selected = $true; title = $r.title; messages = $r.messages }
    }
    'focus' {
      $box = Compose-Box
      if (-not $box) { Fail 'no_box' 'I could not find the message box in Teams.' }
      $box.SetFocus(); Start-Sleep -Milliseconds 120
      $r = $box.Current.BoundingRectangle
      if ($a.click -and $r.Width -gt 10 -and $r.Height -gt 4) {
        [void][AfpWin]::SetCursorPos([int]($r.Left + [Math]::Min($r.Width / 2, 200)), [int]($r.Top + $r.Height / 2))
        Start-Sleep -Milliseconds 50
        [AfpWin]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30; [AfpWin]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 200
      }
      return @{ ok = $true; front = $st.front; text = (Box-Text $box) }
    }
    'readback' {
      $expect = Squash $a.expect
      $until = (Get-Date).AddSeconds([double]$a.wait)
      $text = ''
      do {
        $box = Compose-Box
        if (-not $box) { Fail 'no_box' 'I could not find the message box in Teams.' }
        $text = Box-Text $box
        if (-not $expect -or (Squash $text).Contains($expect)) { break }
        Start-Sleep -Milliseconds 150
      } while ((Get-Date) -lt $until)
      return @{ ok = $true; text = $text }
    }
    'messages' {
      $expect = Squash $a.expect
      $until = (Get-Date).AddSeconds([double]$a.wait)
      $r = $null; $found = $false
      do {
        $r = Read-Messages ([int]$a.limit)
        if ($expect) { foreach ($m in $r.messages) { if ((Squash $m.text).Contains($expect)) { $found = $true } } }
        if (-not $expect -or $found) { break }
        Start-Sleep -Milliseconds 250
      } while ((Get-Date) -lt $until)
      return @{ ok = $true; title = $r.title; messages = $r.messages; found = $found }
    }
    default { Fail 'bad_command' 'Unknown command.' }
  }
}

Out-Json @{ ready = $true }
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  if (-not $line.Trim()) { continue }
  try {
    $a = $line | ConvertFrom-Json
    Out-Json (Handle $a)
  } catch {
    $m = [string]$_.Exception.Message
    if ($m.StartsWith('FAIL|')) { $p = $m.Split('|', 3); Out-Json @{ ok = $false; code = $p[1]; error = $p[2] } }
    else { Out-Json @{ ok = $false; code = 'error'; error = $m.Substring(0, [Math]::Min(300, $m.Length)) } }
  }
}
'''


@dataclass(frozen=True)
class Chat:
    raw: str
    name: str
    group: bool
    unread: bool


_STATUS_TAIL = re.compile(
    r"\s+(Unread|Has context menu|Has pinned messages|Available|Away|Busy|Do not disturb|Offline|Be right back|Out of office|Muted|Presenting|In a call|In a meeting|"
    r"Focusing|Mentioned you|Urgent|Pinned|Blocked|Status unknown|Away, last seen.*)$", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")


def parse_chat(raw: str) -> Chat:
    """"Chat T J, Jayalakshmi Away" -> name "T J, Jayalakshmi"; "Group chat Sonar issue ..." -> a group."""
    unread = bool(re.search(r"\bUnread\b", raw))
    body = raw
    group = False
    for prefix, is_group in (("Group chat ", True), ("Meeting chat ", True), ("Chat ", False)):
        if body.startswith(prefix):
            body, group = body[len(prefix):], is_group
            break
    while True:
        trimmed = _STATUS_TAIL.sub("", body).strip()
        if trimmed == body:
            break
        body = trimmed
    return Chat(raw, body, group, unread)


def match_chats(query: str, chats: list[Chat]) -> list[Chat]:
    """The chats `query` means. Every word typed must start a word of the name, so "vinod" finds "Vinod Kumar" and "Kumar, Vinod". One-to-one chats win over groups."""
    words = _TOKEN.findall(query.lower())
    if not words:
        return []

    def hits(chat: Chat) -> bool:
        tokens = _TOKEN.findall(chat.name.lower())
        return all(any(t.startswith(w) for t in tokens) for w in words)

    found = [c for c in chats if hits(c)]
    singles = [c for c in found if not c.group]
    found = singles or found
    exact = [c for c in found if sorted(_TOKEN.findall(c.name.lower())) == sorted(words)]
    return exact if len(exact) == 1 else found


def clean_chat_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip()


class _Session:
    """A PowerShell running the Teams script, kept open between questions and closed after a quiet spell."""

    IDLE_SECONDS = 300

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._timer: threading.Timer | None = None
        atexit.register(self.close)

    @staticmethod
    def _script_path() -> Path:
        digest = hashlib.sha256(TEAMS_SCRIPT.encode()).hexdigest()[:12]
        path = Path(tempfile.gettempdir()) / f"afp_teams_{digest}.ps1"
        if not path.exists():
            path.write_bytes(b"\xef\xbb\xbf" + TEAMS_SCRIPT.encode("utf-8"))
        return path

    def _start(self) -> None:
        self._lines = queue.Queue()
        try:
            proc = subprocess.Popen([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self._script_path())],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except OSError as exc:
            raise AppError(f"Windows would not run the Teams helper: {exc}") from None
        self._proc = proc

        def pump(stream=proc.stdout, lines=self._lines):
            for raw in iter(stream.readline, b""):
                lines.put(raw.decode("utf-8", "replace").strip())
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        ready = self._read(30)
        if not ready or '"ready"' not in ready:
            self._kill()
            raise AppError("The Teams helper did not start. Is PowerShell blocked on this PC?")

    def _read(self, timeout: float) -> str | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def request(self, args: dict, timeout: float = 60.0) -> dict:
        with self._lock:
            for attempt in (1, 2):
                if self._proc is None or self._proc.poll() is not None:
                    self._start()
                try:
                    self._proc.stdin.write((json.dumps(args) + "\n").encode("ascii"))
                    self._proc.stdin.flush()
                except (OSError, ValueError):
                    self._kill()
                    continue
                line = self._read(timeout)
                if line is None:  # no answer: it hung or died
                    self._kill()
                    if attempt == 2:
                        raise AppError("Teams did not answer in time. Is it busy or signing in?")
                    continue
                self._rearm()
                try:
                    return json.loads(line)
                except ValueError:
                    raise AppError(f"I could not read Teams' window ({line[:200] or 'no answer'}).") from None
            raise AppError("Teams did not answer in time. Is it busy or signing in?")

    def _rearm(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.IDLE_SECONDS, self.close)
        self._timer.daemon = True
        self._timer.start()

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except OSError:
                    pass


_session = _Session()


class TeamsDriver:
    """Runs the UI Automation script. Everything that touches Windows goes through `call` (and `type_text`/`press_enter`), so tests can stand in for it."""

    def __init__(self, call: Callable[[dict], dict] | None = None, type_text: Callable[[str], None] | None = None, press_enter: Callable[[], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep, starter: Callable[[], None] | None = None):
        self._call = call or self._run_script
        self._type = type_text or _type_text
        self._enter = press_enter or _press_enter
        self._sleep = sleep
        self._starter = starter or _start_teams
        self._lock = threading.Lock()  # one conversation with Teams at a time: two would fight over the keyboard

    # -- the script: one PowerShell stays open (starting one and loading Windows' accessibility library costs a second each time)
    def _run_script(self, args: dict) -> dict:
        return _session.request(args)

    def call(self, **args) -> dict:
        reply = self._call(args)
        if not reply.get("ok"):
            if reply.get("code") == "not_running":
                raise _NotRunning(reply.get("error", ""))
            raise AppError(reply.get("error") or "Teams did not do that.")
        return reply

    def _ready(self, **args) -> dict:
        """Call, starting Teams first if it is not open."""
        try:
            return self.call(**args)
        except _NotRunning:
            self._starter()
            for _ in range(30):
                self._sleep(1.0)
                try:
                    return self.call(**args)
                except _NotRunning:
                    continue
            raise AppError("Teams did not open. Open it on the PC once, sign in, then ask again.") from None

    # -- what the person asks for
    def chats(self) -> tuple[list[Chat], dict]:
        reply = self._ready(cmd="list")
        return [parse_chat(c["raw"]) for c in reply.get("chats", [])], reply

    def read(self, chat: Chat, limit: int = 6) -> dict:
        return self.call(cmd="read", name=chat.raw, label=chat.name, limit=limit, want=(_TOKEN.findall(chat.name.lower()) or [""])[0])

    def minimize(self) -> None:
        try:
            self.call(cmd="minimize")
        except AppError:
            pass

    def resolve(self, who: str, chats: list[Chat]) -> Chat:
        if re.fullmatch(r"(?:me|myself|my ?self|self|my notes|notes to self|my own chat)", who.strip().lower()):  # your own "(You)" chat
            own = [c for c in chats if re.search(r"\(You\)\s*$", c.name)]
            if len(own) == 1:
                return own[0]
        found = match_chats(who, chats)
        if not found:
            raise AppError(f"I couldn't find a Teams chat with {who!r}. Say their name the way Teams shows it.")
        if len(found) > 1:
            names = ", ".join(c.name for c in found[:6])
            raise AppError(f"More than one chat matches {who!r}: {names}. Say the full name.")
        return found[0]

    def unread_report(self, per_chat: int = 3, max_chats: int = 8) -> list[str]:
        with self._lock:
            chats, info = self.chats()
            try:
                unread = [c for c in chats if c.unread][:max_chats]
                if not unread:
                    recent = ", ".join(c.name for c in chats[:10])
                    return ["You have no unread Teams chats.", f"Your latest chats: {recent}." if recent else "",
                            "Say “show messages from <name> on Teams” to read one."]
                lines = [f"{len(unread)} unread Teams chat{'s' if len(unread) != 1 else ''}:"]
                for chat in unread:
                    got = self.read(chat, per_chat)
                    lines.append("")
                    lines.append(f"{chat.name}")
                    lines += format_messages(got.get("messages", []), chat)
                lines += ["", "(Opening a chat marks it as read in Teams.)"]
                return lines
            finally:
                if info.get("iconic"):
                    self.minimize()

    def chat_report(self, who: str, count: int = 5) -> list[str]:
        with self._lock:
            chats, info = self.chats()
            try:
                chat = self.resolve(who, chats)
                got = self.read(chat, count)
                lines = [f"Latest messages with {chat.name}:"] + format_messages(got.get("messages", []), chat)
                if not got.get("messages"):
                    lines.append("  (I could not see any messages. Is the chat empty?)")
                lines += ["", f"To answer: say “reply: your words”, or “reply to {chat.name.split(',')[-1].strip().split(' ')[0] or chat.name}: your words”."]
                return lines
            finally:
                if info.get("iconic"):
                    self.minimize()

    def reply(self, who: str, text: str) -> list[str]:
        text = " ".join(text.split())
        if not text:
            raise AppError("There is nothing to send. Say what the message should be.")
        if len(text) > 1500:
            raise AppError("That message is too long to send from here (1,500 characters at most).")
        with self._lock:
            chats, info = self.chats()
            try:
                chat = self.resolve(who, chats)
                opened = self.read(chat, 3)
                title = clean_chat_title(opened.get("title", ""))
                if opened.get("selected") is False and not title:
                    raise AppError(f"Teams did not open the chat with {chat.name}, so I stopped without typing anything.")
                if title and not _title_fits(title, chat):
                    raise AppError(f"Teams opened a different chat ({title!r}) from the one I meant ({chat.name!r}), so I stopped without typing anything.")
                if not opened.get("front", True):
                    raise AppError("Windows would not let me bring Teams to the front, so I did not type anything. Click on Teams once and try again.")
                focus = self.call(cmd="focus", click=True)
                if not focus.get("front", True):
                    raise AppError("Teams is not the window in front, so I did not type anything.")
                if _without_placeholder(focus.get("text")):
                    raise AppError("The message box in Teams already has text in it. I did not add to it. Clear it and try again.")
                seen = ""
                for attempt in range(2):  # the first keys can be lost while the box wakes up: try again, but only while the box is still empty
                    self._type(text)
                    seen = self.call(cmd="readback", expect=text, wait=3).get("text", "")  # waits only as long as Teams takes to show it
                    if _squash(text) in _squash(seen) or _without_placeholder(seen):
                        break
                    self.call(cmd="focus", click=True)
                if _squash(text) not in _squash(seen):
                    self._clear_box()
                    shown = _without_placeholder(seen)
                    raise AppError("What Teams shows in its message box isn't what I typed" + (f" (it shows {shown[:60]!r})" if shown else " (it stayed empty: Teams did not take the typing)") + ", so I did not send it. I cleared the box.")
                self._enter()
                after = self.call(cmd="messages", limit=3, expect=text, wait=3).get("messages", [])
                delivered = any(_squash(text) in _squash(m.get("text", "")) for m in after)
                return [f"Sent to {chat.name} in Teams:", f"“{text}”"] + ([] if delivered else ["(I couldn't see it appear in the chat yet. Check Teams to be sure.)"])
            finally:
                if info.get("iconic"):
                    self.minimize()

    def _clear_box(self) -> None:
        try:
            _select_all_delete()
        except OSError:
            pass


class _NotRunning(AppError):
    pass


def _without_placeholder(text: str | None) -> str:
    """What is really typed in the message box: Teams shows "Type a message" in it when it is empty."""
    return re.sub(r"type a message", "", text or "", flags=re.I).strip()


def _squash(text: str) -> str:
    return re.sub(r"[^\w]+", "", (text or "").lower())


def _title_fits(title: str, chat: Chat) -> bool:
    words = _TOKEN.findall(chat.name.lower())
    low = title.lower()
    return not words or all(w in low for w in words[:2])


def format_messages(messages: list[dict], chat: Chat) -> list[str]:
    lines, last = [], ""
    for m in messages:
        who = re.sub(r"\s+(Sent|Seen|Delivered|Sending|Edited|Failed to send)$", "", m.get("author") or "").strip() or last or chat.name.split(",")[0]
        last = who
        when = f"  ({m['time']})" if m.get("time") else ""
        text = " ".join((m.get("text") or "").split())
        lines.append(f"  {who}: {text[:400]}{when}")
    return lines


# ---------------------------------------------------------------------------------------------------------------- typing (the keyboard of the signed-in desktop)


def _type_text(text: str) -> None:
    from .unlock import typer

    typer._send(typer.plan_text(text), 0.005)


def _press_enter() -> None:
    from .unlock import typer

    typer._send(typer.plan_enter(), 0.03)


def _select_all_delete() -> None:
    from .unlock import typer

    typer._send([("vk_down", 0x11), ("vk_down", 0x41), ("vk_up", 0x41), ("vk_up", 0x11), ("vk_down", 0x2E), ("vk_up", 0x2E)], 0.03)  # Ctrl+A, Delete


def _start_teams() -> None:
    teams = find_app("teams", start_apps())
    if teams is None:
        raise AppError("Teams is not installed on this PC.")
    launch(teams)


# ---------------------------------------------------------------------------------------------------------------- the one the job manager talks to


class AppController:
    def __init__(self, teams: TeamsDriver | None = None, apps: Callable[[], list[AppEntry]] | None = None, starter: Callable[[list[str]], None] | None = None):
        self.teams = teams or TeamsDriver()
        self._apps = apps or start_apps
        self._starter = starter

    def apps(self) -> list[AppEntry]:
        return self._apps()

    def find(self, query: str) -> AppEntry | None:
        return find_app(query, self.apps())

    def run(self, kind: str, target: str, text: str | None) -> list[str]:
        """Do one app step and say what happened, in lines for the phone."""
        if kind == "open":
            app = self.find(target)
            if app is None:
                raise AppError(f"I couldn't find an app called {target!r} in this PC's Start menu.")
            launch(app, self._starter)
            return [f"Opening {app.name} on your PC."]
        if kind == "teams_unread":
            return self.teams.unread_report()
        if kind == "teams_chat":
            return self.teams.chat_report(target)
        if kind == "teams_reply":
            return self.teams.reply(target, text or "")
        raise AppError(f"I don't know how to do {kind!r}.")


def main(argv: list[str] | None = None) -> int:
    """`python -m handoff.appctl teams-look` prints what is seen in the Teams window; `apps` lists the Start menu."""
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "help"
    if cmd == "apps":
        for app in start_apps():
            print(f"{app.name}\t{app.appid}")
        return 0
    if cmd == "teams-look":
        driver = TeamsDriver()
        chats, info = driver.chats()
        print(f"{len(chats)} chats (Teams in front: {info.get('front')}, was minimised: {info.get('iconic')})")
        for chat in chats[:15]:
            print(("* " if chat.unread else "  ") + ("[group] " if chat.group else "") + chat.name)
        if info.get("iconic"):
            driver.minimize()
        return 0
    print("usage: python -m handoff.appctl apps | teams-look")
    return 0


if __name__ == "__main__":
    sys.exit(main())
