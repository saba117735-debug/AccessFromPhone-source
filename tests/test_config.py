import pytest
from pydantic import ValidationError

from handoff.config import load_settings


def write_config(tmp_path, folder, port=8443, ip="192.168.1.20"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
server:
  port: {port}
  public_url: http://{ip}:{port}
agent:
  allowed_folders: ['{folder}']
storage:
  results_dir: '{tmp_path / "results"}'
  snapshots_dir: '{tmp_path / "snaps"}'
logging:
  audit_file: '{tmp_path / "audit.log"}'
""",
        encoding="utf-8",
    )
    return cfg


def test_loads_values_from_yaml(tmp_path, sandbox):
    settings = load_settings(write_config(tmp_path, sandbox))
    assert settings.server.public_url == "http://192.168.1.20:8443"
    assert settings.agent.allowed_folders == [sandbox.resolve()]


def test_changing_the_ip_is_one_edit(tmp_path, sandbox):
    settings = load_settings(write_config(tmp_path, sandbox, ip="10.0.0.5"))
    assert settings.server.public_url == "http://10.0.0.5:8443"


def test_env_overrides_file(tmp_path, sandbox, monkeypatch):
    monkeypatch.setenv("HANDOFF_SERVER__PORT", "9000")
    settings = load_settings(write_config(tmp_path, sandbox, port=8443))
    assert settings.server.port == 9000


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(ValidationError, match="does not exist"):
        load_settings(write_config(tmp_path, tmp_path / "nope"))


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="config file not found"):
        load_settings(tmp_path / "absent.yaml")


def test_unknown_setting_is_rejected(tmp_path, sandbox):
    cfg = write_config(tmp_path, sandbox)
    cfg.write_text(cfg.read_text() + "\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(cfg)
