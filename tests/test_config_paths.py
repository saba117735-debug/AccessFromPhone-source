from handoff.config import load_settings


def test_relative_paths_resolve_against_the_config_file(project, monkeypatch, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)  # running from another folder must not matter
    s = load_settings(project / "config.yaml")
    assert s.agent.allowed_folders == [(project / "workspace").resolve()]
    assert s.logging.audit_file == (project / "data" / "audit.log").resolve()
    assert s.server.token_file == (project / "data" / "token.txt").resolve()


def test_public_url_trailing_slash_is_removed(project):
    cfg = project / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace("server:\n", "server:\n  public_url: http://10.0.0.5:8765/\n"),
        encoding="utf-8",
    )
    assert load_settings(cfg).server.public_url == "http://10.0.0.5:8765"
