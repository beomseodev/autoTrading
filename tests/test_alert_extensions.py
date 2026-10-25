import pytest
from app.alert_rules import normalize_rule, evaluate_rule, minimum_bars


def candles(closes, volumes=None):
    return [{'close':v,'volume':(volumes or [100]*len(closes))[i]} for i,v in enumerate(closes)]


AND = {'conditions':[{'type':'price_cross','params':{'price':10,'direction':'up'}},
                     {'type':'volume_spike','params':{'period':2,'multiple':2}}]}


def test_and_same_session_and_normalization():
    params = normalize_rule('all_of',AND)
    assert params == normalize_rule('all_of',{'conditions':AND['conditions'][::-1]})
    assert minimum_bars('all_of',params) == 3
    result = evaluate_rule('all_of',params,candles([9,9,11],[100,100,200]))
    assert result['triggers'] == ['all']
    assert evaluate_rule('all_of',params,candles([9,11,11],[100,100,200]))['status']=='no_signal'
    assert evaluate_rule('all_of',params,candles([9,11]))['status']=='insufficient_data'
    assert evaluate_rule('all_of',params,candles([9,9,11],[0,0,200]))['status']=='invalid_data'


@pytest.mark.parametrize('conditions',[[AND['conditions'][0]],AND['conditions']*2,
    [AND['conditions'][0]]*2,[{'type':'all_of','params':AND},AND['conditions'][0]]])
def test_and_rejects_invalid_children(conditions):
    with pytest.raises(ValueError): normalize_rule('all_of',{'conditions':conditions})


from tests.test_stock_alert_api import db


@pytest.mark.parametrize('split',[False,True])
def test_and_api_dedup_delivery_and_split_guard(db,monkeypatch,split):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    from tests.test_stock_alert_jobs import prepare,NOW
    client,snapshot=prepare(monkeypatch)
    rule=client.get('/api/alerts/rules').json()['items'][0]
    client.delete('/api/alerts/rules/'+rule['id'])
    created=client.post('/api/alerts/rules',json={'instrumentIds':[str(rule['instrument_id'])],'type':'all_of','params':AND})
    assert created.status_code == 200
    assert client.post('/api/alerts/rules',json={'instrumentIds':[str(rule['instrument_id'])],'type':'all_of','params':{'conditions':AND['conditions'][::-1]}}).status_code==409
    snapshot['raw'][-1]['volume']=200
    if split: snapshot['splits']=[{'date':'2026-10-22','priceRatio':0.5}]
    with database() as conn:
        conn.execute("UPDATE alert_rules SET activated_at=%s WHERE kind='all_of'",(NOW.replace(day=21),))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s,fingerprint=%s',(json_value(snapshot),fingerprint(snapshot)))
    sent=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload: (sent.append(payload) or ('accepted','and-mail')))
    jobs.process_alert_run(now=NOW)
    rows=client.get('/api/alerts/events').json()['items']
    if split:
        assert not rows and not sent
        updated=client.get('/api/alerts/rules').json()['items'][0]
        assert not updated['enabled'] and updated['last_result']['status']=='needs_review'
        return
    assert len(rows)==1 and rows[0]['trigger_kind']=='all'
    assert len(sent)==1 and 'volume_spike' in sent[0]['text']


def test_price_subcondition_requires_split_review():
    from app.alert_rules import contains_price_rule
    assert contains_price_rule('all_of',normalize_rule('all_of',AND))
    assert not contains_price_rule('all_of',normalize_rule('all_of',{}))


def test_three_conditions_and_third_unmatched():
    p={'conditions':AND['conditions']+[{'type':'daily_change','params':{'thresholdPct':5,'direction':'up'}}]}
    assert evaluate_rule('all_of',p,candles([9,9,11],[100,100,200]))['triggers']==['all']
    p['conditions'][2]['params']['direction']='down'
    assert evaluate_rule('all_of',p,candles([9,9,11],[100,100,200]))['status']=='no_signal'


def dated(closes):
    return [{**b,'date':f'2026-10-{i+1:02d}'} for i,b in enumerate(candles(closes))]


def test_retest_captures_fixed_level_and_only_triggers_once():
    p={'trigger':{'type':'price_cross','params':{'price':10,'direction':'up'}},'windowSessions':5,'tolerancePct':1}
    first=evaluate_rule('retest',p,dated([9,11]))
    assert first['status']=='no_signal' and first['state']['level']==10
    # Crossing again while armed cannot move the original anchor.
    waiting=evaluate_rule('retest',p,dated([9,11,12]),state=first['state'])
    assert waiting['state']==first['state']
    hit=evaluate_rule('retest',p,dated([9,11,12,10.1]),state=waiting['state'])
    assert hit['triggers']==['retest'] and hit['state']=={}
    assert evaluate_rule('retest',p,dated([9,11,12,10.1,10.05]),state=hit['state'])['status']=='no_signal'


def test_retest_expires_at_session_boundary_and_rejects_nonsensical_source():
    p={'trigger':{'type':'price_cross','params':{'price':10}},'windowSessions':2,'tolerancePct':1}
    armed=evaluate_rule('retest',p,dated([9,11]))['state']
    assert evaluate_rule('retest',p,dated([9,11,12,10]),state=armed)['status']=='signal'
    assert evaluate_rule('retest',p,dated([9,11,12,12,10]),state=armed)['status']=='no_signal'
    with pytest.raises(ValueError):normalize_rule('retest',{'trigger':{'type':'volume_spike','params':{}}})


def test_retest_state_transaction_and_pause_reset(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    client,snapshot=prepare(monkeypatch)
    old=client.get('/api/alerts/rules').json()['items'][0]
    client.delete('/api/alerts/rules/'+old['id'])
    p={'trigger':{'type':'price_cross','params':{'price':10,'direction':'up'}},'windowSessions':5,'tolerancePct':1}
    rid=client.post('/api/alerts/rules',json={'instrumentIds':[old['instrument_id']],'type':'retest','params':p}).json()['ids'][0]
    # Previous session crossed 10; today's close returns to that fixed level.
    snapshot['raw'][-2].update(close=11.,high=11.)
    snapshot['raw'][-1].update(close=10.05,high=11.)
    with database() as conn:
        conn.execute("UPDATE alert_rules SET activated_at=%s WHERE id=%s",(NOW.replace(day=20),rid))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s,fingerprint=%s',(json_value(snapshot),fingerprint(snapshot)))
    sent=[]
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:(sent.append(payload) or ('accepted','retest-mail')))
    jobs.process_alert_run(now=NOW)
    jobs.process_alert_run(now=NOW)
    events=client.get('/api/alerts/events').json()['items']
    assert len(events)==1 and events[0]['trigger_kind']=='retest' and len(sent)==1
    assert events[0]['payload']['result']['stateBefore']['level']==10
    with database() as conn:
        conn.execute("UPDATE alert_rules SET evaluation_state=%s WHERE id=%s",(json_value({'date':'2026-10-22','level':10}),rid))
    assert client.patch('/api/alerts/rules/'+rid,json={'revision':1,'enabled':False}).status_code==200
    rule=client.get('/api/alerts/rules').json()['items'][0]
    assert rule['evaluation_state']=={}
    assert client.patch('/api/alerts/rules/'+rid,json={'revision':2,'enabled':True}).status_code==200
    assert client.get('/api/alerts/rules').json()['items'][0]['evaluation_state']=={}


def test_retest_correction_invalidates_armed_origin(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    from copy import deepcopy
    client,snapshot=prepare(monkeypatch)
    rule=client.get('/api/alerts/rules').json()['items'][0]
    p=normalize_rule('retest',{'trigger':{'type':'price_cross','params':{'price':10}}})
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='retest',params=%s,evaluation_state=%s WHERE id=%s",(json_value(p),json_value({'date':'2026-10-22','level':10.0}),rule['id']))
        cached=conn.execute('SELECT * FROM alert_daily_bars').fetchone()
        changed=deepcopy(snapshot);changed['raw'][-1]['close']=10.
        jobs.mark_corrections(conn,cached['instrument_id'],cached,changed,fingerprint(changed))
    updated=client.get('/api/alerts/rules').json()['items'][0]
    assert not updated['enabled'] and updated['last_result']['status']=='needs_review'


def test_retest_new_breakout_on_first_expired_day():
    p={'trigger':{'type':'price_cross','params':{'price':10,'direction':'up'}},'windowSessions':2,'tolerancePct':1}
    data=dated([9,11,12,9,11,10.05]);state={}
    for n in range(2,6): state=evaluate_rule('retest',p,data[:n],state=state)['state']
    assert state['date']==data[4]['date']
    assert evaluate_rule('retest',p,data,state=state)['status']=='signal'


@pytest.mark.parametrize('gap',[7,70])
def test_retest_outage_replays_state_before_delivery_window(db,monkeypatch,gap):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    client,snapshot=prepare(monkeypatch)
    anchor=snapshot['raw'][-(gap+1)]['date']
    p=normalize_rule('retest',{'trigger':{'type':'price_cross','params':{'price':10,'direction':'up'}},'windowSessions':30})
    for bar,value in zip(snapshot['raw'][-(gap+2):],[9,11,10.05]+[12]*(gap-2)+[10.05]):bar.update(close=value,high=max(12,value),low=min(9,value))
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='retest',params=%s,last_session=%s,evaluation_state=%s,activated_at=now()-interval '1 year'",(json_value(p),anchor,json_value({'date':anchor,'level':10.0})))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s,fingerprint=%s',(json_value(snapshot),fingerprint(snapshot)))
    monkeypatch.setattr(jobs,'send_payload',lambda *a:pytest.fail('An earlier first entry must not become a recent new signal'))
    jobs.process_alert_run(now=NOW)
    if gap>64:
        assert str(client.get('/api/alerts/rules').json()['items'][0]['last_session'])!=snapshot['target']
        jobs.process_alert_run(now=NOW)
    assert str(client.get('/api/alerts/rules').json()['items'][0]['last_session'])==snapshot['target']
    assert client.get('/api/alerts/events').json()['items']==[]
    assert client.get('/api/alerts/rules').json()['items'][0]['evaluation_state']=={}


@pytest.mark.parametrize('completed',[False,True])
def test_retest_intermediate_correction_is_not_accepted_as_valid(db,monkeypatch,completed):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    from copy import deepcopy
    client,snapshot=prepare(monkeypatch)
    anchor=snapshot['raw'][-4]['date']
    p=normalize_rule('retest',{'trigger':{'type':'price_cross','params':{'price':10,'direction':'up'}}})
    for bar,value in zip(snapshot['raw'][-5:],[9,11,12,12,10.05]):bar.update(close=value,high=12.,low=9.)
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='retest',params=%s,last_session=%s,evaluation_state=%s,activated_at=now()-interval '1 year'",(json_value(p),snapshot['raw'][-2]['date'],json_value({'date':anchor,'level':10.0})))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s,fingerprint=%s',(json_value(snapshot),fingerprint(snapshot)))
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','correction-retest'))
    if completed:jobs.process_alert_run(now=NOW)
    changed=deepcopy(snapshot);changed['raw'][-3]['close']=10.05
    with database() as conn:
        cached=conn.execute('SELECT * FROM alert_daily_bars').fetchone()
        jobs.mark_corrections(conn,cached['instrument_id'],cached,changed,fingerprint(changed))
    if completed:
        assert client.get('/api/alerts/events').json()['items'][0]['correction']['result']['status']=='needs_review'
    else:
        assert not client.get('/api/alerts/rules').json()['items'][0]['enabled']


def test_retest_dry_run_uses_armed_state_without_mutating(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    client,snapshot=prepare(monkeypatch)
    p=normalize_rule('retest',{'trigger':{'type':'price_cross','params':{'price':10,'direction':'up'}}})
    snapshot['raw'][-1]['close']=10.05
    state={'date':snapshot['raw'][-2]['date'],'level':10.0}
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='retest',params=%s,last_session=%s,evaluation_state=%s",(json_value(p),state['date'],json_value(state)))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s',(json_value(snapshot),))
    monkeypatch.setenv('STOCK_ALERTS_SEND_ENABLED','false')
    assert jobs.process_alert_run(now=NOW)['counts']=={'signal':1}
    assert client.get('/api/alerts/rules').json()['items'][0]['evaluation_state']==state


def test_retest_dry_run_warms_up_initial_history(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database,json_value
    client,snapshot=prepare(monkeypatch)
    snapshot['raw']=snapshot['raw'][-4:]
    p=normalize_rule('retest',{'trigger':{'type':'price_ma_cross','params':{'period':2}}})
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='retest',params=%s,activated_at=now()-interval '1 year'",(json_value(p),))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s',(json_value(snapshot),))
    monkeypatch.setenv('STOCK_ALERTS_SEND_ENABLED','false')
    assert jobs.process_alert_run(now=NOW)['counts']=={'no_signal':1}
