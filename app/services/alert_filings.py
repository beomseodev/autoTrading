"""SEC/DART filing identifiers and public-source summaries, without investment interpretation."""
from datetime import date,datetime,timedelta
from io import BytesIO
import os
import re
from uuid import uuid4,uuid5,NAMESPACE_URL
from zipfile import ZipFile,BadZipFile
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from app.services.alert_data import DataPending,fingerprint
from app.services.alert_store import database,json_value
from app.services.alert_metrics import record


def supported(meta):
    return meta.get('type')=='Common Stock' and (meta.get('calendar'),meta.get('currency')) in [('XNYS','USD'),('XKRX','KRW')]


def source_day(meta,now):
    return now.astimezone(ZoneInfo('America/New_York' if meta['currency']=='USD' else 'Asia/Seoul')).date()


def xml_root(raw):
    text=raw.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('XML declarations are not supported')
    return ElementTree.fromstring(text)


def check_dart_status(status):
    if status=='020':raise DataPending('provider_rate_limited',True,3600)
    if status in ('010','011','012','901'):raise DataPending('dart_not_configured',False)
    if status in ('800','900'):raise DataPending('filing_unavailable')
    if status not in ('000','013'):raise DataPending('invalid_filing_response',False)


def parse_sec_map(payload):
    try:
        result={}
        for row in payload.values():
            ticker=row['ticker'].upper().replace('.','-');cik=int(row['cik_str'])
            if not re.fullmatch(r'[A-Z0-9-]{1,30}',ticker) or not 0<cik<10**10:raise ValueError()
            value=str(cik).zfill(10)
            if ticker in result and result[ticker]!=value:raise ValueError()
            result[ticker]=value
        if not result:raise ValueError()
        return result
    except (KeyError,ValueError,TypeError,AttributeError):raise DataPending('invalid_filing_mapping',False) from None


def parse_dart_map(content):
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries=archive.infolist()
            if len(entries)!=1 or entries[0].file_size>64*1024*1024:raise ValueError()
            raw=archive.read(entries[0])
        root=xml_root(raw);result={}
        for row in root.findall('list'):
            symbol=(row.findtext('stock_code') or '').strip();corp=row.findtext('corp_code') or ''
            if not symbol:continue
            if not re.fullmatch(r'\d{6}',symbol) or not re.fullmatch(r'\d{8}',corp):raise ValueError()
            if symbol in result and result[symbol]!=corp:raise ValueError()
            result[symbol]=corp
        if not result:raise ValueError()
        return result
    except (BadZipFile,ValueError,KeyError,ElementTree.ParseError,RuntimeError,NotImplementedError,OSError):raise DataPending('invalid_filing_mapping',False) from None


def request(provider,url,params=None,*,binary=False):
    if provider=='sec':
        contact=os.getenv('SEC_USER_AGENT','')
        if '@' not in contact or ' ' not in contact or '\n' in contact or '\r' in contact:raise DataPending('sec_not_configured',False)
        headers={'User-Agent':contact,'Accept':'application/json'};limit=5;period='second'
    else:
        key=os.getenv('DART_API_KEY','')
        if not key:raise DataPending('dart_not_configured',False)
        params={**(params or {}),'crtfc_key':key};headers={};limit=60;period='minute'
    with database() as conn:
        conn.execute("INSERT INTO alert_external_credits(provider,minute) VALUES(%s,date_trunc(%s,now())) ON CONFLICT DO NOTHING",(provider,period))
        allowed=conn.execute("UPDATE alert_external_credits SET used=used+1 WHERE provider=%s AND minute=date_trunc(%s,now()) AND used<%s RETURNING used",(provider,period,limit)).fetchone()
    if not allowed:raise DataPending('provider_rate_limited',True,1 if provider=='sec' else 60)
    try:
        record('dataCalls')
        response=httpx.get(url,params=params,headers=headers,timeout=20)
        if response.status_code==429:
            record('data429');raise DataPending('provider_rate_limited',True,60)
        if response.status_code in (401,403):raise DataPending(provider+'_not_configured',False)
        response.raise_for_status()
        if len(response.content)>64*1024*1024:raise DataPending('invalid_filing_response',False)
        if binary:
            if response.content[:2]!=b'PK':
                try:status=(xml_root(response.content).findtext('status') or '').strip()
                except (ValueError,ElementTree.ParseError):raise DataPending('invalid_filing_response',False) from None
                check_dart_status(status)
                raise DataPending('invalid_filing_response',False)
            return response.content
        data=response.json()
        if not isinstance(data,dict):raise DataPending('invalid_filing_response',False)
        if provider=='dart':
            check_dart_status(data.get('status'))
        return data
    except (httpx.HTTPError,ValueError,TypeError):raise DataPending('filing_unavailable') from None


def enqueue(conn,rule,run_id):
    provider='sec' if rule['metadata']['currency']=='USD' else 'dart'
    for kind,target,payload in [('fetch_filing_map',uuid5(NAMESPACE_URL,provider+':filing-map'),{'provider':provider}),
                                ('fetch_filing',rule['instrument_id'],{'provider':provider,'metadata':rule['metadata']})]:
        conn.execute('INSERT INTO alert_tasks(id,run_id,kind,target_id,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (uuid4(),run_id,kind,target,json_value(payload)))


def fetch_task(task,run,token):
    from app.services.alert_jobs import fence,finish_task
    p=task['payload'];provider=p['provider'];mapping=task['kind']=='fetch_filing_map'
    map_key=provider+':filing-map'
    key=map_key if mapping else str(task['target_id'])+':filings'
    with database() as conn:
        fence(conn,run['id'],token)
        cached=conn.execute('SELECT snapshot FROM alert_information_cache WHERE cache_key=%s',(map_key,)).fetchone()
        if mapping and cached and cached['snapshot']['date']==str(run['digest_date']):
            finish_task(conn,task,{'status':'ready'});return True
        if not mapping:
            dependency=conn.execute("SELECT status,error FROM alert_tasks WHERE run_id=%s AND kind='fetch_filing_map' AND target_id=%s",(run['id'],uuid5(NAMESPACE_URL,provider+':filing-map'))).fetchone()
            if not dependency or dependency['status']=='pending':return False
            if dependency['status']!='done':raise DataPending(dependency['error'] or 'filing_unavailable',False)
    if mapping:
        if provider=='sec':items=parse_sec_map(request(provider,'https://www.sec.gov/files/company_tickers.json'))
        else:items=parse_dart_map(request(provider,'https://opendart.fss.or.kr/api/corpCode.xml',binary=True))
        snapshot={'date':str(run['digest_date']),'mapping':items}
    else:
        if not supported(p['metadata']):raise DataPending('filing_unsupported',False)
        symbol=p['metadata']['symbol'].split('.')[0] if provider=='dart' else p['metadata']['symbol']
        identity=cached['snapshot']['mapping'].get(symbol) if cached else None
        if not identity:raise DataPending('filing_unsupported',False)
        start=run['digest_date']-timedelta(days=14)
        if provider=='sec':
            # The official recent block contains at least a year, enough for the 14-day delivery window.
            data=request(provider,f'https://data.sec.gov/submissions/CIK{identity}.json')
            tickers=data.get('tickers')
            if str(data.get('cik','')).zfill(10)!=identity or not isinstance(tickers,list) or not all(isinstance(s,str) for s in tickers) or symbol not in [s.upper().replace('.','-') for s in tickers]:raise DataPending('invalid_filing_mapping',False)
            try:items=parse_sec(data['filings']['recent'],identity)
            except (KeyError,TypeError):raise DataPending('invalid_filing_response',False) from None
        else:
            page=p.get('page',1)
            data=request(provider,'https://opendart.fss.or.kr/api/list.json',{'corp_code':identity,'bgn_de':start.strftime('%Y%m%d'),
                'end_de':run['digest_date'].strftime('%Y%m%d'),'last_reprt_at':'N','sort':'date','sort_mth':'desc','page_no':page,'page_count':100})
            try:
                if data['status']=='013':rows=[];pages=page
                else:
                    rows=data['list'];pages=int(data['total_page'])
                    if not isinstance(rows,list) or int(data['page_no'])!=page or pages<page or len(rows)>100:raise ValueError()
                items=list(p.get('items',[]))+[parse_dart(row,identity,symbol) for row in rows]
            except (KeyError,TypeError,ValueError):raise DataPending('invalid_filing_response',False) from None
            if page<pages:
                with database() as conn:
                    fence(conn,run['id'],token)
                    conn.execute('UPDATE alert_tasks SET payload=%s WHERE id=%s',(json_value({**p,'page':page+1,'items':items}),task['id']))
                return True
        snapshot={'date':str(run['digest_date']),'items':[i for i in items if str(start)<=i['date']<=str(run['digest_date'])]}
    with database() as conn:
        fence(conn,run['id'],token)
        conn.execute('''INSERT INTO alert_information_cache(cache_key,snapshot,checked_at) VALUES(%s,%s,%s)
            ON CONFLICT(cache_key) DO UPDATE SET snapshot=EXCLUDED.snapshot,checked_at=EXCLUDED.checked_at''',(key,json_value(snapshot),run['cutoff']))
        finish_task(conn,task,{'status':'ready'})
    return True


def parse_sec(columns,cik):
    try:
        keys=['accessionNumber','filingDate','form','primaryDocument','primaryDocDescription']
        if not all(isinstance(columns[k],list) for k in keys) or len({len(columns[k]) for k in keys})!=1:raise ValueError()
        items=[]
        for accession,day,form,document,title in zip(*(columns[k] for k in keys)):
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession) or not re.fullmatch(r'[A-Za-z0-9_./-]+',document) or any(p in ('','.','..') for p in document.split('/')):raise ValueError()
            day=str(date.fromisoformat(day))
            if not isinstance(form,str) or not form or len(form)>40 or not isinstance(title,str) or len(title)>500:raise ValueError()
            items.append({'sourceId':accession,'date':day,'title':form+(' · '+title if title else ''),
                'details':'SEC 접수일 '+day,'sourceUrl':f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-','')}/{document}"})
        return items
    except (KeyError,ValueError,TypeError,AttributeError):raise DataPending('invalid_filing_data',False) from None


def parse_dart(row,corp,symbol):
    try:
        if row['corp_code']!=corp or row['stock_code']!=symbol or not re.fullmatch(r'\d{14}',row['rcept_no']):raise ValueError()
        day=str(datetime.strptime(row['rcept_dt'],'%Y%m%d').date())
        title=row['report_nm']
        if not isinstance(title,str) or not title or len(title)>500:raise ValueError()
        notes=' · 철회 관련 공시' if '철' in row.get('rm','') else ' · 관련 정정 공시 확인' if '정' in row.get('rm','') else ''
        return {'sourceId':row['rcept_no'],'date':day,'title':title,'details':'DART 접수일 '+day+notes,
            'sourceUrl':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+row['rcept_no']}
    except (KeyError,ValueError,TypeError,AttributeError):raise DataPending('invalid_filing_data',False) from None


def evaluate_filings(items,state,today,*,baseline_day=None):
    seen=dict(state.get('seen',{}));baseline=not state.get('initialized')
    events=[]
    since=state.get('since',str(baseline_day or today))
    for item in items:
        version=fingerprint(item)
        previous=seen.get(item['sourceId'])
        if not baseline and item['date']>=since and (not previous or previous['version']!=version):
            events.append({**item,'version':version,'title':('공시 내용 정정 · ' if previous else '')+item['title']})
        seen[item['sourceId']]={'version':version,'date':item['date']}
    seen={k:v for k,v in seen.items() if v['date']>=str(today-timedelta(days=14))}
    return {'events':events,'baseline':baseline,'state':{'initialized':True,'since':since,'seen':seen}}


def cached_items(conn,instrument,day):
    row=conn.execute('SELECT snapshot FROM alert_information_cache WHERE cache_key=%s',(str(instrument)+':filings',)).fetchone()
    if not row or row['snapshot']['date']!=str(day):raise DataPending('filing_snapshot_pending')
    return row['snapshot']['items']


def evaluate_task(task,run,token):
    from app.services.alert_jobs import fence,finish_task
    from app.alert_rules import LABELS
    p=task['payload']
    with database() as conn:
        dep=conn.execute("SELECT status,error FROM alert_tasks WHERE run_id=%s AND kind='fetch_filing' AND target_id=%s",(run['id'],p['instrument_id'])).fetchone()
        if not dep or dep['status']=='pending':return False
        if dep['status']!='done':raise DataPending(dep['error'] or 'filing_unavailable',False)
        items=cached_items(conn,p['instrument_id'],run['digest_date'])
    result=evaluate_filings(items,p.get('evaluation_state',{}),run['digest_date'],baseline_day=source_day(p['metadata'],run['cutoff']))
    with database() as conn:
        fence(conn,run['id'],token)
        current=conn.execute('SELECT * FROM alert_rules WHERE id=%s FOR UPDATE',(task['target_id'],)).fetchone()
        if not current or not current['enabled'] or current['archived'] or current['meaning_version']!=p['meaning_version']:
            finish_task(conn,task,{'status':'canceled'},'canceled');return True
        for item in result['events']:
            conn.execute("""UPDATE alert_events SET correction=%s WHERE user_id=%s AND rule_id=%s
                AND payload->'information'->>'sourceId'=%s AND payload->'information'->>'version'<>%s AND correction IS NULL""",
                (json_value({'reason':'공시 제목·표시 정보 정정'}),current['user_id'],current['id'],item['sourceId'],item['version']))
            payload={'symbol':p['symbol'],'currency':p['metadata']['currency'],'kind':'filing','label':LABELS['filing'],'params':{},'information':item,
                'result':{'status':'signal','triggers':['filing'],'values':{},'reason':item['title']}}
            conn.execute('''INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (uuid4(),current['user_id'],current['id'],current['instrument_id'],current['meaning_version'],run['digest_date'],
                 'filing:'+item['sourceId']+':'+item['version'],item['version'],json_value(payload)))
        summary={'status':'signal' if result['events'] else 'no_signal','triggers':[],
            'reason':'첫 조회 기준을 저장했습니다. 다음 조회부터 새로 확인한 공시를 알립니다.' if result['baseline'] else f"새 공시 {len(result['events'])}건"}
        conn.execute('UPDATE alert_rules SET evaluation_state=%s,last_session=%s,last_result=%s WHERE id=%s',
            (json_value(result['state']),run['digest_date'],json_value(summary),current['id']))
        finish_task(conn,task,summary)
    return True
