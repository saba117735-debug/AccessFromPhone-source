"""How this PC is doing right now: battery, network speed, processor, memory, disk. Shown on the phone.

A small background sampler reads the network counters once a second, so the speed is already known when the phone asks,
and the last minute can be drawn as a graph. Everything is read-only and local; nothing is sent anywhere.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Callable

try:
    import psutil
except ImportError:  # the rest of the app works without it; the status just says so
    psutil = None


def _nic_for(ip: str) -> str | None:
    """The network adapter that holds this PC's current Wi-Fi/LAN address, so a tunnel isn't counted twice."""
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.address == ip for a in addrs):
                return name
    except Exception:
        pass
    return None


def read_network(ip: str = "") -> dict:
    """Totals so far for the adapter in use (or all real adapters), plus what Windows says its link speed is."""
    counters = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    nic = _nic_for(ip) if ip else None
    if nic and nic in counters:
        c = counters[nic]
        return {"adapter": nic, "recv": c.bytes_recv, "sent": c.bytes_sent, "link_mbps": getattr(stats.get(nic), "speed", 0) or 0}
    live = [n for n, s in stats.items() if s.isup and "loopback" not in n.lower() and n in counters]
    return {"adapter": live[0] if len(live) == 1 else "", "recv": sum(counters[n].bytes_recv for n in live), "sent": sum(counters[n].bytes_sent for n in live),
            "link_mbps": max([stats[n].speed for n in live] or [0])}


def read_battery() -> dict | None:
    b = psutil.sensors_battery()
    if b is None:
        return None  # a desktop PC
    left = b.secsleft if isinstance(b.secsleft, int) and b.secsleft > 0 else None
    plugged = bool(b.power_plugged)
    state = ("full" if b.percent >= 99 else "charging") if plugged else "discharging"
    return {"percent": round(b.percent), "plugged": plugged, "state": state, "minutes_left": round(left / 60) if left and not plugged else None}


def read_system() -> dict:
    mem = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + os.sep)
    except OSError:
        disk = None
    return {
        "cpu_percent": round(psutil.cpu_percent(interval=None)),
        "memory": {"percent": round(mem.percent), "used": mem.used, "total": mem.total},
        "disk": {"percent": round(disk.percent), "free": disk.free, "total": disk.total} if disk else None,
        "uptime_s": int(time.time() - psutil.boot_time()),
    }


class Sampler:
    """Reads the counters every `interval` seconds and keeps the last `keep` readings."""

    def __init__(self, interval: float = 1.0, keep: int = 60, ip_getter: Callable[[], str] | None = None,
                 network: Callable[[str], dict] | None = None, battery: Callable[[], dict | None] | None = None,
                 system: Callable[[], dict] | None = None):
        self._interval, self._ip = interval, ip_getter or (lambda: "")
        self._network, self._battery, self._system = network or read_network, battery or read_battery, system or read_system
        self._history: deque[dict] = deque(maxlen=keep)
        self._last: tuple[float, int, int] | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._net: dict = {}

    @property
    def available(self) -> bool:
        return psutil is not None

    def start(self) -> None:
        if not self.available or self._thread:
            return
        try:
            psutil.cpu_percent(interval=None)  # the first reading is always 0: take it now so the next is real
        except Exception:
            pass
        self.sample()
        self._thread = threading.Thread(target=self._loop, name="pc-status", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.sample()
            except Exception:
                pass  # a hiccup in one reading must never stop the next

    def sample(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        net = self._network(self._ip())
        with self._lock:
            down = up = 0.0
            if self._last and now > self._last[0]:
                gap = now - self._last[0]
                down, up = max(0, net["recv"] - self._last[1]) * 8 / gap, max(0, net["sent"] - self._last[2]) * 8 / gap  # bits per second
            self._last = (now, net["recv"], net["sent"])
            self._net = {"adapter": net.get("adapter", ""), "link_mbps": net.get("link_mbps", 0)}
            self._history.append({"down": round(down), "up": round(up)})

    def snapshot(self) -> dict:
        if not self.available:
            return {"available": False, "reason": "Install the psutil package on the PC to see its status here."}
        with self._lock:
            hist = list(self._history)
            net = dict(self._net)
        now = hist[-1] if hist else {"down": 0, "up": 0}
        recent = hist[-3:] or [now]  # a few seconds smooths out the jumps
        data = {
            "available": True, "time": time.time(), "battery": self._battery(), **self._system(),
            "network": {**net, "down_bps": round(sum(r["down"] for r in recent) / len(recent)), "up_bps": round(sum(r["up"] for r in recent) / len(recent)),
                        "history": {"down": [r["down"] for r in hist], "up": [r["up"] for r in hist]}},
        }
        return data


def describe(data: dict) -> str:
    """The same facts as a sentence or two, for "what's my battery?" in the chat."""
    if not data.get("available"):
        return data.get("reason", "I can't read this PC's status.")
    parts = []
    b = data.get("battery")
    if b is None:
        parts.append("This PC has no battery (it runs on mains power).")
    elif b["state"] == "full":
        parts.append(f"Battery is full ({b['percent']}%) and plugged in.")
    elif b["plugged"]:
        parts.append(f"Battery is at {b['percent']}% and charging.")
    else:
        left = f", about {b['minutes_left'] // 60} h {b['minutes_left'] % 60} min left" if b.get("minutes_left") else ""
        parts.append(f"Battery is at {b['percent']}% and running on battery{left}.")
    n = data["network"]
    parts.append(f"Network{' (' + n['adapter'] + ')' if n.get('adapter') else ''}: {mbps(n['down_bps'])} down, {mbps(n['up_bps'])} up right now"
                 + (f"; the link runs at {n['link_mbps']} Mbps." if n.get("link_mbps") else "."))
    parts.append(f"Processor {data['cpu_percent']}% busy, memory {data['memory']['percent']}% used"
                 + (f", {data['disk']['free'] // 1024 ** 3} GB free on the system drive." if data.get("disk") else "."))
    return " ".join(parts)


def mbps(bits_per_second: float) -> str:
    m = bits_per_second / 1_000_000
    return f"{m:.1f} Mbps" if m >= 0.1 else f"{bits_per_second / 1000:.0f} Kbps"


def _default() -> Sampler:
    from .connectivity import lan_ip

    return Sampler(ip_getter=lan_ip)


DEFAULT = _default()


def current() -> dict:
    """The latest status, starting the background sampler the first time."""
    DEFAULT.start()
    return DEFAULT.snapshot()
