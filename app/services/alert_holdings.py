"""Account-owned holdings, valued only from confirmed morning inputs."""
from math import isfinite
from datetime import datetime
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException

from app.dashboard_schemas import SavedDashboard
from app.services.alert_data import DataPending, fingerprint, resolve_instrument
from app.services.alert_store import database, json_value
from app.services.portfolio_store import PortfolioStore


def read_portfolio(user):
    from app.dashboard_state import account_dashboard_path
    raw,revision=PortfolioStore().read(account_dashboard_path({'sub':user['google_sub'],'email':user['email']}))
    if not raw:raise DataPending('saved_portfolio_missing',False)
    portfolio=SavedDashboard.model_validate(raw).portfolio
    for p in portfolio.positions:
        if not p.ticker.endswith(('.KS','.KQ')):p.ticker=p.ticker.replace('.','-')
    if len({p.ticker for p in portfolio.positions})!=len(portfolio.positions):raise DataPending('duplicate_saved_symbol',False)
    return portfolio,revision


def financial_fingerprint(portfolio):
    return fingerprint({'positions':sorted([{'ticker':p.ticker,'quantity':p.quantity,'averageCost':p.averageCost} for p in portfolio.positions],key=lambda p:p['ticker']),
        'cashKRW':portfolio.cashKRW,'cashUSD':portfolio.cashUSD,'baseCurrency':portfolio.baseCurrency})


def validate_connection(user,symbols):
    try:
        portfolio,_=read_portfolio(user)
    except (DataPending,HTTPException,ValueError):
        raise HTTPException(422,'먼저 이 계정의 대시보드에 보유자산을 저장해주세요.') from None
    if not set(symbols)<={p.ticker for p in portfolio.positions}:
        raise HTTPException(422,'계정에 저장된 보유 종목만 연결할 수 있습니다.')
    return portfolio


def prepare_context(user,run,token,symbols,weight=False,deadline=float('inf')):
    from app.services.alert_jobs import fence
    portfolio,revision=read_portfolio(user)
    if weight:symbols=[p.ticker for p in portfolio.positions]
    with database() as conn:
        rows=conn.execute('SELECT * FROM alert_instruments WHERE symbol=ANY(%s)',(symbols,)).fetchall()
    known={r['symbol']:r for r in rows}
    for symbol in sorted(set(symbols)-known.keys()):
        if monotonic()>=deadline:break
        try:meta=resolve_instrument(symbol)
        except (HTTPException,DataPending):continue
        with database() as conn:
            fence(conn,run['id'],token)
            known[symbol]=conn.execute('''INSERT INTO alert_instruments(id,symbol,metadata) VALUES(%s,%s,%s)
                ON CONFLICT(symbol) DO UPDATE SET metadata=EXCLUDED.metadata RETURNING *''',(uuid4(),meta['symbol'],json_value(meta))).fetchone()
    missing=[]
    currencies={p['metadata']['currency'] for p in known.values()}
    if portfolio.cashUSD:currencies.add('USD')
    if portfolio.cashKRW:currencies.add('KRW')
    if weight and len(currencies)>1:
        missing.append({'symbol':'USD/KRW','providerSymbol':'USD/KRW','type':'Forex','currency':'KRW'})
    with database() as conn:
        fence(conn,run['id'],token)
        for meta in missing:
            row=conn.execute('''INSERT INTO alert_instruments(id,symbol,metadata) VALUES(%s,%s,%s)
                ON CONFLICT(symbol) DO UPDATE SET metadata=EXCLUDED.metadata RETURNING *''',(uuid4(),meta['symbol'],json_value(meta))).fetchone()
            known[meta['symbol']]=row
        for row in known.values():
            conn.execute('''INSERT INTO alert_tasks(id,run_id,kind,target_id,payload) VALUES(%s,%s,'fetch',%s,%s)
                ON CONFLICT DO NOTHING''',(uuid4(),run['id'],row['id'],json_value(row['metadata'])))
    return {'user':user,'portfolio':portfolio,'revision':revision,'instruments':known}


def context_result(payload,context,run):
    from app.services.alert_data import expected_session,session_close,expected_fx_day
    instruments={s:r for s,r in context['instruments'].items() if payload['kind']=='holding_weight' or s==payload['symbol']}
    ids=[r['id'] for r in instruments.values()]
    if 'valuationInputs' not in context:
        all_ids=[r['id'] for r in context['instruments'].values()]
        with database() as conn:
            tasks=conn.execute("SELECT target_id,status FROM alert_tasks WHERE run_id=%s AND kind='fetch' AND target_id=ANY(%s)",(run['id'],all_ids)).fetchall() if run.get('id') else []
            rows=conn.execute("""SELECT instrument_id,snapshot->'raw'->-1 AS bar,snapshot->'splits' AS splits,snapshot->>'target' AS target
                FROM alert_daily_bars WHERE instrument_id=ANY(%s)""",(all_ids,)).fetchall()
        context['valuationInputs']=({str(t['target_id']):t['status'] for t in tasks},
            {str(r['instrument_id']):{'raw':[r['bar']],'splits':r['splits'],'target':r['target']} for r in rows})
    states,snapshots=context['valuationInputs']
    if run.get('id'):
        if any(states.get(str(i)) not in ('pending','done',None) for i in ids):raise DataPending('portfolio_prices_unavailable',False)
        if any(states.get(str(i))!='done' for i in ids):raise DataPending('portfolio_prices_pending')
    quotes,fx={},None
    split=False
    activated=datetime.fromisoformat(payload['activated_at']) if isinstance(payload['activated_at'],str) else payload['activated_at']
    for symbol,item in instruments.items():
        snap=snapshots.get(str(item['id']))
        if not snap:raise DataPending('portfolio_prices_pending')
        if item['metadata'].get('type')=='Forex':
            if snap['target']!=str(expected_fx_day(run['cutoff'])):raise DataPending('fx_stale')
            fx={'date':snap['target'],'rate':snap['raw'][-1]['close']}
            continue
        if snap['target']!=str(expected_session(item['metadata'],run['cutoff'])):raise DataPending('portfolio_prices_stale')
        quotes[symbol]={'close':snap['raw'][-1]['close'],'currency':item['metadata']['currency'],'date':snap['target']}
        if payload['kind']=='holding_weight' or symbol==payload['symbol']:
            split=split or any(session_close(item['metadata'],datetime.fromisoformat(a['date']).date())>activated for a in snap['splits'])
    if split:return {'status':'needs_review','triggers':[],'reason':'분할 이후 저장 수량·평단을 확인하고 규칙을 재개해주세요.'}
    result=evaluate_holding(payload['kind'],payload['params'],payload['symbol'],context['portfolio'],quotes,fx,payload.get('evaluation_state',{}))
    result['state']['sourceIds']=[str(r['id']) for s,r in instruments.items() if s in result['state']['dates']]
    return result


def dry_result(rule,cutoff):
    with database() as conn:
        user=conn.execute('SELECT * FROM alert_users WHERE id=%s',(rule['user_id'],)).fetchone()
    portfolio,revision=read_portfolio(user)
    symbols=[p.ticker for p in portfolio.positions] if rule['kind']=='holding_weight' else [rule['symbol']]
    if rule['kind']=='holding_weight':symbols.append('USD/KRW')
    with database() as conn:
        rows=conn.execute('SELECT * FROM alert_instruments WHERE symbol=ANY(%s)',(symbols,)).fetchall()
    currencies={r['metadata']['currency'] for r in rows if r['metadata'].get('type')!='Forex'}
    if portfolio.cashKRW:currencies.add('KRW')
    if portfolio.cashUSD:currencies.add('USD')
    if len(currencies)<=1:rows=[r for r in rows if r['metadata'].get('type')!='Forex']
    return context_result(rule,{'portfolio':portfolio,'instruments':{r['symbol']:r for r in rows}}, {'cutoff':cutoff})


def evaluate_task(task,run,token,context):
    from app.services.alert_jobs import fence,finish_task
    from app.alert_rules import LABELS
    if isinstance(context,Exception):raise context
    if context is None:raise DataPending('portfolio_pending')
    payload=task['payload']
    result=context_result(payload,context,run)
    # Delivery rechecks the financial fingerprint without one Blob read per rule.
    latest=context['portfolio']
    with database() as conn:
        fence(conn,run['id'],token)
        current=conn.execute('SELECT * FROM alert_rules WHERE id=%s FOR UPDATE',(task['target_id'],)).fetchone()
        if not current or not current['enabled'] or current['archived'] or current['meaning_version']!=payload['meaning_version']:
            finish_task(conn,task,{'status':'canceled'},'canceled');return True
        if result['status']=='needs_review':
            conn.execute('UPDATE alert_rules SET enabled=false,revision=revision+1,last_result=%s WHERE id=%s',(json_value(result),current['id']))
            finish_task(conn,task,result,'deferred');return True
        for trigger in result['triggers']:
            event={'kind':payload['kind'],'symbol':payload['symbol'],'currency':payload['metadata']['currency'],'label':LABELS[payload['kind']],
                'params':payload['params'],'result':result,'portfolioFingerprint':financial_fingerprint(latest),'sourceIds':result['state']['sourceIds']}
            conn.execute('''INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (uuid4(),current['user_id'],current['id'],current['instrument_id'],current['meaning_version'],run['digest_date'],trigger,fingerprint(result),json_value(event)))
        conn.execute('UPDATE alert_rules SET last_result=%s,last_session=%s,evaluation_state=%s WHERE id=%s',
            (json_value(result),run['digest_date'],json_value(result['state']),current['id']))
        finish_task(conn,task,{'status':'evaluated'})
    return True


def evaluate_holding(kind,params,symbol,portfolio,quotes,fx,state):
    if not params['useSavedPortfolio']:raise DataPending('portfolio_not_connected',False)
    position=next((p for p in portfolio.positions if p.ticker==symbol),None)
    if not position:raise DataPending('holding_removed',False)
    positions=portfolio.positions if kind=='holding_weight' else [position]
    if any(p.ticker not in quotes for p in positions):raise DataPending('portfolio_prices_pending')
    for p in positions:
        q=quotes[p.ticker]
        if not isfinite(q['close']) or q['close']<=0:raise DataPending('invalid_portfolio_price',False)
    if kind=='holding_return':
        if position.averageCost<=0:raise DataPending('average_cost_missing',False)
        value=(quotes[symbol]['close']/position.averageCost-1)*100
    else:
        currencies={quotes[p.ticker]['currency'] for p in positions}
        if portfolio.cashKRW:currencies.add('KRW')
        if portfolio.cashUSD:currencies.add('USD')
        base=next(iter(currencies))
        if len(currencies)>1 and (not fx or not isfinite(fx['rate']) or fx['rate']<=0):raise DataPending('fx_pending')
        def convert(value,currency):
            if not value or currency==base:return value
            return value*fx['rate'] if currency=='USD' else value/fx['rate']
        values={p.ticker:convert(p.quantity*quotes[p.ticker]['close'],quotes[p.ticker]['currency']) for p in positions}
        total=sum(values.values())+convert(portfolio.cashUSD,'USD')+convert(portfolio.cashKRW,'KRW')
        if not isfinite(total) or total<=0:raise DataPending('invalid_portfolio_value',False)
        value=values[symbol]/total*100
    if not isfinite(value):raise DataPending('invalid_portfolio_value',False)
    revision=financial_fingerprint(portfolio)
    dates={p.ticker:quotes[p.ticker]['date'] for p in positions}
    if kind=='holding_weight' and fx:dates['USD/KRW']=fx['date']
    next_state={'portfolioFingerprint':revision,'value':value,'dates':dates}
    result={'status':'no_signal','triggers':[],'values':{'value':value,'threshold':params['thresholdPct']},'state':next_state,
        'reason':'저장 자산의 첫 평가 또는 보유 내역 변경으로 비교 기준을 초기화했습니다.'}
    if state.get('portfolioFingerprint')!=revision:return result
    if state.get('dates')==dates:return {**result,'state':state,'reason':'새로 완료된 평가 자료가 없습니다.'}
    before,threshold=state['value'],params['thresholdPct']
    triggers=(['up'] if before<=threshold<value else [])+(['down'] if before>=threshold>value else [])
    triggers=[t for t in triggers if params['direction'] in ('both',t)]
    result['values']['previousValue']=before
    return {**result,'previousDates':state['dates'],'status':'signal' if triggers else 'no_signal','triggers':triggers,'reason':'저장 자산 기준 새 교차' if triggers else '저장 자산 기준 새 교차 없음'}
