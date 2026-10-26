"""Resumable morning evaluation. One fenced coordinator; no in-memory work after HTTP."""
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pandas as pd
from fastapi import HTTPException

from app.alert_rules import evaluate_rule, LABELS, contains_price_rule, minimum_bars, HOLDING_TYPES, CALENDAR_TYPES, INFORMATION_TYPES
from app.services.alert_data import (DataPending, adjusted_bars, calendar, expected_session,
                                    fingerprint, load_daily_bars, session_close)
from app.services.alert_email import email_configured, make_payload, send_payload
from app.services.alert_store import database, json_value
from app.services.alert_metrics import current as metrics_context
from app.services import alert_push

UTC = timezone.utc
KST = timezone(timedelta(hours=9))


def utcnow():
    return datetime.now(UTC)


def fence(conn, run_id, token):
    row = conn.execute("SELECT id FROM alert_runs WHERE id=%s AND lease_token=%s AND lease_until>now() FOR UPDATE",
                       (run_id, token)).fetchone()
    if not row:
        raise DataPending("lease_expired")


def initialize(now):
    local = now.astimezone(KST)
    cutoff = local.replace(hour=8, minute=0, second=0, microsecond=0).astimezone(UTC)
    day = local.date()
    run_id, token = uuid4(), uuid4()
    with database() as conn:
        conn.execute("DELETE FROM alert_runs WHERE created_at<now()-interval '400 days'")
        conn.execute("DELETE FROM alert_provider_credits WHERE minute<now()-interval '2 days'")
        conn.execute("DELETE FROM alert_external_credits WHERE minute<now()-interval '2 days'")
        conn.execute("DELETE FROM alert_information_cache WHERE checked_at<now()-interval '400 days'")
        conn.execute("DELETE FROM alert_webhook_receipts WHERE created_at<now()-interval '400 days'")
        conn.execute("DELETE FROM alert_events WHERE created_at<now()-interval '400 days'")
        conn.execute("DELETE FROM alert_digests WHERE created_at<now()-interval '400 days'")
        conn.execute("UPDATE alert_events SET payload='{}' WHERE created_at<now()-interval '180 days' AND payload<>'{}'")
        conn.execute("""UPDATE alert_digests SET payload=jsonb_build_object('eventIds',payload->'eventIds'),payload_cleared=true
            WHERE created_at<now()-interval '180 days' AND NOT payload_cleared""")
        conn.execute("""UPDATE alert_digests SET status=CASE WHEN attempts=0 OR status='retryable_failed' THEN 'missed_window' ELSE 'unknown' END,
            error='delivery_window_expired' WHERE channel='email' AND digest_date<%s AND status IN ('pending','sending','retryable_failed')""", (day,))
        expired=conn.execute("""SELECT id FROM alert_digests d WHERE channel='push' AND digest_date<%s
            AND (status IN ('pending','sending','retryable_failed') OR EXISTS(SELECT 1 FROM alert_push_deliveries p
                WHERE p.digest_id=d.id AND p.status IN ('pending','sending','retryable_failed')))""",(day,)).fetchall()
        for digest in expired:alert_push.finalize_devices(conn,digest['id'],'missed_window')
        inserted = conn.execute("""INSERT INTO alert_runs(id,digest_date,cutoff,deadline) VALUES(%s,%s,%s,%s)
            ON CONFLICT(digest_date) DO NOTHING RETURNING id""", (run_id, day, cutoff, cutoff + timedelta(hours=1))).fetchone()
        run = conn.execute("SELECT * FROM alert_runs WHERE digest_date=%s FOR UPDATE", (day,)).fetchone()
        if run["lease_until"] and run["lease_until"] > now:
            return None, None
        run_id = run["id"]
        conn.execute("UPDATE alert_runs SET lease_token=%s,lease_until=now()+interval '270 seconds',status='running' WHERE id=%s", (token, run_id))
        if inserted:
            audience = conn.execute("SELECT id FROM alert_users WHERE (consent AND suppressed IS NULL) OR push_consent").fetchall()
            conn.execute("UPDATE alert_runs SET audience=%s WHERE id=%s", (json_value([str(u["id"]) for u in audience]), run_id))
            conn.execute("""UPDATE alert_users SET legacy_consolidated=true WHERE consent AND suppressed IS NULL
                AND lower(email)=lower(%s) AND (%s='' OR google_sub=%s)""",
                         (os.getenv("GOOGLE_OWNER_EMAIL", ""), os.getenv("GOOGLE_OWNER_SUB", ""), os.getenv("GOOGLE_OWNER_SUB", "")))
            rules = conn.execute("""SELECT r.*,i.metadata,i.symbol FROM alert_rules r
                JOIN alert_instruments i ON i.id=r.instrument_id WHERE r.enabled AND NOT r.archived""").fetchall()
            for rule in rules:
                payload = {k: (str(v) if isinstance(v, (UUID, datetime)) else v.isoformat() if hasattr(v, "isoformat") else v)
                           for k, v in rule.items()}
                conn.execute("""INSERT INTO alert_tasks(id,run_id,kind,target_id,payload) VALUES(%s,%s,'evaluate',%s,%s)
                    ON CONFLICT DO NOTHING""", (uuid4(), run_id, rule["id"], json_value(payload)))
                if rule['kind']=='filing':
                    from app.services.alert_filings import enqueue
                    enqueue(conn,rule,run_id);continue
                if rule['kind'] in CALENDAR_TYPES:
                    from app.services.alert_calendar import enqueue
                    enqueue(conn,rule,run_id)
                    if rule['kind']!='dividend_change':continue
                conn.execute("""INSERT INTO alert_tasks(id,run_id,kind,target_id,payload) VALUES(%s,%s,'fetch',%s,%s)
                    ON CONFLICT DO NOTHING""", (uuid4(), run_id, rule["instrument_id"], json_value(rule["metadata"])))
                if rule["benchmark_instrument_id"]:
                    conn.execute("""INSERT INTO alert_tasks(id,run_id,kind,target_id,payload)
                        SELECT %s,%s,'fetch',id,metadata FROM alert_instruments WHERE id=%s
                        ON CONFLICT DO NOTHING""",(uuid4(),run_id,rule["benchmark_instrument_id"]))
        return conn.execute("SELECT * FROM alert_runs WHERE id=%s", (run_id,)).fetchone(), token


def finish_task(conn, task, result, status="done"):
    conn.execute("UPDATE alert_tasks SET status=%s,result=%s,error=NULL WHERE id=%s", (status, json_value(result), task["id"]))


def fetch_task(task, run, token, now):
    with database() as conn:
        fence(conn, run["id"], token)
        cached = conn.execute("SELECT * FROM alert_daily_bars WHERE instrument_id=%s", (task["target_id"],)).fetchone()
    snapshot = load_daily_bars(task["payload"], run["cutoff"], cached["snapshot"] if cached else None)
    digest_hash = fingerprint(snapshot)
    with database() as conn:
        fence(conn, run["id"], token)
        stable = bool(cached and cached["fingerprint"] == digest_hash and now - cached["first_seen"] >= timedelta(minutes=5))
        if cached and cached["fingerprint"] != digest_hash:
            mark_corrections(conn, task["target_id"], cached, snapshot, digest_hash)
        conn.execute("""INSERT INTO alert_daily_bars(instrument_id,snapshot,fingerprint) VALUES(%s,%s,%s)
            ON CONFLICT(instrument_id) DO UPDATE SET snapshot=EXCLUDED.snapshot,fingerprint=EXCLUDED.fingerprint,
            first_seen=CASE WHEN alert_daily_bars.fingerprint=EXCLUDED.fingerprint THEN alert_daily_bars.first_seen ELSE now() END,checked_at=now()""",
                     (task["target_id"], json_value(snapshot), digest_hash))
        if stable:
            finish_task(conn, task, {"snapshotHash": digest_hash, "target": snapshot["target"]})
        else:
            conn.execute("UPDATE alert_tasks SET next_attempt_at=now()+interval '5 minutes',error='awaiting_confirmation' WHERE id=%s", (task["id"],))


def mark_corrections(conn, instrument_id, cached, snapshot, new_hash):
    old = cached["snapshot"]
    old_dates = {x["date"] for x in old["raw"]}
    overlap = [x for x in snapshot["raw"] if x["date"] <= old['target']]
    actions = [x for x in snapshot["splits"] if x["date"] <= old["target"]]
    if overlap == old["raw"] and actions == old["splits"]:
        return
    old_by_day={x["date"]:x for x in old["raw"]}
    changed_days={x["date"] for x in overlap if x!=old_by_day.get(x["date"])} | (old_dates-{x['date'] for x in overlap})
    if actions != old["splits"]:
        changed_days.update(old_dates)
    holdings=conn.execute("SELECT * FROM alert_rules WHERE enabled AND NOT archived AND kind=ANY(%s) AND evaluation_state->'sourceIds' ? %s FOR UPDATE",(list(HOLDING_TYPES),str(instrument_id))).fetchall()
    for rule in holdings:
        if changed_days.intersection(rule['evaluation_state'].get('dates',{}).values()):
            conn.execute('UPDATE alert_rules SET enabled=false,revision=revision+1,last_result=%s WHERE id=%s',(json_value({'status':'needs_review','triggers':[],'reason':'보유자산 평가 근거가 정정되었습니다. 저장 내역 확인 후 재개해주세요.'}),rule['id']))
    armed = conn.execute("""SELECT * FROM alert_rules WHERE instrument_id=%s AND kind='retest'
        AND enabled AND NOT archived AND evaluation_state<>'{}' FOR UPDATE""",(instrument_id,)).fetchall()
    for rule in armed:
        if any(rule["evaluation_state"]["date"]<=day<=old["target"] for day in changed_days):
            conn.execute("""UPDATE alert_rules SET enabled=false,revision=revision+1,last_result=%s WHERE id=%s""",
                (json_value({"status":"needs_review","reason":"돌파 근거 자료가 정정되었습니다. 조건 확인 후 재개해주세요.","triggers":[]}),rule["id"]))
    events = conn.execute("""SELECT e.*,r.kind FROM alert_events e JOIN alert_rules r ON r.id=e.rule_id
        WHERE (e.instrument_id=%s OR e.payload->>'benchmarkId'=%s OR e.payload->'sourceIds' ? %s) AND e.created_at>=now()-interval '180 days'""", (instrument_id,str(instrument_id),str(instrument_id))).fetchall()
    corrections=[]
    results={}
    old_hash=fingerprint(old)
    for event in events:
        if event['kind'] in INFORMATION_TYPES:
            info=event['payload'].get('information',{})
            if event['kind']=='dividend_change' and actions!=old['splits'] and info.get('comparisonDate') and any(info['comparisonDate']<s['date']<=info['date'] for s in actions+old['splits']):
                corrections.append(({'reason':'배당 비교 구간 분할 자료 정정'},event['id']))
            continue
        if event['kind'] in HOLDING_TYPES:
            dates=list(event['payload']['result']['state']['dates'].values())+list(event['payload']['result'].get('previousDates',{}).values())
            if changed_days.intersection(dates):
                corrections.append(({'reason':'보유자산 평가에 사용된 자료 정정','instrumentId':str(instrument_id),'previousHash':old_hash,'newHash':new_hash},event['id']))
            continue
        if event["kind"]=="relative_performance":
            days=[b['date'] for b in old['raw'] if b['date']<=str(event['session_date'])][-minimum_bars(event['kind'],event['payload']['params']):]
            if days and any(days[0]<=day<=str(event['session_date']) for day in changed_days):
                corrections.append(({
                    'instrumentId':str(instrument_id),'previousHash':old_hash,'newHash':new_hash,
                    'reason':'상대성과 계산에 사용된 가격 자료가 정정되었습니다. 자동 재발송하지 않습니다.',
                    'result':{'status':'needs_review','triggers':[]}},event['id']))
            continue
        before=event["payload"]["result"].get("stateBefore")
        key=fingerprint([event['kind'],event['payload']['params'],event['session_date'].isoformat(),before])
        if key not in results:
            results[key]=evaluate_rule(event['kind'],event['payload']['params'],adjusted_bars(snapshot,event['session_date'].isoformat()),state=before)
        result=results[key]
        if event["kind"] == "retest" and before:
            if any(before["date"]<=day<=event["session_date"].isoformat() for day in changed_days):
                result={"status":"needs_review","triggers":[],"reason":"돌파 이후 순서 자료 정정으로 재확인 필요"}
        if result != event["payload"]["result"]:
            correction = {"previousHash": event["snapshot_hash"], "newHash": new_hash,
                          "reason": "공급자 과거 자료 정정으로 당시 판정이 달라졌습니다. 자동 재발송하지 않습니다.", "result": result}
            corrections.append((correction,event['id']))
    with conn.cursor() as cur:
        cur.executemany('UPDATE alert_events SET correction=%s WHERE id=%s AND correction IS DISTINCT FROM %s',
            [(json_value(value),identity,json_value(value)) for value,identity in corrections])


def evaluation_bars(rule, snapshot, reference, day):
    bars=adjusted_bars(snapshot,day)
    if rule['kind']!='relative_performance':return bars
    if not reference:raise DataPending('reference_pending')
    size=minimum_bars(rule['kind'],rule['params'])
    bars=bars[-size:]
    ref=adjusted_bars(reference,day)[-size:]
    dates=[b['date'] for b in bars]
    expected=[x.date().isoformat() for x in calendar(rule['metadata']['calendar'],int(day[:4])).sessions_in_range(dates[0],day)] if dates else []
    if dates!=[b['date'] for b in ref] or dates!=expected:
        raise DataPending('reference_session_mismatch')
    return [{**b,'referenceClose':r['close']} for b,r in zip(bars,ref)]


def evaluate_task(task, run, token,portfolio_context=None):
    payload = task["payload"]
    if payload['kind']=='filing':
        from app.services.alert_filings import evaluate_task as evaluate_filing
        return evaluate_filing(task,run,token)
    if payload['kind'] in CALENDAR_TYPES:
        from app.services.alert_calendar import evaluate_task as evaluate_calendar_task
        return evaluate_calendar_task(task,run,token)
    if payload['kind'] in HOLDING_TYPES:
        from app.services.alert_holdings import evaluate_task as evaluate_holding_task
        return evaluate_holding_task(task,run,token,portfolio_context)
    meta = payload["metadata"]
    with database() as conn:
        dependencies=[payload['instrument_id']]+([payload['benchmark_instrument_id']] if payload.get('benchmark_instrument_id') else [])
        fetches = conn.execute("SELECT * FROM alert_tasks WHERE run_id=%s AND kind='fetch' AND target_id=ANY(%s::uuid[])", (run["id"],dependencies)).fetchall()
        if len(fetches)!=len(dependencies) or any(f['status']=='pending' for f in fetches):
            return False
        fetch=next((f for f in fetches if f['status']!='done'),None)
        if fetch:
            fence(conn, run["id"], token)
            result = {"status": "deferred", "reason": fetch["error"], "checkedDate": str(run["digest_date"])}
            conn.execute("UPDATE alert_rules SET last_result=%s WHERE id=%s AND meaning_version=%s",
                         (json_value(result), task["target_id"], payload["meaning_version"]))
            finish_task(conn, task, result, "deferred")
            return True
        cached = conn.execute("SELECT * FROM alert_daily_bars WHERE instrument_id=%s", (payload["instrument_id"],)).fetchone()
        reference=conn.execute('SELECT snapshot FROM alert_daily_bars WHERE instrument_id=%s',(payload.get('benchmark_instrument_id'),)).fetchone() if payload.get('benchmark_instrument_id') else None
    reference=reference['snapshot'] if reference else None
    snapshot = cached["snapshot"]
    target = snapshot["target"]
    activated = datetime.fromisoformat(payload["activated_at"])
    sessions = [bar["date"] for bar in snapshot["raw"]]
    if payload["kind"]=="retest" and payload["last_session"] and payload["last_session"]<sessions[0]:
        raise DataPending("state_history_gap",False)
    eligible = [day for day in sessions if session_close(meta, datetime.fromisoformat(day).date()) > activated and
                (not payload["last_session"] or day > payload["last_session"])]
    expired = len(eligible) > 5
    delivery_days = set(eligible[-5:])
    more = payload["kind"]=="retest" and len(eligible)>64
    eligible = eligible[:64] if payload["kind"]=="retest" else eligible[-5:]
    evaluations = []
    evaluation_state = payload.get("evaluation_state",{})
    price_split = contains_price_rule(payload["kind"],payload["params"]) and any(
        session_close(meta, datetime.fromisoformat(x["date"]).date()) > activated for x in snapshot["splits"])
    if price_split:
        evaluations = [(target, {"status": "needs_review", "reason": "분할 후 지정가격을 확인하고 재개해주세요.", "triggers": []})]
    else:
        for day in eligible:
            age = (len(calendar(meta["calendar"],int(day[:4])).sessions_in_range(evaluation_state["date"],day))-1
                   if payload["kind"]=="retest" and evaluation_state else None)
            result = evaluate_rule(payload["kind"], payload["params"], evaluation_bars(payload,snapshot,reference,day),state=evaluation_state,session_age=age)
            evaluation_state = result.get("state",evaluation_state)
            if result["status"] == "insufficient_data" and day != eligible[-1]:
                # Initial history can warm up during the catch-up window.
                continue
            evaluations.append((day, result))
    with database() as conn:
        fence(conn, run["id"], token)
        current = conn.execute("SELECT * FROM alert_rules WHERE id=%s FOR UPDATE", (task["target_id"],)).fetchone()
        if not current or not current["enabled"] or current["archived"] or current["meaning_version"] != payload["meaning_version"]:
            finish_task(conn, task, {"status": "canceled"}, "canceled")
            return True
        if price_split:
            conn.execute("UPDATE alert_rules SET enabled=false,revision=revision+1,last_result=%s WHERE id=%s",
                         (json_value(evaluations[-1][1]), current["id"]))
        for day, result in evaluations:
            if result["status"] not in ("signal", "no_signal"):
                conn.execute("UPDATE alert_rules SET last_result=%s WHERE id=%s", (json_value(result), current["id"]))
                finish_task(conn, task, result, "deferred")
                return True
            for trigger in (result["triggers"] if day in delivery_days else []):
                event = {"symbol": payload["symbol"], "currency": meta["currency"], "kind": payload["kind"], "label": LABELS[payload["kind"]],
                         "params": payload["params"], "result": result}
                if payload.get('benchmark_instrument_id'):
                    event['benchmarkId']=payload['benchmark_instrument_id']
                    event['benchmarkHash']=fingerprint(reference)
                conn.execute("""INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                             (uuid4(), current["user_id"], current["id"], current["instrument_id"], current["meaning_version"], day,
                              trigger, cached["fingerprint"], json_value(event)))
            conn.execute("UPDATE alert_rules SET last_session=%s,last_result=%s,evaluation_state=%s WHERE id=%s", (day, json_value(result),json_value(result.get("state",{})),current["id"]))
        if more and evaluations:
            updated={**payload,"last_session":evaluations[-1][0],"evaluation_state":evaluation_state}
            conn.execute("UPDATE alert_tasks SET payload=%s WHERE id=%s",(json_value(updated),task["id"]))
            return False
        if not evaluations:
            preview = evaluate_rule(payload["kind"], payload["params"], evaluation_bars(payload,snapshot,reference,target))
            if preview["status"] in ("signal", "no_signal"):
                preview = {**preview, "status": "no_new_session", "reason": "활성화 후 새 완료 거래일이 없습니다.", "triggers": []}
            if payload["kind"]!="retest" or not current["last_result"]:
                conn.execute("UPDATE alert_rules SET last_result=%s WHERE id=%s", (json_value(preview), current["id"]))
        finish_task(conn, task, {"status": "evaluated", "sessions": len(evaluations), "expiredGap": expired})
    return True


def legacy_section(user):
    if user["email"].casefold() != os.getenv("GOOGLE_OWNER_EMAIL", "").casefold():
        return None
    from app.dashboard_schemas import SavedDashboard
    from app.services.portfolio_store import PortfolioStore, storage_ready
    from app.services.dashboard import DashboardService
    if not storage_ready():
        return None
    store = PortfolioStore()
    raw, revision = store.read("dashboard.json")
    if not raw:
        return None
    saved = SavedDashboard.model_validate(raw)
    if not saved.portfolio.emailAlerts:
        return None
    check = DashboardService().portfolio_snapshot(saved.portfolio)["rebalance"]
    if store.read("dashboard.json")[1] != revision:
        raise DataPending("legacy_portfolio_changed")
    if not check["ready"]:
        return "리밸런싱 자료 보류: " + check["message"]
    if check["status"] == "balanced":
        return None
    lines = [f"시세 기준일: {' / '.join(check['quoteDates'])}",
             "시장별 최신 조회 가격 기준 점검이며 장중 가격이 포함될 수 있습니다.",
             "종목별 시세와 환율 기준일은 대시보드에서 확인해주세요."]
    lines.extend(f"{x.get('name', x['ticker'])}: 현재 {x['weightPct']:.2f}% / 목표 {x['targetWeightPct']:.2f}%" for x in check["items"] if x["needsRebalance"])
    return "\n".join(lines)


def prepare_digests(run, token, now, started):
    with database() as conn:
        users = conn.execute("SELECT * FROM alert_users WHERE ((consent AND suppressed IS NULL) OR push_consent) AND id=ANY(%s::uuid[])", (run["audience"],)).fetchall()
    for user,channel in ((u,c) for u in users for c in ('email','push')):
        if time.monotonic() - started > 170:
            break
        email=channel=='email'
        if email and (not user['consent'] or user['suppressed'] or not email_configured()):continue
        if not email and (not user['push_consent'] or not alert_push.public_key()):continue
        frequency=user['email_frequency' if email else 'push_frequency']
        if frequency=='weekly' and run['digest_date'].weekday()!=0:continue
        with database() as conn:
            pending = conn.execute("""SELECT count(*) AS n FROM alert_tasks WHERE run_id=%s AND kind='evaluate'
                AND payload->>'user_id'=%s AND status='pending'""", (run["id"], str(user["id"]))).fetchone()["n"]
            existing = conn.execute("SELECT * FROM alert_digests WHERE user_id=%s AND digest_date=%s AND kind<>'test' AND channel=%s", (user["id"], run["digest_date"],channel)).fetchone()
            devices=conn.execute('SELECT id FROM alert_push_subscriptions WHERE user_id=%s',(user['id'],)).fetchall() if not email else []
            holding_rules=conn.execute('SELECT id FROM alert_rules WHERE user_id=%s AND enabled AND NOT archived AND kind=ANY(%s) LIMIT 1',(user['id'],list(HOLDING_TYPES))).fetchone()
        rebuild=existing and existing['status']=='canceled' and existing['attempts']==0 and existing['kind']==frequency
        if pending or (existing and not rebuild) or (not email and not devices):
            continue
        try:
            legacy = legacy_section(user) if email else None
        except (HTTPException, DataPending):
            legacy = "리밸런싱 자료를 확인하지 못해 해당 점검을 보류했습니다."
        portfolio_hash=None
        if holding_rules:
            from app.services.alert_holdings import read_portfolio,financial_fingerprint
            try:portfolio_hash=financial_fingerprint(read_portfolio(user)[0])
            except (HTTPException,DataPending,ValueError):pass
        with database() as conn:
            fence(conn, run["id"], token)
            current = conn.execute("SELECT * FROM alert_users WHERE id=%s FOR UPDATE", (user["id"],)).fetchone()
            if not current or (email and (not current["consent"] or current["suppressed"] or current["email"] != user["email"] or current['email_frequency']!=frequency)) or (not email and (not current['push_consent'] or current['push_version']!=user['push_version'])):
                continue
            if rebuild:
                latest=conn.execute('SELECT status,attempts FROM alert_digests WHERE id=%s FOR UPDATE',(existing['id'],)).fetchone()
                if not latest or latest['status']!='canceled' or latest['attempts']!=0:continue
            period_filter='e.created_at >= %s' if frequency=='weekly' else 'e.session_date >= %s'
            period_start=run['cutoff']-timedelta(days=7) if frequency=='weekly' else run['digest_date']-timedelta(days=14)
            events = conn.execute(f"""SELECT e.*,i.metadata FROM alert_events e JOIN alert_rules r ON r.id=e.rule_id
                JOIN alert_instruments i ON i.id=e.instrument_id
                WHERE e.user_id=%s AND NOT e.canceled AND r.enabled AND NOT r.archived AND e.meaning_version=r.meaning_version
                AND {period_filter} AND NOT EXISTS (
                    SELECT 1 FROM alert_digests d WHERE d.user_id=e.user_id AND d.channel=%s AND d.attempts>0 AND d.status NOT IN ('missed_window','canceled','permanent_failed')
                    AND d.payload->'eventIds' ? e.id::text) ORDER BY e.session_date,e.id""", (user["id"], period_start,channel)).fetchall()
            # Limit delayed delivery to five actual market sessions per instrument.
            events = [e for e in events if not e['correction'] and (frequency=='weekly' or len(calendar_from_event(e,run))<=5)]
            events=[e for e in events if e['payload'].get('kind') not in HOLDING_TYPES or e['payload'].get('portfolioFingerprint')==portfolio_hash]
            for event in events:
                event["payload"] = {**event["payload"], "delayed": event["session_date"] < expected_session(event["metadata"], run["cutoff"])}
            deferred = conn.execute("""SELECT count(*) AS n FROM alert_tasks WHERE run_id=%s AND kind='evaluate'
                AND payload->>'user_id'=%s AND status='deferred'""", (run["id"], str(user["id"]))).fetchone()["n"]
            envelope = (make_payload(current, run["digest_date"], events, deferred, legacy,frequency=frequency) if email else {'push':True}) if events or legacy else None
            payload = {**({"email": current["email"], "unsubscribeVersion": current["unsubscribe_version"]} if email else {'pushVersion':current['push_version']}),
                       "eventIds": [str(e["id"]) for e in events],
                       "rules": {str(e["rule_id"]): e["meaning_version"] for e in events}, "envelope": envelope,
                       "deferred": deferred,'frequency':frequency}
            if any(e['payload'].get('kind') in HOLDING_TYPES for e in events):payload['portfolioFingerprint']=portfolio_hash
            identity=existing['id'] if rebuild else uuid4()
            conn.execute("""INSERT INTO alert_digests(id,user_id,digest_date,kind,channel,payload,status) VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(user_id,digest_date,kind,channel) DO UPDATE SET payload=EXCLUDED.payload,status=EXCLUDED.status,error=NULL
                WHERE alert_digests.attempts=0""", (identity, user["id"], run["digest_date"],frequency,channel, json_value(payload),
                                                    "pending" if envelope else "deferred" if deferred else "no_signals"))
            if not email and envelope:
                if rebuild:conn.execute('DELETE FROM alert_push_deliveries WHERE digest_id=%s',(identity,))
                for device in devices:conn.execute('INSERT INTO alert_push_deliveries(digest_id,subscription_id) VALUES(%s,%s)',(identity,device['id']))


def calendar_from_event(event, run):
    if event['payload'].get('kind') in HOLDING_TYPES+INFORMATION_TYPES:
        return range(max(0,(run['digest_date']-event['session_date']).days-2))
    meta = event["metadata"]
    target = expected_session(meta, run["cutoff"])
    return calendar(meta["calendar"], target.year).sessions_in_range(event["session_date"], target)


def send_digest(identity, run=None, token=None, *, deadline=None):
    from app.services.alert_holdings import read_portfolio,financial_fingerprint
    with database() as conn:
        check=conn.execute('SELECT d.payload,u.google_sub,u.email FROM alert_digests d JOIN alert_users u ON u.id=d.user_id WHERE d.id=%s',(identity,)).fetchone()
    portfolio_valid=True
    if check and check['payload'].get('portfolioFingerprint'):
        try:portfolio_valid=financial_fingerprint(read_portfolio(check)[0])==check['payload']['portfolioFingerprint']
        except (HTTPException,DataPending,ValueError):return
    with database() as conn:
        if run:
            fence(conn, run["id"], token)
        row = conn.execute("SELECT * FROM alert_digests WHERE id=%s", (identity,)).fetchone()
        if not row:
            return
        user = conn.execute("SELECT * FROM alert_users WHERE id=%s FOR UPDATE", (row["user_id"],)).fetchone()
        row = conn.execute("SELECT * FROM alert_digests WHERE id=%s FOR UPDATE", (identity,)).fetchone()
        if not row or row["status"] not in ("pending", "sending", "unknown", "retryable_failed"):
            return
        if (row['channel']=='email' and not email_configured()) or (row['channel']=='push' and not alert_push.public_key()):return
        payload = row["payload"]
        push=row['channel']=='push'
        valid=portfolio_valid and user and (user['push_consent'] and user['push_version']==payload['pushVersion'] if push else user["consent"] and not user["suppressed"] and user["email"] == payload["email"] and user["unsubscribe_version"] == payload["unsubscribeVersion"])
        if row['kind']!='test':valid=valid and user['push_frequency' if push else 'email_frequency']==payload.get('frequency','daily')
        event_ids=payload.get('eventIds',[])
        live=conn.execute('SELECT count(*) AS n FROM alert_events WHERE id=ANY(%s::uuid[]) AND user_id=%s AND NOT canceled AND correction IS NULL',(event_ids,row['user_id'])).fetchone()['n']
        valid=valid and live==len(event_ids)
        for rule_id, version in payload["rules"].items():
            if not conn.execute("SELECT id FROM alert_rules WHERE id=%s AND user_id=%s AND enabled AND NOT archived AND meaning_version=%s", (rule_id, row["user_id"], version)).fetchone():
                valid = False
        if not valid:
            if push:alert_push.finalize_devices(conn,identity,'canceled')
            else:conn.execute("""UPDATE alert_digests SET status=CASE WHEN attempts>0 AND status IN ('unknown','sending') THEN 'unknown' ELSE 'canceled' END,
                error='delivery_settings_changed' WHERE id=%s""", (identity,))
            return
        if row["first_attempt_at"] and utcnow() - row["first_attempt_at"] >= timedelta(hours=24):
            conn.execute("UPDATE alert_digests SET status='unknown',error='idempotency_expired' WHERE id=%s", (identity,))
            return
        conn.execute("UPDATE alert_digests SET status='sending',attempts=attempts+1,first_attempt_at=coalesce(first_attempt_at,now()) WHERE id=%s", (identity,))
    # Original envelope and id remain unchanged across timeouts/retries.
    outcome = alert_push.send_devices(identity,deadline=deadline) if push else send_payload(identity, payload["envelope"])
    state, provider_id = outcome[:2]
    if not push and row['status'] in ('unknown','sending') and state in ('retryable_failed','permanent_failed'):
        state='unknown'  # A later rejection cannot disprove acceptance of the earlier attempt.
    retry_after = outcome[2] if len(outcome) > 2 else 300
    with database() as conn:
        if run:
            fence(conn, run["id"], token)
        if provider_id:
            from app.services.alert_email import lock_delivery
            lock_delivery(conn, provider_id, identity)
        conn.execute("""UPDATE alert_digests SET status=%s,provider_id=coalesce(%s,provider_id),
            next_attempt_at=now()+(%s * interval '1 second') WHERE id=%s AND status='sending'""", (state, provider_id, retry_after, identity))
        if provider_id:
            from app.services.alert_email import apply_receipts
            apply_receipts(conn, provider_id)


def process_alert_run(*, now=None):
    call_started = time.monotonic()
    now = now or utcnow()
    if os.getenv("STOCK_ALERTS_SEND_ENABLED") != "true":
        # No production run/task/event/digest or cursor mutations in dry-run mode.
        with database() as conn:
            rules = conn.execute("SELECT r.*,b.snapshot,i.symbol,i.metadata,ref.snapshot AS reference FROM alert_rules r JOIN alert_instruments i ON i.id=r.instrument_id LEFT JOIN alert_daily_bars b ON b.instrument_id=r.instrument_id LEFT JOIN alert_daily_bars ref ON ref.instrument_id=r.benchmark_instrument_id WHERE r.enabled AND NOT r.archived").fetchall()
        counts = {}
        for rule in rules:
            try:
                status = evaluate_rule(rule["kind"], rule["params"], evaluation_bars(rule,rule["snapshot"],rule['reference'],rule["snapshot"]["target"]))["status"] if rule["snapshot"] else "data_pending"
            except DataPending:
                status='data_pending'
            if rule['kind'] in HOLDING_TYPES:
                from app.services.alert_holdings import dry_result
                try:status=dry_result(rule,now)['status']
                except (HTTPException,DataPending,ValueError):status='data_pending'
            if rule['kind'] in CALENDAR_TYPES:
                from app.services.alert_calendar import read_items,evaluate_calendar
                try:
                    with database() as conn:items=read_items(conn,rule,{'digest_date':now.astimezone(KST).date()})
                    if rule['kind']=='dividend_change' and (not rule['snapshot'] or rule['snapshot']['target']!=str(expected_session(rule['metadata'],now))):raise DataPending('calendar_snapshot_pending')
                    result=evaluate_calendar(rule['kind'],rule['params'],items,rule['evaluation_state'],now.astimezone(KST).date(),rule['activated_at'],splits=rule['snapshot']['splits'] if rule['snapshot'] else ())
                    status='data_pending' if result['pending'] else 'signal' if result['events'] else 'no_signal'
                except DataPending:status='data_pending'
            if rule['kind']=='filing':
                from app.services.alert_filings import cached_items,evaluate_filings,source_day
                try:
                    with database() as conn:items=cached_items(conn,rule['instrument_id'],now.astimezone(KST).date())
                    result=evaluate_filings(items,rule['evaluation_state'],now.astimezone(KST).date(),baseline_day=source_day(rule['metadata'],now))
                    status='signal' if result['events'] else 'no_signal'
                except DataPending:status='data_pending'
            if rule["kind"]=="retest" and rule["snapshot"]:
                snap=rule["snapshot"]
                pending=[b["date"] for b in snap["raw"] if (not rule["last_session"] or b["date"]>str(rule["last_session"]))
                         and session_close(rule["metadata"],datetime.fromisoformat(b["date"]).date())>rule["activated_at"]]
                state=rule["evaluation_state"]
                status="no_new_session"
                for day in pending[:64]:
                    age=len(calendar(rule["metadata"]["calendar"],int(day[:4])).sessions_in_range(state["date"],day))-1 if state else None
                    result=evaluate_rule("retest",rule["params"],adjusted_bars(snap,day),state=state,session_age=age)
                    status=result["status"]
                    if status=="insufficient_data" and day!=pending[-1]:continue
                    if status not in ("signal","no_signal"):break
                    state=result["state"]
                if len(pending)>64:status="data_pending"
            counts[status] = counts.get(status, 0) + 1
        return {"status": "dry_run", "counts": counts, "sending": False}
    if now.astimezone(KST).hour != 8:
        return {"status": "outside_window"}
    if not email_configured() and not alert_push.public_key():
        raise HTTPException(503, "알림 메일 또는 웹 푸시 환경 설정이 필요합니다.")
    run, token = initialize(now)
    if not run:
        return {"status": "busy"}
    started = time.monotonic()
    metrics = {}
    metrics_token = metrics_context.set(metrics)
    try:
        from app.services.alert_holdings import prepare_context
        contexts={}
        with database() as conn:
            holdings=conn.execute("SELECT payload FROM alert_tasks WHERE run_id=%s AND kind='evaluate' AND status='pending' AND payload->>'kind'=ANY(%s)",(run['id'],list(HOLDING_TYPES))).fetchall()
            owners=conn.execute('SELECT * FROM alert_users WHERE id=ANY(%s::uuid[])',([h['payload']['user_id'] for h in holdings],)).fetchall()
        for owner in owners:
            if time.monotonic()-started>120:break
            selected=[h['payload'] for h in holdings if h['payload']['user_id']==str(owner['id'])]
            try:
                contexts[str(owner['id'])]=prepare_context(owner,run,token,[r['symbol'] for r in selected],any(r['kind']=='holding_weight' for r in selected),deadline=started+120)
            except (HTTPException,ValueError):contexts[str(owner['id'])]=DataPending('portfolio_unavailable')
            except DataPending as exc:contexts[str(owner['id'])]=exc
        with database() as conn:
            tasks = conn.execute("SELECT * FROM alert_tasks WHERE run_id=%s AND status='pending' AND next_attempt_at<=now() ORDER BY kind,id", (run["id"],)).fetchall()
        for task in sorted(tasks, key=lambda x: (not x['kind'].startswith('fetch'),x['kind']!='fetch_filing_map')):
            if time.monotonic() - started > 150:
                break
            try:
                if task['kind'].startswith('fetch'):
                    if now >= run["cutoff"] + timedelta(minutes=45):
                        continue
                    if task['kind']=='fetch':fetch_task(task, run, token, now)
                    else:
                        if task['kind'] in ('fetch_filing','fetch_filing_map'):
                            from app.services.alert_filings import fetch_task as fetch_information
                        else:
                            from app.services.alert_calendar import fetch_task as fetch_information
                        progressed=fetch_information(task,run,token)
                        while progressed is not False and time.monotonic()-started<120:
                            with database() as conn:task=conn.execute('SELECT * FROM alert_tasks WHERE id=%s',(task['id'],)).fetchone()
                            if task['status']!='pending':break
                            progressed=fetch_information(task,run,token)
                else:
                    if task['payload']['kind'] in HOLDING_TYPES:
                        evaluate_task(task,run,token,portfolio_context=contexts.get(task['payload']['user_id']))
                    else:evaluate_task(task, run, token)
            except DataPending as exc:
                if exc.code == "lease_expired":
                    return {"status": "lease_expired"}
                with database() as conn:
                    fence(conn, run["id"], token)
                    # Waiting for shared inputs is not an evaluation failure.
                    increment = int(exc.code not in ('provider_rate_limited','portfolio_pending','portfolio_prices_pending'))
                    if exc.code == "provider_rate_limited":
                        conn.execute("UPDATE alert_runs SET rate_limit_count=rate_limit_count+1 WHERE id=%s", (run["id"],))
                    updated=conn.execute("""UPDATE alert_tasks SET attempts=attempts+%s,error=%s,
                        next_attempt_at=now()+(%s * interval '1 second'),
                        status=CASE WHEN NOT %s OR attempts+%s>=3 THEN 'deferred' ELSE 'pending' END WHERE id=%s RETURNING status""",
                                 (increment, exc.code, exc.delay, exc.retry, increment, task["id"])).fetchone()
                    if task['kind']=='evaluate' and updated['status']=='deferred':
                        conn.execute('UPDATE alert_rules SET last_result=%s WHERE id=%s AND meaning_version=%s',
                            (json_value({'status':'deferred','reason':exc.code,'checkedDate':str(run['digest_date'])}),task['target_id'],task['payload']['meaning_version']))
        if now >= run["cutoff"] + timedelta(minutes=45):
            with database() as conn:
                fence(conn, run["id"], token)
                conn.execute("UPDATE alert_tasks SET status='deferred',error='data_deadline',result=%s WHERE run_id=%s AND status='pending'", (json_value({"status": "deferred", "reason": "자료 마감 시각 초과"}), run["id"]))
                conn.execute("""UPDATE alert_rules r SET last_result=%s FROM alert_tasks t WHERE t.run_id=%s
                    AND t.kind='evaluate' AND t.target_id=r.id AND t.status='deferred'
                    AND r.meaning_version=(t.payload->>'meaning_version')::int""", (json_value({"status": "deferred", "reason": "자료 미확정·조회 실패로 보류"}), run["id"]))
        if time.monotonic() - started < 170:
            prepare_digests(run, token, now, started)
        with database() as conn:
            sends = conn.execute("SELECT id FROM alert_digests WHERE digest_date=%s AND kind IN ('daily','weekly') AND status IN ('pending','sending','unknown','retryable_failed') AND (channel='email' OR status<>'unknown') AND next_attempt_at<=now()", (run["digest_date"],)).fetchall()
        for send in sends:
            if time.monotonic() - started > 180 or utcnow() >= run["deadline"]:
                break
            send_digest(send["id"], run, token,deadline=started+180)
            time.sleep(1)
        with database() as conn:
            fence(conn, run["id"], token)
            counts = conn.execute("SELECT status,count(*) AS n FROM alert_tasks WHERE run_id=%s GROUP BY status", (run["id"],)).fetchall()
        return {"status": "processed", "tasks": counts}
    finally:
        metrics_context.reset(metrics_token)
        with database() as conn:
            conn.execute("""UPDATE alert_runs SET lease_until=NULL,status='waiting',duration_seconds=duration_seconds+%s,
                data_calls=data_calls+%s,data_429=data_429+%s,email_calls=email_calls+%s,email_429=email_429+%s,push_calls=push_calls+%s,push_429=push_429+%s
                WHERE id=%s AND lease_token=%s""", (time.monotonic() - call_started, metrics.get("dataCalls",0),
                metrics.get("data429",0),metrics.get("emailCalls",0),metrics.get("email429",0),metrics.get('pushCalls',0),metrics.get('push429',0),run["id"],token))
