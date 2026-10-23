import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.alert_store import database, migrate
from tests.conftest import session_cookie

META = {"symbol": "SCHD", "providerSymbol": "SCHD", "name": "Schwab ETF", "mic": "ARCX", "currency": "USD",
        "timezone": "America/New_York", "calendar": "XNYS", "type": "ETF", "policy": "raw-split-only-v1"}


@pytest.fixture
def db(monkeypatch):
    url = os.getenv("ALERT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set ALERT_TEST_DATABASE_URL to an isolated PostgreSQL database")
    if not url.split("?")[0].endswith("/alerts_test"):
        pytest.fail("Only the dedicated alerts_test database may be reset")
    monkeypatch.setenv("ALERT_DATABASE_URL", url)
    migrate(url)
    with database() as conn:
        conn.execute("TRUNCATE alert_users,alert_instruments,alert_runs,alert_provider_credits,alert_webhook_receipts CASCADE")
    monkeypatch.setattr("app.stock_alerts.resolve_instrument", lambda symbol: {**META, "symbol": symbol.upper()})
    return url


def test_settings_watch_rules_isolation_and_revisions(db):
    a = TestClient(app)
    b = TestClient(app, cookies={"portfolio_session": session_cookie({"sub": "other", "email": "other@gmail.com"})})
    settings = a.get("/api/alerts/settings").json()
    assert settings["consent"] is False
    assert a.patch("/api/alerts/settings", json={"revision": 1, "consent": True, "email": "forged@test.com"}).status_code == 422
    assert a.patch("/api/alerts/settings", json={"revision": 1, "consent": True}).status_code == 200
    assert a.patch("/api/alerts/settings", json={"revision": 1, "consent": False}).status_code == 409
    item = a.post("/api/alerts/watchlist", json={"symbol": "SCHD"}).json()
    assert b.get("/api/alerts/watchlist").json()["items"] == []
    assert b.delete("/api/alerts/watchlist/" + item["id"]).status_code == 404
    payload = {"instrumentIds": [item["instrumentId"]], "type": "price_ma_cross", "params": {}}
    assert b.post("/api/alerts/rules", json=payload).status_code == 404
    identity = a.post("/api/alerts/rules", json=payload).json()["ids"][0]
    assert a.post("/api/alerts/rules", json=payload).status_code == 409
    assert b.patch("/api/alerts/rules/" + identity, json={"revision": 1, "enabled": False}).status_code == 404
    assert a.patch("/api/alerts/rules/" + identity, json={"revision": 1, "enabled": False}).status_code == 200
    assert a.patch("/api/alerts/rules/" + identity, json={"revision": 1, "enabled": True}).status_code == 409
    assert a.patch("/api/alerts/rules/" + identity, json={"revision": 2, "enabled": True}).status_code == 200
    row = a.get("/api/alerts/rules").json()["items"][0]
    assert row["meaning_version"] == 2
    assert a.delete("/api/alerts/watchlist/" + item["id"]).status_code == 200
    assert a.get("/api/alerts/rules").json()["items"] == []


def test_batch_is_atomic_and_email_change_revokes_consent(db):
    client = TestClient(app)
    item = client.post("/api/alerts/watchlist", json={"symbol": "SCHD"}).json()
    assert client.post("/api/alerts/rules", json={"instrumentIds": [item["instrumentId"], str(uuid4())],
           "type": "price_cross", "params": {"price": 10}}).status_code == 404
    assert client.get("/api/alerts/rules").json()["items"] == []
    client.patch("/api/alerts/settings", json={"revision": 1, "consent": True})
    changed = TestClient(app, cookies={"portfolio_session": session_cookie({"sub": "google-owner", "email": "new@gmail.com"})})
    result = changed.get("/api/alerts/settings").json()
    assert result["email"] == "new@gmail.com" and not result["consent"]
    assert result["revision"] == 3


def test_concurrent_duplicate_watch_and_migration_checksum(db):
    def add(_):
        with TestClient(app) as client:
            return client.post("/api/alerts/watchlist", json={"symbol": "SCHD"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(add, range(2))) == [200, 409]
    with database() as conn:
        conn.execute("UPDATE alert_schema_versions SET checksum='wrong' WHERE name='001_stock_alerts.sql'")
    try:
        with pytest.raises(RuntimeError):
            migrate(db)
    finally:
        from hashlib import sha256
        from pathlib import Path
        checksum = sha256(Path("migrations/001_stock_alerts.sql").read_text(encoding="utf-8").encode()).hexdigest()
        with database() as conn:
            conn.execute("UPDATE alert_schema_versions SET checksum=%s WHERE name='001_stock_alerts.sql'", (checksum,))


def test_unconfigured_alerts_do_not_break_existing_dashboard(monkeypatch):
    monkeypatch.delenv("ALERT_DATABASE_URL", raising=False)
    client = TestClient(app)
    assert client.get("/api/alerts/settings").status_code == 503
    assert client.get("/").status_code == 200
    assert client.get("/api/dashboard/state").status_code == 200
