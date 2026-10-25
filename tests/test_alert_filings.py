from datetime import date
import pytest

from tests.test_stock_alert_api import db


def sec_columns(**extra):
    return {'accessionNumber':['0000320193-26-000123'],'filingDate':['2026-10-23'],'form':['8-K'],
        'primaryDocument':['aapl-20261023.htm'],'primaryDocDescription':['Current report'],**extra}


def dart_row(**extra):
    return {'corp_code':'00126380','stock_code':'005930','corp_cls':'Y','rcept_no':'20261023000123',
        'rcept_dt':'20261023','report_nm':'[기재정정]사업보고서','rm':'정',**extra}


def test_sec_column_lengths_ids_and_document_paths_are_validated():
    from app.services.alert_filings import parse_sec
    from app.services.alert_data import DataPending
    items=parse_sec(sec_columns(),'0000320193')
    assert items[0]['sourceUrl']=='https://www.sec.gov/Archives/edgar/data/320193/000032019326000123/aapl-20261023.htm'
    assert '8-K' in items[0]['title']
    for bad in [sec_columns(form=[]),sec_columns(primaryDocument=['../outside']),sec_columns(accessionNumber=['bad'])]:
        with pytest.raises(DataPending):parse_sec(bad,'0000320193')


def test_dart_preserves_amendment_and_validates_company_identity():
    from app.services.alert_filings import parse_dart
    from app.services.alert_data import DataPending
    item=parse_dart(dart_row(),'00126380','005930')
    assert '[기재정정]' in item['title'] and item['sourceUrl'].endswith('rcpNo=20261023000123')
    with pytest.raises(DataPending):parse_dart(dart_row(corp_code='99999999'),'00126380','005930')


def test_filings_first_snapshot_baseline_then_new_ids_once_and_no_delete_inference():
    from app.services.alert_filings import evaluate_filings,parse_dart
    old=parse_dart(dart_row(),'00126380','005930')
    first=evaluate_filings([old],{},date(2026,10,23))
    assert not first['events'] and first['baseline']
    new={**old,'sourceId':'20261024000123','date':'2026-10-24','title':'분기보고서'}
    second=evaluate_filings([old,new],first['state'],date(2026,10,24))
    assert [i['sourceId'] for i in second['events']]==[new['sourceId']]
    empty=evaluate_filings([],second['state'],date(2026,10,25))
    assert not evaluate_filings([new],empty['state'],date(2026,10,26))['events']


def test_issuer_maps_reject_ambiguity_and_unsafe_xml():
    from app.services.alert_filings import parse_sec_map,parse_dart_map
    from app.services.alert_data import DataPending
    from io import BytesIO
    from zipfile import ZipFile
    assert parse_sec_map({'0':{'ticker':'BRK.B','cik_str':1067983}})=={'BRK-B':'0001067983'}
    with pytest.raises(DataPending):parse_sec_map({'0':{'ticker':'A','cik_str':1},'1':{'ticker':'A','cik_str':2}})
    def zipped(xml):
        output=BytesIO()
        with ZipFile(output,'w') as archive:archive.writestr('CORPCODE.xml',xml)
        return output.getvalue()
    data=zipped('<result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list></result>')
    assert parse_dart_map(data)=={'005930':'00126380'}
    with pytest.raises(DataPending):parse_dart_map(zipped('<!DOCTYPE result [<!ENTITY x "bad">]><result>&x;</result>'))


def test_filings_jobs_baseline_shared_fetch_and_new_report_delivery(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_filings as filings
    from app.services.alert_store import database,json_value
    from tests.test_stock_alert_api import META
    from datetime import timedelta
    client,_=prepare(monkeypatch)
    with database() as conn:
        conn.execute('DELETE FROM alert_rules')
        conn.execute('UPDATE alert_instruments SET metadata=%s',(json_value({**META,'type':'Common Stock'}),))
    iid=client.get('/api/alerts/watchlist').json()['items'][0]['instrumentId']
    response=client.post('/api/alerts/rules',json={'instrumentIds':[iid],'type':'filing','params':{}})
    assert response.status_code==200,response.text
    current=sec_columns();calls=[];sent=[]
    def request(provider,url,params=None,**kwargs):
        calls.append(url)
        if url.endswith('company_tickers.json'):return {'0':{'ticker':'SCHD','cik_str':320193}}
        return {'cik':320193,'tickers':['SCHD'],'filings':{'recent':current}}
    monkeypatch.setattr(filings,'request',request)
    monkeypatch.setattr(jobs,'load_daily_bars',lambda *a:pytest.fail('Filings do not fetch quotes'))
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:sent.append(payload) or ('accepted',str(identity)))
    jobs.process_alert_run(now=NOW)
    assert not sent and not client.get('/api/alerts/events').json()['items']
    current=sec_columns(accessionNumber=['0000320193-26-000124'],filingDate=['2026-10-24'])
    monkeypatch.setattr(jobs,'utcnow',lambda:NOW+timedelta(days=1))
    jobs.process_alert_run(now=NOW+timedelta(days=1))
    jobs.process_alert_run(now=NOW+timedelta(days=1))
    assert len(sent)==1 and '8-K' in sent[0]['text'] and 'sec.gov/Archives' in sent[0]['text']
    assert len(calls)==4


def test_filing_etf_registration_is_explicitly_unsupported(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client=TestClient(app)
    iid=client.post('/api/alerts/watchlist',json={'symbol':'SCHD'}).json()['instrumentId']
    response=client.post('/api/alerts/rules',json={'instrumentIds':[iid],'type':'filing','params':{}})
    assert response.status_code==422 and '일반주' in response.text


def test_filing_http_contract_and_provider_quota_errors(db,monkeypatch):
    import httpx
    from app.services import alert_filings as filings
    from app.services.alert_data import DataPending
    monkeypatch.setenv('SEC_USER_AGENT','Example test@example.com')
    monkeypatch.setenv('DART_API_KEY','test-key')
    calls=[]
    def get(url,**kwargs):
        calls.append(kwargs)
        return httpx.Response(200,json={'status':'020'} if 'dart' in url else {'ok':True},request=httpx.Request('GET',url))
    monkeypatch.setattr(filings.httpx,'get',get)
    assert filings.request('sec','https://data.sec.gov/submissions/CIK0000320193.json')=={'ok':True}
    assert calls[-1]['headers']['User-Agent']=='Example test@example.com'
    with pytest.raises(DataPending) as error:filings.request('dart','https://opendart.fss.or.kr/api/list.json')
    assert error.value.code=='provider_rate_limited' and error.value.delay==3600
    assert calls[-1]['params']['crtfc_key']=='test-key'


def test_dart_paging_commits_only_complete_snapshot(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_filings as filings
    from app.services.alert_store import database,json_value
    from io import BytesIO
    from zipfile import ZipFile
    client,_=prepare(monkeypatch)
    meta={'symbol':'005930.KS','providerSymbol':'005930','name':'Samsung','mic':'XKRX','currency':'KRW','calendar':'XKRX','timezone':'Asia/Seoul','type':'Common Stock'}
    with database() as conn:
        conn.execute("UPDATE alert_instruments SET symbol='005930.KS',metadata=%s",(json_value(meta),))
        conn.execute("UPDATE alert_rules SET kind='filing',params='{}'")
    archive=BytesIO()
    with ZipFile(archive,'w') as z:z.writestr('CORPCODE.xml','<result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list></result>')
    pages=[]
    def request(provider,url,params=None,**kwargs):
        if kwargs.get('binary'):return archive.getvalue()
        page=params['page_no'];pages.append(page)
        assert params['last_reprt_at']=='N' and params['corp_code']=='00126380'
        return {'status':'000','page_no':page,'total_page':2,'list':[dart_row(rcept_no='2026102300012'+str(page))]}
    monkeypatch.setattr(filings,'request',request)
    run,token=jobs.initialize(NOW)
    with database() as conn:
        mapping=conn.execute("SELECT * FROM alert_tasks WHERE kind='fetch_filing_map'").fetchone()
        task=conn.execute("SELECT * FROM alert_tasks WHERE kind='fetch_filing'").fetchone()
    assert filings.fetch_task(task,run,token) is False
    filings.fetch_task(mapping,run,token)
    filings.fetch_task(task,run,token)
    with database() as conn:
        assert not conn.execute("SELECT * FROM alert_information_cache WHERE cache_key LIKE '%%:filings'").fetchone()
        task=conn.execute('SELECT * FROM alert_tasks WHERE id=%s',(task['id'],)).fetchone()
    filings.fetch_task(task,run,token)
    with database() as conn:
        row=conn.execute("SELECT snapshot FROM alert_information_cache WHERE cache_key LIKE '%%:filings'").fetchone()
    assert pages==[1,2] and len(row['snapshot']['items'])==2


def test_filing_shared_public_fetch_keeps_user_events_separate(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from tests.conftest import session_cookie
    from tests.test_stock_alert_api import META
    from app.services import alert_filings as filings
    from app.services.alert_store import database,json_value
    from fastapi.testclient import TestClient
    from app.main import app
    from datetime import timedelta
    first,_=prepare(monkeypatch)
    second=TestClient(app,cookies={'portfolio_session':session_cookie({'sub':'other','email':'other@example.com'})})
    second.post('/api/alerts/watchlist',json={'symbol':'SCHD'})
    with database() as conn:
        conn.execute('DELETE FROM alert_rules')
        conn.execute('UPDATE alert_instruments SET metadata=%s',(json_value({**META,'type':'Common Stock'}),))
    for client in (first,second):
        iid=client.get('/api/alerts/watchlist').json()['items'][0]['instrumentId']
        assert client.post('/api/alerts/rules',json={'instrumentIds':[iid],'type':'filing','params':{}}).status_code==200
    calls=[];current=sec_columns()
    def request(provider,url,**kwargs):
        calls.append(url)
        return {'0':{'ticker':'SCHD','cik_str':320193}} if url.endswith('company_tickers.json') else {'cik':320193,'tickers':['SCHD'],'filings':{'recent':current}}
    monkeypatch.setattr(filings,'request',request)
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','first-only'))
    jobs.process_alert_run(now=NOW)
    current=sec_columns(accessionNumber=['0000320193-26-000124'],filingDate=['2026-10-24'])
    monkeypatch.setattr(jobs,'utcnow',lambda:NOW+timedelta(days=1))
    jobs.process_alert_run(now=NOW+timedelta(days=1))
    a=first.get('/api/alerts/events').json()['items'];b=second.get('/api/alerts/events').json()['items']
    assert len(calls)==4 and len(a)==len(b)==1 and a[0]['id']!=b[0]['id']
    assert first.delete('/api/alerts/rules/'+b[0]['rule_id']).status_code==404


def test_corrupted_zip_is_a_deferred_provider_result(monkeypatch):
    from app.services import alert_filings as filings
    from app.services.alert_data import DataPending
    from io import BytesIO
    from zipfile import ZipFile
    data=BytesIO()
    with ZipFile(data,'w') as archive:archive.writestr('CORPCODE.xml','<result/>')
    monkeypatch.setattr(filings.ZipFile,'read',lambda *a:(_ for _ in ()).throw(RuntimeError('unsupported encrypted archive')))
    with pytest.raises(DataPending):filings.parse_dart_map(data.getvalue())


@pytest.mark.parametrize('status,retry,code',[('800',True,'filing_unavailable'),('900',True,'filing_unavailable'),('010',False,'dart_not_configured'),('020',True,'provider_rate_limited')])
def test_dart_mapping_xml_errors_keep_retry_semantics(db,monkeypatch,status,retry,code):
    from app.services import alert_filings as filings
    from app.services.alert_data import DataPending
    import httpx
    monkeypatch.setenv('DART_API_KEY','test-only')
    monkeypatch.setattr(filings.httpx,'get',lambda url,**kw:httpx.Response(200,content=f'<result><status> {status} </status><message>test</message></result>'.encode(),request=httpx.Request('GET',url)))
    with pytest.raises(DataPending) as error:filings.request('dart','https://opendart.fss.or.kr/api/corpCode.xml',binary=True)
    assert (error.value.retry,error.value.code)==(retry,code)


def test_sec_first_baseline_uses_source_local_date(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_filings as filings
    from app.services.alert_store import database,json_value
    from tests.test_stock_alert_api import META
    from datetime import timedelta
    client,_=prepare(monkeypatch)
    with database() as conn:
        conn.execute("UPDATE alert_rules SET kind='filing',params='{}'")
        conn.execute('UPDATE alert_instruments SET metadata=%s',(json_value({**META,'type':'Common Stock'}),))
    columns=sec_columns(filingDate=['2026-10-22'])
    def request(provider,url,**kwargs):
        return {'0':{'ticker':'SCHD','cik_str':320193}} if url.endswith('company_tickers.json') else {'cik':320193,'tickers':['SCHD'],'filings':{'recent':columns}}
    monkeypatch.setattr(filings,'request',request)
    monkeypatch.setattr(jobs,'send_payload',lambda *a:('accepted','source-day'))
    jobs.process_alert_run(now=NOW)
    columns=sec_columns(accessionNumber=['0000320193-26-000124'],filingDate=['2026-10-22'])
    monkeypatch.setattr(jobs,'utcnow',lambda:NOW+timedelta(days=1))
    jobs.process_alert_run(now=NOW+timedelta(days=1))
    assert len(client.get('/api/alerts/events').json()['items'])==1


def test_dart_mapping_maintenance_recovers_in_same_run(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare,NOW,jobs
    from app.services import alert_filings as filings
    from app.services.alert_store import database,json_value
    from io import BytesIO
    from zipfile import ZipFile
    from datetime import timedelta
    import httpx
    client,_=prepare(monkeypatch)
    meta={'symbol':'005930.KS','providerSymbol':'005930','name':'Samsung','mic':'XKRX','currency':'KRW','calendar':'XKRX','timezone':'Asia/Seoul','type':'Common Stock'}
    with database() as conn:
        conn.execute("UPDATE alert_instruments SET symbol='005930.KS',metadata=%s",(json_value(meta),))
        conn.execute("UPDATE alert_rules SET kind='filing',params='{}'")
    archive=BytesIO()
    with ZipFile(archive,'w') as z:z.writestr('CORPCODE.xml','<result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list></result>')
    failures=[True]
    def response(url,**kwargs):
        if url.endswith('corpCode.xml'):
            content=b'<result><status>800</status></result>' if failures else archive.getvalue()
            failures.clear()
            return httpx.Response(200,content=content,request=httpx.Request('GET',url))
        return httpx.Response(200,json={'status':'013'},request=httpx.Request('GET',url))
    monkeypatch.setenv('DART_API_KEY','test-only')
    monkeypatch.setattr(filings.httpx,'get',response)
    jobs.process_alert_run(now=NOW)
    with database() as conn:
        row=conn.execute("SELECT * FROM alert_tasks WHERE kind='fetch_filing_map'").fetchone()
        assert row['status']=='pending' and row['error']=='filing_unavailable'
        conn.execute('UPDATE alert_tasks SET next_attempt_at=now()')
    jobs.process_alert_run(now=NOW+timedelta(minutes=5))
    assert client.get('/api/alerts/rules').json()['items'][0]['last_result']['status']=='no_signal'


@pytest.mark.parametrize('content',[b'<broken',b'\xff\xfe',b'<!DOCTYPE result><result><status>800</status></result>'])
def test_invalid_dart_xml_is_validation_failure(db,monkeypatch,content):
    from app.services import alert_filings as filings
    from app.services.alert_data import DataPending
    import httpx
    monkeypatch.setenv('DART_API_KEY','test-only')
    monkeypatch.setattr(filings.httpx,'get',lambda url,**kw:httpx.Response(200,content=content,request=httpx.Request('GET',url)))
    with pytest.raises(DataPending) as error:filings.request('dart','https://opendart.fss.or.kr/api/corpCode.xml',binary=True)
    assert error.value.code=='invalid_filing_response' and not error.value.retry
