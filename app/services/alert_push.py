"""Web Push uses generic notices; ambiguous sends are never retried automatically."""
import base64
import json
import os
import re
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pywebpush import webpush,WebPushException

from app.services.alert_store import database
from app.services.alert_metrics import record


def decode(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}',value):raise ValueError('Invalid base64url')
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


def configuration():
    subject=os.getenv('WEB_PUSH_VAPID_SUBJECT','')
    if not re.fullmatch(r'mailto:[^\s@]+@[^\s@]+',subject):raise ValueError('Invalid contact')
    key=serialization.load_der_private_key(decode(os.getenv('WEB_PUSH_VAPID_PRIVATE_KEY','')),None)
    if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):raise ValueError('Invalid key')
    return key,subject


def public_key():
    try:
        key,_=configuration()
        return base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
    except (ValueError,TypeError,UnsupportedAlgorithm):return None


def validate_subscription(value):
    endpoint=value['endpoint'];url=urlsplit(endpoint)
    host=url.hostname or ''
    allowed=host in ('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com') or bool(re.fullmatch(r'[a-z0-9.-]+\.notify\.windows\.com',host))
    if not allowed or url.scheme!='https' or url.port not in (None,443) or url.username or url.password or url.fragment or any(ord(c)<33 or c=='\\' for c in endpoint):
        raise ValueError('지원되는 브라우저의 HTTPS 푸시 주소가 필요합니다.')
    keys=value['keys']
    point=decode(keys['p256dh'])
    if len(point)!=65 or point[0]!=4:raise ValueError('Invalid public key')
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),point)
    if len(decode(keys['auth']))!=16:raise ValueError('Invalid subscription secret')
    return {'endpoint':endpoint,'keys':keys}


def notification_payload(identity):
    return json.dumps({'title':'관심종목 알림','body':'새 알림이 있습니다. 로그인 후 이력을 확인해주세요.',
        'tag':'stock-alerts-'+str(identity),'url':'/alerts'},ensure_ascii=False)


class PushSession(requests.Session):
    def post(self,url,**kwargs):
        # Only first-party push hosts are accepted; never follow a provider redirect.
        return super().post(url,allow_redirects=False,**kwargs)


def send_notification(subscription,identity):
    try:
        validate_subscription(subscription)
        key,subject=configuration()
        with PushSession() as session:
            session.trust_env=False
            record('pushCalls')
            response=webpush(subscription_info=subscription,data=notification_payload(identity),
                vapid_private_key=Vapid(private_key=key),vapid_claims={'sub':subject},
                ttl=3600,timeout=10,requests_session=session,headers={'Topic':str(identity).replace('-','')})
        return ('accepted' if 200<=response.status_code<300 else 'permanent_failed'),300
    except WebPushException as exc:
        response=exc.response
        status=response.status_code if response is not None else None
        if status in (404,410):return 'expired',300
        if status==429:
            record('push429')
            try:delay=min(3600,max(1,int(response.headers.get('Retry-After','300'))))
            except ValueError:delay=300
            return 'retryable_failed',delay
        return ('unknown' if status is None or status>=500 else 'permanent_failed'),300
    except requests.RequestException:return 'unknown',300
    except (ValueError,TypeError,KeyError):return 'permanent_failed',300


def delivery_status(statuses):
    if statuses & {'pending','retryable_failed'}:return 'retryable_failed'
    if statuses & {'sending','unknown'}:return 'unknown'
    if 'accepted' in statuses:return 'accepted' if statuses=={'accepted'} else 'partially_accepted'
    if 'missed_window' in statuses:return 'missed_window'
    if 'canceled' in statuses:return 'canceled'
    return 'permanent_failed'


def finalize_devices(conn,identity,reason):
    conn.execute("""UPDATE alert_push_deliveries SET status=CASE WHEN status='sending' THEN 'unknown' ELSE %s END
        WHERE digest_id=%s AND status IN ('pending','retryable_failed','sending')""",(reason,identity))
    rows=conn.execute('SELECT status FROM alert_push_deliveries WHERE digest_id=%s',(identity,)).fetchall()
    state=delivery_status({r['status'] for r in rows}) if rows else reason
    conn.execute('UPDATE alert_digests SET status=%s,error=%s WHERE id=%s',(state,reason,identity))


def send_devices(identity,*,deadline=None):
    import time
    with database() as conn:
        devices=conn.execute('SELECT * FROM alert_push_deliveries WHERE digest_id=%s',(identity,)).fetchall()
    for device in devices:
        if deadline is not None and time.monotonic()>=deadline:break
        with database() as conn:
            row=conn.execute('SELECT * FROM alert_push_deliveries WHERE digest_id=%s AND subscription_id=%s FOR UPDATE',(identity,device['subscription_id'])).fetchone()
            if row['status']=='sending':
                conn.execute("UPDATE alert_push_deliveries SET status='unknown' WHERE digest_id=%s AND subscription_id=%s",(identity,device['subscription_id']));continue
            if row['status'] not in ('pending','retryable_failed'):continue
            sub=conn.execute('''SELECT s.* FROM alert_push_subscriptions s JOIN alert_digests d ON d.user_id=s.user_id
                JOIN alert_users u ON u.id=s.user_id
                WHERE s.id=%s AND d.id=%s AND u.push_consent AND u.push_version=(d.payload->>'pushVersion')::int''',(device['subscription_id'],identity)).fetchone()
            if not sub:
                conn.execute("UPDATE alert_push_deliveries SET status='canceled' WHERE digest_id=%s AND subscription_id=%s",(identity,device['subscription_id']));continue
            claimed=conn.execute("UPDATE alert_push_deliveries SET status='sending' WHERE digest_id=%s AND subscription_id=%s AND next_attempt_at<=now() RETURNING subscription_id",(identity,device['subscription_id'])).fetchone()
            if not claimed:continue
        state,delay=send_notification({'endpoint':sub['endpoint'],'keys':sub['keys']},identity)
        with database() as conn:
            conn.execute("UPDATE alert_push_deliveries SET status=%s,next_attempt_at=now()+(%s*interval '1 second') WHERE digest_id=%s AND subscription_id=%s",(state,delay,identity,device['subscription_id']))
            if state=='expired':conn.execute('DELETE FROM alert_push_subscriptions WHERE id=%s',(device['subscription_id'],))
    with database() as conn:
        rows=conn.execute('SELECT status,next_attempt_at FROM alert_push_deliveries WHERE digest_id=%s',(identity,)).fetchall()
    statuses={r['status'] for r in rows}
    return delivery_status(statuses),None,60
