from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from svix.webhooks import Webhook

from app.main import app
from app.services import alert_jobs as jobs
from app.services.alert_data import calendar, fingerprint
from app.services.alert_email import unsubscribe_token
from app.services.alert_store import database, json_value
from tests.test_stock_alert_api import db, META

NOW = datetime(2026, 10, 22, 23, tzinfo=timezone.utc)


def prepare(monkeypatch):
    monkeypatch.setenv("STOCK_ALERTS_SEND_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "test-only")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "alerts@example.com")
    monkeypatch.setenv("ALERT_UNSUBSCRIBE_SECRET", "test-unsubscribe-secret-with-more-than-32-chars")
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(jobs.time, "sleep", lambda _: None)
    monkeypatch.setattr(jobs, "legacy_section", lambda _: None)
    client = TestClient(app)
    client.patch("/api/alerts/settings", json={"revision": 1, "consent": True})
    item = client.post("/api/alerts/watchlist", json={"symbol": "SCHD"}).json()
    client.post("/api/alerts/rules", json={"instrumentIds": [item["instrumentId"]], "type": "price_ma_cross", "params": {}})
    dates = calendar("XNYS", 2026).sessions_in_range("2025-01-01", "2026-10-22")[-201:]
    raw = [{"date": x.date().isoformat(), "open": 10., "high": 11., "low": 10., "close": 11. if idx==200 else 10., "volume": 100.} for idx,x in enumerate(dates)]
    snapshot = {"raw": raw, "splits": [], "policy": "raw-split-only-v1", "target": "2026-10-22"}
    with database() as conn:
        conn.execute("UPDATE alert_rules SET activated_at=%s", (NOW - timedelta(days=3),))
        conn.execute("INSERT INTO alert_daily_bars(instrument_id,snapshot,fingerprint,first_seen) VALUES(%s,%s,%s,%s)",
                     (item["instrumentId"], json_value(snapshot), fingerprint(snapshot), NOW - timedelta(minutes=10)))
    monkeypatch.setattr(jobs, "load_daily_bars", lambda *args: snapshot)
    return client, snapshot


def test_digest_once_and_unknown_retries_same_key(db, monkeypatch):
    client, snapshot = prepare(monkeypatch)
    sent = []
    def send(identity, payload):
        sent.append((identity, payload))
        return ("unknown", None) if len(sent)==1 else ("accepted", "provider-1")
    monkeypatch.setattr(jobs, "send_payload", send)
    assert jobs.process_alert_run(now=NOW)["status"] == "processed"
    assert len(sent) == 1
    with database() as conn:
        conn.execute("UPDATE alert_digests SET next_attempt_at=now()-interval '1 second'")
    jobs.process_alert_run(now=NOW + timedelta(minutes=5))
    jobs.process_alert_run(now=NOW + timedelta(minutes=10))
    assert len(sent) == 2 and sent[0] == sent[1]
    assert len(client.get("/api/alerts/events").json()["items"]) == 1
    assert client.get("/api/alerts/digests").json()["items"][0]["status"] == "accepted"
    assert "envelope" not in str(client.get("/api/alerts/digests").json())


def test_dry_run_no_mutations_and_first_registration_no_old_signal(db, monkeypatch):
    client, snapshot = prepare(monkeypatch)
    monkeypatch.setenv("STOCK_ALERTS_SEND_ENABLED", "false")
    assert jobs.process_alert_run(now=NOW)["status"] == "dry_run"
    with database() as conn:
        assert conn.execute("SELECT count(*) AS n FROM alert_runs").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM alert_events").fetchone()["n"] == 0
        assert conn.execute("SELECT last_session FROM alert_rules").fetchone()["last_session"] is None
        conn.execute("UPDATE alert_rules SET activated_at=%s", (NOW,))
    monkeypatch.setenv("STOCK_ALERTS_SEND_ENABLED", "true")
    monkeypatch.setattr(jobs, "send_payload", lambda *args: pytest.fail("Must not send historical signal"))
    jobs.process_alert_run(now=NOW)
    assert client.get("/api/alerts/events").json()["items"] == []


def test_lease_fencing_and_no_duplicate_coordinator(db, monkeypatch):
    prepare(monkeypatch)
    run, token = jobs.initialize(NOW)
    assert jobs.initialize(NOW) == (None, None)
    from app.services.alert_data import DataPending
    with database() as conn:
        with pytest.raises(DataPending):
            jobs.fence(conn, run["id"], uuid4())


def test_get_unsubscribe_does_not_change_consent_and_post_revokes(db, monkeypatch):
    client, _ = prepare(monkeypatch)
    with database() as conn:
        u = conn.execute("SELECT * FROM alert_users").fetchone()
    token = unsubscribe_token(u)
    assert client.get("/notifications/unsubscribe/"+token).status_code == 200
    assert client.get("/api/alerts/settings").json()["consent"] is True
    assert client.post("/notifications/unsubscribe/"+token, headers={"Origin": "https://mail-client.example"}).status_code == 200
    assert client.get("/api/alerts/settings").json()["consent"] is False
    assert client.post("/notifications/unsubscribe/forged").status_code == 400


def test_cron_secret_does_not_unlock_other_apis(db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "cron-only")
    client = TestClient(app, cookies={})
    assert client.get("/api/cron/stock-alerts").status_code == 401
    assert client.get("/api/alerts/settings", headers={"Authorization": "Bearer cron-only"}).status_code == 401
    assert client.post("/api/webhooks/alert-email", json={}).status_code in (400, 503)


def test_signed_bounce_then_delivery_stays_suppressed(db, monkeypatch):
    import base64
    import json
    client, _ = prepare(monkeypatch)
    monkeypatch.setattr(jobs, "send_payload", lambda *args: ("accepted", "mail-id"))
    jobs.process_alert_run(now=NOW)
    secret = "whsec_" + base64.b64encode(b"test-signing-secret-of-32-bytes!!").decode()
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", secret)
    def deliver(kind, identity):
        body = json.dumps({"type": kind, "data": {"email_id": "mail-id"}})
        timestamp = datetime.now(timezone.utc)
        signature = Webhook(secret).sign(identity, timestamp, body)
        return client.post("/api/webhooks/alert-email", content=body,
                           headers={"svix-id":identity,"svix-timestamp":str(int(timestamp.timestamp())),"svix-signature":signature})
    assert deliver("email.bounced", "event-1").status_code == 200
    assert deliver("email.bounced", "event-1").json()["duplicate"]
    assert deliver("email.delivered", "event-2").status_code == 200
    assert client.get("/api/alerts/settings").json()["suppressed"] == "bounced"
    assert client.get("/api/alerts/digests").json()["items"][0]["status"] == "bounced"


def test_webhook_before_send_result_is_reconciled(db, monkeypatch):
    client, _ = prepare(monkeypatch)
    def send(*_):
        with database() as conn:
            conn.execute("INSERT INTO alert_webhook_receipts(id,provider_id,kind) VALUES('early','early-mail','email.bounced')")
        return "accepted", "early-mail"
    monkeypatch.setattr(jobs, "send_payload", send)
    jobs.process_alert_run(now=NOW)
    assert client.get("/api/alerts/settings").json()["suppressed"] == "bounced"


def test_failed_data_is_not_reported_as_no_signals(db, monkeypatch):
    from app.services.alert_data import DataPending
    client, _ = prepare(monkeypatch)
    def rejected(*_):
        raise DataPending("provider_rejected", False)
    monkeypatch.setattr(jobs, "load_daily_bars", rejected)
    jobs.process_alert_run(now=NOW)
    assert client.get("/api/alerts/rules").json()["items"][0]["last_result"]["status"] == "deferred"
    result = client.get("/api/alerts/digests").json()["items"][0]
    assert result["status"] == "deferred" and result["deferredCount"] == 1


def test_legacy_failure_does_not_block_signals(db, monkeypatch):
    from fastapi import HTTPException
    client, _ = prepare(monkeypatch)
    def failed(_):
        raise HTTPException(503, "legacy unavailable")
    monkeypatch.setattr(jobs, "legacy_section", failed)
    sent = []
    monkeypatch.setattr(jobs, "send_payload", lambda *args: (sent.append(args) or ("accepted", "legacy-failure")))
    jobs.process_alert_run(now=NOW)
    assert len(sent) == 1
    assert "보류" in sent[0][1]["text"]


def test_source_correction_marks_old_event_without_resending(db, monkeypatch):
    client, snap = prepare(monkeypatch)
    sent = []
    monkeypatch.setattr(jobs, "send_payload", lambda *args: (sent.append(args) or ("accepted", "correction-mail")))
    jobs.process_alert_run(now=NOW)
    import copy
    changed = copy.deepcopy(snap); changed["raw"][-1]["close"] = 10.
    with database() as conn:
        cached = conn.execute("SELECT * FROM alert_daily_bars").fetchone()
        jobs.mark_corrections(conn, cached["instrument_id"], cached, changed, fingerprint(changed))
    event = client.get("/api/alerts/events").json()["items"][0]
    assert event["correction"]["result"]["status"] == "no_signal"
    assert len(sent) == 1


def test_expired_unsubscribe_token_cannot_revoke_new_consent(db, monkeypatch):
    client, _ = prepare(monkeypatch)
    with database() as conn:
        old = conn.execute("SELECT * FROM alert_users").fetchone()
    token = unsubscribe_token(old)
    client.patch("/api/alerts/settings", json={"revision": old["revision"], "consent": True})
    assert client.post("/notifications/unsubscribe/"+token).status_code == 400
    assert client.get("/api/alerts/settings").json()["consent"] is True


def test_concurrent_receipt_and_send_result_serialize(db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.services.alert_email import lock_delivery, apply_receipts
    client, _ = prepare(monkeypatch)
    monkeypatch.setattr(jobs, "send_payload", lambda *args: ("unknown", None))
    jobs.process_alert_run(now=NOW)
    with database() as conn:
        identity = conn.execute("SELECT id FROM alert_digests").fetchone()["id"]
    inserted, release = Event(), Event()
    def webhook():
        with database() as conn:
            lock_delivery(conn, "race-mail")
            conn.execute("INSERT INTO alert_webhook_receipts(id,provider_id,kind) VALUES('race','race-mail','email.bounced')")
            inserted.set()
            assert release.wait(5)
            apply_receipts(conn, "race-mail")
    def completion():
        assert inserted.wait(5)
        with database() as conn:
            lock_delivery(conn, "race-mail", identity)
            conn.execute("UPDATE alert_digests SET provider_id='race-mail',status='accepted' WHERE id=%s", (identity,))
            apply_receipts(conn, "race-mail")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(webhook); assert inserted.wait(5)
        second = pool.submit(completion); release.set()
        first.result(timeout=10); second.result(timeout=10)
    assert client.get("/api/alerts/settings").json()["suppressed"] == "bounced"


def test_deleting_alerts_never_revives_legacy_mail(db, monkeypatch):
    from app.services.alert_store import legacy_consolidated
    from app.services.notification_jobs import run_notification
    client, _ = prepare(monkeypatch)
    assert legacy_consolidated()
    assert client.delete("/api/alerts/account").status_code == 200
    assert legacy_consolidated()
    assert run_notification()["outcome"] == "consolidated"


@pytest.mark.parametrize("first_state,expected", [("retryable_failed","pending"),("unknown","no_signals")])
def test_next_day_releases_only_definite_nonacceptance(db, monkeypatch, first_state, expected):
    import time
    client, _ = prepare(monkeypatch)
    monkeypatch.setattr(jobs,"send_payload",lambda *_:(first_state,None,600))
    jobs.process_alert_run(now=NOW)
    later = NOW+timedelta(days=1)
    monkeypatch.setattr(jobs,"utcnow",lambda:later)
    run,token=jobs.initialize(later)
    with database() as conn:
        conn.execute("UPDATE alert_tasks SET status='done' WHERE run_id=%s",(run["id"],))
    jobs.prepare_digests(run,token,later,time.monotonic())
    with database() as conn:
        row=conn.execute("SELECT * FROM alert_digests WHERE digest_date=%s",(run["digest_date"],)).fetchone()
        assert row["status"]==expected
        assert len(row["payload"]["eventIds"])==(1 if first_state=="retryable_failed" else 0)
