"""Stopping work that is already running: kill a command, or hang up on a slow model.

A job gets one `CancelToken`. Whatever is blocking (a command's process, a request to the model) registers a
hook that frees it, and `cancel()` runs those hooks at once. Code that cannot be interrupted checks the token
between steps instead.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Callable


class Cancelled(Exception):
    """The user pressed Stop."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._hooks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()

    def on_cancel(self, hook: Callable[[], None]) -> Callable[[], None]:
        """Run `hook` if this is cancelled. Returns a function that takes the hook back."""
        with self._lock:
            if not self._event.is_set():
                self._hooks.append(hook)
                return lambda: self._forget(hook)
        self._run(hook)  # already cancelled: free whatever was just about to block
        return lambda: None

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            hooks, self._hooks = self._hooks, []
        for hook in hooks:
            self._run(hook)

    def _forget(self, hook: Callable[[], None]) -> None:
        with self._lock:
            if hook in self._hooks:
                self._hooks.remove(hook)

    @staticmethod
    def _run(hook: Callable[[], None]) -> None:
        try:
            hook()
        except Exception:  # a hook that fails must not stop the others
            pass


# The token of the job being worked on in this thread, so deep code (the HTTP call to the model) can find it.
CURRENT: contextvars.ContextVar[CancelToken | None] = contextvars.ContextVar("handoff_cancel", default=None)
