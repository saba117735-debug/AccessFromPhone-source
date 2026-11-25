import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from handoff import connectivity
from handoff.admin import create_admin_app
from handoff.config import ServerConfig, load_settings
from handoff.connectivity import Anywhere, Tailnet, lan_base, read_tailnet
from handoff.planner import DemoPlanner
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


def done(payload, code=0, err=""):
    return subprocess.CompletedProcess(["tailscale"], code, stdout=json.dumps(payload) if isinstance(payload, dict) else payload, stderr=err)


STATUS_OK = {"BackendState": "Running", "Self": {"DNSName": "afza-pc.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.7", "fd7a::7"]},
             "CertDomains": ["afza-pc.tail1234.ts.net"]}


# ---------------------------------------------------------------- the Wi-Fi address

def test_the_wifi_address_is_found_without_being_typed_in():
    ip = connectivity.lan_ip()
    assert ip.count(".") == 3 and not ip.startswith("0.")


def test_auto_means_the_address_at_this_start_not_the_one_in_config(monkeypatch):
    monkeypatch.setattr(connectivity, "lan_ip", lambda: "192.168.7.7")
    cfg = ServerConfig(https=True, port=9000)
    assert cfg.public_url == "auto" and cfg.public_url_now == "https://192.168.7.7:9000" and cfg.link_base == "https://192.168.7.7:9000"
    monkeypatch.setattr(connectivity, "lan_ip", lambda: "10.1.1.1")  # the PC moved to another network
    assert cfg.link_base == "https://10.1.1.1:9000"


def test_a_fixed_address_still_wins():
    assert ServerConfig(public_url="http://10.0.0.5:8765").public_url_now == "http://10.0.0.5:8765"


def test_the_qr_can_hold_the_local_name_which_survives_an_address_change(monkeypatch):
    monkeypatch.setattr(connectivity.socket, "gethostname", lambda: "AFZA-PC.corp.local")
    server = ServerConfig(https=True)
    by_name = type("C", (), {"lan_address": "name"})()
    by_ip = type("C", (), {"lan_address": "ip"})()
    assert lan_base(server, by_name) == "https://afza-pc.local:8765"
    assert lan_base(server, by_ip) == server.link_base


# ---------------------------------------------------------------- Tailscale

def test_tailscale_not_installed_is_a_normal_state(monkeypatch):
    monkeypatch.setattr(connectivity, "find_tailscale", lambda configured="": "")
    t = read_tailnet()
    assert not t.installed and not t.ready and "isn't installed" in t.error


def test_a_ready_tailnet_is_read_from_its_status(monkeypatch):
    monkeypatch.setattr(connectivity, "find_tailscale", lambda configured="": "tailscale.exe")
    t = read_tailnet(runner=lambda *a, **k: done(STATUS_OK))
    assert t.ready and t.name == "afza-pc.tail1234.ts.net" and t.ip == "100.64.0.7"  # no trailing dot, IPv4 only


def test_signed_out_and_https_off_are_told_apart(monkeypatch):
    monkeypatch.setattr(connectivity, "find_tailscale", lambda configured="": "tailscale.exe")
    out = read_tailnet(runner=lambda *a, **k: done({"BackendState": "NeedsLogin", "Self": {}}))
    assert out.installed and not out.running and "sign in" in out.error
    no_https = read_tailnet(runner=lambda *a, **k: done({**STATUS_OK, "CertDomains": []}))
    assert no_https.running and not no_https.https_enabled and not no_https.ready


def test_garbled_output_never_raises(monkeypatch):
    monkeypatch.setattr(connectivity, "find_tailscale", lambda configured="": "tailscale.exe")
    assert not read_tailnet(runner=lambda *a, **k: done("not json")).ready

    def boom(*a, **k):
        raise OSError("gone")
    assert not read_tailnet(runner=boom).ready


READY = Tailnet(installed=True, running=True, name="afza-pc.tail1234.ts.net", ip="100.64.0.7", https_enabled=True, cli="tailscale.exe")


def make(tmp_path, tailnet, issuer=None, enabled=True):
    started = []

    def fake_start(app, host, port, cert, key):
        started.append((host, port, cert.name))
        return object()

    def fake_issue(t, folder):
        return folder / "c.crt", folder / "c.key"
    return Anywhere(object(), "", 8443, tmp_path, enabled=enabled, reader=lambda: tailnet, issuer=issuer or fake_issue, starter=fake_start), started


def test_anywhere_starts_a_private_listener_on_the_tailscale_address_once(tmp_path):
    anywhere, started = make(tmp_path, READY)
    first = anywhere.enable()
    again = anywhere.enable()
    assert first["active"] and first["url"] == "https://afza-pc.tail1234.ts.net:8443" and again["url"] == first["url"]
    assert started == [("100.64.0.7", 8443, "c.crt")]  # bound to 100.x only, and only once


def test_anywhere_waits_until_every_step_is_done_and_says_which(tmp_path):
    anywhere, started = make(tmp_path, Tailnet(installed=True, running=True, name="afza-pc.tail1234.ts.net", ip="100.64.0.7", https_enabled=False))
    status = anywhere.enable()
    assert not status["active"] and started == []
    assert {s["key"]: s["ok"] for s in status["steps"]} == {"install": True, "signin": True, "https": False, "listen": False}
    assert "Enable HTTPS" in next(s["hint"] for s in status["steps"] if s["key"] == "https")


def test_a_failed_certificate_is_shown_not_raised(tmp_path):
    def refuse(t, folder):
        raise RuntimeError("Access denied: use tailscale set --operator")
    status = make(tmp_path, READY, issuer=refuse)[0].enable()
    assert not status["active"] and "operator" in status["error"]


def test_anywhere_can_be_switched_off(tmp_path):
    anywhere, started = make(tmp_path, READY, enabled=False)
    assert not anywhere.enable()["active"] and started == []


def test_the_certificate_command_is_the_documented_one(tmp_path):
    seen = []

    def runner(cli, *args, **k):
        seen.append(args)
        (tmp_path / "afza-pc.tail1234.ts.net.crt").write_text("c")
        (tmp_path / "afza-pc.tail1234.ts.net.key").write_text("k")
        return done("")
    cert, key = connectivity.issue_certificate(READY, tmp_path, runner=runner)
    assert seen[0][0] == "cert" and "--cert-file" in seen[0] and seen[0][-1] == "afza-pc.tail1234.ts.net" and cert.name.endswith(".crt")


# ---------------------------------------------------------------- the PC page

@pytest.fixture
def pc(project, monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    phone = create_app(settings, DemoPlanner())
    anywhere, _ = make(tmp_path, READY)
    admin = TestClient(create_admin_app(settings, phone.state.accounts, phone.state.manager, anywhere), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    return admin, anywhere


def test_the_connection_card_reports_wifi_anywhere_wake_and_power(pc):
    admin, _ = pc
    info = admin.get("/admin/connection").json()
    assert info["lan"]["url"].startswith("http") and info["anywhere"]["ready"] is True and info["anywhere"]["active"] is False
    assert info["wake"]["mac"].count(":") == 5 and info["power"]["keep_awake"] is True


def test_the_anywhere_qr_needs_anywhere_to_be_on_first(pc):
    admin, anywhere = pc
    assert admin.post("/admin/pairing", json={"via": "anywhere"}, headers=H).status_code == 409
    assert admin.post("/admin/connection/anywhere", headers=H).json()["active"] is True
    link = admin.post("/admin/pairing", json={"via": "anywhere"}, headers=H).json()["link"]
    assert link.startswith("https://afza-pc.tail1234.ts.net:8443/#pair=")


def test_the_wifi_qr_is_unchanged_without_a_body(pc):
    admin, _ = pc
    assert "/#pair=" in admin.post("/admin/pairing", headers=H).json()["link"]


# ---------------------------------------------------------------- staying awake and being found

def test_keep_awake_never_raises_and_can_be_released():
    assert isinstance(connectivity.keep_awake(True), bool)
    assert isinstance(connectivity.keep_awake(False), bool)


def test_a_missing_zeroconf_package_is_fine(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_zeroconf(name, *a, **k):
        if name.startswith("zeroconf"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_zeroconf)
    assert connectivity.advertise(8765, True, "afza-pc", "10.0.0.5") is None


def test_the_local_name_is_in_the_certificate(monkeypatch):
    from handoff.tls import hosts_for
    monkeypatch.setattr("socket.gethostname", lambda: "AFZA-PC")
    assert "afza-pc.local" in hosts_for("http://10.1.2.3:8765")


def test_a_bare_off_in_yaml_means_off_not_false(tmp_path):
    from handoff.config import ConnectivityConfig
    assert ConnectivityConfig(anywhere=False).anywhere == "off" and ConnectivityConfig(anywhere=True).anywhere == "auto"
    assert ConnectivityConfig(anywhere="off").anywhere == "off"
