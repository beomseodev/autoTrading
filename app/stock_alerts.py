import json
import os
from hashlib import sha256
from uuid import UUID, uuid4
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alert_rules import PARAMS, LABELS, CORE_TYPES, normalize_rule
from app.services.alert_data import resolve_instrument
from app.services.alert_store import database, sync_user, owned, lock_user, check_revision, json_value

router = APIRouter()


@router.get("/api/alerts/operations")
def operations(request: Request):
    from app.auth import is_owner
    if not is_owner(request.session.get("user")):
        raise HTTPException(403, "운영자만 확인할 수 있습니다.")
    with database() as conn:
        runs = conn.execute("""SELECT id,digest_date,status,cutoff,deadline,lease_until,
            duration_seconds,rate_limit_count,data_calls,data_429,email_calls,email_429
            FROM alert_runs ORDER BY digest_date DESC LIMIT 14""").fetchall()
        ids = [r["id"] for r in runs]
        days = [r["digest_date"] for r in runs]
        tasks = conn.execute("""SELECT run_id,kind,status,count(*) AS count,
            min(created_at) FILTER (WHERE status='pending') AS oldest FROM alert_tasks
            WHERE run_id=ANY(%s) GROUP BY run_id,kind,status ORDER BY kind,status""", (ids,)).fetchall()
        deliveries = conn.execute("""SELECT digest_date,status,count(*) AS count,
            min(created_at) FILTER (WHERE status IN ('pending','sending','retryable_failed','unknown')) AS oldest
            FROM alert_digests WHERE digest_date=ANY(%s) AND kind<>'test'
            GROUP BY digest_date,status ORDER BY status""", (days,)).fetchall()
        for run in runs:
            rt = [t for t in tasks if t["run_id"] == run["id"]]
            rd = [d for d in deliveries if d["digest_date"] == run["digest_date"]]
            run["tasks"] = [{k:t[k] for k in ("kind","status","count")} for t in rt]
            run["deliveries"] = [{k:d[k] for k in ("status","count")} for d in rd]
            run["oldestPending"] = min((r["oldest"] for r in rt + rd if r["oldest"]), default=None)
    return {"runs": runs}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsInput(Input):
    revision: int = Field(ge=1)
    consent: bool


class WatchInput(Input):
    symbol: str = Field(min_length=1, max_length=20)


class RuleInput(Input):
    instrumentIds: list[UUID] = Field(min_length=1, max_length=50)
    type: str
    params: dict[str, Any]
    name: str = Field("", max_length=100)
    restoreArchived: bool = False

    @model_validator(mode="after")
    def validate_params(self):
        self.params = normalize_rule(self.type, self.params)
        if len(set(self.instrumentIds)) != len(self.instrumentIds):
            raise ValueError("중복 종목을 제거해주세요.")
        return self


class RulePatch(Input):
    revision: int = Field(ge=1)
    params: dict[str, Any] | None = None
    enabled: bool | None = None
    name: str | None = Field(None, max_length=100)


def user(request):
    return sync_user(request.session["user"])


def rule_hash(kind, params):
    return sha256(json.dumps([kind, params], sort_keys=True).encode()).hexdigest()


def register_reference(conn, meta, instrument):
    if meta is None:return None
    target=conn.execute('SELECT symbol,metadata FROM alert_instruments WHERE id=%s',(instrument,)).fetchone()
    if target['symbol']==meta['symbol'] or any(target['metadata'][key]!=meta[key] for key in ('currency','calendar')):
        raise HTTPException(422,'비교 종목은 같은 시장·통화의 다른 종목을 선택해주세요.')
    return conn.execute("""INSERT INTO alert_instruments(id,symbol,metadata) VALUES(%s,%s,%s)
        ON CONFLICT(symbol) DO UPDATE SET metadata=EXCLUDED.metadata RETURNING id""",(uuid4(),meta['symbol'],json_value(meta))).fetchone()['id']


@router.get("/alerts")
def alerts_page():
    from app.main import STATIC_DIR
    return FileResponse(STATIC_DIR / "alerts.html")


@router.get("/api/alerts/settings")
def settings(request: Request):
    u = user(request)
    return {"email": u["email"], "consent": u["consent"], "revision": u["revision"],
            "suppressed": u["suppressed"], "sendTime": "한국시간 오전 8시대 · 새 신호가 있는 날만",
            "sendingEnabled": os.getenv("STOCK_ALERTS_SEND_ENABLED") == "true",
            "types": [{"type": k, "label": LABELS[k], "composable": k in CORE_TYPES, "defaults": normalize_rule(k, {"price": 100} if k == "price_cross" else {})} for k in PARAMS]}


@router.patch("/api/alerts/settings")
def set_settings(payload: SettingsInput, request: Request):
    u = user(request)
    with database() as conn:
        current = lock_user(conn, u["id"])
        check_revision(current, payload.revision)
        if payload.consent and current["suppressed"] not in (None, "deleted"):
            raise HTTPException(409, "반송·스팸신고로 발송이 중지되었습니다. 운영자에게 문의해주세요.")
        conn.execute("""UPDATE alert_users SET consent=%s, consent_at=CASE WHEN %s THEN now() ELSE consent_at END,
            revision=revision+1, unsubscribe_version=unsubscribe_version+1,
            suppressed=CASE WHEN suppressed='deleted' THEN NULL ELSE suppressed END WHERE id=%s""",
                     (payload.consent, payload.consent, u["id"]))
        if payload.consent and os.getenv("STOCK_ALERTS_SEND_ENABLED") == "true":
            from app.auth import is_owner
            if is_owner(request.session["user"]):
                conn.execute("UPDATE alert_users SET legacy_consolidated=true WHERE id=%s", (u["id"],))
    return {"revision": payload.revision + 1, "consent": payload.consent}


@router.get("/api/alerts/instrument")
def instrument_preview(symbol: str = Query(min_length=1, max_length=20)):
    return resolve_instrument(symbol)


@router.get("/api/alerts/watchlist")
def watchlist(request: Request):
    u = user(request)
    with database() as conn:
        rows = conn.execute("""SELECT w.id, i.id AS "instrumentId", i.symbol, i.metadata,
            (SELECT count(*) FROM alert_rules r WHERE r.user_id=w.user_id AND r.instrument_id=i.id AND NOT r.archived) AS "ruleCount",
            ARRAY(SELECT DISTINCT COALESCE(r.last_result->>'status', 'data_pending')
                  FROM alert_rules r WHERE r.user_id=w.user_id AND r.instrument_id=i.id AND NOT r.archived
                  ORDER BY 1) AS "dataStatuses"
            FROM alert_watchlist w JOIN alert_instruments i ON i.id=w.instrument_id
            WHERE w.user_id=%s AND NOT w.archived ORDER BY i.symbol""", (u["id"],)).fetchall()
    return {"items": rows}


@router.post("/api/alerts/watchlist")
def add_watch(payload: WatchInput, request: Request):
    u = user(request)
    meta = resolve_instrument(payload.symbol)
    with database() as conn:
        lock_user(conn, u["id"])
        if conn.execute("SELECT count(*) AS n FROM alert_watchlist WHERE user_id=%s AND NOT archived", (u["id"],)).fetchone()["n"] >= 50:
            raise HTTPException(422, "관심종목은 최대 50개입니다.")
        instrument = conn.execute("""INSERT INTO alert_instruments(id,symbol,metadata) VALUES(%s,%s,%s)
            ON CONFLICT(symbol) DO UPDATE SET metadata=EXCLUDED.metadata RETURNING id""",
                                  (uuid4(), meta["symbol"], json_value(meta))).fetchone()["id"]
        existing = conn.execute("SELECT * FROM alert_watchlist WHERE user_id=%s AND instrument_id=%s", (u["id"], instrument)).fetchone()
        if existing and not existing["archived"]:
            raise HTTPException(409, "이미 등록된 관심종목입니다.")
        identity = existing["id"] if existing else uuid4()
        conn.execute("""INSERT INTO alert_watchlist(id,user_id,instrument_id) VALUES(%s,%s,%s)
            ON CONFLICT(user_id,instrument_id) DO UPDATE SET archived=false""", (identity, u["id"], instrument))
    return {"id": identity, "instrumentId": instrument, "metadata": meta}


@router.delete("/api/alerts/watchlist/{identity}")
def delete_watch(identity: UUID, request: Request):
    u = user(request)
    with database() as conn:
        lock_user(conn, u["id"])
        row = owned(conn, "alert_watchlist", identity, u["id"], lock=True)
        conn.execute("UPDATE alert_watchlist SET archived=true WHERE id=%s", (identity,))
        conn.execute("UPDATE alert_rules SET archived=true,enabled=false,revision=revision+1 WHERE user_id=%s AND instrument_id=%s",
                     (u["id"], row["instrument_id"]))
        conn.execute("UPDATE alert_events SET canceled=true WHERE user_id=%s AND instrument_id=%s", (u["id"], row["instrument_id"]))
    return {"archived": True}


@router.get("/api/alerts/rules")
def rules(request: Request):
    u = user(request)
    with database() as conn:
        rows = conn.execute("""SELECT r.*,i.symbol,i.metadata FROM alert_rules r JOIN alert_instruments i ON i.id=r.instrument_id
            WHERE r.user_id=%s AND NOT r.archived ORDER BY i.symbol,r.activated_at""", (u["id"],)).fetchall()
    return {"items": rows}


@router.post("/api/alerts/rules")
def add_rules(payload: RuleInput, request: Request):
    u = user(request)
    reference=resolve_instrument(payload.params['benchmark']) if payload.type=='relative_performance' else None
    with database() as conn:
        lock_user(conn, u["id"])
        count = conn.execute("SELECT count(*) AS n FROM alert_rules WHERE user_id=%s AND enabled AND NOT archived", (u["id"],)).fetchone()["n"]
        if count + len(payload.instrumentIds) > 100:
            raise HTTPException(422, "활성 규칙은 최대 100개입니다.")
        identities = []
        for instrument in payload.instrumentIds:
            if not conn.execute("SELECT id FROM alert_watchlist WHERE user_id=%s AND instrument_id=%s AND NOT archived", (u["id"], instrument)).fetchone():
                raise HTTPException(404, "본인 관심종목을 선택해주세요.")
            reference_id=register_reference(conn,reference,instrument)
            existing = conn.execute("SELECT * FROM alert_rules WHERE user_id=%s AND instrument_id=%s AND fingerprint=%s FOR UPDATE",
                                    (u["id"], instrument, rule_hash(payload.type, payload.params))).fetchone()
            if existing:
                if not existing["archived"]:
                    raise HTTPException(409, "이미 등록된 조건입니다. 기존 규칙을 수정하거나 재개해주세요.")
                if not payload.restoreArchived:
                    raise HTTPException(409, "보관된 동일 조건이 있습니다. 보관된 조건 재사용에 체크한 뒤 다시 저장해주세요.")
                conn.execute("""UPDATE alert_rules SET archived=false,enabled=true,name=%s,revision=revision+1,
                    meaning_version=meaning_version+1,activated_at=now(),last_session=NULL,last_result=NULL,evaluation_state='{}' WHERE id=%s""", (payload.name, existing["id"]))
                identities.append(existing["id"])
                conn.execute('UPDATE alert_rules SET benchmark_instrument_id=%s WHERE id=%s',(reference_id,existing['id']))
                continue
            identity = uuid4()
            conn.execute("""INSERT INTO alert_rules(id,user_id,instrument_id,kind,params,name,fingerprint)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""", (identity, u["id"], instrument, payload.type,
                json_value(payload.params), payload.name, rule_hash(payload.type, payload.params)))
            identities.append(identity)
            conn.execute('UPDATE alert_rules SET benchmark_instrument_id=%s WHERE id=%s',(reference_id,identity))
    return {"ids": identities}


@router.patch("/api/alerts/rules/{identity}")
def patch_rule(identity: UUID, payload: RulePatch, request: Request):
    u = user(request)
    reference=None
    if payload.params is not None:
        with database() as conn:
            before=owned(conn,'alert_rules',identity,u['id'])
        if before['kind']=='relative_performance':
            try:p=normalize_rule(before['kind'],payload.params)
            except ValueError:raise HTTPException(422,'규칙 매개변수를 확인해주세요.') from None
            reference=resolve_instrument(p['benchmark'])
    with database() as conn:
        lock_user(conn, u["id"])
        row = owned(conn, "alert_rules", identity, u["id"], lock=True)
        check_revision(row, payload.revision)
        if reference:
            reference_id=register_reference(conn,reference,row['instrument_id'])
            conn.execute('UPDATE alert_rules SET benchmark_instrument_id=%s WHERE id=%s',(reference_id,identity))
        try:
            p = normalize_rule(row["kind"], payload.params) if payload.params is not None else row["params"]
        except ValueError:
            raise HTTPException(422, "규칙 매개변수를 확인해주세요.") from None
        enabled = payload.enabled if payload.enabled is not None else row["enabled"]
        if enabled and not row["enabled"]:
            count = conn.execute("SELECT count(*) AS n FROM alert_rules WHERE user_id=%s AND enabled AND NOT archived", (u["id"],)).fetchone()["n"]
            if count >= 100:
                raise HTTPException(422, "활성 규칙은 최대 100개입니다.")
        semantic = p != row["params"] or (enabled and not row["enabled"])
        conn.execute("""UPDATE alert_rules SET params=%s,fingerprint=%s,name=%s,enabled=%s,revision=revision+1,
            meaning_version=meaning_version+%s,activated_at=CASE WHEN %s THEN now() ELSE activated_at END,
            last_session=CASE WHEN %s THEN NULL ELSE last_session END,last_result=CASE WHEN %s THEN NULL ELSE last_result END
            WHERE id=%s AND user_id=%s""", (json_value(p), rule_hash(row["kind"], p), payload.name if payload.name is not None else row["name"],
            enabled, int(semantic), semantic, semantic, semantic, identity, u["id"]))
        if semantic or not enabled:
            conn.execute("UPDATE alert_rules SET evaluation_state='{}' WHERE id=%s",(identity,))
            conn.execute("UPDATE alert_events SET canceled=true WHERE rule_id=%s AND user_id=%s", (identity, u["id"]))
    return {"revision": payload.revision + 1}


@router.delete("/api/alerts/rules/{identity}")
def delete_rule(identity: UUID, request: Request):
    u = user(request)
    with database() as conn:
        lock_user(conn, u["id"])
        owned(conn, "alert_rules", identity, u["id"], lock=True)
        conn.execute("UPDATE alert_rules SET archived=true,enabled=false,revision=revision+1 WHERE id=%s AND user_id=%s", (identity, u["id"]))
        conn.execute("UPDATE alert_events SET canceled=true WHERE rule_id=%s AND user_id=%s", (identity, u["id"]))
    return {"archived": True}


def history(table, request, cursor, limit):
    u = user(request)
    with database() as conn:
        # Cursor lookup is also account scoped; UUID knowledge never grants access.
        anchor = conn.execute(f"SELECT created_at,id FROM {table} WHERE id=%s AND user_id=%s", (cursor, u["id"])).fetchone() if cursor else None
        if cursor and not anchor:
            raise HTTPException(404, "이력 위치를 찾을 수 없습니다.")
        rows = conn.execute(f"SELECT * FROM {table} WHERE user_id=%s AND created_at>=now()-interval '180 days'" +
                            (" AND (created_at,id)<(%s,%s)" if anchor else "") +
                            " ORDER BY created_at DESC,id DESC LIMIT %s",
                            (u["id"], *([anchor["created_at"], anchor["id"]] if anchor else []), limit + 1)).fetchall()
    return {"items": rows[:limit], "nextCursor": str(rows[limit-1]["id"]) if len(rows) > limit else None}


@router.get("/api/alerts/events")
def events(request: Request, cursor: UUID | None = None, limit: int = Query(50, ge=1, le=100)):
    return history("alert_events", request, cursor, limit)


@router.get("/api/alerts/digests")
def digests(request: Request, cursor: UUID | None = None, limit: int = Query(50, ge=1, le=100)):
    result = history("alert_digests", request, cursor, limit)
    # Never return unsubscribe credentials embedded in frozen outbound bodies.
    for row in result["items"]:
        row["deferredCount"] = row["payload"].get("deferred", 0)
        row.pop("payload", None)
    return result


@router.delete("/api/alerts/account")
def delete_account(request: Request):
    u = user(request)
    with database() as conn:
        lock_user(conn, u["id"])
        for table in ("alert_events", "alert_digests", "alert_rules", "alert_watchlist"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=%s", (u["id"],))
        # Preserve the legacy migration tombstone so unsubscribing cannot revive old mail.
        conn.execute("""UPDATE alert_users SET email='',consent=false,suppressed='deleted',revision=revision+1,
                     unsubscribe_version=unsubscribe_version+1 WHERE id=%s""", (u["id"],))
    return {"deleted": True}


@router.get("/api/cron/stock-alerts")
def stock_alert_cron():
    from app.services.alert_jobs import process_alert_run
    return process_alert_run()


@router.get("/notifications/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str):
    from app.services.alert_email import unsubscribe
    unsubscribe(token)
    return "<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>종가 알림 해지</title><h1>종가 알림 수신 해지</h1><p>관심종목 알림 전체를 중지합니다. 이미 접수된 메일은 한 통 도착할 수 있습니다.</p><form method='post'><button>전체 알림 해지</button></form></html>"


@router.post("/notifications/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_apply(token: str):
    from app.services.alert_email import unsubscribe
    unsubscribe(token, apply=True)
    return "<!doctype html><html lang='ko'><meta charset='utf-8'><title>해지 완료</title><h1>종가 알림을 해지했습니다.</h1><a href='/alerts'>알림 설정</a></html>"


@router.post("/api/webhooks/alert-email")
async def email_webhook(request: Request):
    from app.services.alert_email import process_webhook
    raw = await request.body()
    if len(raw) > 262144:
        raise HTTPException(413, "메일 이벤트가 너무 큽니다.")
    return process_webhook(raw, dict(request.headers))


@router.post("/api/alerts/email-test")
def email_test(request: Request):
    from datetime import datetime, timedelta, timezone
    from app.services.alert_email import email_configured, make_payload
    from app.services.alert_jobs import send_digest
    if os.getenv("STOCK_ALERTS_SEND_ENABLED") != "true" or not email_configured():
        raise HTTPException(503, "실제 발송은 꺼져 있습니다. 운영 설정 후 테스트할 수 있습니다.")
    u = user(request)
    day = datetime.now(timezone(timedelta(hours=9))).date()
    identity = uuid4()
    with database() as conn:
        current = lock_user(conn, u["id"])
        if not current["consent"] or current["suppressed"]:
            raise HTTPException(409, "먼저 본인 이메일 수신에 동의해주세요.")
        if conn.execute("SELECT id FROM alert_digests WHERE user_id=%s AND digest_date=%s AND kind='test'", (u["id"], day)).fetchone():
            raise HTTPException(429, "메일 테스트는 하루 한 번만 요청할 수 있습니다.")
        payload = {"email": current["email"], "unsubscribeVersion": current["unsubscribe_version"], "rules": {}, "eventIds": [],
                   "envelope": make_payload(current, day, [], test=True)}
        conn.execute("INSERT INTO alert_digests(id,user_id,digest_date,kind,payload) VALUES(%s,%s,%s,'test',%s)", (identity, u["id"], day, json_value(payload)))
    send_digest(identity)
    with database() as conn:
        result = conn.execute("SELECT status FROM alert_digests WHERE id=%s", (identity,)).fetchone()
    return result
