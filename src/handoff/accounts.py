"""The owner of this PC and the phones paired to it, kept in the database.

The owner sets a password once, on the PC. A phone joins by scanning a one-time code shown on the PC; it then holds its
own long random token, which the PC stores only as a hash. The owner can rename, block or remove any phone.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import winauth
from .db import Database, open_database, retire

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out and type
MIN_PASSWORD = 8


class AccountError(Exception):
    """Something the person can fix; the message says what."""


class DeviceLimit(AccountError):
    """The PC already has as many phones as it allows."""


class Locked(AccountError):
    def __init__(self, seconds: int):
        super().__init__(f"Too many wrong tries. Wait {seconds} seconds and try again.")
        self.seconds = seconds


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_between(earlier_iso: str, later_iso: str) -> float:
    try:
        return (datetime.fromisoformat(later_iso) - datetime.fromisoformat(earlier_iso)).total_seconds()
    except ValueError:
        return 1e9


def _hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + digest.hex()


def _check_secret(secret: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(secret.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()  # tokens are 192-bit random: no salt or slow hash needed


def normalize_code(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def new_code(groups: int = 2) -> str:
    return "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(groups))


class Throttle:
    """At most `limit` failures per `window` seconds for one key (an address); then it must wait."""

    def __init__(self, limit: int = 5, window: int = 60):
        self._limit, self._window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            recent = [t for t in self._fails.get(key, []) if now - t < self._window]
            self._fails[key] = recent
            if len(recent) >= self._limit:
                raise Locked(max(1, int(self._window - (now - recent[0]))))

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


@dataclass
class Device:
    id: str
    name: str
    created: str
    last_seen: str
    last_ip: str
    blocked: bool = False
    signed_out: bool = False

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created, "last_seen": self.last_seen,
                "last_ip": self.last_ip, "blocked": self.blocked, "signed_out": self.signed_out}


def resolve_mode(mode: str) -> str:
    """"windows" signs in with the PC's own Windows user name and password; "password" is an app password made at setup."""
    if mode == "auto":
        return "windows" if winauth.available() else "password"
    if mode == "windows" and not winauth.available():
        return "password"  # Windows sign-in can't work elsewhere
    return mode


class AccountStore:
    def __init__(self, source: Database | Path, pairing_minutes: int = 2, max_devices: int = 10, legacy_file: Path | None = None,
                 mode: str = "password", forget_after_days: int = 0):
        self.mode = resolve_mode(mode)
        self._forget_after_days = forget_after_days
        self.windows_throttle = Throttle(3, 300)  # a domain can lock the real Windows account after a few wrong tries: stay well under
        self.on_signed_out = None  # called with the Device when a phone signs itself out (its entry stays)
        self.on_removed = None  # set by whoever wants to know when a phone is removed: called with the Device
        self._db, from_path = open_database(source)
        self._pairing_seconds = pairing_minutes * 60
        self._max_devices = max_devices
        self._lock = threading.RLock()
        self._codes: dict[str, float] = {}  # normalized pairing code -> expiry (monotonic); lives only in memory
        self.pair_throttle = Throttle(5, 60)
        self.login_throttle = Throttle(5, 60)
        legacy = legacy_file or from_path
        if legacy is not None:
            self._import(legacy)

    # ------------------------------------------------------------------ the old file
    def _import(self, file: Path) -> None:
        """accounts.json from before the database: the owner and the paired phones move over once."""
        if not file.is_file():
            return
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        owner = data.get("owner")
        with self._db.transaction():
            if isinstance(owner, dict) and not self._db.one("SELECT 1 FROM owner"):
                self._db._conn.execute(
                    "INSERT INTO owner (id, name, password, recovery, created) VALUES (1, ?, ?, ?, ?)",
                    (owner.get("name", ""), owner.get("password", ""), owner.get("recovery", ""), owner.get("created", _now())))
            for d in data.get("devices", []):
                if isinstance(d, dict) and d.get("id") and d.get("token_hash"):
                    self._db._conn.execute(
                        "INSERT OR IGNORE INTO devices (id, name, token_hash, created, last_seen, last_ip, blocked) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (d["id"], d.get("name", "Phone"), d["token_hash"], d.get("created", _now()), d.get("last_seen", _now()),
                         d.get("last_ip", ""), int(bool(d.get("blocked")))))
        retire(file)

    # ------------------------------------------------------------------ signing in with Windows
    def windows_user(self) -> str:
        return winauth.label(*winauth.current_user())

    @staticmethod
    def _is_this_user(typed: str) -> bool:
        """Only the Windows user who runs this app is the owner. Typing just the name, DOMAIN-slash-name or name@domain all mean them."""
        domain, user = winauth.current_user()
        typed = typed.strip().lower()
        if "\\" in typed:
            d, u = typed.split("\\", 1)
            return u == user.lower() and d in (domain.lower(), ".")
        if "@" in typed:
            return typed.split("@", 1)[0] == user.lower()
        return typed == user.lower()

    def windows_sign_in(self, typed_user: str, password: str) -> bool:
        """The first successful sign-in makes that Windows user the owner; after that it is the same check every time."""
        with self._lock:
            self.windows_throttle.check("windows")
            domain, user = winauth.current_user()
            if not (self._is_this_user(typed_user) and winauth.check(domain, user, password)):
                self.windows_throttle.fail("windows")
                return False
            self.windows_throttle.clear("windows")
            if self._owner() is None:
                self._db.run("INSERT INTO owner (id, name, password, recovery, created) VALUES (1, ?, '', '', ?)", (user, _now()))
            return True

    # ------------------------------------------------------------------ the owner
    def _owner(self):
        return self._db.one("SELECT * FROM owner WHERE id=1")

    @property
    def owner_exists(self) -> bool:
        return self._owner() is not None

    @property
    def owner_name(self) -> str:
        row = self._owner()
        return row["name"] if row else ""

    def _check_new_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"Use at least {MIN_PASSWORD} characters for the password.")

    def setup_owner(self, name: str, password: str) -> str:
        """First-run setup. Returns the recovery code, shown once."""
        if self.mode == "windows":
            raise AccountError("This PC uses your Windows user name and password. Just sign in.")
        with self._lock:
            if self._owner() is not None:
                raise AccountError("This PC is already set up.")
            name = name.strip()
            if not name:
                raise AccountError("Enter your name.")
            self._check_new_password(password)
            recovery = new_code(4)
            self._db.run("INSERT INTO owner (id, name, password, recovery, created) VALUES (1, ?, ?, ?, ?)",
                         (name[:60], _hash_secret(password), _hash_secret(normalize_code(recovery)), _now()))
            return recovery

    def verify_owner(self, password: str, who: str = "") -> bool:
        self.login_throttle.check(who)
        owner = self._owner()
        ok = owner is not None and _check_secret(password, owner["password"])
        (self.login_throttle.clear if ok else self.login_throttle.fail)(who)
        return ok

    def change_password(self, old: str, new: str) -> None:
        if self.mode == "windows":
            raise AccountError("This PC page uses your Windows password. Change it in Windows.")
        with self._lock:
            if not self.verify_owner(old, "change"):
                raise AccountError("The current password is not right.")
            self._check_new_password(new)
            self._db.run("UPDATE owner SET password=? WHERE id=1", (_hash_secret(new),))

    def reset_password(self, recovery_code: str, new: str, who: str = "") -> str:
        """Forgot the password: the recovery code sets a new one and issues a fresh recovery code."""
        if self.mode == "windows":
            raise AccountError("This PC page uses your Windows password, so there is no recovery code. Reset it in Windows.")
        with self._lock:
            self.login_throttle.check(who)
            owner = self._owner()
            if owner is None or not _check_secret(normalize_code(recovery_code), owner["recovery"]):
                self.login_throttle.fail(who)
                raise AccountError("That recovery code is not right.")
            self.login_throttle.clear(who)
            self._check_new_password(new)
            recovery = new_code(4)
            self._db.run("UPDATE owner SET password=?, recovery=? WHERE id=1", (_hash_secret(new), _hash_secret(normalize_code(recovery))))
            return recovery

    def reset_everything(self) -> int:
        """Lost both the password and the recovery code: forget the owner and every paired phone, so setup starts again.

        Only reachable from the command line on the PC itself (`python -m handoff --reset-owner`). Returns how many phones were removed.
        """
        with self._lock, self._db.transaction():
            phones = self._db.one("SELECT COUNT(*) AS n FROM devices")["n"]
            self._db._conn.execute("DELETE FROM devices")
            self._db._conn.execute("DELETE FROM owner")
        return phones

    # ------------------------------------------------------------------ pairing
    def new_pairing_code(self) -> tuple[str, int]:
        with self._lock:
            now = time.monotonic()
            self._codes = {c: e for c, e in self._codes.items() if e > now}
            if len(self._codes) >= 5:  # a handful at a time is plenty; older ones expire on their own
                self._codes.pop(min(self._codes, key=self._codes.get))
            code = new_code(2)
            self._codes[normalize_code(code)] = now + self._pairing_seconds
            return code, self._pairing_seconds

    def pair(self, code: str, device_name: str, ip: str = "", previous: str = "", install: str = "") -> tuple[Device, str]:
        """Trade a one-time code for this phone's own token. The code works once.

        A phone that pairs again and still holds its old token (`previous`) takes over its own entry instead of adding
        another, so it keeps its name, its notifications and its place in the list. A phone that signed out (so its token is
        gone) is still recognised by `install`, an id its app keeps for itself: it gets its own entry back, not a new one.
        Another phone has another install id, so it is always a new entry."""
        with self._lock:
            self.pair_throttle.check(ip)
            key, now = normalize_code(code), time.monotonic()
            expiry = self._codes.pop(key, 0.0)
            if expiry <= now:
                self.pair_throttle.fail(ip)
                raise AccountError("That code is wrong or has run out. Ask the PC for a new one.")
            if not self._owner():
                raise AccountError("Finish setting up on the PC first.")
            self.forget_stale()
            again = self._row_for_token(previous)
            if again is not None and again["blocked"]:
                again = None  # a blocked phone is not let back in by pairing: it is a new entry the owner can see
            ihash = _token_hash("install:" + install) if install else ""
            if again is None and ihash:
                again = self._db.one("SELECT * FROM devices WHERE install_hash=? AND blocked=0", (ihash,))
            if again is None and self._db.one("SELECT COUNT(*) AS n FROM devices")["n"] >= self._max_devices:
                self._codes[key] = expiry  # not the code's fault; keep it usable
                raise DeviceLimit(f"Device limit reached. This PC already has {self._max_devices} devices connected. Remove one in the PC's window (Phones), then try again.")
            self.pair_throttle.clear(ip)
            token = secrets.token_urlsafe(24)
            if again is not None:
                self._db.run("UPDATE devices SET token_hash=?, last_seen=?, last_ip=?, install_hash=CASE WHEN ?<>'' THEN ? ELSE install_hash END WHERE id=?",
                             (_token_hash(token), _now(), ip, ihash, ihash, again["id"]))
                return self.get(again["id"]), token
            device = Device(id=secrets.token_hex(4), name=self._unique_name(device_name), created=_now(), last_seen=_now(), last_ip=ip)
            self._db.run(
                "INSERT INTO devices (id, name, token_hash, created, last_seen, last_ip, blocked, install_hash) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (device.id, device.name, _token_hash(token), device.created, device.last_seen, device.last_ip, ihash))
            return device, token

    def _row_for_token(self, token: str):
        return self._db.one("SELECT * FROM devices WHERE token_hash=?", (_token_hash(token),)) if token else None

    def _unique_name(self, wanted: str) -> str:
        """Two entries called "Phone" can't be told apart, so the second is "Phone (2)", the third "Phone (3)"."""
        base = (wanted.strip() or "Phone")[:34]
        taken = {r["name"] for r in self._db.rows("SELECT name FROM devices")}
        name, n = base, 1
        while name in taken:
            n += 1
            name = f"{base} ({n})"
        return name

    def forget_stale(self) -> list[str]:
        """Remove phones not seen for `forget_after_days` (off when 0): the entries left behind by cleared browsers and reinstalls."""
        if self._forget_after_days <= 0:
            return []
        cutoff = self._forget_after_days * 86400
        gone = []
        with self._lock:
            for row in self._db.rows("SELECT id, last_seen FROM devices"):
                if _seconds_between(row["last_seen"], _now()) > cutoff:
                    self.remove(row["id"])
                    gone.append(row["id"])
        return gone

    # ------------------------------------------------------------------ devices
    def authenticate(self, token: str, ip: str = "") -> Device | None:
        if not token:
            return None
        row = self._db.one("SELECT * FROM devices WHERE token_hash=?", (_token_hash(token),))
        if row is None or row["blocked"]:
            return None
        seen = _now()
        if _seconds_between(row["last_seen"], seen) > 30 or (ip and ip != row["last_ip"]):  # "last seen" need not be exact
            self._db.run("UPDATE devices SET last_seen=?, last_ip=? WHERE id=?", (seen, ip or row["last_ip"], row["id"]))
            row = self._db.one("SELECT * FROM devices WHERE id=?", (row["id"],))
        return self._device(row)

    @staticmethod
    def _device(row) -> Device:
        return Device(id=row["id"], name=row["name"], created=row["created"], last_seen=row["last_seen"],
                      last_ip=row["last_ip"], blocked=bool(row["blocked"]), signed_out=row["token_hash"].startswith("out:"))

    def devices(self) -> list[dict]:
        self.forget_stale()
        return [self._device(r).public() for r in self._db.rows("SELECT * FROM devices ORDER BY last_seen DESC")]

    def get(self, device_id: str) -> Device | None:
        row = self._db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        return self._device(row) if row else None

    def rename(self, device_id: str, name: str) -> Device:
        with self._lock:
            self._need(device_id)
            name = name.strip()
            if not name:
                raise AccountError("Give the phone a name.")
            self._db.run("UPDATE devices SET name=? WHERE id=?", (name[:40], device_id))
            return self.get(device_id)

    def set_blocked(self, device_id: str, blocked: bool) -> Device:
        with self._lock:
            self._need(device_id)
            self._db.run("UPDATE devices SET blocked=? WHERE id=?", (int(blocked), device_id))
            return self.get(device_id)

    def remove(self, device_id: str) -> Device:
        with self._lock:
            gone = self._need(device_id)
            self._db.run("DELETE FROM devices WHERE id=?", (device_id,))
            self._db.run("DELETE FROM push_subs WHERE device_id=?", (device_id,))  # a removed phone is never notified
            if self.on_removed is not None:
                self.on_removed(gone)  # whoever tracks this phone (its access level, its session) lets go of it too
            return gone

    def sign_out(self, device_id: str) -> Device:
        """A phone signs itself out: its token stops working for good, but its entry stays, so the same phone
        connecting again is the same person, with the same name and level, not a new one."""
        with self._lock:
            gone = self._need(device_id)
            self._db.run("UPDATE devices SET token_hash=? WHERE id=?", ("out:" + secrets.token_hex(16), device_id))
            self._db.run("DELETE FROM push_subs WHERE device_id=?", (device_id,))
            if self.on_signed_out is not None:
                self.on_signed_out(gone)
            return gone

    def _need(self, device_id: str) -> Device:
        device = self.get(device_id)
        if device is None:
            raise AccountError("That phone is not paired any more.")
        return device
