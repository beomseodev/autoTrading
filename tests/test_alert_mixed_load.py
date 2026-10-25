"""Opt-in mixed-source acceptance; HTTP and delivery are local fakes only."""
from collections import Counter
from datetime import timedelta
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx
import pytest

from app.alert_rules import PARAMS,HOLDING_TYPES,normalize_rule
from app.dashboard_schemas import PortfolioSnapshotRequest
from app.services import alert_jobs as jobs,alert_holdings,alert_calendar,alert_filings,alert_push
from app.services.alert_data import calendar,fingerprint
from app.services.alert_store import database,json_value
from app.stock_alerts import rule_hash
from tests.test_stock_alert_api import db,META
from tests.test_stock_alert_jobs import NOW
from tests.test_alert_push import configure

pytestmark=pytest.mark.skipif(os.getenv('ALERT_RUN_LOAD_TEST')!='1',reason='opt-in mixed-source PostgreSQL load')


def test_fifty_accounts_all_rule_types_and_both_channels(db,monkeypatch):
    configure(monkeypatch)
    for key,value in {'STOCK_ALERTS_SEND_ENABLED':'true','RESEND_API_KEY':'fake','ALERT_EMAIL_FROM':'alerts@example.com',
        'ALERT_UNSUBSCRIBE_SECRET':'test-secret-with-more-than-32-characters','SEC_USER_AGENT':'Test test@example.com',
        'BENZINGA_API_KEY':'fake','BENZINGA_REQUESTS_PER_MINUTE':'600'}.items():monkeypatch.setenv(key,value)
    monday=NOW+timedelta(days=3);day=monday.astimezone(jobs.KST).date()
    dates=calendar('XNYS',2026).sessions_in_range('2025-01-01','2026-10-23')[-253:]
    raw=[{'date':str(d.date()),'open':100.,'high':110.,'low':99.,'close':110. if n==252 else 100.,'volume':300. if n==252 else 100.} for n,d in enumerate(dates)]
    snapshot={'raw':raw,'splits':[],'target':'2026-10-23','policy':'raw-split-only-v1'}
    flat={**snapshot,'raw':[{**r,'close':100.} for r in raw]}
    users=[];instruments=[];caches=[];rules=[];portfolios={};mapping={};subscriptions=[]
    for n in range(50):
        uid=uuid4();symbol=f'LOAD{n}A';benchmark=f'LOAD{n}B';iid,bid=uuid4(),uuid4()
        users.append((uid,f'mixed-{n}',f'mixed-{n}@example.com'))
        p=PortfolioSnapshotRequest(positions=[{'ticker':symbol,'quantity':10,'averageCost':100},{'ticker':benchmark,'quantity':1,'averageCost':100}],cashUSD=1000,baseCurrency='USD')
        portfolios[f'mixed-{n}']=p
        for identity,ticker,snap in [(iid,symbol,snapshot),(bid,benchmark,flat)]:
            meta={**META,'symbol':ticker,'providerSymbol':ticker,'type':'Common Stock'}
            instruments.append((identity,ticker,json_value(meta)));caches.append((identity,json_value(snap),fingerprint(snap),monday-timedelta(minutes=10)))
        mapping[str(n)]={'ticker':symbol,'cik_str':100000+n}
        for kind in PARAMS:
            custom={'price':100} if kind=='price_cross' else {'benchmark':benchmark,'thresholdPct':5} if kind=='relative_performance' else {'daysBefore':0} if kind in ('earnings_event','dividend_event') else {}
            if kind in HOLDING_TYPES:custom={'useSavedPortfolio':True,'thresholdPct':5 if kind=='holding_return' else 49}
            params=normalize_rule(kind,custom);state={}
            if kind in HOLDING_TYPES:
                quotes={s:{'close':100.,'currency':'USD','date':'2026-10-22'} for s in (symbol,benchmark)}
                state=alert_holdings.evaluate_holding(kind,params,symbol,p,quotes,None,{})['state']
            if kind=='retest':state={'date':'2026-10-22','level':110.,'direction':'up'}
            if kind=='filing':state={'initialized':True,'since':'2026-10-22','seen':{}}
            rules.append((uuid4(),uid,iid,kind,json_value(params),rule_hash(kind,params),monday-timedelta(days=30),json_value(state),bid if kind=='relative_performance' else None))
        for device in range(5):subscriptions.append((uuid4(),uid,f'https://fcm.googleapis.com/fcm/send/{n}-{device}',json_value({'p256dh':'fake','auth':'fake'})))
    with database() as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO alert_users(id,google_sub,email,consent,email_frequency,push_consent,push_frequency) VALUES(%s,%s,%s,true,'weekly',true,'weekly')",users)
            cur.executemany('INSERT INTO alert_instruments(id,symbol,metadata) VALUES(%s,%s,%s)',instruments)
            cur.executemany('INSERT INTO alert_daily_bars(instrument_id,snapshot,fingerprint,first_seen) VALUES(%s,%s,%s,%s)',caches)
            cur.executemany("INSERT INTO alert_rules(id,user_id,instrument_id,kind,params,fingerprint,activated_at,evaluation_state,benchmark_instrument_id,last_session) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'2026-10-22')",rules)
            cur.executemany('INSERT INTO alert_push_subscriptions(id,user_id,endpoint,keys) VALUES(%s,%s,%s,%s)',subscriptions)
    quotes=[];http_calls=Counter();mail_calls=Counter();push_calls=Counter();durations=[];virtual=[0.]
    original_monotonic=time.monotonic
    monkeypatch.setattr(time,'monotonic',lambda:original_monotonic()+virtual[0])
    monkeypatch.setattr(jobs.time,'sleep',lambda seconds:virtual.__setitem__(0,virtual[0]+seconds))
    monkeypatch.setattr(jobs,'legacy_section',lambda _:None)
    monkeypatch.setattr(alert_holdings,'read_portfolio',lambda user:(portfolios[user['google_sub']],'fixed'))
    monkeypatch.setattr(jobs,'load_daily_bars',lambda meta,*args:quotes.append(meta['symbol']) or (flat if meta['symbol'].endswith('B') else snapshot))
    def get(url,**kwargs):
        params=kwargs.get('params',{});http_calls[url]+=1
        if url.endswith('company_tickers.json'):data=mapping
        elif 'data.sec.gov' in url:
            cik=int(url.split('CIK')[1].split('.')[0]);symbol=f'LOAD{cik-100000}A'
            data={'cik':cik,'tickers':[symbol],'filings':{'recent':{'accessionNumber':[f'{cik:010d}-26-000001'],'filingDate':['2026-10-23'],'form':['8-K'],'primaryDocument':['report.htm'],'primaryDocDescription':['Test report']}}}
        elif 'calendar-removed' in url:
            category=params['type'];page=params['page']
            data={'removed':[{'id':f'removed-{n}','type':category,'updated':int(monday.timestamp())} for n in range(page*1000,min((page+1)*1000,9000))]}
        else:
            ticker=params['parameters[tickers]'];updated=int(monday.timestamp());category=url.split('/')[-1]
            common={'ticker':ticker,'currency':'USD','updated':updated}
            if category=='earnings':rows=[{**common,'id':'earning-'+ticker,'date':str(day),'date_confirmed':1,'period_year':2026,'period':'Q3'}]
            else:rows=[{**common,'id':f'dividend-{ticker}-{n}','date':str(day),'ex_dividend_date':str(day-timedelta(days=30)) if n==0 else str(day),
                'payable_date':str(day+timedelta(days=10)),'dividend':str(n+1),'confirmed':True,'dividend_type':'Cash','frequency':4} for n in range(2)]
            data={category:rows}
        return httpx.Response(200,json=data,request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx,'get',get)
    def mail(identity,payload):
        mail_calls[str(identity)]+=1;virtual[0]+=2
        return ('retryable_failed',None,300) if len(mail_calls)%10==0 and mail_calls[str(identity)]==1 else ('accepted',str(identity))
    def push(sub,identity):
        key=sub['endpoint'];push_calls[key]+=1;virtual[0]+=2
        return ('retryable_failed',300) if key.endswith('-4') and push_calls[key]==1 else ('accepted',300)
    monkeypatch.setattr(jobs,'send_payload',mail);monkeypatch.setattr(alert_push,'send_notification',push)
    for tick in range(12):
        current=monday+timedelta(minutes=5*tick);monkeypatch.setattr(jobs,'utcnow',lambda:current)
        # Advance persisted retry due times to model the next five-minute invocation.
        with database() as conn:
            for table in ('alert_tasks','alert_digests','alert_push_deliveries'):conn.execute(f"UPDATE {table} SET next_attempt_at=now()-interval '1 second'")
        start=time.monotonic();jobs.process_alert_run(now=current);durations.append(time.monotonic()-start)
        with database() as conn:accepted=conn.execute("SELECT count(*) AS n FROM alert_digests WHERE status='accepted'").fetchone()['n']
        if accepted==100:break
    with database() as conn:
        tasks=conn.execute("SELECT status,count(*) AS n FROM alert_tasks WHERE kind='evaluate' GROUP BY status").fetchall()
        kinds={r['kind'] for r in conn.execute("SELECT DISTINCT payload->>'kind' AS kind FROM alert_events").fetchall()}
        assert tasks==[{'status':'done','n':50*len(PARAMS)}],tasks
        assert accepted==100
        cached=conn.execute('SELECT * FROM alert_daily_bars LIMIT 1').fetchone()
        changed={**cached['snapshot'],'raw':[{**r,'close':r['close']+.1} for r in cached['snapshot']['raw']]}
        start=original_monotonic();jobs.mark_corrections(conn,cached['instrument_id'],cached,changed,fingerprint(changed));correction_seconds=original_monotonic()-start
    assert set(HOLDING_TYPES)|{'all_of','retest','relative_performance','earnings_event','dividend_event','dividend_change','filing'} <= kinds
    assert len(quotes)==len(set(quotes))==100
    assert len(push_calls)==250 and max(push_calls.values())==2
    assert max(durations)<210 and len(durations)<=9
    assert http_calls['https://api.benzinga.com/api/v2.1/calendar-removed/']==20
    Path('output').mkdir(exist_ok=True)
    Path('output/stock-alert-mixed-load.json').write_text(json.dumps({'users':50,'rules':len(rules),'instruments':100,'channels':2,'pushDevices':250,
        'priceFetches':len(quotes),'httpCalls':sum(http_calls.values()),'mailAttempts':sum(mail_calls.values()),'pushAttempts':sum(push_calls.values()),
        'callSecondsIncludingSimulatedDelay':durations,'simulatedDelaySeconds':virtual[0],'correctionSeconds':correction_seconds}),encoding='utf-8')


def test_mature_ledger_and_weekly_accumulation(db,monkeypatch):
    from tests.test_stock_alert_jobs import prepare
    from tests.test_alert_calendar import dividend
    client,snapshot=prepare(monkeypatch)
    monkeypatch.setattr(jobs,'send_payload',lambda identity,payload:('accepted',str(identity)))
    jobs.process_alert_run(now=NOW)
    monday=NOW+timedelta(days=3)
    monkeypatch.setattr(jobs,'utcnow',lambda:monday)
    with database() as conn:
        seed=conn.execute('SELECT * FROM alert_events').fetchone()
        rule=conn.execute('SELECT * FROM alert_rules').fetchone()
        conn.execute("UPDATE alert_users SET email_frequency='weekly'")
        conn.execute('DELETE FROM alert_digests')
        conn.execute('DELETE FROM alert_events')
        rule_ids=[rule['id']]+[uuid4() for _ in range(99)]
        with conn.cursor() as cur:
            cur.executemany('''INSERT INTO alert_rules(id,user_id,instrument_id,kind,params,fingerprint,activated_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',[(rid,rule['user_id'],rule['instrument_id'],rule['kind'],json_value(rule['params']),str(rid),NOW-timedelta(days=400)) for rid in rule_ids[1:]])
            rows=[(uuid4(),rule['user_id'],rid,rule['instrument_id'],monday.date()-timedelta(days=day),json_value(seed['payload']),monday-timedelta(days=day)) for day in range(1,181) for rid in rule_ids]
            cur.executemany('''INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload,created_at)
                VALUES(%s,%s,%s,%s,1,%s,'up','mature',%s,%s)''',rows)
            old_ids=[str(uuid4()) for _ in range(700)]
            ledger=[(uuid4(),rule['user_id'],monday.date()-timedelta(days=day),channel,json_value({'eventIds':old_ids+([str(rows[0][0])] if day==1 else [])})) for day in range(1,401) for channel in ('email','push')]
            cur.executemany("INSERT INTO alert_digests(id,user_id,digest_date,channel,payload,status,attempts) VALUES(%s,%s,%s,%s,%s,'accepted',1)",ledger)
    run,token=jobs.initialize(monday)
    with database() as conn:conn.execute("UPDATE alert_tasks SET status='done' WHERE run_id=%s",(run['id'],))
    start=time.monotonic();jobs.prepare_digests(run,token,monday,start);prepare_seconds=time.monotonic()-start
    with database() as conn:
        digest=conn.execute('SELECT * FROM alert_digests WHERE digest_date=%s',(run['digest_date'],)).fetchone()
        assert len(digest['payload']['eventIds'])==699
        assert str(rows[0][0]) not in digest['payload']['eventIds']
        assert set(digest['payload']['eventIds'])=={str(r[0]) for r in rows[1:700]}
        cached=conn.execute('SELECT * FROM alert_daily_bars').fetchone()
        start=time.monotonic();jobs.mark_corrections(conn,rule['instrument_id'],cached,{**snapshot,'raw':[{**r,'close':r['close']+.1} for r in snapshot['raw']]},'corrected');correction_seconds=time.monotonic()-start
    # Stress the full 400-day comparison window even though regular dividends are much sparser.
    items=[alert_calendar.parse_item('dividends',dividend(id=f'd-{n}',ex_dividend_date=str(monday.date()-timedelta(days=399-n)),dividend='1.2' if n==399 else '1.0',updated=int(monday.timestamp())),META) for n in range(400)]
    start=time.monotonic()
    for _ in range(50):
        evaluated=alert_calendar.evaluate_calendar('dividend_change',{'direction':'both','thresholdPct':0},items,{},monday.date(),monday-timedelta(days=1))
        assert len(evaluated['events'])==1
    comparison_seconds=time.monotonic()-start
    assert prepare_seconds<30 and correction_seconds<10 and comparison_seconds<30
    Path('output').mkdir(exist_ok=True)
    Path('output/stock-alert-mature-load.json').write_text(json.dumps({'users':1,'rules':100,'eventHistoryDays':180,'events':18000,'ledgerDays':400,'ledgerRows':800,
        'weeklyCandidates':700,'alreadySent':1,'prepared':699,'prepareSeconds':prepare_seconds,'correctionSeconds':correction_seconds,
        'calendarItems':400,'calendarEvaluations':50,'calendarSeconds':comparison_seconds,'emailBytes':len(json.dumps(digest['payload']['envelope'],ensure_ascii=False).encode())}),encoding='utf-8')
