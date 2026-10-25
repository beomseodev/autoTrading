import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat,PrivateFormat,NoEncryption

from app.main import app
from app.services.alert_store import database
from tests.conftest import session_cookie
from tests.test_stock_alert_api import db


def b64(raw):return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def subscription(suffix='one'):
    key=ec.generate_private_key(ec.SECP256R1())
    return {'endpoint':'https://fcm.googleapis.com/fcm/send/'+suffix,'expirationTime':None,
        'keys':{'p256dh':b64(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)),'auth':b64(b'a'*16)}}


def configure(monkeypatch):
    key=ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv('WEB_PUSH_VAPID_PRIVATE_KEY',b64(key.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption())))
    monkeypatch.setenv('WEB_PUSH_VAPID_SUBJECT','mailto:ops@example.com')


def test_subscription_validates_destination_keys_and_account_ownership(db,monkeypatch):
    configure(monkeypatch);a=TestClient(app)
    assert a.get('/api/alerts/settings').json()['pushConfigured']
    sub=subscription()
    for endpoint in ['http://fcm.googleapis.com/a','https://127.0.0.1/a','https://fcm.googleapis.com.evil.test/a','https://fcm.googleapis.com@localhost/a','https://fcm.googleapis.com:444/a']:
        assert a.post('/api/alerts/push/subscriptions',json={**sub,'endpoint':endpoint}).status_code==422
    assert a.post('/api/alerts/push/subscriptions',json={**sub,'keys':{'auth':'bad','p256dh':'bad'}}).status_code==422
    response=a.post('/api/alerts/push/subscriptions',json=sub)
    assert response.status_code==200,response.text
    identity=response.json()['id']
    assert a.post('/api/alerts/push/subscriptions',json=sub).json()['id']==identity
    b=TestClient(app,cookies={'portfolio_session':session_cookie({'sub':'other','email':'other@example.com'})})
    assert b.get('/api/alerts/push/subscriptions').json()['items']==[]
    assert b.delete('/api/alerts/push/subscriptions/'+identity).status_code==404
    assert b.post('/api/alerts/push/subscriptions',json=sub).status_code==409
    assert 'keys' not in str(a.get('/api/alerts/push/subscriptions').json())
    assert a.delete('/api/alerts/push/subscriptions/'+identity).status_code==200


def test_push_without_email_sends_generic_payload_once(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':False})
    assert client.post('/api/alerts/push/subscriptions',json=subscription()).status_code==200
    assert client.patch('/api/alerts/push/settings',json={'revision':3,'consent':True,'frequency':'daily'}).status_code==200
    monkeypatch.setenv('RESEND_API_KEY','')
    sent=[]
    monkeypatch.setattr(push,'send_notification',lambda sub,identity:sent.append((sub,identity)) or ('accepted',300))
    jobs.process_alert_run(now=NOW);jobs.process_alert_run(now=NOW+timedelta(minutes=5))
    assert len(sent)==1
    rows=client.get('/api/alerts/digests').json()['items']
    assert len(rows)==1 and rows[0]['channel']=='push' and rows[0]['status']=='accepted'
    assert 'SCHD' not in push.notification_payload(str(sent[0][1]))


def test_push_weekly_is_independent_from_daily_email(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.post('/api/alerts/push/subscriptions',json=subscription())
    assert client.patch('/api/alerts/push/settings',json={'revision':2,'consent':True,'frequency':'weekly'}).status_code==200
    mails=[];pushes=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:mails.append(identity) or ('accepted',str(identity)))
    monkeypatch.setattr(push,'send_notification',lambda sub,identity:pushes.append(identity) or ('accepted',300))
    jobs.process_alert_run(now=NOW)
    assert len(mails)==1 and not pushes
    with database() as conn:conn.execute('UPDATE alert_events SET created_at=%s',(NOW,))
    monday=NOW+timedelta(days=3);monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    jobs.process_alert_run(now=monday)
    assert len(mails)==1 and len(pushes)==1


def test_ambiguous_push_does_not_retry_and_expired_subscription_is_removed(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':False})
    for name in ('one','two'):client.post('/api/alerts/push/subscriptions',json=subscription(name))
    client.patch('/api/alerts/push/settings',json={'revision':3,'consent':True,'frequency':'daily'})
    sent=[]
    def send(sub,identity):
        sent.append(sub['endpoint']);return ('unknown' if sub['endpoint'].endswith('one') else 'expired',300)
    monkeypatch.setattr(push,'send_notification',send)
    jobs.process_alert_run(now=NOW)
    with database() as conn:conn.execute("UPDATE alert_digests SET next_attempt_at=now()-interval '1 second'")
    jobs.process_alert_run(now=NOW+timedelta(minutes=5))
    assert len(sent)==2
    assert len(client.get('/api/alerts/push/subscriptions').json()['items'])==1
    assert client.get('/api/alerts/digests').json()['items'][0]['status']=='unknown'
    with database() as conn:assert conn.execute("SELECT attempts FROM alert_digests WHERE channel='push'").fetchone()['attempts']==1


def test_real_push_encryption_and_redirect_protection(monkeypatch):
    from app.services import alert_push as push
    import requests,http_ece,json
    from uuid import uuid4
    configure(monkeypatch);key=ec.generate_private_key(ec.SECP256R1())
    sub=subscription();sub['keys']['p256dh']=b64(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint))
    calls=[]
    def post(session,url,**kwargs):
        calls.append((url,kwargs));response=requests.Response();response.status_code=201;return response
    monkeypatch.setattr(requests.Session,'post',post)
    identity=uuid4()
    assert push.send_notification(sub,identity)[0]=='accepted'
    assert calls[0][1]['allow_redirects'] is False and calls[0][1]['timeout']==10
    decoded=json.loads(http_ece.decrypt(calls[0][1]['data'],private_key=key,auth_secret=b'a'*16,version='aes128gcm'))
    assert decoded['url']=='/alerts' and str(identity) in decoded['tag']
    assert set(decoded)=={'title','body','tag','url'}


@pytest.mark.parametrize('status,expected',[(410,'expired'),(404,'expired'),(429,'retryable_failed'),(500,'unknown'),(403,'permanent_failed'),(302,'permanent_failed')])
def test_push_provider_statuses(monkeypatch,status,expected):
    from app.services import alert_push as push
    import requests
    configure(monkeypatch)
    def post(*args,**kwargs):
        response=requests.Response();response.status_code=status;response.headers['Retry-After']='120';return response
    monkeypatch.setattr(requests.Session,'post',post)
    assert push.send_notification(subscription(),'a'*32)[0]==expected


def test_partial_delivery_keeps_accepted_evidence_after_frequency_change(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':False})
    for name in ('one','two'):client.post('/api/alerts/push/subscriptions',json=subscription(name))
    client.patch('/api/alerts/push/settings',json={'revision':3,'consent':True,'frequency':'daily'})
    sent=[]
    monkeypatch.setattr(push,'send_notification',lambda sub,identity:sent.append(sub['endpoint']) or ('accepted' if sub['endpoint'].endswith('one') else 'retryable_failed',300))
    jobs.process_alert_run(now=NOW)
    client.patch('/api/alerts/push/settings',json={'revision':4,'consent':True,'frequency':'weekly'})
    with database() as conn:identity=conn.execute("SELECT id FROM alert_digests WHERE channel='push'").fetchone()['id']
    jobs.send_digest(identity)
    assert client.get('/api/alerts/digests').json()['items'][0]['status']=='partially_accepted'
    monday=NOW+timedelta(days=3);monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    jobs.process_alert_run(now=monday)
    assert len(sent)==2


def test_deleting_alerts_erases_subscriptions_and_disables_push(db,monkeypatch):
    configure(monkeypatch);client=TestClient(app)
    client.post('/api/alerts/push/subscriptions',json=subscription())
    client.patch('/api/alerts/push/settings',json={'revision':1,'consent':True,'frequency':'daily'})
    assert client.delete('/api/alerts/account').status_code==200
    assert not client.get('/api/alerts/settings').json()['pushConsent']
    assert client.get('/api/alerts/push/subscriptions').json()['items']==[]


def test_rate_limited_device_retries_without_resending_accepted_device(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':False})
    for name in ('one','two'):client.post('/api/alerts/push/subscriptions',json=subscription(name))
    client.patch('/api/alerts/push/settings',json={'revision':3,'consent':True,'frequency':'daily'})
    sent=[]
    def send(sub,identity):
        sent.append(sub['endpoint']);return ('retryable_failed' if sub['endpoint'].endswith('two') and sent.count(sub['endpoint'])==1 else 'accepted',300)
    monkeypatch.setattr(push,'send_notification',send)
    jobs.process_alert_run(now=NOW)
    assert client.patch('/api/alerts/push/settings',json={'revision':4,'consent':True,'frequency':'daily'}).status_code==200
    with database() as conn:
        conn.execute("UPDATE alert_push_deliveries SET next_attempt_at=now()-interval '1 second'")
        conn.execute("UPDATE alert_digests SET next_attempt_at=now()-interval '1 second'")
    jobs.process_alert_run(now=NOW+timedelta(minutes=5))
    assert len(sent)==3 and sum(s.endswith('one') for s in sent)==1
    row=client.get('/api/alerts/digests').json()['items'][0]
    assert row['status']=='accepted' and row['devices']==[{'status':'accepted','count':2}]


def test_email_unsubscribe_does_not_disable_push(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare
    from app.services.alert_email import unsubscribe_token
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.post('/api/alerts/push/subscriptions',json=subscription())
    client.patch('/api/alerts/push/settings',json={'revision':2,'consent':True,'frequency':'daily'})
    with database() as conn:current=conn.execute('SELECT * FROM alert_users').fetchone()
    response=client.post('/notifications/unsubscribe/'+unsubscribe_token(current))
    assert response.status_code==200
    settings=client.get('/api/alerts/settings').json()
    assert not settings['consent'] and settings['pushConsent']


@pytest.mark.parametrize('before,after,parent',[(('sending','pending'),{'unknown','missed_window'},'unknown'),(('accepted','pending'),{'accepted','missed_window'},'partially_accepted'),(('accepted','accepted'),{'accepted'},'accepted')])
def test_expired_delivery_window_finalizes_each_device_without_resending(db,monkeypatch,before,after,parent):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':False})
    for name in ('one','two'):client.post('/api/alerts/push/subscriptions',json=subscription(name))
    client.patch('/api/alerts/push/settings',json={'revision':3,'consent':True,'frequency':'daily'})
    monkeypatch.setattr(push,'send_devices',lambda *args,**kwargs:('retryable_failed',None,300))
    jobs.process_alert_run(now=NOW)
    with database() as conn:
        devices=conn.execute('SELECT * FROM alert_push_deliveries ORDER BY subscription_id').fetchall()
        for device,state in zip(devices,before):conn.execute('UPDATE alert_push_deliveries SET status=%s WHERE subscription_id=%s',(state,device['subscription_id']))
    jobs.initialize(NOW+timedelta(days=1))
    with database() as conn:
        states={r['status'] for r in conn.execute('SELECT status FROM alert_push_deliveries').fetchall()}
        digest=conn.execute("SELECT status FROM alert_digests WHERE channel='push'").fetchone()
    assert states==after and digest['status']==parent


def test_push_connection_cap_and_same_endpoint_retry(db,monkeypatch):
    configure(monkeypatch);client=TestClient(app);saved=[]
    for index in range(5):
        sub=subscription(str(index));saved.append(sub)
        assert client.post('/api/alerts/push/subscriptions',json=sub).status_code==200
    assert client.post('/api/alerts/push/subscriptions',json=saved[0]).status_code==200
    assert client.post('/api/alerts/push/subscriptions',json=subscription('six')).status_code==422
    assert len(client.get('/api/alerts/push/subscriptions').json()['items'])==5


def test_interrupted_push_resumes_remaining_devices_but_not_uncertain_one(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_push as push
    client,_=prepare(monkeypatch);configure(monkeypatch)
    client.patch('/api/alerts/settings',json={'revision':2,'consent':False})
    for name in ('one','two','three'):client.post('/api/alerts/push/subscriptions',json=subscription(name))
    client.patch('/api/alerts/push/settings',json={'revision':3,'consent':True,'frequency':'daily'})
    sent=[]
    def send(sub,identity):
        sent.append(sub['endpoint'])
        if len(sent)==1:raise RuntimeError('interrupted before receipt persistence')
        return 'accepted',300
    monkeypatch.setattr(push,'send_notification',send)
    with pytest.raises(RuntimeError):jobs.process_alert_run(now=NOW)
    jobs.process_alert_run(now=NOW+timedelta(minutes=5))
    assert len(sent)==len(set(sent))==3
    row=client.get('/api/alerts/digests').json()['items'][0]
    assert row['status']=='unknown' and {r['status']:r['count'] for r in row['devices']}=={'accepted':2,'unknown':1}
