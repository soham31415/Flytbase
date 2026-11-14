from tests.conftest import needs_snapshots


def test_health_without_snapshots(empty_client):
    r = empty_client.get("/api/health")
    assert r.status_code == 200
    h = r.json()
    assert h["ok"] is True and h["live_mode"] is False
    assert h["accounts"] == [] and h["counts"]["raw_payloads"] == 0


def test_health_reports_live_mode(monkeypatch, tmp_path):
    from tests.conftest import make_client

    (tmp_path / "empty").mkdir()
    with make_client(monkeypatch, tmp_path, tmp_path / "empty", live_mode="true") as c:
        h = c.get("/api/health").json()
    assert h["live_mode"] is True
    assert h["data_dir"] == tmp_path.as_posix()


def test_health_never_exposes_token(monkeypatch, empty_client):
    monkeypatch.setenv("AQ_TOKEN", "tok-should-not-leak")
    r = empty_client.get("/api/health")
    assert "tok-should-not-leak" not in r.text
    assert "token" not in r.text.lower()


def test_reload_refused_in_live_mode(monkeypatch, empty_client):
    monkeypatch.setenv("LIVE_MODE", "true")
    assert empty_client.post("/api/reload").status_code == 409


def test_index_page(empty_client):
    r = empty_client.get("/")
    assert r.status_code == 200
    assert "FlytBase CRM" in r.text and "/static/app.js" in r.text
    assert empty_client.get("/static/app.js").status_code == 200


@needs_snapshots
def test_crm_sections(client):
    for path in ["/api/home", "/api/accounts", "/api/accounts/A", "/api/deals", "/api/contacts", "/api/activities", "/api/insights"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/accounts/ZZ").status_code == 404
