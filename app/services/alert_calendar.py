"""Benzinga calendar contracts; absence from a response never means cancellation."""
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from math import isfinite
import os
from uuid import uuid4,uuid5,NAMESPACE_URL

import httpx

from app.services.alert_data import DataPending,fingerprint
from app.services.alert_store import database,json_value
from app.services.alert_metrics import record

FETCH_TYPES=('fetch_earnings','fetch_dividends','fetch_removed_earnings','fetch_removed_dividends')
REMOVED_ID=uuid5(NAMESPACE_URL,'https://api.benzinga.com/api/v2.1/calendar-removed/')


def reserve_request():
    try:limit=int(os.getenv('BENZINGA_REQUESTS_PER_MINUTE','0'))
    except ValueError:limit=0
    if limit<1:raise DataPending('calendar_quota_not_configured',False)
    with database() as conn:
        conn.execute("INSERT INTO alert_external_credits(provider,minute) VALUES('benzinga',date_trunc('minute',now())) ON CONFLICT DO NOTHING")
        row=conn.execute("UPDATE alert_external_credits SET used=used+1 WHERE provider='benzinga' AND minute=date_trunc('minute',now()) AND used<%s RETURNING used",(limit,)).fetchone()
    if not row:raise DataPending('provider_rate_limited',True,60)


def provider_page(path,params,key):
    token=os.getenv('BENZINGA_API_KEY')
    if not token:raise DataPending('calendar_not_configured',False)
    reserve_request()
    try:
        record('dataCalls')
        response=httpx.get('https://api.benzinga.com'+path,params={**params,'token':token},headers={'Accept':'application/json'},timeout=20)
        if response.status_code==429:
            record('data429')
            raise DataPending('provider_rate_limited',True,min(3600,max(1,int(response.headers.get('Retry-After','60')))))
        if response.status_code>=500:raise DataPending('calendar_unavailable')
        payload=response.json()
        if response.status_code==404 and isinstance(payload,dict) and isinstance(payload.get('errors'),list) and any(isinstance(e,dict) and e.get('code')=='no_data_found' for e in payload['errors']):
            return []
        if response.status_code in (401,403):raise DataPending('calendar_not_configured',False)
        response.raise_for_status()
        if not isinstance(payload,dict) or not isinstance(payload.get(key),list):raise DataPending('invalid_calendar_response',False)
        return payload[key]
    except (httpx.HTTPError,ValueError,TypeError):raise DataPending('calendar_unavailable') from None


def enqueue(conn,rule,run_id):
    category='earnings' if rule['kind']=='earnings_event' else 'dividends'
    for kind,target,payload in [('fetch_'+category,rule['instrument_id'],{'metadata':rule['metadata']}),('fetch_removed_'+category,REMOVED_ID,{})]:
        conn.execute('INSERT INTO alert_tasks(id,run_id,kind,target_id,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (uuid4(),run_id,kind,target,json_value(payload)))


def fetch_task(task,run,token):
    from app.services.alert_jobs import fence,finish_task
    category=task['kind'].split('_')[-1]
    removed=task['kind'].startswith('fetch_removed_')
    payload=task['payload'];page=payload.get('page',0)
    cache_key=('removed' if removed else str(task['target_id']))+':'+category
    with database() as conn:
        fence(conn,run['id'],token)
        old=conn.execute('SELECT * FROM alert_information_cache WHERE cache_key=%s',(cache_key,)).fetchone()
    if removed:
        since=payload.get('since',int((old['checked_at']-timedelta(seconds=1) if old else run['cutoff']-timedelta(days=14)).timestamp()))
        params={'page':page,'pageSize':1000,'type':category,'updated':since}
        path='/api/v2.1/calendar-removed/'
    else:
        params={'page':page,'pagesize':1000,'parameters[tickers]':payload['metadata']['providerSymbol'],
            'parameters[date_from]':str(run['digest_date']-timedelta(days=400)),'parameters[date_to]':str(run['digest_date']+timedelta(days=366))}
        path='/api/'+('v2.1' if category=='earnings' else 'v2.2')+'/calendar/'+category
    rows=provider_page(path,params,'removed' if removed else category)
    accumulated=list(payload.get('items',[]))
    for row in rows:
        if removed:
            if not isinstance(row,dict) or row.get('type')!=category or not isinstance(row.get('id'),str) or not row['id'] or not isinstance(row.get('updated'),int) or row['updated']<0:
                raise DataPending('invalid_calendar_removal',False)
            accumulated.append({'id':row['id'],'updated':row['updated'],'observedDate':str(run['digest_date'])})
        else:accumulated.append(parse_item(category,row,payload['metadata']))
    with database() as conn:
        fence(conn,run['id'],token)
        if len(rows)==1000:
            conn.execute('UPDATE alert_tasks SET payload=%s WHERE id=%s',(json_value({**payload,'page':page+1,'items':accumulated,**({'since':since} if removed else {})}),task['id']))
            return
        items={}
        for item in accumulated:
            if item['id'] not in items or item['updated']>=items[item['id']]['updated']:items[item['id']]=item
        if old:
            for item in old['snapshot']['items']:
                if item['id'] not in items:
                    if removed and item.get('observedDate','0001-01-01')>=str(run['digest_date']-timedelta(days=400)):items[item['id']]=item
                    elif max(d for d in [item.get('date'),item.get('exDate'),item.get('payDate'),'0001-01-01'] if d)>=str(run['digest_date']-timedelta(days=14)):
                        items[item['id']]={**item,'stale':True}
        snapshot={'items':list(items.values()),'date':str(run['digest_date'])}
        conn.execute('''INSERT INTO alert_information_cache(cache_key,snapshot,checked_at) VALUES(%s,%s,%s)
            ON CONFLICT(cache_key) DO UPDATE SET snapshot=EXCLUDED.snapshot,checked_at=EXCLUDED.checked_at''',(cache_key,json_value(snapshot),run['cutoff']))
        finish_task(conn,task,{'status':'ready','cacheKey':cache_key})


def supported(kind,meta):
    return meta.get('currency')=='USD' and meta.get('calendar')=='XNYS' and (kind!='earnings_event' or meta.get('type')=='Common Stock')


def parse_item(category,raw,meta):
    try:
        if raw['ticker'].upper().replace('.','-')!=meta['symbol'] or raw['currency']!=meta['currency']:
            raise ValueError('wrong calendar instrument')
        identity=raw['id']
        if not isinstance(identity,str) or not identity or len(identity)>200:raise ValueError('invalid identity')
        def day(key):
            return str(date.fromisoformat(raw[key])) if raw.get(key) else None
        changed=int(raw['updated'])
        if changed<0:raise ValueError('invalid modified time')
        item={'id':identity,'category':category,'date':day('date'),'updated':changed,'currency':raw['currency'],'canceled':False}
        if category=='earnings':
            if raw.get('date_confirmed') not in (0,1):raise ValueError('unknown confirmation')
            item.update(confirmed=bool(raw['date_confirmed']),period=str(raw.get('period','')),year=int(raw['period_year']),time=str(raw.get('time',''))[:40])
        else:
            amount=float(raw['dividend']) if raw.get('dividend') not in ('',None) else None
            if amount is not None and (not isfinite(amount) or amount<0):raise ValueError('invalid dividend')
            if not isinstance(raw['confirmed'],bool):raise ValueError('unknown confirmation')
            item.update(confirmed=raw['confirmed'],exDate=day('ex_dividend_date'),payDate=day('payable_date'),
                amount=amount,payoutType=str(raw['dividend_type']),frequency=int(raw['frequency']))
        return item
    except (KeyError,ValueError,TypeError,OverflowError,AttributeError):
        raise DataPending('invalid_calendar_data',False) from None


def evaluate_calendar(kind,params,items,state,today,activated,*,splits=()):
    category='earnings' if kind=='earnings_event' else 'dividends'
    items=[i for i in items if i['category']==category]
    seen=state.get('seen',{})
    evaluated=dict(state.get('evaluated',{}))
    reminders=set(state.get('reminders',[]))
    events=[]
    field='date' if kind=='earnings_event' else params.get('dateField','exDate')
    pending=any(not i['canceled'] and (i.get('stale') or not i.get(field)) for i in items)
    next_seen={}
    for item in items:
        day=item.get('date' if kind=='earnings_event' else params.get('dateField','exDate'))
        evidence={k:v for k,v in item.items() if k not in ('updated','stale')}
        prior=None
        if kind=='dividend_change' and day:
            candidates=[i for i in items if i['id']!=item['id'] and not i['canceled'] and i['confirmed'] and i.get('exDate') and i['exDate']<day
                and not i.get('stale') and i['currency']==item['currency'] and i['payoutType']==item['payoutType'] and i['frequency']==item['frequency'] and i['amount'] and i['amount']>0]
            prior=max(candidates,key=lambda i:i['exDate']) if candidates else None
            evidence['comparison']={k:v for k,v in prior.items() if k not in ('updated','stale')} if prior else None
            evidence['splits']=[s for s in splits if prior and prior['exDate']<s['date']<=day]
        signature=fingerprint(evidence)
        previous=seen.get(item['id'])
        if kind=='dividend_change' and not item['canceled'] and (item.get('stale') or not prior) and previous:
            signature=previous['signature']
        next_seen[item['id']]={'signature':signature,'canceled':item['canceled'],'date':day}
        base={'id':item['id'],'version':signature,'item':item,'date':day}
        if kind!='dividend_change' and previous and previous['signature']!=signature and any(d and d>=str(today-timedelta(days=7)) for d in (day,previous.get('date'))):
            events.append({**base,'action':'canceled' if item['canceled'] else 'changed','previousDate':previous.get('date')})
        if item['canceled'] or item.get('stale') or not day:continue
        if kind!='dividend_change':
            key=item['id']+':'+day
            if (date.fromisoformat(day)-today).days==params['daysBefore'] and key not in reminders:
                events.append({**base,'action':'reminder'});reminders.add(key)
        elif item['confirmed'] and item['payoutType']=='Cash' and item['frequency']>0 and item['amount'] is not None and day>=str(today) and item['updated']>activated.timestamp():
            if not prior or evidence['splits']:
                pending=True;continue
            if evaluated.get(item['id'])==signature:continue
            delta=(Decimal(str(item['amount']))/Decimal(str(prior['amount']))-1)*100
            if not isfinite(float(delta)):pending=True;continue
            evaluated[item['id']]=signature
            direction='up' if delta>0 else 'down'
            if delta and abs(delta)>=Decimal(str(params['thresholdPct'])) and params['direction'] in ('both',direction):
                events.append({**base,'action':direction,'previousAmount':prior['amount'],'changePct':float(delta),'previousDate':prior['exDate']})
    # Keep state bounded to the provider window, without inferring deletions.
    kept={i['id'] for i in items}
    reminders={k for k in reminders if k.rsplit(':',1)[0] in kept}
    return {'events':events,'pending':pending,'state':{'seen':next_seen,'reminders':sorted(reminders),'evaluated':{k:v for k,v in evaluated.items() if k in kept}}}


def information(kind,event):
    item=event['item']
    labels={'reminder':'일정 사전 알림','changed':'일정·내용 변경','canceled':'일정 취소·삭제','up':'정기 현금배당 인상','down':'정기 현금배당 삭감'}
    field='실적 발표일' if kind=='earnings_event' else '배당 기준일'
    detail=f"{field}: {event['date'] or '미확정'} · {'확정' if item['confirmed'] else '잠정'}"
    if item['category']=='earnings':detail+=f" · 발표 시각 {item['time'] or '미확정'} (공급자 표기 · 시간대는 원문 확인) · 회계기간 {item['year']} {item['period'] or '미확정'}"
    if item['category']=='dividends':detail+=f" · 배당락일 {item.get('exDate') or '미확정'} · 지급일 {item.get('payDate') or '미확정'} · 주당 {item['amount'] if item['amount'] is not None else '미확정'} {item['currency']} ({item['payoutType']})"
    if event['action']=='changed':detail+=f" · 이전 기준일 {event.get('previousDate') or '미확정'}"
    if 'changePct' in event:detail+=f" · 직전 같은 주기 {event['previousAmount']} → {item['amount']} {item['currency']} ({event['changePct']:+.6g}%)"
    return {'title':labels[event['action']],'details':detail,'date':event['date'],'sourceId':event['id'],'version':event['version'],
        'comparisonDate':event.get('previousDate') if kind=='dividend_change' else None,
        'sourceUrl':'https://www.benzinga.com/'+item['category']}


def read_items(conn,payload,run):
    category='earnings' if payload['kind']=='earnings_event' else 'dividends'
    keys=[str(payload['instrument_id'])+':'+category,'removed:'+category]
    rows=conn.execute('SELECT * FROM alert_information_cache WHERE cache_key=ANY(%s)',(keys,)).fetchall()
    caches={r['cache_key']:r['snapshot'] for r in rows}
    if any(k not in caches or caches[k]['date']!=str(run['digest_date']) for k in keys):raise DataPending('calendar_snapshot_pending')
    removed={i['id']:i['updated'] for i in caches[keys[1]]['items']}
    return [{**i,'canceled':i['id'] in removed and (removed[i['id']]==0 or removed[i['id']]>=i['updated'])} for i in caches[keys[0]]['items']]


def evaluate_task(task,run,token):
    from app.services.alert_jobs import fence,finish_task
    from app.alert_rules import LABELS
    p=task['payload'];category='earnings' if p['kind']=='earnings_event' else 'dividends'
    if not supported(p['kind'],p['metadata']):raise DataPending('calendar_unsupported',False)
    with database() as conn:
        dependencies=[('fetch_'+category,p['instrument_id']),('fetch_removed_'+category,REMOVED_ID)]
        if p['kind']=='dividend_change':dependencies.append(('fetch',p['instrument_id']))
        for kind,target in dependencies:
            dep=conn.execute('SELECT status,error FROM alert_tasks WHERE run_id=%s AND kind=%s AND target_id=%s',(run['id'],kind,target)).fetchone()
            if not dep or dep['status']=='pending':return False
            if dep['status']!='done':raise DataPending(dep['error'] or 'calendar_unavailable',False)
        items=read_items(conn,p,run)
        prices=conn.execute('SELECT snapshot FROM alert_daily_bars WHERE instrument_id=%s',(p['instrument_id'],)).fetchone() if p['kind']=='dividend_change' else None
    result=evaluate_calendar(p['kind'],p['params'],items,p.get('evaluation_state',{}),run['digest_date'],datetime.fromisoformat(p['activated_at']),splits=prices['snapshot']['splits'] if prices else ())
    pending=result['pending']
    with database() as conn:
        fence(conn,run['id'],token)
        current=conn.execute('SELECT * FROM alert_rules WHERE id=%s FOR UPDATE',(task['target_id'],)).fetchone()
        if not current or not current['enabled'] or current['archived'] or current['meaning_version']!=p['meaning_version']:
            finish_task(conn,task,{'status':'canceled'},'canceled');return True
        signatures={identity:seen['signature'] for identity,seen in result['state']['seen'].items()}
        conn.execute("""UPDATE alert_events SET correction=%s WHERE user_id=%s AND rule_id=%s AND payload->'information'->>'sourceId'=ANY(%s)
            AND payload->'information'->>'version'<>(%s::jsonb)->>(payload->'information'->>'sourceId') AND correction IS NULL""",
            (json_value({'reason':'공급자 일정·내용 변경 또는 취소 확인'}),current['user_id'],current['id'],list(signatures),json_value(signatures)))
        for e in result['events']:
            info=information(p['kind'],e)
            event={'symbol':p['symbol'],'currency':p['metadata']['currency'],'kind':p['kind'],'label':LABELS[p['kind']],
                'params':p['params'],'information':info,'result':{'status':'signal','triggers':[e['action']],'values':{},'reason':info['details']}}
            conn.execute('''INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (uuid4(),current['user_id'],current['id'],current['instrument_id'],current['meaning_version'],run['digest_date'],
                 'calendar:'+e['id']+':'+e['action']+':'+e['version'],e['version'],json_value(event)))
        status='deferred' if pending else 'signal' if result['events'] else 'no_signal'
        summary={'status':status,'triggers':[],'reason':'일정·비교 자료 미확정 또는 분할로 일부 보류' if pending else f"새 일정 알림 {len(result['events'])}건"}
        conn.execute('UPDATE alert_rules SET evaluation_state=%s,last_session=%s,last_result=%s WHERE id=%s',
            (json_value(result['state']),run['digest_date'],json_value(summary),current['id']))
        finish_task(conn,task,summary,'deferred' if pending else 'done')
    return True
