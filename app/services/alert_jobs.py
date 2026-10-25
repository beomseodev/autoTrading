"""Resumable morning evaluation. One fenced coordinator; no in-memory work after HTTP."""
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pandas as pd
from fastapi import HTTPException

from app.alert_rules import evaluate_rule, LABELS, contains_price_rule
from app.services.alert_data import (DataPending, adjusted_bars, calendar, expected_session,
                                    fingerprint, load_daily_bars, session_close)
from app.services.alert_email import email_configured, make_payload, send_payload
from app.services.alert_store import database, json_value
from app.services.alert_metrics import current as metrics_context

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
        conn.execute("DELETE FROM alert_webhook_receipts WHERE created_at<now()-interval '400 days'")
        conn.execute("DELETE FROM alert_events WHERE created_at<now()-interval '400 days'")
        conn.execute("DELETE FROM alert_digests WHERE created_at<now()-interval '400 days'")
        conn.execute("UPDATE alert_events SET payload='{}' WHERE created_at<now()-interval '180 days' AND payload<>'{}'")
        conn.execute("""UPDATE alert_digests SET payload=jsonb_build_object('eventIds',payload->'eventIds'),payload_cleared=true
            WHERE created_at<now()-interval '180 days' AND NOT payload_cleared""")
        conn.execute("""UPDATE alert_digests SET status=CASE WHEN attempts=0 OR status='retryable_failed' THEN 'missed_window' ELSE 'unknown' END,
            error='delivery_window_expired' WHERE digest_date<%s AND status IN ('pending','sending','retryable_failed')""", (day,))
        inserted = conn.execute("""INSERT INTO alert_runs(id,digest_date,cutoff,deadline) VALUES(%s,%s,%s,%s)
            ON CONFLICT(digest_date) DO NOTHING RETURNING id""", (run_id, day, cutoff, cutoff + timedelta(hours=1))).fetchone()
        run = conn.execute("SELECT * FROM alert_runs WHERE digest_date=%s FOR UPDATE", (day,)).fetchone()
        if run["lease_until"] and run["lease_until"] > now:
            return None, None
        run_id = run["id"]
        conn.execute("UPDATE alert_runs SET lease_token=%s,lease_until=now()+interval '270 seconds',status='running' WHERE id=%s", (token, run_id))
        if inserted:
            audience = conn.execute("SELECT id FROM alert_users WHERE consent AND suppressed IS NULL").fetchall()
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
                conn.execute("""INSERT INTO alert_tasks(id,run_id,kind,target_id,payload) VALUES(%s,%s,'fetch',%s,%s)
                    ON CONFLICT DO NOTHING""", (uuid4(), run_id, rule["instrument_id"], json_value(rule["metadata"])))
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
    overlap = [x for x in snapshot["raw"] if x["date"] in old_dates]
    actions = [x for x in snapshot["splits"] if x["date"] <= old["target"]]
    if overlap == old["raw"] and actions == old["splits"]:
        return
    events = conn.execute("""SELECT e.*,r.kind FROM alert_events e JOIN alert_rules r ON r.id=e.rule_id
        WHERE e.instrument_id=%s AND e.created_at>=now()-interval '180 days'""", (instrument_id,)).fetchall()
    for event in events:
        result = evaluate_rule(event["kind"], event["payload"]["params"], adjusted_bars(snapshot, event["session_date"].isoformat()))
        if result != event["payload"]["result"]:
            correction = {"previousHash": event["snapshot_hash"], "newHash": new_hash,
                          "reason": "공급자 과거 자료 정정으로 당시 판정이 달라졌습니다. 자동 재발송하지 않습니다.", "result": result}
            conn.execute("UPDATE alert_events SET correction=%s WHERE id=%s AND correction IS DISTINCT FROM %s", (json_value(correction), event["id"], json_value(correction)))


def evaluate_task(task, run, token):
    payload = task["payload"]
    meta = payload["metadata"]
    with database() as conn:
        fetch = conn.execute("SELECT * FROM alert_tasks WHERE run_id=%s AND kind='fetch' AND target_id=%s", (run["id"], payload["instrument_id"])).fetchone()
        if fetch["status"] == "pending":
            return False
        if fetch["status"] != "done":
            fence(conn, run["id"], token)
            result = {"status": "deferred", "reason": fetch["error"], "checkedDate": str(run["digest_date"])}
            conn.execute("UPDATE alert_rules SET last_result=%s WHERE id=%s AND meaning_version=%s",
                         (json_value(result), task["target_id"], payload["meaning_version"]))
            finish_task(conn, task, result, "deferred")
            return True
        cached = conn.execute("SELECT * FROM alert_daily_bars WHERE instrument_id=%s", (payload["instrument_id"],)).fetchone()
    snapshot = cached["snapshot"]
    target = snapshot["target"]
    activated = datetime.fromisoformat(payload["activated_at"])
    sessions = [bar["date"] for bar in snapshot["raw"]]
    eligible = [day for day in sessions if session_close(meta, datetime.fromisoformat(day).date()) > activated and
                (not payload["last_session"] or day > payload["last_session"])]
    expired = len(eligible) > 5
    eligible = eligible[-5:]
    evaluations = []
    price_split = contains_price_rule(payload["kind"],payload["params"]) and any(
        session_close(meta, datetime.fromisoformat(x["date"]).date()) > activated for x in snapshot["splits"])
    if price_split:
        evaluations = [(target, {"status": "needs_review", "reason": "분할 후 지정가격을 확인하고 재개해주세요.", "triggers": []})]
    else:
        for day in eligible:
            result = evaluate_rule(payload["kind"], payload["params"], adjusted_bars(snapshot, day))
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
            for trigger in result["triggers"]:
                event = {"symbol": payload["symbol"], "currency": meta["currency"], "kind": payload["kind"], "label": LABELS[payload["kind"]],
                         "params": payload["params"], "result": result}
                conn.execute("""INSERT INTO alert_events(id,user_id,rule_id,instrument_id,meaning_version,session_date,trigger_kind,snapshot_hash,payload)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                             (uuid4(), current["user_id"], current["id"], current["instrument_id"], current["meaning_version"], day,
                              trigger, cached["fingerprint"], json_value(event)))
            conn.execute("UPDATE alert_rules SET last_session=%s,last_result=%s WHERE id=%s", (day, json_value(result), current["id"]))
        if not evaluations:
            preview = evaluate_rule(payload["kind"], payload["params"], adjusted_bars(snapshot, target))
            if preview["status"] in ("signal", "no_signal"):
                preview = {**preview, "status": "no_new_session", "reason": "활성화 후 새 완료 거래일이 없습니다.", "triggers": []}
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
    return "\n".join(f"{x.get('name', x['ticker'])}: 현재 {x['weightPct']:.2f}% / 목표 {x['targetWeightPct']:.2f}%" for x in check["items"] if x["needsRebalance"])


def prepare_digests(run, token, now, started):
    with database() as conn:
        users = conn.execute("SELECT * FROM alert_users WHERE consent AND suppressed IS NULL AND id=ANY(%s::uuid[])", (run["audience"],)).fetchall()
    for user in users:
        if time.monotonic() - started > 170:
            break
        with database() as conn:
            pending = conn.execute("""SELECT count(*) AS n FROM alert_tasks WHERE run_id=%s AND kind='evaluate'
                AND payload->>'user_id'=%s AND status='pending'""", (run["id"], str(user["id"]))).fetchone()["n"]
            existing = conn.execute("SELECT * FROM alert_digests WHERE user_id=%s AND digest_date=%s AND kind='daily'", (user["id"], run["digest_date"])).fetchone()
        if pending or existing:
            continue
        try:
            legacy = legacy_section(user)
        except (HTTPException, DataPending):
            legacy = "리밸런싱 자료를 확인하지 못해 해당 점검을 보류했습니다."
        with database() as conn:
            fence(conn, run["id"], token)
            current = conn.execute("SELECT * FROM alert_users WHERE id=%s FOR UPDATE", (user["id"],)).fetchone()
            if not current or not current["consent"] or current["suppressed"] or current["email"] != user["email"]:
                continue
            events = conn.execute("""SELECT e.*,i.metadata FROM alert_events e JOIN alert_rules r ON r.id=e.rule_id
                JOIN alert_instruments i ON i.id=e.instrument_id
                WHERE e.user_id=%s AND NOT e.canceled AND r.enabled AND NOT r.archived AND e.meaning_version=r.meaning_version
                AND e.session_date >= %s::date - 14 AND NOT EXISTS (
                    SELECT 1 FROM alert_digests d WHERE d.user_id=e.user_id AND d.attempts>0 AND d.status NOT IN ('missed_window','canceled','permanent_failed')
                    AND d.payload->'eventIds' ? e.id::text) ORDER BY e.session_date,e.id""", (user["id"], run["digest_date"])).fetchall()
            # Limit delayed delivery to five actual market sessions per instrument.
            events = [e for e in events if len(calendar_from_event(e, run)) <= 5 and not e["correction"]]
            for event in events:
                event["payload"] = {**event["payload"], "delayed": event["session_date"] < expected_session(event["metadata"], run["cutoff"])}
            deferred = conn.execute("""SELECT count(*) AS n FROM alert_tasks WHERE run_id=%s AND kind='evaluate'
                AND payload->>'user_id'=%s AND status='deferred'""", (run["id"], str(user["id"]))).fetchone()["n"]
            envelope = make_payload(current, run["digest_date"], events, deferred, legacy) if events or legacy else None
            payload = {"email": current["email"], "unsubscribeVersion": current["unsubscribe_version"],
                       "eventIds": [str(e["id"]) for e in events],
                       "rules": {str(e["rule_id"]): e["meaning_version"] for e in events}, "envelope": envelope,
                       "deferred": deferred}
            conn.execute("""INSERT INTO alert_digests(id,user_id,digest_date,payload,status) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(user_id,digest_date,kind) DO UPDATE SET payload=EXCLUDED.payload,status=EXCLUDED.status
                WHERE alert_digests.attempts=0""", (uuid4(), user["id"], run["digest_date"], json_value(payload),
                                                    "pending" if envelope else "deferred" if deferred else "no_signals"))


def calendar_from_event(event, run):
    meta = event["metadata"]
    target = expected_session(meta, run["cutoff"])
    return calendar(meta["calendar"], target.year).sessions_in_range(event["session_date"], target)


def send_digest(identity, run=None, token=None):
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
        payload = row["payload"]
        valid = user and user["consent"] and not user["suppressed"] and user["email"] == payload["email"] and user["unsubscribe_version"] == payload["unsubscribeVersion"]
        for rule_id, version in payload["rules"].items():
            if not conn.execute("SELECT id FROM alert_rules WHERE id=%s AND user_id=%s AND enabled AND NOT archived AND meaning_version=%s", (rule_id, row["user_id"], version)).fetchone():
                valid = False
        if not valid:
            conn.execute("UPDATE alert_digests SET status='canceled' WHERE id=%s", (identity,))
            return
        if row["first_attempt_at"] and utcnow() - row["first_attempt_at"] >= timedelta(hours=24):
            conn.execute("UPDATE alert_digests SET status='unknown',error='idempotency_expired' WHERE id=%s", (identity,))
            return
        conn.execute("UPDATE alert_digests SET status='sending',attempts=attempts+1,first_attempt_at=coalesce(first_attempt_at,now()) WHERE id=%s", (identity,))
    # Original envelope and id remain unchanged across timeouts/retries.
    outcome = send_payload(identity, payload["envelope"])
    state, provider_id = outcome[:2]
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
            rules = conn.execute("SELECT r.*,b.snapshot FROM alert_rules r LEFT JOIN alert_daily_bars b ON b.instrument_id=r.instrument_id WHERE r.enabled AND NOT r.archived").fetchall()
        counts = {}
        for rule in rules:
            status = evaluate_rule(rule["kind"], rule["params"], adjusted_bars(rule["snapshot"], rule["snapshot"]["target"]))["status"] if rule["snapshot"] else "data_pending"
            counts[status] = counts.get(status, 0) + 1
        return {"status": "dry_run", "counts": counts, "sending": False}
    if now.astimezone(KST).hour != 8:
        return {"status": "outside_window"}
    if not email_configured():
        raise HTTPException(503, "종가 알림 메일 환경 설정이 필요합니다.")
    run, token = initialize(now)
    if not run:
        return {"status": "busy"}
    started = time.monotonic()
    metrics = {}
    metrics_token = metrics_context.set(metrics)
    try:
        with database() as conn:
            tasks = conn.execute("SELECT * FROM alert_tasks WHERE run_id=%s AND status='pending' AND next_attempt_at<=now() ORDER BY kind,id", (run["id"],)).fetchall()
        for task in sorted(tasks, key=lambda x: x["kind"] != "fetch"):
            if time.monotonic() - started > 150:
                break
            try:
                if task["kind"] == "fetch":
                    if now >= run["cutoff"] + timedelta(minutes=45):
                        continue
                    fetch_task(task, run, token, now)
                else:
                    evaluate_task(task, run, token)
            except DataPending as exc:
                if exc.code == "lease_expired":
                    return {"status": "lease_expired"}
                with database() as conn:
                    fence(conn, run["id"], token)
                    # Rate limiting does not consume a data-retry attempt.
                    increment = int(exc.code != "provider_rate_limited")
                    if exc.code == "provider_rate_limited":
                        conn.execute("UPDATE alert_runs SET rate_limit_count=rate_limit_count+1 WHERE id=%s", (run["id"],))
                    conn.execute("""UPDATE alert_tasks SET attempts=attempts+%s,error=%s,
                        next_attempt_at=now()+(%s * interval '1 second'),
                        status=CASE WHEN NOT %s OR attempts+%s>=3 THEN 'deferred' ELSE 'pending' END WHERE id=%s""",
                                 (increment, exc.code, exc.delay, exc.retry, increment, task["id"]))
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
            sends = conn.execute("SELECT id FROM alert_digests WHERE digest_date=%s AND kind='daily' AND status IN ('pending','sending','unknown','retryable_failed') AND next_attempt_at<=now()", (run["digest_date"],)).fetchall()
        for send in sends:
            if time.monotonic() - started > 180 or utcnow() >= run["deadline"]:
                break
            send_digest(send["id"], run, token)
            time.sleep(1)
        with database() as conn:
            fence(conn, run["id"], token)
            counts = conn.execute("SELECT status,count(*) AS n FROM alert_tasks WHERE run_id=%s GROUP BY status", (run["id"],)).fetchall()
        return {"status": "processed", "tasks": counts}
    finally:
        metrics_context.reset(metrics_token)
        with database() as conn:
            conn.execute("""UPDATE alert_runs SET lease_until=NULL,status='waiting',duration_seconds=duration_seconds+%s,
                data_calls=data_calls+%s,data_429=data_429+%s,email_calls=email_calls+%s,email_429=email_429+%s
                WHERE id=%s AND lease_token=%s""", (time.monotonic() - call_started, metrics.get("dataCalls",0),
                metrics.get("data429",0),metrics.get("emailCalls",0),metrics.get("email429",0),run["id"],token))
