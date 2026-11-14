from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_env(monkeypatch):
    monkeypatch.setenv("LIVE_MODE", "true")
    monkeypatch.setenv("DATA_DIR", "/data")
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "live_mode": True, "data_dir": "/data"}


def test_health_defaults(monkeypatch):
    monkeypatch.delenv("LIVE_MODE", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert client.get("/api/health").json() == {"ok": True, "live_mode": False, "data_dir": "data"}


def test_health_never_exposes_token(monkeypatch):
    monkeypatch.setenv("AQ_TOKEN", "tok-should-not-leak")
    r = client.get("/api/health")
    assert "tok-should-not-leak" not in r.text
    assert "token" not in r.text.lower()


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "Questathon CRM: syncing" in r.text
    assert client.get("/static/app.js").status_code == 200
