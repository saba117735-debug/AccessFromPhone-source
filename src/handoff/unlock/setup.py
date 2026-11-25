"""Set up (or remove) unlocking this PC from the phone. Run at the PC, by someone who can approve an administrator prompt.

    python -m handoff.unlock.setup install      save the Windows sign-in, choose the unlock PIN, start the helper
    python -m handoff.unlock.setup status       is it running, is it on, has it stopped itself
    python -m handoff.unlock.setup resume       start trying again after it stopped (after checking the saved sign-in)
    python -m handoff.unlock.setup restart      restart the helper (needed after the app is updated: it keeps running the old code until then)
    python -m handoff.unlock.setup repair       recreate the helper's task and start it (keeps the saved sign-in), and say why if it won't start
    python -m handoff.unlock.setup uninstall    remove the helper and the saved sign-in

Nothing is ever asked for or shown on the phone. The Windows sign-in is typed here, kept encrypted for this PC only, and read by nobody
except the helper, which only ever sends it to the keyboard of the lock screen.
"""
from __future__ import annotations

import getpass
import os
from xml.sax.saxutils import escape
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

from . import dpapi
from .agent import DEFAULT_PORT, data_dir
from .client import AgentClient
from .store import PIN_MAX, PIN_MIN, UnlockStore

TASK = "AccessFromPhone Unlock"
NGC_PROVIDER = "{D6886603-9D2F-4EB2-B667-1971041FA96B}"  # the Windows Hello PIN sign-in
RISK = """
Unlocking from the phone means this PC will hold your Windows sign-in, encrypted so it opens on this PC only.
Anyone who could pass the unlock PIN you choose next, from a phone already paired with this PC, could unlock it.

  - A Windows Hello PIN is the safer choice: it works on this one PC and nothing else. A password also opens your account elsewhere.
  - Wrong unlock PINs pause it after 3 tries. If typing the sign-in doesn't unlock the PC twice, it stops by itself.
  - You can switch it off or remove everything at any time:  python -m handoff.unlock.setup uninstall
"""


LOG = Path(__file__).resolve().parents[3] / "data" / "unlock-setup.log"


def note(line: str) -> None:
    """What the installer did, in a file next to the app (never the sign-in or the PIN), so that a window that closes can still be explained."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def relaunch_elevated(args: list[str], windowed: bool = False) -> bool:
    """Asks Windows for administrator rights and runs this again in a new window. False if that was refused. `windowed` = no console (the setup window)."""
    import ctypes

    exe = sys.executable
    if windowed and exe.lower().endswith("python.exe") and Path(exe[:-len("python.exe")] + "pythonw.exe").exists():
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    params = subprocess.list2cmdline(["-m", "handoff.unlock.setup", *args])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(Path(__file__).resolve().parents[3]), 1)
    if int(result) <= 32:
        note(f"Windows refused the administrator request (ShellExecute code {int(result)}; 5 = refused or declined, 2/3 = program not found)")
    return int(result) > 32


def elevation_refusal_reason(read_policy: Callable[[], int | None] | None = None, groups: Callable[[], str] | None = None) -> str:
    """When Windows refused the administrator request, why - if that can be told. A work PC's policy can refuse every such request for an account that is not an
    administrator, without ever showing a prompt, which looks exactly like a declined one. "" when the reason isn't known."""
    try:
        if read_policy is not None:
            mode = read_policy()
        else:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System") as key:
                mode = int(winreg.QueryValueEx(key, "ConsentPromptBehaviorUser")[0])
        if mode != 0:   # 0 = "automatically deny elevation requests" for accounts that are not administrators
            return ""
        if groups is not None:
            listing = groups()
        else:
            listing = subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"), "/groups"], capture_output=True, text=True, timeout=10).stdout
        if "S-1-5-32-544" in listing:   # in the Administrators group (even if only for approving a prompt): the refusal is not this policy
            return ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return ("This Windows account isn't an administrator on this PC, and this PC's settings automatically refuse administrator requests from it, so Windows said no without asking. "
            "Ask your IT team, or sign in with an administrator account and, in an administrator PowerShell here, run  python -m handoff.unlock.setup status  to see what is missing "
            "(it says whether to run repair or install).")


def sign_in_looks_like_pin(read: Callable[[], str] | None = None) -> bool:
    """True when Windows last signed in with the Hello PIN, so a PIN is the thing to save."""
    try:
        if read is not None:
            return read().upper() == NGC_PROVIDER
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI") as key:
            return str(winreg.QueryValueEx(key, "LastLoggedOnProvider")[0]).upper() == NGC_PROVIDER
    except OSError:
        return False


def password_is_right(user: str, password: str) -> bool:
    """Checks a password with Windows without signing in (so a typo here can't lock anything out, and a wrong saved one is never typed later)."""
    import ctypes
    from ctypes import wintypes

    handle = wintypes.HANDLE()
    domain = "." if "\\" not in user else user.split("\\", 1)[0]
    name = user.split("\\", 1)[-1]
    ok = ctypes.windll.advapi32.LogonUserW(name, domain, password, 3, 0, ctypes.byref(handle))  # 3 = network logon, 0 = default provider
    if ok:
        ctypes.windll.kernel32.CloseHandle(handle)
    return bool(ok)


def lock_down(folder: Path, run: Callable = subprocess.run) -> None:
    """Only the system and administrators may read the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    run(["icacls", str(folder), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"], check=True, capture_output=True)


def task_command(python: str, root: Path, port: int, out: Path | None = None) -> str:
    """What the scheduled task runs. Whatever the helper prints if it dies goes to agent.out beside its log, so a helper that won't start can be explained."""
    out = out or data_dir() / "agent.out"
    return f'cmd.exe /c cd /d "{root}" && "{python}" -m handoff.unlock.agent --port {port} >> "{out}" 2>&1'


def task_xml(python: str, root: Path, port: int, out: Path | None = None) -> str:
    """The scheduled task, written out in full.

    `schtasks /Create` with plain options makes a task that WON'T START ON BATTERY and STOPS when a laptop is unplugged, which is exactly what
    would switch the unlock helper off whenever the laptop is away from its charger. So this states every setting: runs on battery, never
    stopped for taking too long, restarted a minute after it dies, started at boot, as the system, and only one copy at a time.
    """
    out = out or data_dir() / "agent.out"
    arguments = f'/c cd /d "{root}" && "{python}" -m handoff.unlock.agent --port {port} >> "{out}" 2>&1'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Lets your phone unlock this PC (AccessFromPhone). Remove with: python -m handoff.unlock.setup uninstall</Description></RegistrationInfo>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>cmd.exe</Command><Arguments>{escape(arguments)}</Arguments></Exec></Actions>
</Task>
"""


def create_task(python: str, root: Path, port: int, run: Callable = subprocess.run, out: Path | None = None, xml_path: Path | None = None) -> None:
    xml_path = xml_path or data_dir() / "task.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(task_xml(python, root, port, out), encoding="utf-16")  # Task Scheduler reads UTF-16
    try:
        run(["schtasks", "/Create", "/TN", TASK, "/XML", str(xml_path), "/F"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        note("the full task description was refused: creating a plain task, then fixing its battery settings")
        run(["schtasks", "/Create", "/TN", TASK, "/TR", task_command(python, root, port, out), "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"], check=True, capture_output=True)
        run(["powershell", "-NoProfile", "-Command",
             f"Set-ScheduledTask -TaskName '{TASK}' -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
             "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 -RestartInterval ([TimeSpan]::FromMinutes(1)) -StartWhenAvailable)"],
            check=True, capture_output=True)
    run(["schtasks", "/Run", "/TN", TASK], check=False, capture_output=True)


def wait_until_running(port: int = DEFAULT_PORT, seconds: float = 12.0, ask: Callable[[], dict] | None = None, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Did the helper come up? Looks for a few seconds (it takes a moment to start)."""
    ask = ask or (lambda: AgentClient(port).status())
    waited = 0.0
    while waited <= seconds:
        if ask().get("running"):
            return True
        sleep(1.0)
        waited += 1.0
    return False


def tail(path: Path, lines: int = 12) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(nothing there)"


def explain_not_running(say: Callable[[str], None], run: Callable = subprocess.run) -> None:
    """The helper did not come up: show what Windows says about the task, and what the helper printed or logged."""
    shown = run(["schtasks", "/Query", "/TN", TASK, "/V", "/FO", "LIST"], capture_output=True, text=True).stdout or ""
    for line in shown.splitlines():
        if line.split(":", 1)[0].strip() in ("Status", "Last Run Time", "Last Result", "Run As User", "Task To Run"):
            say("  " + line.strip())
    say("What the helper printed when it stopped (agent.out):\n" + tail(data_dir() / "agent.out"))
    say("Its own log (unlock.log):\n" + tail(data_dir() / "unlock.log", 6))


def remove_task(run: Callable = subprocess.run) -> None:
    run(["schtasks", "/End", "/TN", TASK], check=False, capture_output=True)
    run(["schtasks", "/Delete", "/TN", TASK, "/F"], check=False, capture_output=True)


def perform_install(kind: str, secret: str, pin: str, folder: Path | None = None, run: Callable = subprocess.run, port: int = DEFAULT_PORT) -> None:
    """Save the sign-in, lock the folder, create and start the helper. Needs administrator rights. Used by the window and by `install`."""
    folder = folder or data_dir()
    UnlockStore(folder / "unlock.json", dpapi.protect, dpapi.unprotect).configure(kind, secret, pin)
    note("sign-in saved")
    lock_down(folder, run)
    note("folder locked down")
    create_task(sys.executable, Path(__file__).resolve().parents[3], port, run, out=folder / "agent.out", xml_path=folder / "task.xml")
    note("task created and started")
    if run is subprocess.run and not wait_until_running(port):
        explain = []
        explain_not_running(explain.append)
        raise RuntimeError("the helper was set up but did not start. " + " ".join(explain)[:600])


def ask_secret(label: str, ask: Callable[[str], str]) -> str:
    first = ask(f"{label}: ")
    if ask(f"{label} again: ") != first:
        raise ValueError("The two didn't match.")
    return first


def install(ask: Callable[[str], str] = getpass.getpass, say: Callable[[str], None] = print, folder: Path | None = None, run: Callable = subprocess.run,
            port: int = DEFAULT_PORT, check_password: Callable[[str, str], bool] = password_is_right, pin_default: bool | None = None,
            read: Callable[[str], str] = input) -> int:
    folder = folder or data_dir()
    note("install started")
    say(RISK)
    if read("Type YES to go on: ").strip() != "YES":
        say("Nothing was changed.")
        return 1
    use_pin = sign_in_looks_like_pin() if pin_default is None else pin_default
    say("This PC last signed in with a " + ("Windows Hello PIN." if use_pin else "password (or another method)."))
    kind = "pin" if read(f"Save the Windows Hello PIN? [{'Y/n' if use_pin else 'y/N'}]: ").strip().lower() in (("", "y", "yes") if use_pin else ("y", "yes")) else "password"
    try:
        secret = ask_secret("Your Windows " + ("Hello PIN" if kind == "pin" else "password"), ask)
        if kind == "password":
            user = os.environ.get("USERDOMAIN", ".") + "\\" + getpass.getuser()
            if not check_password(user, secret):
                say("Windows says that password isn't right, so it was not saved.")
                return 1
        say(f"Now choose the unlock PIN ({PIN_MIN}-{PIN_MAX} characters). You will type it on the phone each time. Make it different from the Windows sign-in.")
        pin = ask_secret("Unlock PIN", ask)
        store = UnlockStore(folder / "unlock.json", dpapi.protect, dpapi.unprotect)
        store.configure(kind, secret, pin)
    except ValueError as exc:
        note(f"stopped: {exc}")
        say(str(exc))
        return 1
    note("sign-in saved")
    lock_down(folder, run)
    note("folder locked down")
    create_task(sys.executable, Path(__file__).resolve().parents[3], port, run, out=folder / "agent.out", xml_path=folder / "task.xml")
    note("task created and started")
    say("\nDone. The unlock helper is installed and started. To use it, lock this PC, then write to the app from your phone:  unlock my PC")
    return 0


def status(say: Callable[[str], None] = print, port: int = DEFAULT_PORT) -> int:
    info = AgentClient(port).status()
    if not info.get("running"):
        try:
            saved = (data_dir() / "unlock.json").exists()
            seen = "found" if saved else "MISSING"
        except PermissionError:  # the folder is locked to administrators on purpose
            saved, seen = True, "present (only administrators can look inside, so this can't say more)"
        task = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True).returncode == 0
        say("The unlock helper is not running.")
        say(f"  Saved sign-in file ({data_dir() / 'unlock.json'}): {seen}")
        say(f"  Scheduled task \"{TASK}\": {'found' if task else 'MISSING'}")
        if not saved and not task:
            say("Setup has not finished (nothing was installed). Open PowerShell as Administrator and run  python -m handoff.unlock.setup install  there, and read what it says.")
            say(f"What it did is noted in {LOG}")
        elif task and saved:
            say("Both are there but nothing is listening. In an Administrator PowerShell run  python -m handoff.unlock.setup repair  : it recreates the task, starts it, and shows what went wrong if it still won't start.")
        return 1
    say(f"Running. Set up: {info['configured']}. On: {info['enabled']}. Saved sign-in: {info['kind'] or '-'}. Locked right now: {info['locked']}.")
    if info.get("log"):
        say("Recent helper log (newest last):\n  " + "\n  ".join(info["log"]))
    if info.get("attention"):
        wait = info.get("auto_resume_in") or 0
        again = f" It will start trying again by itself in about {max(1, round(wait / 60))} minute(s)." if wait else " It has already tried again by itself and is waiting for you."
        say("It has STOPPED itself: typing the saved sign-in didn't unlock the PC twice." + again + " Check that the saved sign-in is still right, then run  python -m handoff.unlock.setup resume  to start now.")
    if info.get("locked_for"):
        say(f"Paused for {info['locked_for']} s after wrong PINs.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    command = args[0] if args else "status"
    if command == "status":
        return status()
    if command == "gui":  # the setup window (opened by the button on the PC's page)
        if not is_admin():
            return 0 if relaunch_elevated(["gui"], windowed=True) else 1
        note("setup window opened")
        from . import setup_gui

        setup_gui.run()
        return 0
    if command in ("install", "uninstall", "resume", "restart", "repair"):
        if not is_admin():
            print("Windows will ask for administrator rights and open a NEW window. Finish there, and read what it says.")
            note(f"{command}: asking for administrator rights")
            ok = relaunch_elevated(args)
            if not ok:
                print(elevation_refusal_reason() or "Windows did not give administrator rights (the prompt was declined or blocked). Open PowerShell as Administrator and run the same command there.")
                note("administrator rights refused")
            return 0 if ok else 1
        try:
            if command == "install":
                code = install()
            elif command in ("restart", "repair"):  # after the app is updated the helper keeps running the old code until it is restarted
                if command == "repair":
                    create_task(sys.executable, Path(__file__).resolve().parents[3], DEFAULT_PORT)
                else:
                    subprocess.run(["schtasks", "/End", "/TN", TASK], check=False, capture_output=True)
                    time.sleep(2)
                    subprocess.run(["schtasks", "/Run", "/TN", TASK], check=True, capture_output=True)
                if wait_until_running():
                    print("The unlock helper is running.")
                    code = 0
                else:
                    print("The unlock helper did not start. This is what Windows and the helper say:")
                    explain_not_running(print)
                    code = 1
            elif command == "resume":
                UnlockStore(data_dir() / "unlock.json", dpapi.protect, dpapi.unprotect).clear_attention()
                print("It will try again the next time you ask.")
                code = 0
            else:
                remove_task()
                UnlockStore(data_dir() / "unlock.json", dpapi.protect, dpapi.unprotect).forget()
                print("The unlock helper and the saved sign-in are removed.")
                code = 0
        except subprocess.CalledProcessError as exc:  # a Windows tool refused: say which one and what it said
            detail = (exc.stderr or exc.stdout or b"").decode("utf-8", "replace").strip()
            print(f"\nWindows refused this step: {' '.join(map(str, exc.cmd[:2]))}\n{detail}")
            note(f"failed: {exc.cmd[:2]} {detail}")
            code = 1
        except Exception:  # noqa: BLE001  (a window that closes by itself explains nothing)
            print("\nSomething went wrong:\n")
            traceback.print_exc()
            note("failed: " + traceback.format_exc().splitlines()[-1])
            code = 1
        input("\nPress Enter to close this window.")
        return code
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
