"""Push notifications: the encryption and signatures are checked the way a push service and a phone would check them."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient

from handoff.config import PushConfig, load_settings
from handoff.db import Database
from handoff.accounts import AccountStore
from handoff.push import (FAILED, GONE, OK, RETRY, Credentials, Fcm, Message, Notifier, WebPush, b64u, encrypt_payload, load_credentials,
                          public_https, unb64u)
from handoff.server import create_app

UNCOMPRESSED = serialization.PublicFormat.UncompressedPoint
X962 = serialization.Encoding.X962
ENDPOINT = "https://push.example.com/send/abc123"


def raw_public(key) -> bytes:
    return key.public_key().public_bytes(X962, UNCOMPRESSED)


class Phone:
    """A browser's end of a push subscription: it can read what was encrypted for it, and nothing else can."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.p256dh = raw_public(self.key)

    @property
    def subscription(self) -> dict:
        return {"p256dh": b64u(self.p256dh), "auth": b64u(self.auth)}

    def read(self, body: bytes) -> dict:
        """RFC 8291, from the receiving side."""
        salt, size, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        sender_public, sealed = body[21:21 + idlen], body[21 + idlen:]
        assert size == 4096 and idlen == 65
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public))
        ikm = HKDF(hashes.SHA256(), 32, self.auth, b"WebPush: info\x00" + self.p256dh + sender_public).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, sealed, None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


def vapid_pair():
    key = ec.generate_private_key(ec.SECP256R1())
    private = b64u(key.private_numbers().private_value.to_bytes(32, "big"))
    return private, b64u(raw_public(key))


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def make_notifier(tmp_path, handler, *, web=True, fcm=None, **cfg):
    private, public = vapid_pair()
    creds = Credentials(vapid_public=public, vapid_private=private if web else "", vapid_subject="mailto:me@example.com", firebase=fcm)
    db = Database(tmp_path / "app.db")
    accounts = AccountStore(db, pairing_minutes=2, max_devices=5, mode="password")
    accounts.setup_owner("Owner", "correct horse")
    device, _ = accounts.pair(accounts.new_pairing_code()[0], "Phone", "")
    notifier = Notifier(PushConfig(enabled=True, **cfg), db, creds, client_for(handler))
    return notifier, accounts, device


# -- encryption and signatures ---------------------------------------------------------

def test_a_browser_push_can_be_read_by_the_phone_it_was_made_for_and_not_by_another():
    phone, other = Phone(), Phone()
    body = encrypt_payload(json.dumps({"title": "Plan ready – tap to approve"}, ensure_ascii=False).encode(), phone.p256dh, phone.auth)
    assert phone.read(body) == {"title": "Plan ready – tap to approve"}
    with pytest.raises(Exception):
        other.read(body)


def test_the_vapid_signature_checks_out_against_the_public_key():
    private, public = vapid_pair()
    web = WebPush(Credentials(vapid_public=public, vapid_private=private, vapid_subject="mailto:me@example.com"), client_for(lambda r: httpx.Response(201)))
    header = web._authorization(ENDPOINT)
    assert header.startswith("vapid t=") and header.endswith(f", k={public}")
    token = header[len("vapid t="):].split(", k=")[0]
    head, claims, signature = token.split(".")
    raw = unb64u(signature)
    assert len(raw) == 64
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(public)).verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    data = json.loads(unb64u(claims))
    assert data["aud"] == "https://push.example.com" and data["sub"] == "mailto:me@example.com" and data["exp"] > 0
    assert json.loads(unb64u(head)) == {"typ": "JWT", "alg": "ES256"}


def test_a_wrong_public_key_in_the_settings_never_reaches_the_push_service():
    private, _ = vapid_pair()
    _, someone_elses = vapid_pair()
    web = WebPush(Credentials(vapid_public=someone_elses, vapid_private=private), client_for(lambda r: httpx.Response(201)))
    assert web.public_key != someone_elses  # the key that matches the private half is the one used, and the one the phone is given


def test_only_public_https_addresses_are_called():
    assert public_https(ENDPOINT) and public_https("https://fcm.googleapis.com/fcm/send/xyz")
    for bad in ("http://push.example.com/x", "https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://intranet/x",
                "https://push.example.com:8443/x", "ftp://push.example.com/x", "not a url"):
        assert not public_https(bad), bad


# -- sending ------------------------------------------------------------------------

def test_a_push_goes_to_the_phone_and_arrives_readable(tmp_path):
    phone, seen = Phone(), []

    def push_service(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    notifier, _, device = make_notifier(tmp_path, push_service)
    notifier.subscribe_web(device.id, ENDPOINT, **{"p256dh": phone.subscription["p256dh"], "auth": phone.subscription["auth"]})
    report = notifier.send(Message("Plan ready – tap to approve", "Nothing changes until you approve.", "ab12cd34", "awaiting"))
    assert report.sent == 1 and report.failed == 0
    request = seen[0]
    assert str(request.url) == ENDPOINT
    assert request.headers["content-encoding"] == "aes128gcm" and request.headers["urgency"] == "high"
    assert request.headers["topic"] == "ab12cd34"  # a newer message about the same request replaces this one
    assert request.headers["authorization"].startswith("vapid t=")
    assert phone.read(request.content)["title"] == "Plan ready – tap to approve"
    assert phone.read(request.content)["job"] == "ab12cd34"


def test_a_phone_that_no_longer_accepts_push_is_forgotten_but_a_busy_service_is_retried_later(tmp_path):
    answers = iter([httpx.Response(410), httpx.Response(503), httpx.Response(403)])
    notifier, _, device = make_notifier(tmp_path, lambda r: next(answers))
    for n in range(3):
        phone = Phone()
        notifier.subscribe_web(device.id, f"{ENDPOINT}{n}", phone.subscription["p256dh"], phone.subscription["auth"])
    report = notifier.send(Message("t", "b"))
    assert (report.sent, report.removed, report.failed) == (0, 1, 2)
    assert any("VAPID" in e for e in report.errors)  # a 403 says what to check
    assert len(notifier.db.rows("SELECT 1 FROM push_subs")) == 2  # only the gone one was dropped


def test_a_blocked_or_removed_phone_gets_nothing(tmp_path):
    seen = []
    notifier, accounts, device = make_notifier(tmp_path, lambda r: seen.append(r) or httpx.Response(201))
    phone = Phone()
    notifier.subscribe_web(device.id, ENDPOINT, phone.subscription["p256dh"], phone.subscription["auth"])
    accounts.set_blocked(device.id, True)
    assert notifier.send(Message("t", "b")).sent == 0 and not seen
    accounts.set_blocked(device.id, False)
    assert notifier.send(Message("t", "b")).sent == 1
    accounts.remove(device.id)
    assert notifier.db.rows("SELECT 1 FROM push_subs") == []


def test_bad_subscriptions_are_refused(tmp_path):
    notifier, _, device = make_notifier(tmp_path, lambda r: httpx.Response(201))
    phone = Phone()
    good = phone.subscription
    for endpoint, p256dh, auth in ((" http://push.example.com/x", good["p256dh"], good["auth"]), (ENDPOINT, "AAAA", good["auth"]), (ENDPOINT, good["p256dh"], "AA")):
        with pytest.raises(ValueError):
            notifier.subscribe_web(device.id, endpoint, p256dh, auth)
    with pytest.raises(ValueError):
        notifier.subscribe_fcm(device.id, "short")


def test_one_phone_keeps_only_its_latest_few_addresses(tmp_path):
    notifier, _, device = make_notifier(tmp_path, lambda r: httpx.Response(201))
    for n in range(8):
        phone = Phone()
        notifier.subscribe_web(device.id, f"{ENDPOINT}{n}", phone.subscription["p256dh"], phone.subscription["auth"])
    assert len(notifier.db.rows("SELECT 1 FROM push_subs")) == 5


def firebase_account():
    key = rsa.generate_private_key(65537, 2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return {"project_id": "demo-project", "client_email": "svc@demo-project.iam.gserviceaccount.com", "private_key": pem}, key.public_key()


def test_firebase_push_signs_in_with_the_service_account_and_sends_data_only(tmp_path):
    account, public = firebase_account()
    calls = []

    def google(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            from urllib.parse import unquote
            head, claims, sig = unquote(form["assertion"]).split(".")
            public.verify(unb64u(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            assert json.loads(unb64u(claims))["scope"] == Fcm.SCOPE
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.test"
        return httpx.Response(200, json={"name": "projects/demo-project/messages/1"})

    notifier, _, device = make_notifier(tmp_path, google, web=False, fcm=account)
    notifier.subscribe_fcm(device.id, "f" * 50 + ":APA91bExampleToken_-" + "x" * 40)
    assert notifier.send(Message("Job finished", "Tap to see the result.", "ab12cd34", "done")).sent == 1
    notifier.send(Message("Job finished", "again", "ab12cd34", "done"))
    assert sum(c.url.host == "oauth2.googleapis.com" for c in calls) == 1  # the access token is reused
    sent = json.loads(calls[1].content)["message"]
    assert calls[1].url.path == "/v1/projects/demo-project/messages:send"
    assert "notification" not in sent  # data only: the app draws the notification, so it can stay quiet while it is open
    assert sent["data"]["title"] == "Job finished" and sent["data"]["job"] == "ab12cd34" and sent["android"]["priority"] == "high"


def test_firebase_forgets_a_token_it_no_longer_knows(tmp_path):
    account, _ = firebase_account()
    notifier, _, device = make_notifier(
        tmp_path, lambda r: httpx.Response(200, json={"access_token": "t", "expires_in": 3600}) if r.url.host == "oauth2.googleapis.com" else httpx.Response(404), web=False, fcm=account)
    notifier.subscribe_fcm(device.id, "a" * 80)
    assert notifier.send(Message("t", "b")).removed == 1
    assert notifier.db.rows("SELECT 1 FROM push_subs") == []


# -- the keys: found, never copied ------------------------------------------------------

def test_keys_are_read_from_an_env_file_beside_a_service_account_file(tmp_path):
    private, public = vapid_pair()
    account, _ = firebase_account()
    other = tmp_path / "other-project"
    other.mkdir()
    (other / "sa.json").write_text(json.dumps(account), encoding="utf-8")
    (other / ".env").write_text(f"# comment\nPORT=1\nVAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY=\"{private}\"\nVAPID_SUBJECT=me@example.com\nFIREBASE_SERVICE_ACCOUNT_PATH=./sa.json\n", encoding="utf-8")
    creds = load_credentials(PushConfig(enabled=True, credentials_file=other / ".env"))
    assert (creds.vapid_public, creds.vapid_private) == (public, private)
    assert creds.vapid_subject == "mailto:me@example.com"
    assert creds.firebase["project_id"] == "demo-project"
    own = load_credentials(PushConfig(enabled=True, credentials_file=other / ".env", vapid_private_key="from-the-environment"))
    assert own.vapid_private == "from-the-environment"  # config / environment wins over the file


def test_missing_or_broken_keys_turn_push_off_with_a_reason_and_never_crash(tmp_path):
    db = Database(tmp_path / "app.db")
    assert not Notifier(PushConfig(enabled=False), db).enabled
    nothing = Notifier(PushConfig(enabled=True), db, Credentials())
    assert not nothing.enabled and "no keys" in nothing.problems[0]
    broken = Notifier(PushConfig(enabled=True), db, Credentials(vapid_private="not a key"))
    assert not broken.enabled and broken.problems
    assert broken.send(Message("t", "b")).sent == 0


# -- when a notification is worth sending ---------------------------------------------------

def job(**kw):
    base = dict(id="ab12cd34", summary="Delete 12 files in Reports", access=["C:\\Reports"], error="boom", outputs=["Found 3 files"], changed=["a.txt"])
    return SimpleNamespace(**{**base, **kw})


def test_by_default_a_notification_never_says_what_you_asked(tmp_path):
    notifier, _, _ = make_notifier(tmp_path, lambda r: httpx.Response(201))
    for event, title in (("awaiting", "Plan ready – tap to approve"), ("needs_access", "Folder access needed"), ("done", "Job finished"), ("failed", "Job failed")):
        message = notifier.compose(job(), event)
        assert message.title == title
        for secret in ("Delete 12 files", "Reports", "boom", "Found 3 files"):
            assert secret not in message.body


def test_details_appear_only_when_asked_for(tmp_path):
    notifier, _, _ = make_notifier(tmp_path, lambda r: httpx.Response(201), show_details=True)
    assert "Delete 12 files" in notifier.compose(job(), "awaiting").body
    assert "boom" in notifier.compose(job(), "failed").body
    assert notifier.compose(job(), "awaiting").body.count("…") == 0
    assert len(notifier.compose(job(summary="x " * 400), "awaiting").body) <= 140


def test_events_are_filtered_deduplicated_and_quiet_for_instant_answers(tmp_path):
    seen = []
    notifier, _, device = make_notifier(tmp_path, lambda r: seen.append(r) or httpx.Response(201), min_seconds=15, notify_on=["awaiting", "done"])
    phone = Phone()
    notifier.subscribe_web(device.id, ENDPOINT, phone.subscription["p256dh"], phone.subscription["auth"])

    notifier.on_job_event(job(), "failed", 99)      # not asked for
    notifier.on_job_event(job(id="quick"), "done", 2)  # an instant answer: no buzz
    notifier.on_job_event(job(id="slow"), "done", 40)
    notifier.on_job_event(job(id="slow"), "done", 40)  # the same event twice
    notifier.on_job_event(job(id="plan"), "awaiting", 0)  # a plan waiting for you always matters
    notifier.wait()
    assert sorted(phone.read(r.content)["job"] for r in seen) == ["plan", "slow"]


def test_a_broken_notifier_never_breaks_a_request(tmp_path):
    def explode(request):
        raise httpx.ConnectError("no network")

    notifier, _, device = make_notifier(tmp_path, explode)
    phone = Phone()
    notifier.subscribe_web(device.id, ENDPOINT, phone.subscription["p256dh"], phone.subscription["auth"])
    notifier.on_job_event(job(), "awaiting", 0)
    notifier.wait()
    assert len(notifier.db.rows("SELECT 1 FROM push_subs")) == 1  # a network error is not "gone": it is tried again next time


# -- the phone-facing API and page ---------------------------------------------------------

@pytest.fixture
def phone_client(project, monkeypatch):
    private, public = vapid_pair()
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_PUSH__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_PUSH__VAPID_PRIVATE_KEY", private)
    app = create_app(load_settings(project / "config.yaml"))
    app.state.accounts.setup_owner("Owner", "correct horse")
    c = TestClient(app)
    token = c.post("/pair/claim", json={"code": app.state.accounts.new_pairing_code()[0], "name": "Phone"}).json()["token"]
    c.headers.update({"X-Token": token})
    sent = []
    app.state.notifier.web._http = client_for(lambda r: sent.append(r) or httpx.Response(201))
    return c, app, sent, public


def test_the_phone_learns_what_is_possible_and_subscribes(phone_client):
    c, app, sent, public = phone_client
    info = c.get("/api/push").json()
    assert info == {"enabled": True, "why": "", "web": public, "fcm": False, "subscribed": []}  # the page is given the public key only
    phone = Phone()
    body = {"kind": "web", "endpoint": ENDPOINT, "keys": phone.subscription, "expirationTime": None}
    assert c.post("/api/push/subscribe", json=body).json()["subscribed"] == ["web"]
    assert c.post("/api/push/subscribe", json={"kind": "web", "endpoint": "http://x/y", "keys": phone.subscription}).status_code == 422
    assert c.post("/api/push/subscribe", json={"kind": "fcm", "token": "a" * 80}).status_code == 422  # no Firebase key on this PC

    assert c.post("/api/push/test").json()["sent"] == 1
    assert phone.read(sent[-1].content)["kind"] == "test"
    assert c.post("/api/push/unsubscribe", json={}).json()["subscribed"] == []
    assert c.post("/api/push/test").json()["sent"] == 0


def test_a_request_waiting_for_approval_notifies_the_phone_end_to_end(phone_client):
    c, app, sent, _ = phone_client
    phone = Phone()
    c.post("/api/push/subscribe", json={"kind": "web", "endpoint": ENDPOINT, "keys": phone.subscription})
    job_id = c.post("/api/jobs", json={"prompt": "create hi.txt with hello"}).json()["id"]
    app.state.notifier.wait()
    message = phone.read(sent[-1].content)
    assert message["title"] == "Plan ready – tap to approve" and message["job"] == job_id and message["kind"] == "awaiting"
    assert "hi.txt" not in message["body"]


def test_push_is_off_unless_it_is_set_up_and_says_so(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    app = create_app(load_settings(project / "config.yaml"))
    app.state.accounts.setup_owner("Owner", "correct horse")
    c = TestClient(app)
    token = c.post("/pair/claim", json={"code": app.state.accounts.new_pairing_code()[0], "name": "Phone"}).json()["token"]
    c.headers.update({"X-Token": token})
    assert c.get("/api/push").json() == {"enabled": False, "why": "push: enabled is off in the config.yaml this app loaded", "web": None, "fcm": False, "subscribed": []}
    assert c.post("/api/push/test").status_code == 404
    assert TestClient(app).get("/api/push").status_code == 401  # and only a paired phone may ask at all


def test_the_service_worker_is_served_from_the_root_and_versioned(settings):
    c = TestClient(create_app(settings))
    res = c.get("/sw.js")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/javascript")
    assert res.headers["service-worker-allowed"] == "/" and res.headers["cache-control"] == "no-cache"
    assert "__VERSION__" not in res.text and "addEventListener(\"push\"" in res.text


def test_the_page_can_be_installed_with_real_icons(settings):
    c = TestClient(create_app(settings))
    manifest = c.get("/static/manifest.webmanifest").json()
    sizes = {(i["sizes"], i["purpose"]) for i in manifest["icons"] if i["type"] == "image/png"}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in manifest["icons"]:
        assert c.get(icon["src"]).status_code == 200, icon["src"]
    assert 'href="/static/apple-touch-icon.png"' in c.get("/").text and c.get("/static/apple-touch-icon.png").status_code == 200
    assert c.get("/static/badge-96.png").status_code == 200


def test_a_phone_that_switched_a_type_off_is_not_sent_it(tmp_path):
    """The choice is applied where the notice is sent: nothing leaves the PC for a phone that asked not to hear about it."""
    account, _ = firebase_account()
    posts = []

    def google(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        posts.append(request)
        return httpx.Response(200, json={"name": "projects/demo-project/messages/1"})

    notifier, _, device = make_notifier(tmp_path, google, web=False, fcm=account)
    notifier.subscribe_fcm(device.id, "f" * 50 + ":APA91bExampleToken_-" + "x" * 40)
    notifier.set_prefs(device.id, {"types": {"done": False}})
    report = notifier.send(Message("Job finished", "x", "ab12cd34", "done"))
    assert report.sent == 0 and report.skipped == 1 and posts == []
    assert notifier.send(Message("Plan ready", "x", "ab12cd34", "awaiting")).sent == 1  # the others still arrive
    assert notifier.send(Message("Notifications are on", "x", "", "test")).sent == 1      # and a test always does
