"""Push notifications: "Plan ready, tap to approve", "Job finished", on a phone that is locked or has the page closed.

Two channels, because phones need two:
  - Browser push (Web Push with VAPID keys). Works for the page added to the home screen, on Android and on iPhone (iOS 16.4+).
  - Firebase push (FCM). Works for the Android app.

Both are written here with the `cryptography` and `httpx` packages the app already uses, so nothing new is installed.
A push passes through Google or Apple on its way to the phone. Browser push is end-to-end encrypted; Firebase push is not,
so by default no notification says what the request was (see `push.show_details` in config.yaml).
"""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import PushConfig
from .db import Database

log = logging.getLogger("handoff.push")

OK, GONE, RETRY, FAILED = "ok", "gone", "retry", "failed"  # what happened to one notification
MAX_PER_DEVICE = 5  # browsers and apps one phone may have registered
_TOKEN = re.compile(r"^[\w:.\-]{40,4096}$")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Message:
    title: str
    body: str
    job: str = ""
    kind: str = ""
    ttl: int = 3600  # seconds a push service may hold it for a phone that is offline. "PC is going to sleep" is worthless a few minutes later

    def payload(self) -> dict:
        return {"title": self.title, "body": self.body, "job": self.job, "kind": self.kind, "tag": self.job or self.kind}


@dataclass
class Credentials:
    vapid_public: str = ""
    vapid_private: str = ""
    vapid_subject: str = ""
    firebase: dict | None = None


# -- finding the keys (read, never copied or printed) ------------------------------

def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        log.warning("push: cannot read %s: %s", path, exc.strerror or exc)
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _read_service_account(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("push: the Firebase service-account file %s cannot be used: %s", path, exc)
        return None
    if not all(isinstance(data.get(k), str) and data.get(k) for k in ("client_email", "private_key", "project_id")):
        log.warning("push: %s is not a Firebase service-account file", path)
        return None
    return data


def load_credentials(cfg: PushConfig) -> Credentials:
    """The keys, from config.yaml / environment variables first, then from the optional .env file `push.credentials_file`."""
    env: dict[str, str] = _read_env_file(cfg.credentials_file) if cfg.credentials_file else {}
    account = cfg.firebase_service_account_file
    if account is None and env.get("FIREBASE_SERVICE_ACCOUNT_PATH") and cfg.credentials_file:
        account = (cfg.credentials_file.parent / env["FIREBASE_SERVICE_ACCOUNT_PATH"]).resolve()
    subject = cfg.vapid_subject or env.get("VAPID_SUBJECT", "")
    if subject and not subject.startswith(("mailto:", "https:")):
        subject = "mailto:" + subject
    return Credentials(
        vapid_public=cfg.vapid_public_key or env.get("VAPID_PUBLIC_KEY", ""),
        vapid_private=cfg.vapid_private_key or env.get("VAPID_PRIVATE_KEY", ""),
        vapid_subject=subject,
        firebase=_read_service_account(account),
    )


def _ssl_context(ca_file: Path | None) -> ssl.SSLContext:
    context = ssl.create_default_context()  # on Windows this includes what Windows itself trusts, such as a company's own certificate
    if ca_file:
        context.load_verify_locations(cafile=str(ca_file))
    return context


# -- browser push: RFC 8030 (delivery), RFC 8291 (encryption), RFC 8292 (VAPID) ----------

def public_https(url: str) -> bool:
    """A push endpoint must be a public https address: this PC will call it, and a paired phone supplies it."""
    try:
        parts = urlparse(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "." not in host or host == "localhost" or port not in (None, 443):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # a bare IP address is never a push service
    except ValueError:
        return True


def encrypt_payload(payload: bytes, p256dh: bytes, auth: bytes, *, key: ec.EllipticCurvePrivateKey | None = None, salt: bytes | None = None) -> bytes:
    """Web Push message encryption (aes128gcm). Only the phone's browser can read what this returns."""
    if len(payload) > 3900:
        raise ValueError("a push message must be small")
    sender = key or ec.generate_private_key(ec.SECP256R1())
    sender_public = sender.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + p256dh + sender_public).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    sealed = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 marks the last (and only) record
    return salt + (4096).to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public + sealed


class WebPush:
    def __init__(self, creds: Credentials, http: httpx.Client, ttl: int = 3600):
        private = creds.vapid_private.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", private):  # decoding is forgiving: without this a typo would quietly become a different key
            raise ValueError("it should be 43 characters (letters, digits, - and _)")
        self._key = ec.derive_private_key(int.from_bytes(unb64u(private), "big"), ec.SECP256R1())
        self.public_key = b64u(self._key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        if creds.vapid_public and creds.vapid_public != self.public_key:
            log.warning("push: VAPID_PUBLIC_KEY does not belong to the private key. Using the one that does.")
        self._subject = creds.vapid_subject or "mailto:admin@localhost.invalid"
        self._http = http
        self._ttl = ttl

    def _authorization(self, endpoint: str) -> str:
        parts = urlparse(endpoint)
        claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": self._subject}
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64u(json.dumps(claims).encode())
        r, s = decode_dss_signature(self._key.sign(head.encode(), ec.ECDSA(hashes.SHA256())))
        return f"vapid t={head}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={self.public_key}"

    def send(self, endpoint: str, keys: dict, message: Message) -> tuple[str, str]:
        if not public_https(endpoint):
            return GONE, "not a public https address"
        body = encrypt_payload(json.dumps(message.payload(), ensure_ascii=False).encode(), unb64u(keys["p256dh"]), unb64u(keys["auth"]))
        headers = {"Authorization": self._authorization(endpoint), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                   "TTL": str(message.ttl or self._ttl), "Urgency": "high"}
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", message.job):
            headers["Topic"] = message.job  # a newer message about the same request replaces the older one
        try:
            res = self._http.post(endpoint, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return RETRY, type(exc).__name__
        if res.status_code in (200, 201, 202):
            return OK, ""
        if res.status_code in (404, 410):
            return GONE, "the phone's browser no longer accepts push"
        if res.status_code == 429 or res.status_code >= 500:
            return RETRY, f"push service said {res.status_code}"
        return FAILED, f"push service refused it ({res.status_code}). 403 usually means the VAPID keys changed since the phone subscribed"


# -- Android app push: Firebase Cloud Messaging, HTTP v1 -----------------------------

class Fcm:
    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

    def __init__(self, account: dict, http: httpx.Client):
        self._account = account
        self._http = http
        self._key = serialization.load_pem_private_key(account["private_key"].encode(), None)
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()
        self.project = account["project_id"]

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            uri = self._account.get("token_uri") or "https://oauth2.googleapis.com/token"
            issued = int(time.time())
            head = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + "." + b64u(json.dumps(
                {"iss": self._account["client_email"], "scope": self.SCOPE, "aud": uri, "iat": issued, "exp": issued + 3600}).encode())
            signature = self._key.sign(head.encode(), padding.PKCS1v15(), hashes.SHA256())
            res = self._http.post(uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{head}.{b64u(signature)}"})
            res.raise_for_status()
            data = res.json()
            self._token, self._expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
            return self._token

    def send(self, token: str, message: Message) -> tuple[str, str]:
        try:
            auth = self._access_token()
            res = self._http.post(
                f"https://fcm.googleapis.com/v1/projects/{self.project}/messages:send",
                headers={"Authorization": f"Bearer {auth}"},
                json={"message": {"token": token,
                                  "data": {k: str(v) for k, v in message.payload().items()},  # data only: the app builds the notification, so it can stay quiet while open
                                  "android": {"priority": "high", "ttl": f"{message.ttl}s"}}})
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return RETRY, type(exc).__name__
        if res.status_code == 200:
            return OK, ""
        if res.status_code == 404:
            return GONE, "the app was removed or its token changed"
        if res.status_code == 429 or res.status_code >= 500:
            return RETRY, f"Firebase said {res.status_code}"
        try:
            reason = res.json().get("error", {}).get("status", "")
        except ValueError:
            reason = ""
        return FAILED, f"Firebase refused it ({res.status_code} {reason}). Is this app registered in the Firebase project {self.project}?"


# -- who gets what, and when ---------------------------------------------------------

@dataclass
class Report:
    sent: int = 0
    removed: int = 0
    failed: int = 0
    skipped: int = 0  # phones that chose not to hear about this (a type switched off, or quiet hours)
    errors: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {"sent": self.sent, "removed": self.removed, "failed": self.failed, "skipped": self.skipped, "errors": self.errors[:3]}


# -- what each phone chose to hear about -----------------------------------------------------------

PREF_TYPES = ("approval", "done", "failed", "pc", "security")  # a plan or access waiting · a job finished · a job failed · the PC going away or low on battery · who can reach this PC
_TYPE_OF = {"awaiting": "approval", "needs_access": "approval", "done": "done", "failed": "failed", "security": "security"}
_ALWAYS = {"test"}  # a test the person asked for is never held back
_NEVER_QUIET = {"security"}  # worth waking someone for


def default_prefs() -> dict:
    return {"types": {t: True for t in PREF_TYPES}, "quiet": {"on": False, "start": "22:00", "end": "07:00", "tz": 0}}


def _minutes(text: str) -> int | None:
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def clean_prefs(raw: dict | None) -> dict:
    """What a phone sent, made safe: known types only, real times, a believable time zone. Anything odd falls back to the default."""
    out = default_prefs()
    raw = raw if isinstance(raw, dict) else {}
    types = raw.get("types") if isinstance(raw.get("types"), dict) else {}
    for t in PREF_TYPES:
        if isinstance(types.get(t), bool):
            out["types"][t] = types[t]
    quiet = raw.get("quiet") if isinstance(raw.get("quiet"), dict) else {}
    if isinstance(quiet.get("on"), bool):
        out["quiet"]["on"] = quiet["on"]
    for key in ("start", "end"):
        if _minutes(quiet.get(key)) is not None:
            out["quiet"][key] = quiet[key]
    tz = quiet.get("tz")
    if isinstance(tz, int) and not isinstance(tz, bool) and -840 <= tz <= 840:
        out["quiet"]["tz"] = tz
    return out


def allows(prefs: dict, kind: str, now_utc: datetime) -> bool:
    """Should a notification of this kind reach a phone with these choices, right now? (`now_utc` is the real time; quiet hours use the phone's own clock.)"""
    if kind in _ALWAYS:
        return True
    group = "pc" if kind.startswith("pc_") else _TYPE_OF.get(kind)
    if group is not None and not prefs["types"].get(group, True):
        return False
    quiet = prefs["quiet"]
    if quiet["on"] and kind not in _NEVER_QUIET:
        local = now_utc + timedelta(minutes=quiet["tz"])
        now_min, start, end = local.hour * 60 + local.minute, _minutes(quiet["start"]), _minutes(quiet["end"])
        if start is not None and end is not None and start != end:
            inside = start <= now_min < end if start < end else now_min >= start or now_min < end
            if inside:
                return False
    return True


class Notifier:
    def __init__(self, cfg: PushConfig, db: Database, creds: Credentials | None = None, http: httpx.Client | None = None):
        self.cfg = cfg
        self.db = db
        self.web: WebPush | None = None
        self.fcm: Fcm | None = None
        self.problems: list[str] = []
        self._announced: set[tuple[str, str]] = set()
        self._threads: list[threading.Thread] = []
        self._pc_last: dict[str, float] = {}
        self._clock = time.monotonic  # replaced in tests
        if not cfg.enabled:
            return
        creds = creds if creds is not None else load_credentials(cfg)
        client = http or httpx.Client(verify=_ssl_context(cfg.ca_file), timeout=10)
        if creds.vapid_private:
            try:
                self.web = WebPush(creds, client)
            except (ValueError, TypeError) as exc:
                self.problems.append(f"The browser-push (VAPID) key cannot be used: {exc}")
        if creds.firebase:
            try:
                self.fcm = Fcm(creds.firebase, client)
            except (ValueError, TypeError) as exc:
                self.problems.append(f"The Firebase key cannot be used: {exc}")
        if self.web is None and self.fcm is None:
            self.problems.append("Push is on, but no keys were found. See push: in config.yaml.")
        for problem in self.problems:
            log.warning("push: %s", problem)

    @property
    def enabled(self) -> bool:
        return self.web is not None or self.fcm is not None

    def why_off(self) -> str:
        """In plain words, why this PC cannot send notifications ("" when it can). The phone shows it."""
        if self.enabled:
            return ""
        if not self.cfg.enabled:
            return "push: enabled is off in the config.yaml this app loaded"
        return "; ".join(self.problems) or "no push keys were found"

    def describe(self) -> str:
        if not self.cfg.enabled:
            return "off"
        channels = [name for name, on in (("browser", self.web), ("Android app", self.fcm)) if on]
        return "on: " + " + ".join(channels) if channels else "on, but no keys found"

    # what each phone chose to hear about

    def prefs(self, device_id: str) -> dict:
        row = self.db.one("SELECT data FROM push_prefs WHERE device_id=?", (device_id,))
        try:
            return clean_prefs(json.loads(row["data"])) if row else default_prefs()
        except ValueError:
            return default_prefs()

    def set_prefs(self, device_id: str, raw: dict) -> dict:
        data = clean_prefs(raw)
        self.db.run("INSERT INTO push_prefs (device_id, data) VALUES (?, ?) ON CONFLICT(device_id) DO UPDATE SET data=excluded.data", (device_id, json.dumps(data)))
        return data

    # subscriptions

    def subscribe_web(self, device_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        if self.web is None:
            raise ValueError("browser push is not set up on this PC")
        try:
            ok = len(unb64u(p256dh)) == 65 and unb64u(p256dh)[0] == 4 and len(unb64u(auth)) >= 16
        except ValueError:
            ok = False
        if not public_https(endpoint) or not ok or len(endpoint) > 1000:
            raise ValueError("that browser subscription is not valid")
        self._store(device_id, "web", endpoint, json.dumps({"p256dh": p256dh, "auth": auth}))

    def subscribe_fcm(self, device_id: str, token: str) -> None:
        if self.fcm is None:
            raise ValueError("app push is not set up on this PC")
        if not _TOKEN.match(token):
            raise ValueError("that app token is not valid")
        self._store(device_id, "fcm", token, "")

    def _store(self, device_id: str, kind: str, target: str, keys: str) -> None:
        with self.db.transaction() as db:
            db.run("INSERT INTO push_subs (target, device_id, kind, keys, created) VALUES (?, ?, ?, ?, ?) "
                   "ON CONFLICT(target) DO UPDATE SET device_id=excluded.device_id, kind=excluded.kind, keys=excluded.keys",
                   (target, device_id, kind, keys, _now()))
            db.run("DELETE FROM push_subs WHERE device_id=? AND target NOT IN "
                   "(SELECT target FROM push_subs WHERE device_id=? ORDER BY created DESC LIMIT ?)", (device_id, device_id, MAX_PER_DEVICE))

    def unsubscribe(self, device_id: str, kind: str | None = None) -> int:
        if kind:
            return self.db.run("DELETE FROM push_subs WHERE device_id=? AND kind=?", (device_id, kind))
        return self.db.run("DELETE FROM push_subs WHERE device_id=?", (device_id,))

    def kinds(self, device_id: str) -> list[str]:
        return sorted({r["kind"] for r in self.db.rows("SELECT kind FROM push_subs WHERE device_id=?", (device_id,))})

    # sending

    def send(self, message: Message, device_id: str | None = None) -> Report:
        """To every phone that is allowed in (not blocked), or to one phone. Dead subscriptions are forgotten."""
        report = Report()
        if not self.enabled:
            return report
        sql = "SELECT s.target, s.kind, s.keys, s.device_id FROM push_subs s JOIN devices d ON d.id = s.device_id WHERE d.blocked = 0"
        rows = self.db.rows(sql + (" AND s.device_id = ?" if device_id else ""), (device_id,) if device_id else ())
        now, chosen = datetime.now(timezone.utc), {}
        for row in rows:
            who = row["device_id"]
            if who not in chosen:
                chosen[who] = self.prefs(who)
            if not allows(chosen[who], message.kind, now):  # this phone asked not to hear about that (or not at this hour)
                report.skipped += 1
                continue
            try:
                if row["kind"] == "web" and self.web:
                    outcome, why = self.web.send(row["target"], json.loads(row["keys"]), message)
                elif row["kind"] == "fcm" and self.fcm:
                    outcome, why = self.fcm.send(row["target"], message)
                else:
                    continue
            except Exception as exc:  # one bad subscription must not stop the others
                outcome, why = FAILED, type(exc).__name__
            if outcome == OK:
                report.sent += 1
                continue
            if outcome == GONE:
                self.db.run("DELETE FROM push_subs WHERE target=?", (row["target"],))
                report.removed += 1
            else:
                report.failed += 1
            if why:
                report.errors.append(why)
                log.warning("push: %s", why)
        return report

    def wait(self, timeout: float = 5) -> None:
        for thread in list(self._threads):
            thread.join(timeout)

    # the moments worth a notification

    def compose(self, job, event: str) -> Message:
        details = self.cfg.show_details

        def clip(text: str, n: int = 140) -> str:
            text = " ".join((text or "").split())
            return text if len(text) <= n else text[: n - 1] + "…"

        if event == "awaiting":
            return Message("Plan ready – tap to approve", clip(job.summary) if details and job.summary else "Nothing changes until you approve.", job.id, event)
        if event == "needs_access":
            return Message("Folder access needed", clip(", ".join(job.access)) if details and job.access else "Tap to allow or decline.", job.id, event)
        if event == "failed":
            return Message("Job failed", clip(job.error or "") if details and job.error else "Tap to see what went wrong.", job.id, event)
        shown = (job.outputs[0] if job.outputs else "") or (f"{len(job.changed)} file(s) changed" if job.changed else "")
        return Message("Job finished", clip(shown) if details and shown else "Tap to see the result.", job.id, "done")

    def on_job_event(self, job, event: str, seconds: float) -> None:
        """Called by the job manager when a request needs the person, or ends. `seconds` is how long the PC has been working on it."""
        if not self.enabled or event not in self.cfg.notify_on or (job.id, event) in self._announced:
            return
        if event == "done" and seconds < self.cfg.min_seconds:
            return
        self._announced.add((job.id, event))
        message = self.compose(job, event)

        def work() -> None:
            try:
                self.send(message)
            except Exception:  # a notification is never worth breaking a request over
                log.exception("push failed")

        thread = threading.Thread(target=work, name="push", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    # -- the PC itself: tell the phone BEFORE it goes away (afterwards it has no network to tell it with) ------------------

    PC_TEXT = {
        "pc_sleep": ("Your PC is going to sleep", "This phone reconnects when it wakes up."),
        "pc_restart": ("Your PC is restarting", "The app reconnects by itself when it is back."),
        "pc_shutdown": ("Your PC is shutting down", "If it is only restarting, the app reconnects by itself."),
        "pc_closing": ("The app is closing on your PC", "Open it again on the PC to reconnect."),
        "pc_battery": ("Your PC's battery is low", "Plug it in, or it may switch off soon."),
        "pc_plugged": ("Your PC is charging", "The charger is plugged in."),
        "pc_unplugged": ("Your PC is on battery", "The charger was unplugged."),
    }
    PC_COOLDOWN = {"pc_battery": 0.0, "pc_plugged": 5.0, "pc_unplugged": 5.0}  # seconds before the same notice may go out again (the others: 30). Battery repeats are prevented by BatteryAlert itself
    PC_TTL = {"pc_battery": 1800}        # seconds a push service may keep it for a phone that is offline (the others: 2 minutes)

    def notify_pc(self, kind: str, percent: int | None = None, wait: bool = False, timeout: float = 2.0) -> bool:
        """Tell every phone this PC is about to go away (or its battery is low). `wait`: hold the caller up to `timeout` seconds so the
        message has left before the PC does. Returns whether a notice was sent."""
        if not self.enabled or kind not in self.PC_TEXT or kind not in self.cfg.notify_on:
            return False
        now = self._clock()
        if now - self._pc_last.get(kind, -1e9) < self.PC_COOLDOWN.get(kind, 30.0):
            return False
        # Shutting down closes the app a moment later: one notice about it is enough.
        if kind == "pc_closing" and now - max((t for k, t in self._pc_last.items() if k in ("pc_sleep", "pc_restart", "pc_shutdown")), default=-1e9) < 30.0:
            return False
        self._pc_last[kind] = now
        title, body = self.PC_TEXT[kind]
        if kind == "pc_battery" and percent is not None:
            title = f"Your PC's battery is low ({percent}%)"
        elif kind in ("pc_plugged", "pc_unplugged") and percent is not None:
            title = f"{title} ({percent}%)"
        message = Message(title, body, "", kind, ttl=self.PC_TTL.get(kind, 120))

        def work() -> None:
            try:
                self.send(message)
            except Exception:
                log.exception("push failed")

        thread = threading.Thread(target=work, name="push-pc", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()
        if wait:
            thread.join(timeout)
        return True
