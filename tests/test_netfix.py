"""A phone that vanishes while connecting must not stop the server listening."""
import asyncio
import sys

import pytest

from handoff import netfix


class FakeListener:
    def fileno(self):
        return 7


def gone(code=64):
    exc = OSError(22, "The specified network name is no longer available", None, code)
    return exc


class FakeProactor:
    """Stands in for asyncio's IocpProactor: each accept() answers from a script of results."""

    def __init__(self, loop, script):
        self._loop, self.script, self.calls = loop, list(script), 0

    def accept(self, listener):
        self.calls += 1
        future = self._loop.create_future()
        step = self.script.pop(0)
        if step == "pending":
            return future
        if isinstance(step, BaseException):
            future.set_exception(step)
        else:
            future.set_result(step)
        return future


def hardened():
    class Proactor(FakeProactor):
        pass

    netfix.harden_accept(Proactor)
    return Proactor


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(netfix, "RETRY_AFTER", 0.001)


def test_a_vanished_connection_is_skipped_and_the_next_one_is_accepted():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = hardened()(loop, [gone(64), gone(10054), ("conn", "addr")])
        return await proactor.accept(FakeListener()), proactor.calls

    assert run(go()) == (("conn", "addr"), 3)


def test_the_windows_errors_that_only_mean_gone_are_all_skipped():
    for code in sorted(netfix.GONE):
        async def go(code=code):
            loop = asyncio.get_running_loop()
            return await hardened()(loop, [gone(code), "ok"]).accept(FakeListener())

        assert run(go()) == "ok", code


def test_a_real_failure_still_reaches_the_caller():
    async def go():
        loop = asyncio.get_running_loop()
        await hardened()(loop, [OSError(24, "Too many open files")]).accept(FakeListener())

    with pytest.raises(OSError):
        run(go())


def test_it_stops_retrying_when_the_listening_socket_is_closed():
    class Closed(FakeListener):
        def fileno(self):
            return -1

    async def go():
        loop = asyncio.get_running_loop()
        await hardened()(loop, [gone(64)]).accept(Closed())

    with pytest.raises(OSError):
        run(go())


def test_cancelling_the_accept_cancels_the_real_one():
    async def go():
        loop = asyncio.get_running_loop()
        proactor = hardened()(loop, ["pending"])
        outer = proactor.accept(FakeListener())
        outer.cancel()
        await asyncio.sleep(0)
        return outer.cancelled()

    assert run(go()) is True


def test_it_is_applied_once():
    class Proactor(FakeProactor):
        pass

    assert netfix.harden_accept(Proactor) is True
    assert netfix.harden_accept(Proactor) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_the_real_windows_loop_is_patched_and_still_serves():
    from asyncio.windows_events import IocpProactor

    netfix.install()
    assert getattr(IocpProactor.accept, "_afp_hardened", False)

    async def go():
        async def handle(reader, writer):
            writer.write(b"hello")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        # a client that connects and vanishes straight away, then a real one
        _, w = await asyncio.open_connection("127.0.0.1", port)
        w.transport.abort()
        await asyncio.sleep(0.2)
        r, w2 = await asyncio.open_connection("127.0.0.1", port)
        data = await r.read(5)
        w2.close()
        server.close()
        return data

    assert asyncio.new_event_loop().run_until_complete(go()) == b"hello"


def test_only_the_noise_about_vanished_connections_is_hidden():
    import logging

    filt = netfix._QuietGone()

    def record(message, exc):
        return logging.LogRecord("asyncio", logging.ERROR, __file__, 1, message, None, (type(exc), exc, None))

    noise = "Task exception was never retrieved\nfuture: <Task coro=<IocpProactor.accept.<locals>.accept_coro() done>>"
    assert filt.filter(record(noise, gone(64))) is False
    assert filt.filter(record(noise, OSError(24, "Too many open files"))) is True
    assert filt.filter(record("Task exception was never retrieved\nfuture: <Task coro=<other()>>", gone(64))) is True
