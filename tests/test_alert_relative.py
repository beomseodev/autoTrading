import pytest
from app.alert_rules import evaluate_rule,normalize_rule,minimum_bars
from tests.test_stock_alert_api import db,META


def test_relative_price_return_difference_and_missing_reference():
    p=normalize_rule('relative_performance',{'benchmark':'spy','period':2})
    assert p['benchmark']=='SPY' and minimum_bars('relative_performance',p)==4
    bars=[{'date':f'2026-10-{i+1:02d}','close':c,'volume':100,'referenceClose':100} for i,c in enumerate([100,100,100,120])]
    r=evaluate_rule('relative_performance',p,bars)
    assert r['triggers']==['up'] and r['values']['value']==pytest.approx(20)
    bars[-1]['referenceClose']=None
    assert evaluate_rule('relative_performance',p,bars)['status'] not in ('signal','no_signal')


def test_relative_registration_requires_same_market_and_different_symbol(db,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client=TestClient(app)
    iid=client.post('/api/alerts/watchlist',json={'symbol':'SCHD'}).json()['instrumentId']
    data={'instrumentIds':[iid],'type':'relative_performance','params':{'benchmark':'SCHD'}}
    assert client.post('/api/alerts/rules',json=data).status_code==422
    monkeypatch.setattr('app.stock_alerts.resolve_instrument',lambda symbol:{**META,'symbol':symbol,'currency':'KRW','calendar':'XKRX'})
    data['params']['benchmark']='005930.KS'
    assert client.post('/api/alerts/rules',json=data).status_code==422
    monkeypatch.setattr('app.stock_alerts.resolve_instrument',lambda symbol:{**META,'symbol':symbol})
    data['params']['benchmark']='SPY'
    assert client.post('/api/alerts/rules',json=data).status_code==200
    rule=client.get('/api/alerts/rules').json()['items'][0]
    update=client.patch('/api/alerts/rules/'+rule['id'],json={'revision':rule['revision'],'params':{'benchmark':'QQQ','thresholdPct':-2}})
    assert update.status_code==200
    changed=client.get('/api/alerts/rules').json()['items'][0]
    assert changed['meaning_version']==rule['meaning_version']+1 and changed['last_session'] is None
    assert changed['benchmark_instrument_id']!=rule['benchmark_instrument_id']
    from tests.conftest import session_cookie
    other=TestClient(app,cookies={'portfolio_session':session_cookie({'sub':'other','email':'other@gmail.com'})})
    otheriid=other.post('/api/alerts/watchlist',json={'symbol':'SCHD'}).json()['instrumentId']
    assert other.post('/api/alerts/rules',json={'instrumentIds':[otheriid],'type':'relative_performance','params':{'benchmark':'QQQ'}}).status_code==200
    assert other.patch('/api/alerts/rules/'+rule['id'],json={'revision':2,'params':{'benchmark':'SPY'}}).status_code==404
    assert other.get('/api/alerts/events').json()['items']==[]


def test_relative_alignment_never_bridges_missing_session():
    from app.services.alert_jobs import evaluation_bars
    from app.services.alert_data import DataPending
    raw=[{'date':f'2026-10-{i+1:02d}','open':100,'high':120,'low':100,'close':c,'volume':10} for i,c in enumerate([100,100,100,120])]
    snapshot={'raw':raw,'splits':[],'target':raw[-1]['date']}
    ref={**snapshot,'raw':[{**b,'close':100} for b in raw]}
    for b,day in zip(raw,['2026-10-01','2026-10-02','2026-10-05','2026-10-06']):b['date']=day
    for b,day in zip(ref['raw'],['2026-10-01','2026-10-02','2026-10-05','2026-10-06']):b['date']=day
    rule={'kind':'relative_performance','params':{'period':2},'metadata':META}
    assert evaluate_rule(rule['kind'],rule['params'],evaluation_bars(rule,snapshot,ref,raw[-1]['date']))['triggers']==['up']
    ref['raw'].pop(1)
    with pytest.raises(DataPending):evaluation_bars(rule,snapshot,ref,raw[-1]['date'])
    snapshot['raw'].pop(1)
    with pytest.raises(DataPending):evaluation_bars(rule,snapshot,ref,raw[-1]['date'])


def test_relative_split_adjustment_and_negative_threshold():
    from app.services.alert_jobs import evaluation_bars
    raw=[{'date':f'2026-10-{i+1:02d}','open':c,'high':c,'low':c,'close':c,'volume':10} for i,c in enumerate([100,100,100,50])]
    snapshot={'raw':raw,'splits':[{'date':raw[-1]['date'],'priceRatio':.5}],'target':raw[-1]['date']}
    ref={**snapshot,'splits':[],'raw':[{**b,'close':100} for b in raw]}
    for b,day in zip(raw,['2026-10-01','2026-10-02','2026-10-05','2026-10-06']):b['date']=day
    for b,day in zip(ref['raw'],['2026-10-01','2026-10-02','2026-10-05','2026-10-06']):b['date']=day
    snapshot['splits'][0]['date']=raw[-1]['date']
    rule={'kind':'relative_performance','params':{'period':2,'thresholdPct':-5,'direction':'down'},'metadata':META}
    result=evaluate_rule(rule['kind'],rule['params'],evaluation_bars(rule,snapshot,ref,raw[-1]['date']))
    assert result['status']=='no_signal' and result['values']['value']==0
    snapshot['raw'][-1]['close']=45
    assert evaluate_rule(rule['kind'],rule['params'],evaluation_bars(rule,snapshot,ref,raw[-1]['date']))['triggers']==['down']


def test_relative_pipeline_waits_for_reference_and_fetches_once(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    from datetime import timedelta
    client,snapshot=prepare(monkeypatch)
    iid=client.get('/api/alerts/watchlist').json()['items'][0]['instrumentId']
    monkeypatch.setattr('app.stock_alerts.resolve_instrument',lambda symbol:{**META,'symbol':symbol,'providerSymbol':symbol})
    for period in [2,3]:
        response=client.post('/api/alerts/rules',json={'instrumentIds':[str(iid)],'type':'relative_performance','params':{'period':period}})
        assert response.status_code==200,response.text
    with database() as conn:
        conn.execute('UPDATE alert_rules SET activated_at=%s',(NOW-timedelta(days=3),))
    run,token=jobs.initialize(NOW)
    with database() as conn:
        tasks=conn.execute('SELECT * FROM alert_tasks WHERE run_id=%s',(run['id'],)).fetchall()
        assert len([t for t in tasks if t['kind']=='fetch'])==2
        conn.execute("UPDATE alert_tasks SET status='done' WHERE kind='fetch' AND target_id=%s",(iid,))
    task=next(t for t in tasks if t['payload'].get('kind')=='relative_performance')
    assert jobs.evaluate_task(task,run,token) is False
    reference={**snapshot,'raw':[{**b,'close':10} for b in snapshot['raw']]}
    refid=task['payload']['benchmark_instrument_id']
    with database() as conn:
        conn.execute('INSERT INTO alert_daily_bars(instrument_id,snapshot,fingerprint) VALUES(%s,%s,%s)',(refid,json_value(reference),fingerprint(reference)))
        conn.execute("UPDATE alert_tasks SET status='done' WHERE kind='fetch'")
    assert jobs.evaluate_task(task,run,token) is True
    with database() as conn:
        event=conn.execute('SELECT * FROM alert_events WHERE rule_id=%s',(task['target_id'],)).fetchone()
        assert event['payload']['result']['values']['value']==pytest.approx(10)
        corrected={**reference,'raw':[{**b,'close':12} if b['date']==reference['target'] else b for b in reference['raw']]}
        jobs.mark_corrections(conn,refid,{'snapshot':reference},corrected,fingerprint(corrected))
        assert conn.execute('SELECT correction FROM alert_events WHERE id=%s',(event['id'],)).fetchone()['correction']
        conn.execute('UPDATE alert_events SET correction=NULL')
        removed={**reference,'raw':reference['raw'][:-2]+reference['raw'][-1:]}
        jobs.mark_corrections(conn,refid,{'snapshot':reference},removed,fingerprint(removed))
        assert conn.execute('SELECT correction FROM alert_events WHERE id=%s',(event['id'],)).fetchone()['correction']
        conn.execute('UPDATE alert_events SET correction=NULL')
        jobs.mark_corrections(conn,refid,{'snapshot':removed},reference,fingerprint(reference))
        assert conn.execute('SELECT correction FROM alert_events WHERE id=%s',(event['id'],)).fetchone()['correction']
    old_task=next(t for t in tasks if t['payload'].get('kind')=='relative_performance' and t['id']!=task['id'])
    assert client.patch('/api/alerts/rules/'+str(old_task['target_id']),json={'revision':1,'params':{'benchmark':'QQQ','period':3}}).status_code==200
    assert jobs.evaluate_task(old_task,run,token) is True
    with database() as conn:
        assert conn.execute('SELECT status FROM alert_tasks WHERE id=%s',(old_task['id'],)).fetchone()['status']=='canceled'


def test_relative_email_explains_units_and_price_only(monkeypatch):
    from app.services.alert_email import make_payload
    monkeypatch.setenv('APP_BASE_URL','https://example.com')
    monkeypatch.setenv('ALERT_EMAIL_FROM','alerts@example.com')
    monkeypatch.setenv('ALERT_UNSUBSCRIBE_SECRET','test-secret-for-relative-email-long-enough')
    user={'id':'account','unsubscribe_version':1,'email':'test@example.com'}
    event={'trigger_kind':'up','session_date':'2026-10-22','payload':{'kind':'relative_performance','symbol':'SCHD','currency':'USD','label':'상대성과',
        'params':normalize_rule('relative_performance',{}),'result':{'values':{'value':3,'threshold':0,'close':100}}}}
    text=make_payload(user,'2026-10-23',[event])['text']
    assert '3%p' in text and 'SPY' in text and '현금배당 제외' in text


def test_evaluation_exhaustion_updates_visible_rule_status(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_data import DataPending
    from app.services.alert_store import database
    from datetime import timedelta
    client,_=prepare(monkeypatch)
    def mismatch(*args):raise DataPending('reference_session_mismatch')
    monkeypatch.setattr(jobs,'evaluate_task',mismatch)
    for i in range(3):
        jobs.process_alert_run(now=NOW+timedelta(minutes=i*5))
        with database() as conn:conn.execute("UPDATE alert_tasks SET next_attempt_at=now()-interval '1 second'")
    assert client.get('/api/alerts/rules').json()['items'][0]['last_result']['status']=='deferred'

