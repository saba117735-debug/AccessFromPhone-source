"""Keep the server listening when a phone vanishes while it is connecting.

On Windows, Python 3.11's event loop closes the WHOLE listening port if accepting one connection fails with an error such as
"[WinError 64] The specified network name is no longer available" (a phone that dropped off Wi-Fi, or went to sleep, at the wrong
moment). The app then looks alive but no phone can connect until it is restarted. A connection that disappeared is not a reason to stop
listening, so this makes those few errors mean "that one is gone, accept the next".
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("handoff.netfix")

# Windows error numbers that only mean "that one connection is gone": network name no longer available, semaphore timeout,
# connection aborted, and the Winsock "aborted" and "reset by peer".
GONE = frozenset({64, 121, 1236, 10053, 10054})
RETRY_AFTER = 0.05  # seconds: a pause so a stream of failures cannot spin the loop


def _is_gone(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in GONE


class _QuietGone(logging.Filter):
    """The loop also prints a long "Task exception was never retrieved" for each vanished connection we now skip. Hide only those."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            exc = record.exc_info[1] if record.exc_info else None
            text = record.getMessage()
            if "accept_coro" in text and "never retrieved" in text and exc is not None and _is_gone(exc):
                return False
            if "Accept failed on a socket" in text and exc is not None and _is_gone(exc):
                return False
        except Exception:  # noqa: BLE001 - a logging filter must never raise
            pass
        return True


def harden_accept(proactor_class) -> bool:
    """Wrap `proactor_class.accept` so a vanished connection is skipped instead of closing the listening socket. Safe to call twice."""
    original = proactor_class.accept
    if getattr(original, "_afp_hardened", False):
        return False

    def accept(self, listener):
        loop = self._loop
        outer = loop.create_future()
        current = [None]  # the real accept in flight, so cancelling ours cancels it

        def attempt() -> None:
            if outer.done():
                return
            try:
                inner = original(self, listener)
            except BaseException as exc:  # noqa: BLE001 - handed on exactly as the original would have raised it
                outer.set_exception(exc)
                return
            current[0] = inner
            inner.add_done_callback(finished)

        def finished(inner) -> None:
            if outer.done():
                return
            if inner.cancelled():
                outer.cancel()
                return
            exc = inner.exception()
            if exc is None:
                outer.set_result(inner.result())
            elif _is_gone(exc) and not loop.is_closed() and listener.fileno() != -1:
                log.debug("a phone dropped while connecting (%s); still listening", exc)
                loop.call_later(RETRY_AFTER, attempt)
            else:
                outer.set_exception(exc)

        def cancelled(_outer) -> None:
            if _outer.cancelled() and current[0] is not None:
                current[0].cancel()

        outer.add_done_callback(cancelled)
        attempt()
        return outer

    accept._afp_hardened = True  # type: ignore[attr-defined]
    proactor_class.accept = accept
    return True


def install() -> bool:
    """Apply the fix where it is needed (Windows). Returns whether anything was changed."""
    if sys.platform != "win32":
        return False
    try:
        from asyncio.windows_events import IocpProactor
    except ImportError:
        return False
    quiet = _QuietGone()
    asyncio_log = logging.getLogger("asyncio")
    if not any(isinstance(f, _QuietGone) for f in asyncio_log.filters):
        asyncio_log.addFilter(quiet)
    return harden_accept(IocpProactor)
