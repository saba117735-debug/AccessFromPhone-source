"""Run a command on the PC for the agent.

Three things keep this safe: it is off unless `commands.enabled` is true, every run
waits for the user's approval, and it only runs inside a writable folder. The deny
list and the optional allow list are extra checks on top, not the main protection.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .cancel import CancelToken
from .config import Action, CommandsConfig
from .policy import PolicyEngine, PolicyError

MAX_COMMAND_CHARS = 2000
_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "HANDOFF_")
_CHAINING = re.compile(r"[;&|<>`\n]|\$\(")


class CommandRefused(PolicyError):
    """The command is not allowed to run."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    stopped: bool = False  # the user pressed Stop

    def render(self, command: str) -> str:
        status = ("stopped by you" if self.stopped else "timed out and was stopped" if self.timed_out
                  else f"exit code {self.exit_code}")
        body = self.output.strip() or "(no output)"
        return f"$ {command}\n{body}\n({status})"


def _clean_env() -> dict[str, str]:
    """The environment a command sees, without anything that looks like a secret."""
    return {k: v for k, v in os.environ.items() if not any(w in k.upper() for w in _SECRET_WORDS)}


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell:
            utf8 = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            return [shell, "-NoProfile", "-NonInteractive", "-Command", utf8 + command]
        return ["cmd.exe", "/c", command]
    return ["/bin/sh", "-c", command]


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class CommandRunner:
    def __init__(self, config: CommandsConfig, policy: PolicyEngine, audit: AuditLog):
        self._config = config
        self._policy = policy
        self._audit = audit
        self._deny = [re.compile(p, re.I) for p in config.deny]

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def check(self, command: str) -> None:
        """Raise CommandRefused unless this command may be proposed and run."""
        if not self._config.enabled:
            raise CommandRefused("Running commands is turned off. Set commands.enabled: true in config.yaml to allow it.")
        text = command.strip()
        if not text:
            raise CommandRefused("the command is empty")
        if len(text) > MAX_COMMAND_CHARS:
            raise CommandRefused(f"the command is longer than {MAX_COMMAND_CHARS} characters")
        for pattern in self._deny:
            if pattern.search(text):
                raise CommandRefused(f"that command is blocked by commands.deny ({pattern.pattern})")
        allowed = self._config.allow_only
        if allowed:
            if not any(text.lower().startswith(a.lower()) for a in allowed):
                raise CommandRefused("that command is not on the commands.allow_only list: " + ", ".join(allowed))
            if _CHAINING.search(text):
                raise CommandRefused("with commands.allow_only, a command cannot chain or redirect (; & | < > ` $()")

    def run(self, job_id: str, cwd: str | Path, command: str, approved: bool, token: CancelToken | None = None) -> CommandResult:
        self.check(command)
        if token:
            token.check()
        real = self._policy.authorize(Action.RUN, cwd, approved)  # writable folder, and approved
        if not real.is_dir():
            raise NotADirectoryError(f"{real} is not a folder")
        self._audit.record(job_id, "run_command", cwd=real, command=command.strip())
        proc = subprocess.Popen(
            _shell_argv(command.strip()),
            cwd=real,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_clean_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
        forget = token.on_cancel(lambda: _kill_tree(proc)) if token else None  # Stop kills the command and its children
        timed_out = False
        try:
            raw, _ = proc.communicate(timeout=self._config.timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raw, _ = proc.communicate()
            timed_out = True
        finally:
            if forget:
                forget()
        stopped = bool(token and token.cancelled)
        text = raw.decode("utf-8", "replace")
        limit = self._config.max_output_chars
        if len(text) > limit:
            text = text[:limit] + "\n... (output truncated)"
        result = CommandResult(None if timed_out or stopped else proc.returncode, text, timed_out and not stopped, stopped)
        self._audit.record(job_id, "command_finished", exit_code=result.exit_code, timed_out=result.timed_out, stopped=stopped)
        return result
