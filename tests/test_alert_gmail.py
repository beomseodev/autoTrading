from uuid import uuid4

from fastapi import HTTPException

from app.services import alert_email, notification_email


def test_gmail_is_owner_only_and_uncertain_failures_are_recorded(monkeypatch):
    monkeypatch.setenv('ALERT_EMAIL_TRANSPORT', 'gmail')
    monkeypatch.setenv('GOOGLE_OWNER_EMAIL', 'owner@example.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD', 'a' * 16)
    monkeypatch.setenv('ALERT_UNSUBSCRIBE_SECRET', 'a' * 32)
    monkeypatch.setenv('APP_BASE_URL', 'https://example.com')
    calls = []
    monkeypatch.setattr(notification_email, 'send_email', lambda *args: calls.append(args))
    payload = {'to': ['owner@example.com'], 'subject': 'test', 'text': 'hello'}
    assert alert_email.email_configured()
    assert alert_email.send_payload(uuid4(), payload) == ('accepted', None)
    assert calls == [('test', 'hello')]
    assert alert_email.send_payload(uuid4(), {**payload, 'to': ['other@example.com']}) == ('permanent_failed', None)
    assert len(calls) == 1
    def fail(*_):
        raise HTTPException(502)
    monkeypatch.setattr(notification_email, 'send_email', fail)
    assert alert_email.send_payload(uuid4(), payload) == ('unknown', None)


def test_stock_cron_uses_separate_secret(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import alert_jobs
    monkeypatch.setenv('CRON_SECRET', 'rebalance-secret')
    monkeypatch.setenv('ALERT_CRON_SECRET', 'stock-secret')
    monkeypatch.setattr(alert_jobs, 'process_alert_run', lambda: {'status': 'ok'})
    with TestClient(app) as client:
        assert client.get('/api/cron/stock-alerts', headers={'Authorization': 'Bearer rebalance-secret'}).status_code == 401
        assert client.get('/api/cron/stock-alerts', headers={'Authorization': 'Bearer stock-secret'}).json() == {'status': 'ok'}
