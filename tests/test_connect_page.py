from handoff.connect import open_connect_page, write_connect_page


def test_the_connect_page_holds_a_qr_code_and_the_link(tmp_path):
    link = "https://10.0.0.5:8765/?token=abc&x=1"
    page = write_connect_page(link, tmp_path, https=True).read_text(encoding="utf-8")
    assert "<svg" in page and "Connect your phone" in page
    assert "token=abc&amp;x=1" in page  # the visible link is escaped
    assert "not private" in page


def test_a_plain_http_link_has_no_certificate_warning(tmp_path):
    assert "not private" not in write_connect_page("http://10.0.0.5:8765/?token=a", tmp_path, https=False).read_text(encoding="utf-8")


def test_a_browser_that_will_not_open_never_stops_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda *_: (_ for _ in ()).throw(RuntimeError("no browser")))
    assert open_connect_page("http://x/?token=a", tmp_path, https=False) is None
