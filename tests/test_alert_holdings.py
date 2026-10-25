import pytest
from app.dashboard_schemas import PortfolioSnapshotRequest
from tests.test_stock_alert_api import db,META


def portfolio(**extra):
    return PortfolioSnapshotRequest(positions=[{'ticker':'SCHD','quantity':10,'averageCost':100}],cashUSD=1000,baseCurrency='USD',**extra)


def test_saved_holdings_baseline_cross_and_edit_reset():
    from app.services.alert_holdings import evaluate_holding,financial_fingerprint
    p=portfolio()
    params={'thresholdPct':5,'direction':'up','useSavedPortfolio':True}
    quotes={'SCHD':{'close':100,'currency':'USD','date':'2026-10-21'}}
    first=evaluate_holding('holding_return',params,'SCHD',p,quotes,None,{})
    assert first['status']=='no_signal' and first['reason']
    quotes['SCHD'].update(close=110,date='2026-10-22')
    result=evaluate_holding('holding_return',params,'SCHD',p,quotes,None,first['state'])
    assert result['triggers']==['up'] and result['values']['value']==pytest.approx(10)
    assert result['previousDates']=={'SCHD':'2026-10-21'}
    p.positions[0].averageCost=80
    assert evaluate_holding('holding_return',params,'SCHD',p,quotes,None,first['state'])['triggers']==[]
    before=financial_fingerprint(p)
    p.rebalanceTolerancePct=10
    assert financial_fingerprint(p)==before


@pytest.mark.parametrize('change',['quantity','averageCost','cashUSD','cashKRW','baseCurrency'])
def test_each_financial_edit_resets_baseline(change):
    from app.services.alert_holdings import evaluate_holding
    saved=portfolio();params={'thresholdPct':5,'direction':'up','useSavedPortfolio':True}
    quotes={'SCHD':{'close':100,'currency':'USD','date':'2026-10-21'}}
    prior=evaluate_holding('holding_return',params,'SCHD',saved,quotes,None,{})['state']
    if change in ('quantity','averageCost'):setattr(saved.positions[0],change,getattr(saved.positions[0],change)+1)
    elif change=='baseCurrency':saved.baseCurrency='KRW'
    else:setattr(saved,change,getattr(saved,change)+1)
    quotes['SCHD'].update(close=110,date='2026-10-22')
    assert evaluate_holding('holding_return',params,'SCHD',saved,quotes,None,prior)['triggers']==[]


def test_holding_weight_includes_cash_fx_and_every_position():
    from app.services.alert_holdings import evaluate_holding
    p=PortfolioSnapshotRequest(positions=[{'ticker':'SCHD','quantity':10,'averageCost':100},{'ticker':'005930.KS','quantity':1,'averageCost':1}],cashKRW=1300,cashUSD=1,baseCurrency='USD')
    quotes={'SCHD':{'close':100,'currency':'USD','date':'2026-10-22'},'005930.KS':{'close':1300,'currency':'KRW','date':'2026-10-22'}}
    params={'thresholdPct':50,'direction':'both','useSavedPortfolio':True}
    result=evaluate_holding('holding_weight',params,'SCHD',p,quotes,{'rate':1300,'date':'2026-10-21'},{})
    assert result['values']['value']==pytest.approx(1000/1003*100)
    from app.services.alert_data import DataPending
    with pytest.raises(DataPending):evaluate_holding('holding_weight',params,'SCHD',p,quotes,None,{})
    quotes.pop('005930.KS')
    with pytest.raises(DataPending):evaluate_holding('holding_weight',params,'SCHD',p,quotes,{'rate':1300,'date':'2026-10-21'},{})
    single=portfolio()
    assert evaluate_holding('holding_weight',params,'SCHD',single,quotes,None,{})['values']['value']==50


def test_holding_weight_crossing_equality_and_duplicate_day():
    from app.services.alert_holdings import evaluate_holding
    p=portfolio();params={'thresholdPct':60,'direction':'both','useSavedPortfolio':True}
    quotes={'SCHD':{'close':100,'currency':'USD','date':'2026-10-21'}}
    result=evaluate_holding('holding_weight',params,'SCHD',p,quotes,None,{})
    quotes['SCHD'].update(close=150,date='2026-10-22')
    result=evaluate_holding('holding_weight',params,'SCHD',p,quotes,None,result['state'])
    assert result['values']['value']==60 and result['triggers']==[]
    quotes['SCHD'].update(close=160,date='2026-10-23')
    result=evaluate_holding('holding_weight',params,'SCHD',p,quotes,None,result['state'])
    assert result['triggers']==['up']
    assert evaluate_holding('holding_weight',params,'SCHD',p,quotes,None,result['state'])['triggers']==[]
    quotes['SCHD'].update(close=100,date='2026-10-26')
    assert evaluate_holding('holding_weight',params,'SCHD',p,quotes,None,result['state'])['triggers']==['down']


def test_holding_connection_requires_explicit_choice_and_own_saved_position(db,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import alert_holdings
    client=TestClient(app)
    iid=client.post('/api/alerts/watchlist',json={'symbol':'SCHD'}).json()['instrumentId']
    body={'instrumentIds':[iid],'type':'holding_return','params':{}}
    assert client.post('/api/alerts/rules',json=body).status_code==422
    monkeypatch.setattr(alert_holdings,'read_portfolio',lambda u:(portfolio(),'r1'))
    body['params']['useSavedPortfolio']=True
    response=client.post('/api/alerts/rules',json=body)
    assert response.status_code==200,response.text
    from tests.conftest import session_cookie
    other=TestClient(app,cookies={'portfolio_session':session_cookie({'sub':'other','email':'other@gmail.com'})})
    otheriid=other.post('/api/alerts/watchlist',json={'symbol':'SCHD'}).json()['instrumentId']
    monkeypatch.setattr(alert_holdings,'read_portfolio',lambda u:(portfolio() if u['google_sub']=='google-owner' else PortfolioSnapshotRequest(),'r1'))
    assert other.post('/api/alerts/rules',json={**body,'instrumentIds':[otheriid]}).status_code==422


def test_holding_job_baselines_then_crosses_once(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs,alert_holdings
    from app.services.alert_store import database,json_value
    from datetime import timedelta
    client,snapshot=prepare(monkeypatch)
    saved=portfolio();saved.positions[0].averageCost=10
    monkeypatch.setattr(alert_holdings,'read_portfolio',lambda user:(saved,'r1'))
    iid=client.get('/api/alerts/watchlist').json()['items'][0]['instrumentId']
    response=client.post('/api/alerts/rules',json={'instrumentIds':[iid],'type':'holding_return','params':{'useSavedPortfolio':True,'thresholdPct':5,'direction':'up'}})
    rid=response.json()['ids'][0]
    with database() as conn:conn.execute("UPDATE alert_rules SET enabled=false WHERE kind='price_ma_cross'")
    monkeypatch.setattr(jobs,'send_payload',lambda *args:('accepted','holding-email'))
    jobs.process_alert_run(now=NOW)
    with database() as conn:
        rule=conn.execute('SELECT * FROM alert_rules WHERE id=%s',(rid,)).fetchone()
        assert rule['evaluation_state']['value']==pytest.approx(10)
        assert conn.execute('SELECT count(*) AS n FROM alert_events').fetchone()['n']==0
    monkeypatch.setenv('STOCK_ALERTS_SEND_ENABLED','false')
    assert jobs.process_alert_run(now=NOW)['counts']=={'no_signal':1}
    monkeypatch.setenv('STOCK_ALERTS_SEND_ENABLED','true')
    with database() as conn:
        state={**rule['evaluation_state'],'value':0,'dates':{'SCHD':'2026-10-21'}}
        conn.execute('UPDATE alert_rules SET evaluation_state=%s',(json_value(state),))
        conn.execute('DELETE FROM alert_runs')
        conn.execute('DELETE FROM alert_digests')
    jobs.process_alert_run(now=NOW)
    jobs.process_alert_run(now=NOW+timedelta(minutes=5))
    events=client.get('/api/alerts/events').json()['items']
    assert len(events)==1 and events[0]['payload']['kind']=='holding_return'
    from app.services.alert_data import fingerprint
    revised={**snapshot,'raw':[{**b,'close':12,'high':12} if b['date']=='2026-10-21' else b for b in snapshot['raw']]}
    with database() as conn:
        jobs.mark_corrections(conn,iid,{'snapshot':snapshot},revised,fingerprint(revised))
        assert conn.execute('SELECT correction FROM alert_events').fetchone()['correction']
        assert conn.execute('SELECT enabled FROM alert_rules WHERE id=%s',(rid,)).fetchone()['enabled']


def test_fx_uses_completed_utc_day_and_validates_pair(monkeypatch):
    from tests.test_stock_alert_jobs import NOW
    from app.services import alert_data
    from datetime import datetime,timezone
    response={'symbol':'USD/KRW','rate':1300,'timestamp':int(datetime(2026,10,21,23,59,59,tzinfo=timezone.utc).timestamp())}
    def get(path,params):
        assert path=='exchange_rate' and params['timezone']=='UTC' and params['date']=='2026-10-21T23:59:59'
        return response
    monkeypatch.setattr(alert_data,'provider_get',get)
    assert alert_data.load_fx_bars(NOW)['target']=='2026-10-21'
    response['timestamp']+=1
    with pytest.raises(alert_data.DataPending):alert_data.load_fx_bars(NOW)
    response['timestamp']-=1;response['symbol']='USD/JPY'
    with pytest.raises(alert_data.DataPending):alert_data.load_fx_bars(NOW)


def test_saved_financial_change_cancels_frozen_delivery(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare
    from app.services import alert_jobs as jobs,alert_holdings
    from app.services.alert_store import database,json_value
    from uuid import uuid4
    prepare(monkeypatch)
    saved=portfolio()
    old=alert_holdings.financial_fingerprint(saved)
    monkeypatch.setattr(alert_holdings,'read_portfolio',lambda user:(saved,'r2'))
    monkeypatch.setattr(jobs,'send_payload',lambda *args:pytest.fail('changed holdings must not send'))
    identity=uuid4()
    with database() as conn:
        user=conn.execute('SELECT * FROM alert_users').fetchone()
        payload={'portfolioFingerprint':old,'email':user['email'],'unsubscribeVersion':user['unsubscribe_version'],'rules':{},'envelope':{}}
        conn.execute('INSERT INTO alert_digests(id,user_id,digest_date,payload) VALUES(%s,%s,current_date,%s)',(identity,user['id'],json_value(payload)))
    saved.positions[0].quantity+=1
    jobs.send_digest(identity)
    with database() as conn:assert conn.execute('SELECT status FROM alert_digests WHERE id=%s',(identity,)).fetchone()['status']=='canceled'


def test_holdings_setup_checkpoints_and_unrelated_missing_price_does_not_block_cost(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs,alert_holdings
    from app.services.alert_store import database
    from fastapi import HTTPException
    prepare(monkeypatch)
    saved=PortfolioSnapshotRequest(positions=[{'ticker':s,'quantity':1,'averageCost':10} for s in ['SCHD','AAPL','QQQ']])
    monkeypatch.setattr(alert_holdings,'read_portfolio',lambda u:(saved,'r1'))
    def resolve(symbol):
        if symbol=='AAPL':raise HTTPException(503,'test unavailable')
        return {**META,'symbol':symbol}
    monkeypatch.setattr(alert_holdings,'resolve_instrument',resolve)
    run,token=jobs.initialize(NOW)
    with database() as conn:user=conn.execute('SELECT * FROM alert_users').fetchone()
    limited=alert_holdings.prepare_context(user,run,token,['SCHD'],weight=True,deadline=0)
    assert set(limited['instruments'])=={'SCHD'}
    context=alert_holdings.prepare_context(user,run,token,['SCHD'],weight=True)
    assert set(context['instruments'])=={'SCHD','QQQ'}
    with database() as conn:
        task=conn.execute("SELECT * FROM alert_tasks WHERE kind='evaluate'").fetchone()
        conn.execute("UPDATE alert_tasks SET status='done' WHERE kind='fetch' AND target_id=%s",(task['payload']['instrument_id'],))
    payload={**task['payload'],'kind':'holding_return','params':{'useSavedPortfolio':True,'thresholdPct':5,'direction':'up'}}
    assert alert_holdings.context_result(payload,context,run)['status']=='no_signal'
    assert all(len(s['raw'])==1 for s in context['valuationInputs'][1].values())
    monkeypatch.setattr(alert_holdings,'database',lambda:pytest.fail('confirmed inputs must be shared across rules'))
    assert alert_holdings.context_result(payload,context,run)['status']=='no_signal'
    payload['kind']='holding_weight'
    from app.services.alert_data import DataPending
    with pytest.raises(DataPending):alert_holdings.context_result(payload,context,run)


@pytest.mark.parametrize('code',['portfolio_pending','portfolio_prices_pending'])
def test_portfolio_dependency_wait_does_not_exhaust_evaluation(db,monkeypatch,code):
    from tests.test_stock_alert_jobs import prepare,NOW
    from app.services import alert_jobs as jobs
    from app.services.alert_store import database
    from app.services.alert_data import DataPending
    from datetime import timedelta
    prepare(monkeypatch)
    evaluate=jobs.evaluate_task
    def waiting(*args):raise DataPending(code)
    monkeypatch.setattr(jobs,'evaluate_task',waiting)
    for i in range(4):
        jobs.process_alert_run(now=NOW+timedelta(minutes=i*5))
        with database() as conn:conn.execute("UPDATE alert_tasks SET next_attempt_at=now()-interval '1 second'")
    with database() as conn:
        row=conn.execute("SELECT * FROM alert_tasks WHERE kind='evaluate'").fetchone()
        assert row['status']=='pending' and row['attempts']==0
    monkeypatch.setattr(jobs,'evaluate_task',evaluate)
    monkeypatch.setattr(jobs,'send_payload',lambda *args:('accepted','after-data-ready'))
    jobs.process_alert_run(now=NOW+timedelta(minutes=20))
    with database() as conn:assert conn.execute("SELECT status FROM alert_tasks WHERE kind='evaluate'").fetchone()['status']=='done'


def test_holdings_read_uses_the_actual_account_path(monkeypatch):
    from app.services import alert_holdings
    from hashlib import sha256
    paths=[]
    class Store:
        def read(self,path):
            paths.append(path)
            p=portfolio();p.positions[0].averageCost=100 if path=='dashboard.json' else 200
            return {'version':1,'portfolio':p.model_dump()},'revision'
    monkeypatch.setattr(alert_holdings,'PortfolioStore',Store)
    a=alert_holdings.read_portfolio({'google_sub':'google-owner','email':'owner@gmail.com'})[0]
    b=alert_holdings.read_portfolio({'google_sub':'other','email':'other@gmail.com'})[0]
    assert paths==['dashboard.json','users/'+sha256(b'other').hexdigest()+'/dashboard.json']
    assert a.positions[0].averageCost==100 and b.positions[0].averageCost==200


def test_fx_weekend_refreshes_previous_point_and_rejects_bad_rate(monkeypatch):
    from app.services import alert_data
    from datetime import datetime,timezone
    calls=[]
    def get(path,p):
        calls.append(p['date'])
        return {'symbol':'USD/KRW','rate':1400,'timestamp':int(datetime.fromisoformat(p['date']).replace(tzinfo=timezone.utc).timestamp())}
    monkeypatch.setattr(alert_data,'provider_get',get)
    before=alert_data.load_fx_bars(datetime(2026,10,23,23,tzinfo=timezone.utc))
    weekend=alert_data.load_fx_bars(datetime(2026,10,25,23,tzinfo=timezone.utc),before)
    assert weekend['target']=='2026-10-23' and len(weekend['raw'])==2
    assert calls[-2:]==['2026-10-22T23:59:59','2026-10-23T23:59:59']
    for bad in (0,-1,float('nan'),float('inf')):
        def invalid(path,p):return {**get(path,p),'rate':bad}
        monkeypatch.setattr(alert_data,'provider_get',invalid)
        with pytest.raises(alert_data.DataPending):alert_data.load_fx_bars(datetime(2026,10,25,23,tzinfo=timezone.utc))
