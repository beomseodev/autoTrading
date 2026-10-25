from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app
from app.services.alert_store import database
from tests.conftest import session_cookie
from tests.test_stock_alert_api import db


def test_operations_is_owner_only_even_without_database(monkeypatch):
    monkeypatch.delenv('ALERT_DATABASE_URL', raising=False)
    with TestClient(app, cookies={'portfolio_session': session_cookie({'sub':'other','email':'other@test.com'})}) as client:
        assert client.get('/api/alerts/operations').status_code == 403


def test_operations_aggregates_without_personal_payloads(db):
    identity = uuid4()
    with database() as conn:
        conn.execute("INSERT INTO alert_runs(id,digest_date,cutoff,deadline) VALUES(%s,current_date,now(),now()+interval '1 hour')", (identity,))
        conn.execute("INSERT INTO alert_tasks(id,run_id,kind,target_id,payload,status,error) VALUES(%s,%s,'fetch',%s,%s,'deferred','secret@example.com')", (uuid4(),identity,uuid4(),'{"secret":"private"}'))
    response = TestClient(app).get('/api/alerts/operations')
    assert response.status_code == 200
    assert response.json()['runs'][0]['tasks'] == [{'kind':'fetch','status':'deferred','count':1}]
    assert 'secret' not in response.text and 'private' not in response.text


def test_operations_wait_age_uses_creation_not_retry_time(db):
    identity = uuid4()
    with database() as conn:
        conn.execute("INSERT INTO alert_runs(id,digest_date,cutoff,deadline) VALUES(%s,current_date,now(),now()+interval '1 hour')", (identity,))
        conn.execute("INSERT INTO alert_tasks(id,run_id,kind,target_id,payload,created_at,next_attempt_at) VALUES(%s,%s,'fetch',%s,'{}',now()-interval '1 day',now()+interval '1 hour')", (uuid4(),identity,uuid4()))
    result = TestClient(app).get('/api/alerts/operations').json()['runs'][0]
    assert result['oldestPending'] < result['cutoff']


def test_http_metrics_separate_local_quota_and_provider_429(monkeypatch):
    import httpx
    from app.services import alert_metrics, alert_data, alert_email
    from app.services.alert_data import DataPending
    import pytest
    counters = {}
    token = alert_metrics.current.set(counters)
    try:
        monkeypatch.setenv('TWELVE_DATA_API_KEY','fixture')
        monkeypatch.setattr(alert_data,'reserve_credits',lambda cost: None)
        monkeypatch.setattr(httpx,'get',lambda *a,**k: httpx.Response(429))
        with pytest.raises(DataPending): alert_data.provider_get('time_series',{})
        monkeypatch.setattr(alert_email,'email_configured',lambda:True)
        monkeypatch.setenv('RESEND_API_KEY','fixture')
        monkeypatch.setattr(httpx,'post',lambda *a,**k: httpx.Response(429))
        alert_email.send_payload(uuid4(),{})
        assert counters == {'dataCalls':1,'data429':1,'emailCalls':1,'email429':1}
        def blocked(_): raise DataPending('provider_rate_limited')
        monkeypatch.setattr(alert_data,'reserve_credits',blocked)
        with pytest.raises(DataPending): alert_data.provider_get('time_series',{})
        assert counters['dataCalls'] == 1
    finally:
        alert_metrics.current.reset(token)


def test_operations_pending_mail_and_test_mail_are_separate(db):
    client = TestClient(app)
    client.get('/api/alerts/settings')
    with database() as conn:
        uid = conn.execute('SELECT id FROM alert_users').fetchone()['id']
        conn.execute("INSERT INTO alert_runs(id,digest_date,cutoff,deadline) VALUES(%s,current_date,now(),now()+interval '1 hour')", (uuid4(),))
        for kind,status in [('test','accepted'),('daily','retryable_failed')]:
            conn.execute("INSERT INTO alert_digests(id,user_id,digest_date,kind,payload,status,created_at) VALUES(%s,%s,current_date,%s,'{}',%s,now()-interval '1 hour')", (uuid4(),uid,kind,status))
    run = client.get('/api/alerts/operations').json()['runs'][0]
    assert run['deliveries'] == [{'channel':'email','status':'retryable_failed','count':1}]
    assert run['oldestPending'] < run['cutoff']


def test_run_duration_includes_initialize(db, monkeypatch):
    from tests.test_stock_alert_jobs import prepare, NOW
    from app.services import alert_jobs as jobs
    prepare(monkeypatch)
    clock = [0.0]
    original = jobs.initialize
    def initialize(now):
        result = original(now)
        clock[0] += 30
        return result
    monkeypatch.setattr(jobs,'initialize',initialize)
    monkeypatch.setattr(jobs.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','duration-test'))
    jobs.process_alert_run(now=NOW)
    with database() as conn:
        assert conn.execute('SELECT duration_seconds FROM alert_runs').fetchone()['duration_seconds'] == 30
