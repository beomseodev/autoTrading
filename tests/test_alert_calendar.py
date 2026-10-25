from datetime import datetime,timezone,date
import pytest

from tests.test_stock_alert_api import db,META


def dividend(**extra):
    return {'id':'d2','ticker':'SCHD','exchange':'NYSE','currency':'USD','date':'2026-10-20','ex_dividend_date':'2026-10-24',
        'payable_date':'2026-10-30','dividend':'1.2','dividend_prior':'1.0','dividend_type':'Cash','frequency':4,'confirmed':True,
        'updated':1792540800,**extra}


def test_calendar_contract_rejects_wrong_symbol_and_keeps_stable_identity():
    from app.services.alert_calendar import parse_item
    item=parse_item('dividends',dividend(),META)
    assert item['id']=='d2' and item['amount']==1.2 and item['exDate']=='2026-10-24'
    from app.services.alert_data import DataPending
    with pytest.raises(DataPending):parse_item('dividends',dividend(ticker='SPY'),META)
    with pytest.raises(DataPending):parse_item('dividends',dividend(dividend='NaN'),META)


def test_reminder_changes_and_cancellation_are_distinct_and_idempotent():
    from app.services.alert_calendar import parse_item,evaluate_calendar
    item=parse_item('dividends',dividend(),META)
    p={'daysBefore':1,'dateField':'exDate'}
    activated=datetime(2026,10,19,tzinfo=timezone.utc)
    first=evaluate_calendar('dividend_event',p,[item],{},date(2026,10,23),activated)
    assert len(first['events'])==1 and first['events'][0]['action']=='reminder'
    assert evaluate_calendar('dividend_event',p,[item],first['state'],date(2026,10,23),activated)['events']==[]
    changed={**item,'exDate':'2026-10-26'}
    result=evaluate_calendar('dividend_event',p,[changed],first['state'],date(2026,10,24),activated)
    assert result['events'][0]['action']=='changed'
    canceled=evaluate_calendar('dividend_event',p,[{**changed,'canceled':True}],result['state'],date(2026,10,25),activated)
    assert canceled['events'][0]['action']=='canceled'


def test_dividend_change_compares_only_matching_confirmed_cash_frequency():
    from app.services.alert_calendar import parse_item,evaluate_calendar
    item=parse_item('dividends',dividend(),META)
    previous=parse_item('dividends',dividend(id='d1',date='2026-07-01',ex_dividend_date='2026-07-10',dividend='1.0'),META)
    p={'direction':'both','thresholdPct':0}
    activated=datetime(2026,10,19,tzinfo=timezone.utc)
    result=evaluate_calendar('dividend_change',p,[previous,item],{},date(2026,10,23),activated)
    assert len(result['events'])==1 and result['events'][0]['changePct']==pytest.approx(20)
    assert evaluate_calendar('dividend_change',p,[{**previous,'frequency':0},item],{},date(2026,10,23),activated)['events']==[]


def test_calendar_jobs_share_fetches_and_send_readable_information_once(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database
    client,_=prepare(monkeypatch)
    with database() as conn:conn.execute('DELETE FROM alert_rules')
    iid=client.get('/api/alerts/watchlist').json()['items'][0]['instrumentId']
    for field in ('exDate','payDate'):
        response=client.post('/api/alerts/rules',json={'instrumentIds':[iid],'type':'dividend_event','params':{'dateField':field,'daysBefore':1 if field=='exDate' else 7}})
        assert response.status_code==200,response.text
    calls=[];sent=[]
    def page(path,params,key):
        calls.append((path,params))
        return [] if key=='removed' else [dividend()]
    monkeypatch.setattr(calendars,'provider_page',page)
    monkeypatch.setattr(jobs,'load_daily_bars',lambda *a:pytest.fail('Schedule reminders do not fetch quotes'))
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('accepted','calendar-mail'))
    jobs.process_alert_run(now=NOW)
    jobs.process_alert_run(now=NOW)
    assert len(calls)==2 and len(sent)==1
    assert '일정 사전 알림' in sent[0]['text'] and '2026-10-24' in sent[0]['text']
    assert len(client.get('/api/alerts/events').json()['items'])==2


def test_unsupported_earnings_etf_is_not_a_provider_outage(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client=TestClient(app)
    iid=client.post('/api/alerts/watchlist',json={'symbol':'SCHD'}).json()['instrumentId']
    response=client.post('/api/alerts/rules',json={'instrumentIds':[iid],'type':'earnings_event','params':{}})
    assert response.status_code==422 and '지원' in response.text


def test_corrected_event_cancels_frozen_unsent_digest(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services.alert_store import database,json_value
    client,_=prepare(monkeypatch)
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('retryable_failed',None))
    jobs.process_alert_run(now=NOW)
    with database() as conn:
        conn.execute('UPDATE alert_events SET correction=%s',(json_value({'reason':'changed'}),))
        digest=conn.execute('SELECT id FROM alert_digests').fetchone()['id']
    monkeypatch.setattr(jobs,'send_payload',lambda *a:pytest.fail('Corrected information must not be sent'))
    jobs.send_digest(digest)
    assert client.get('/api/alerts/digests').json()['items'][0]['status']=='canceled'


def test_calendar_provider_contract_and_failures(monkeypatch):
    import httpx
    from app.services import alert_calendar as calendars
    from app.services.alert_data import DataPending
    monkeypatch.setenv('BENZINGA_API_KEY','test-only')
    monkeypatch.setattr(calendars,'reserve_request',lambda:None)
    def response(status,payload,headers=None):
        def get(url,**kwargs):
            assert url=='https://api.benzinga.com/api/v2.2/calendar/dividends'
            assert kwargs['params']['token']=='test-only' and kwargs['headers']['Accept']=='application/json'
            return httpx.Response(status,json=payload,headers=headers,request=httpx.Request('GET',url))
        monkeypatch.setattr(calendars.httpx,'get',get)
    path='/api/v2.2/calendar/dividends'
    response(200,{'dividends':[dividend()]})
    assert calendars.provider_page(path,{},'dividends')[0]['id']=='d2'
    response(404,{'errors':[{'code':'no_data_found'}]})
    assert calendars.provider_page(path,{},'dividends')==[]
    for status,payload,code in [(401,{},'calendar_not_configured'),(429,{},'provider_rate_limited'),(200,{},'invalid_calendar_response'),(404,{'errors':[None]},'calendar_unavailable')]:
        response(status,payload)
        with pytest.raises(DataPending) as error:calendars.provider_page(path,{},'dividends')
        assert error.value.code==code


def test_calendar_pagination_checkpoints_and_missing_is_not_canceled(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database,json_value
    from datetime import timedelta
    client,_=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_event',params=%s",(json_value({'dateField':'exDate','daysBefore':1}),))
    run,token=jobs.initialize(NOW)
    with database() as conn:task=conn.execute("SELECT * FROM alert_tasks WHERE kind='fetch_dividends'").fetchone()
    pages=[]
    def provider(path,params,key):
        pages.append(params['page'])
        return [dividend(id=str(i)) for i in range(1000)] if params['page']==0 else []
    monkeypatch.setattr(calendars,'provider_page',provider)
    calendars.fetch_task(task,run,token)
    with database() as conn:
        task=conn.execute('SELECT * FROM alert_tasks WHERE id=%s',(task['id'],)).fetchone()
        assert task['status']=='pending' and task['payload']['page']==1
        assert conn.execute('SELECT count(*) AS n FROM alert_information_cache').fetchone()['n']==0
    calendars.fetch_task(task,run,token)
    assert pages==[0,1]
    with database() as conn:
        assert len(conn.execute('SELECT snapshot FROM alert_information_cache').fetchone()['snapshot']['items'])==1000
        conn.execute('UPDATE alert_runs SET lease_until=now()')
    run,token=jobs.initialize(NOW+timedelta(days=1))
    monkeypatch.setattr(calendars,'provider_page',lambda *a:[])
    with database() as conn:task=conn.execute("SELECT * FROM alert_tasks WHERE run_id=%s AND kind='fetch_dividends'",(run['id'],)).fetchone()
    calendars.fetch_task(task,run,token)
    with database() as conn:
        items=conn.execute('SELECT snapshot FROM alert_information_cache').fetchone()['snapshot']['items']
        assert all(i['stale'] and not i['canceled'] for i in items)


def test_calendar_removed_feed_marks_old_events_and_sends_cancellation(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database,json_value
    from datetime import timedelta
    client,_=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_event',params=%s",(json_value({'dateField':'exDate','daysBefore':1}),))
    removed=[];sent=[]
    monkeypatch.setattr(calendars,'provider_page',lambda path,params,key:removed if key=='removed' else [dividend()])
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('accepted',str(identity)))
    jobs.process_alert_run(now=NOW)
    removed.append({'id':'d2','type':'dividends','updated':1792800000})
    monkeypatch.setattr(jobs,'utcnow',lambda:NOW+timedelta(days=1))
    jobs.process_alert_run(now=NOW+timedelta(days=1))
    events=client.get('/api/alerts/events').json()['items']
    assert len(events)==2 and sum(bool(e['correction']) for e in events)==1
    assert '일정 취소·삭제' in sent[-1]['text']


def test_dry_run_does_not_predict_dividend_increase_across_split(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services.alert_calendar import parse_item
    from app.services.alert_store import database,json_value
    client,snapshot=prepare(monkeypatch)
    snapshot['splits']=[{'date':'2026-08-01','priceRatio':0.5}]
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='dividend_change',activated_at=%s,params=%s",(datetime(2026,10,19,tzinfo=timezone.utc),json_value({'direction':'both','thresholdPct':0})))
        conn.execute('UPDATE alert_daily_bars SET snapshot=%s',(json_value(snapshot),))
        iid=conn.execute('SELECT instrument_id FROM alert_rules').fetchone()['instrument_id']
        items=[parse_item('dividends',dividend(),META),parse_item('dividends',dividend(id='d1',ex_dividend_date='2026-07-10',dividend='1.0'),META)]
        for key,values in [(str(iid)+':dividends',items),('removed:dividends',[])]:
            conn.execute('INSERT INTO alert_information_cache(cache_key,snapshot) VALUES(%s,%s)',(key,json_value({'date':'2026-10-23','items':values})))
    monkeypatch.setenv('STOCK_ALERTS_SEND_ENABLED','false')
    assert jobs.process_alert_run(now=NOW)['counts']=={'data_pending':1}
    with database() as conn:assert conn.execute('SELECT count(*) AS n FROM alert_events').fetchone()['n']==0


def test_earnings_tentative_confirmation_change_and_missing_date():
    from app.services.alert_calendar import parse_item,evaluate_calendar,information
    raw={'id':'earn-1','ticker':'SCHD','currency':'USD','date':'2026-10-24','date_confirmed':0,'period':'Q3','period_year':2026,'time':'16:00:00','updated':1792540800}
    item=parse_item('earnings',raw,{**META,'type':'Common Stock'})
    activated=datetime(2026,10,19,tzinfo=timezone.utc)
    first=evaluate_calendar('earnings_event',{'daysBefore':1},[item],{},date(2026,10,23),activated)
    assert '잠정' in information('earnings_event',first['events'][0])['details']
    confirmed=evaluate_calendar('earnings_event',{'daysBefore':1},[{**item,'confirmed':True}],first['state'],date(2026,10,23),activated)
    assert [e['action'] for e in confirmed['events']]==['changed']
    missing=evaluate_calendar('earnings_event',{'daysBefore':1},[{**item,'date':None}],{},date(2026,10,23),activated)
    assert missing['pending'] and not missing['events']


def test_calendar_unconfigured_is_visible_deferred_not_no_signal(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services.alert_store import database,json_value
    client,_=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_event',params=%s",(json_value({'dateField':'exDate','daysBefore':1}),))
    monkeypatch.setattr(jobs,'send_payload',lambda *a:pytest.fail('Unconfigured calendars must not send'))
    jobs.process_alert_run(now=NOW)
    result=client.get('/api/alerts/rules').json()['items'][0]['last_result']
    assert result['status']=='deferred' and result['reason']=='calendar_not_configured'


@pytest.mark.parametrize('amount,count',[('1.2',1),('0.8',1),('1.199999',0),('0.800001',0)])
def test_dividend_exact_twenty_percent_boundary(amount,count):
    from app.services.alert_calendar import parse_item,evaluate_calendar
    prior=parse_item('dividends',dividend(id='prior',ex_dividend_date='2026-07-01',dividend='1'),META)
    current=parse_item('dividends',dividend(dividend=amount),META)
    result=evaluate_calendar('dividend_change',{'thresholdPct':20,'direction':'both'},[prior,current],{},date(2026,10,23),datetime(2026,10,19,tzinfo=timezone.utc))
    assert len(result['events'])==count


def test_dividend_recovers_late_prior_and_tracks_prior_only_corrections():
    from app.services.alert_calendar import parse_item,evaluate_calendar
    prior=parse_item('dividends',dividend(id='prior',ex_dividend_date='2026-07-01',dividend='1'),META)
    current=parse_item('dividends',dividend(),META)
    def evaluate(items,state):return evaluate_calendar('dividend_change',{'thresholdPct':0,'direction':'both'},items,state,date(2026,10,23),datetime(2026,10,19,tzinfo=timezone.utc))
    missing=evaluate([current],{})
    assert missing['pending']
    restored=evaluate([prior,current],missing['state'])
    assert len(restored['events'])==1
    assert not evaluate([prior,current],restored['state'])['events']
    corrected=evaluate([{**prior,'amount':1.2},current],restored['state'])
    assert not corrected['events']
    assert corrected['state']['seen']['d2']['signature']!=restored['state']['seen']['d2']['signature']


def test_quote_correction_does_not_invalidate_calendar_events(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    from copy import deepcopy
    client,snapshot=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_event',params=%s",(json_value({'dateField':'exDate','daysBefore':1}),))
    monkeypatch.setattr(calendars,'provider_page',lambda path,params,key:[] if key=='removed' else [dividend()])
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','calendar-only'))
    jobs.process_alert_run(now=NOW)
    revised=deepcopy(snapshot);revised['raw'][0]['volume']=999
    with database() as conn:
        cached=conn.execute('SELECT * FROM alert_daily_bars').fetchone()
        jobs.mark_corrections(conn,cached['instrument_id'],cached,revised,fingerprint(revised))
    assert client.get('/api/alerts/events').json()['items'][0]['correction'] is None


def test_large_removed_feed_finishes_within_one_cron(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database,json_value
    client,_=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_event',params=%s",(json_value({'dateField':'exDate','daysBefore':1}),))
    pages=[]
    def provider(path,params,key):
        if key!='removed':return [dividend()]
        page=params['page'];pages.append(page)
        return [{'id':str(i),'type':'dividends','updated':1792540800} for i in range(page*1000,(page+1)*1000)] if page<9 else []
    monkeypatch.setattr(calendars,'provider_page',provider)
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','large-feed'))
    jobs.process_alert_run(now=NOW)
    assert pages==list(range(10))
    assert len(client.get('/api/alerts/events').json()['items'])==1


def test_earnings_time_change_survives_information_and_email(monkeypatch):
    from app.services.alert_calendar import parse_item,evaluate_calendar,information
    from app.services.alert_email import make_payload
    raw={'id':'e1','ticker':'SCHD','currency':'USD','date':'2026-10-24','date_confirmed':1,'period':'Q3','period_year':2026,'time':'08:00:00','updated':1792540800}
    item=parse_item('earnings',raw,META)
    def evaluate(items,state):return evaluate_calendar('earnings_event',{'daysBefore':1},items,state,date(2026,10,23),datetime(2026,10,19,tzinfo=timezone.utc))
    first=evaluate([item],{})
    second=evaluate([{**item,'time':'16:00:00','period':'Q4'}],first['state'])
    info=information('earnings_event',second['events'][0])
    assert '16:00:00' in info['details'] and '2026 Q4' in info['details']
    monkeypatch.setenv('APP_BASE_URL','https://example.com')
    monkeypatch.setenv('ALERT_EMAIL_FROM','alerts@example.com')
    monkeypatch.setenv('ALERT_UNSUBSCRIBE_SECRET','test-unsubscribe-secret-with-more-than-32-chars')
    event={'payload':{'symbol':'SCHD','label':'실적 일정','information':info}}
    envelope=make_payload({'id':'test','email':'user@example.com','unsubscribe_version':1},date(2026,10,23),[event])
    assert '16:00:00' in envelope['text'] and '2026 Q4' in envelope['text']


def test_prior_only_correction_invalidates_old_dividend_change_event(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database,json_value
    from datetime import timedelta
    client,_=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_change',activated_at=%s,params=%s",(datetime(2026,10,19,tzinfo=timezone.utc),json_value({'direction':'both','thresholdPct':0})))
    prior=dividend(id='prior',ex_dividend_date='2026-07-01',dividend='1')
    monkeypatch.setattr(calendars,'provider_page',lambda path,params,key:[] if key=='removed' else [prior,dividend()])
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','dividend-change'))
    jobs.process_alert_run(now=NOW)
    assert len(client.get('/api/alerts/events').json()['items'])==1
    prior['dividend']='1.2'
    monkeypatch.setattr(jobs,'utcnow',lambda:NOW+timedelta(days=1))
    jobs.process_alert_run(now=NOW+timedelta(days=1))
    events=client.get('/api/alerts/events').json()['items']
    assert len(events)==1 and events[0]['correction']


def test_late_split_corrects_only_affected_dividend_comparison(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_calendar as calendars
    from app.services.alert_store import database,json_value
    from app.services.alert_data import fingerprint
    from copy import deepcopy
    client,snapshot=prepare(monkeypatch)
    with database() as conn:conn.execute("UPDATE alert_rules SET kind='dividend_change',activated_at=%s,params=%s",(datetime(2026,10,19,tzinfo=timezone.utc),json_value({'direction':'both','thresholdPct':0})))
    monkeypatch.setattr(calendars,'provider_page',lambda path,params,key:[] if key=='removed' else [dividend(id='prior',ex_dividend_date='2026-07-01',dividend='1'),dividend()])
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','split-comparison'))
    jobs.process_alert_run(now=NOW)
    revised=deepcopy(snapshot);revised['splits']=[{'date':'2026-08-01','priceRatio':0.5}]
    with database() as conn:
        cached=conn.execute('SELECT * FROM alert_daily_bars').fetchone()
        jobs.mark_corrections(conn,cached['instrument_id'],cached,revised,fingerprint(revised))
    assert client.get('/api/alerts/events').json()['items'][0]['correction']['reason']=='배당 비교 구간 분할 자료 정정'


def test_current_dividend_stale_recovery_emits_only_if_not_evaluated_before():
    from app.services.alert_calendar import parse_item,evaluate_calendar
    prior=parse_item('dividends',dividend(id='prior',ex_dividend_date='2026-07-01',dividend='1'),META)
    current=parse_item('dividends',dividend(),META)
    def evaluate(item,state):return evaluate_calendar('dividend_change',{'thresholdPct':0,'direction':'both'},[prior,item],state,date(2026,10,23),datetime(2026,10,19,tzinfo=timezone.utc))
    stale=evaluate({**current,'stale':True},{})
    assert stale['pending'] and not stale['events']
    restored=evaluate(current,stale['state'])
    assert len(restored['events'])==1
    stale_again=evaluate({**current,'stale':True},restored['state'])
    assert not evaluate(current,stale_again['state'])['events']
    prior['stale']=True
    missing_prior=evaluate(current,restored['state'])
    assert missing_prior['state']['seen']['d2']==restored['state']['seen']['d2']
    del prior['stale']
    assert not evaluate(current,missing_prior['state'])['events']
