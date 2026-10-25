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
