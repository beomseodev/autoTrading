from datetime import timedelta
from tests.test_stock_alert_api import db
import pytest


def test_frequency_validation_and_existing_daily_default(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client=TestClient(app)
    assert client.get('/api/alerts/settings').json()['emailFrequency']=='daily'
    assert client.patch('/api/alerts/settings',json={'revision':1,'consent':True,'emailFrequency':'monthly'}).status_code==422
    assert client.patch('/api/alerts/settings',json={'revision':1,'consent':True,'emailFrequency':'weekly'}).status_code==200
    assert client.get('/api/alerts/settings').json()['emailFrequency']=='weekly'
    assert client.patch('/api/alerts/settings',json={'revision':2,'consent':True}).status_code==200
    assert client.get('/api/alerts/settings').json()['emailFrequency']=='weekly'


def test_weekly_collects_during_week_and_sends_once_on_monday(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services.alert_store import database
    client,_=prepare(monkeypatch)
    response=client.patch('/api/alerts/settings',json={'revision':2,'consent':True,'emailFrequency':'weekly'})
    assert response.status_code==200,response.text
    sent=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('accepted',str(identity)))
    jobs.process_alert_run(now=NOW)
    assert not sent and len(client.get('/api/alerts/events').json()['items'])==1
    assert not client.get('/api/alerts/digests').json()['items']
    with database() as conn:conn.execute('UPDATE alert_events SET created_at=%s',(NOW,))
    monday=NOW+timedelta(days=3)
    monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    jobs.process_alert_run(now=monday)
    jobs.process_alert_run(now=monday+timedelta(minutes=5))
    assert len(sent)==1 and '주간' in sent[0]['subject']
    row=client.get('/api/alerts/digests').json()['items'][0]
    assert row['kind']=='weekly' and row['channel']=='email'


def test_frequency_change_does_not_forget_ambiguous_prior_send(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services.alert_store import database
    client,_=prepare(monkeypatch)
    sent=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('unknown',None))
    jobs.process_alert_run(now=NOW)
    with database() as conn:digest=conn.execute('SELECT id FROM alert_digests').fetchone()['id']
    response=client.patch('/api/alerts/settings',json={'revision':2,'consent':True,'emailFrequency':'weekly'})
    assert response.status_code==200,response.text
    jobs.send_digest(digest)
    assert client.get('/api/alerts/digests').json()['items'][0]['status']=='unknown'
    monday=NOW+timedelta(days=3)
    monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    jobs.process_alert_run(now=monday)
    assert len(sent)==1


def test_weekly_window_uses_discovery_time_including_delayed_sessions(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services.alert_store import database
    from uuid import uuid4
    client,_=prepare(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':True,'emailFrequency':'weekly'})
    sent=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('accepted',str(identity)))
    jobs.process_alert_run(now=NOW)
    monday=NOW+timedelta(days=3)
    with database() as conn:
        conn.execute('UPDATE alert_events SET created_at=%s',(monday-timedelta(days=7,seconds=1),))
        conn.execute('''INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload,created_at)
            SELECT %s,user_id,rule_id,instrument_id,meaning_version,%s,trigger_kind,snapshot_hash,payload,%s FROM alert_events''',
            (uuid4(),monday.date()-timedelta(days=19),monday-timedelta(days=1)))
    monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    jobs.process_alert_run(now=monday)
    assert len(sent)==1 and '1신호' in sent[0]['subject'] and str(monday.date()-timedelta(days=19)) in sent[0]['text']


def test_changing_frequency_does_not_send_second_legacy_digest_same_day(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    client,_=prepare(monkeypatch)
    monday=NOW+timedelta(days=3);sent=[]
    monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    monkeypatch.setattr(jobs,'legacy_section',lambda u:'리밸런싱 점검')
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('accepted',str(identity)))
    jobs.process_alert_run(now=monday)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':True,'emailFrequency':'weekly'})
    jobs.process_alert_run(now=monday+timedelta(minutes=5))
    assert len(sent)==1


def test_frequency_change_keeps_received_email_unsubscribe_valid(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare
    from app.services.alert_email import unsubscribe_token,unsubscribe
    from app.services.alert_store import database
    client,_=prepare(monkeypatch)
    with database() as conn:current=conn.execute('SELECT * FROM alert_users').fetchone()
    token=unsubscribe_token(current)
    assert client.patch('/api/alerts/settings',json={'revision':2,'consent':True,'emailFrequency':'weekly'}).status_code==200
    unsubscribe(token,apply=True)
    assert not client.get('/api/alerts/settings').json()['consent']
    assert client.patch('/api/alerts/settings',json={'revision':4,'consent':True}).status_code==200
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):unsubscribe(token,apply=True)


@pytest.mark.parametrize('channel',['email','push'])
def test_unattempted_weekly_digest_rebuilds_remaining_valid_events(db,monkeypatch,channel):
    import time
    from uuid import uuid4
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from tests.test_alert_push import configure,subscription
    from app.services.alert_store import database,json_value
    from app.services import alert_push
    client,_=prepare(monkeypatch)
    monkeypatch.setattr(jobs,'send_payload',lambda *_:('retryable_failed',None))
    if channel=='push':
        configure(monkeypatch)
        assert client.post('/api/alerts/push/subscriptions',json=subscription()).status_code==200
        assert client.patch('/api/alerts/push/settings',json={'revision':2,'consent':True,'frequency':'weekly'}).status_code==200
    else:client.patch('/api/alerts/settings',json={'revision':2,'consent':True,'emailFrequency':'weekly'})
    jobs.process_alert_run(now=NOW)
    monday=NOW+timedelta(days=3);monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    with database() as conn:
        conn.execute('DELETE FROM alert_digests')
        original=conn.execute('SELECT * FROM alert_rules').fetchone();other=uuid4();event=uuid4()
        conn.execute('''INSERT INTO alert_rules(id,user_id,instrument_id,kind,params,fingerprint,activated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)''',(other,original['user_id'],original['instrument_id'],original['kind'],json_value(original['params']),str(other),NOW-timedelta(days=7)))
        conn.execute('UPDATE alert_events SET created_at=%s',(monday-timedelta(days=6),))
        conn.execute('''INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload,created_at)
            SELECT %s,user_id,%s,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload,created_at FROM alert_events''',(event,other))
    run,token=jobs.initialize(monday)
    with database() as conn:conn.execute("UPDATE alert_tasks SET status='done' WHERE run_id=%s",(run['id'],))
    jobs.prepare_digests(run,token,monday,time.monotonic())
    with database() as conn:
        digest=conn.execute('SELECT * FROM alert_digests WHERE digest_date=%s AND channel=%s',(run['digest_date'],channel)).fetchone()
        assert len(digest['payload']['eventIds'])==2 and digest['attempts']==0
    assert client.patch('/api/alerts/rules/'+str(original['id']),json={'revision':1,'enabled':False}).status_code==200
    sent=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(identity) or ('accepted',str(identity)))
    monkeypatch.setattr(alert_push,'send_notification',lambda sub,identity:sent.append(identity) or ('accepted',300))
    jobs.send_digest(digest['id'])
    assert not sent
    jobs.prepare_digests(run,token,monday,time.monotonic())
    with database() as conn:
        rebuilt=conn.execute('SELECT * FROM alert_digests WHERE id=%s',(digest['id'],)).fetchone()
        assert rebuilt['status']=='pending' and rebuilt['payload']['eventIds']==[str(event)]
    jobs.send_digest(digest['id']);jobs.send_digest(digest['id'])
    assert len(sent)==1
