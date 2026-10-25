import os
from datetime import datetime, timezone
from html import escape
from uuid import UUID

import httpx
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from svix.webhooks import Webhook, WebhookVerificationError

from app.services.alert_store import database
from app.services.alert_metrics import record


def email_configured():
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("ALERT_EMAIL_FROM") and
                len(os.getenv("ALERT_UNSUBSCRIBE_SECRET", "")) >= 32 and os.getenv("APP_BASE_URL"))


def serializer():
    secret = os.getenv("ALERT_UNSUBSCRIBE_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "알림 수신 해지 키 설정이 필요합니다.")
    return URLSafeTimedSerializer(secret, salt="stock-alerts-unsubscribe-v1")


def unsubscribe_token(user):
    return serializer().dumps([str(user["id"]), user["unsubscribe_version"]])


def unsubscribe(token, *, apply=False):
    try:
        identity, version = serializer().loads(token, max_age=180 * 86400)
        identity = UUID(identity)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        raise HTTPException(400, "유효하지 않거나 만료된 수신 해지 링크입니다.") from None
    with database() as conn:
        u = (conn.execute("UPDATE alert_users SET consent=false,revision=revision+1 WHERE id=%s AND unsubscribe_version=%s RETURNING id", (identity, version)).fetchone()
             if apply else conn.execute("SELECT id FROM alert_users WHERE id=%s AND unsubscribe_version=%s", (identity, version)).fetchone())
        if not u:
            raise HTTPException(400, "만료된 수신 해지 링크입니다.")


def make_payload(user, day, events, deferred=0, legacy=None, test=False,frequency='daily'):
    base = os.environ["APP_BASE_URL"].rstrip("/")
    link = base + "/notifications/unsubscribe/" + unsubscribe_token(user)
    lines = ["관심종목 알림 · 가격 조건은 정규장 종가 기준", "한국시간 발송일: " + str(day), ""]
    if frequency=='weekly':lines.extend(['주간 요약 · 최근 7일 동안 확인한 새 신호 · 보류 수는 이번 점검 기준',''])
    for event in events:
        payload = event["payload"]
        if payload.get('information'):
            info=payload['information']
            lines.extend([f"{payload['symbol']} · {payload['label']} · {info['title']}",info['details'],'출처: '+info['sourceUrl'],''])
            continue
        direction = {"up": "상향", "down": "하향", "volume": "거래량 급증", "drawdown": "낙폭 진입",'all':'같은 거래일의 모든 조건 충족','retest':'고정 가격 재접근'}.get(event["trigger_kind"], event["trigger_kind"])
        if payload.get('kind') in ('holding_return','holding_weight'):
            p,v=payload['params'],payload['result']['values']
            lines.extend([f"{payload['symbol']} · {event['session_date']} 점검 · {payload['label']}",
                f"직전 평가 {v['previousValue']:.6g}% → 현재 {v['value']:.6g}% · 기준 {p['thresholdPct']}% {direction} 돌파",
                '계정에 저장된 보유자산 기준 · 가격 수익률은 현금배당·수수료 제외, 비중은 현금 포함',
                '자료 기준일: '+', '.join(f'{s} {d}' for s,d in payload['result']['state']['dates'].items()),''])
            continue
        if payload.get('kind')=='relative_performance':
            p,v=payload['params'],payload['result']['values']
            lines.extend([f"{payload['symbol']} · {event['session_date']} 종가",
                f"최근 {p['period']}거래일 가격 수익률 − {p['benchmark']} 수익률: {v['value']:.6g}%p",
                f"기준 {p['thresholdPct']}%p {direction} 돌파 · 같은 시장·통화, 분할 조정·현금배당 제외",
                ('지연 확인' if payload.get('delayed') else ''),''])
            continue
        lines.extend([("지연 확인: " if payload.get("delayed") else "") + f"{payload['symbol']} · {event['session_date']} 종가 · {payload['currency']}",
                      payload["label"] + " · " + direction, readable_values(payload["result"]["values"],payload.get('kind',''),payload['params'],payload['currency']),
                      "조건: " + readable_params(payload["params"]), ""])
        for child in payload["result"].get("conditions",[]):
            from app.alert_rules import LABELS
            lines.append(LABELS[child["type"]]+" · "+readable_values(child["result"]["values"],child['type'],child['params'],payload['currency']))
    if legacy:
        lines.extend(["리밸런싱 점검", legacy, ""])
    if deferred:
        lines.append(f"자료 미확정·조회 실패 등으로 {deferred}개 조건을 보류했습니다. 화면에서 확인해주세요.")
    if test:
        lines.append("메일 연결 테스트입니다. 종목 신호가 발생했다는 의미가 아닙니다.")
    lines.extend(["설정과 이력: " + base + "/alerts", "이메일 알림 해지: " + link,
                  "해지 전에 이미 접수된 메일은 한 통 도착할 수 있습니다."])
    text = "\n".join(lines)
    return {"from": os.environ["ALERT_EMAIL_FROM"], "to": [user["email"]],
            "subject": ("[관심종목 알림] 메일 연결 테스트" if test else f"[{'주간 요약' if frequency=='weekly' else '관심종목 알림'}] {day} · {len({e['payload']['symbol'] for e in events})}종목 {len(events)}신호"),
            "text": text, "html": "<div style='white-space:pre-wrap'>" + escape(text) + "</div>",
            "headers": {"List-Unsubscribe": "<" + link + ">", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}}


def readable_params(params):
    from app.alert_rules import LABELS
    labels={'average':'평균 방식','period':'기간','direction':'방향','shortPeriod':'단기','longPeriod':'장기',
        'price':'지정가격','thresholdPct':'변동 기준','level':'RSI 기준','multiple':'거래량 배수','fastPeriod':'MACD 단기',
        'slowPeriod':'MACD 장기','signalPeriod':'시그널 기간','crossMode':'MACD 비교선','stddev':'표준편차 배수',
        'event':'발동 조건','windowSessions':'대기 기간','tolerancePct':'재접근 허용폭'}
    options={'sma':'단순이동평균 SMA','ema':'지수이동평균 EMA','both':'양방향','up':'상향','down':'하향',
        'signal':'시그널선','zero':'0선','upper_exit':'상단 위 돌파','lower_exit':'하단 아래 이탈','upper_reentry':'상단 아래 복귀','lower_reentry':'하단 위 복귀'}
    parts=[]
    for key,value in params.items():
        if key=='conditions':parts.append('같은 거래일에 모두 충족: '+' 그리고 '.join(LABELS[c['type']]+' ('+readable_params(c['params'])+')' for c in value))
        elif key=='trigger':parts.append('돌파 기준: '+LABELS[value['type']]+' ('+readable_params(value['params'])+')')
        elif key in labels:
            suffix='거래일' if key.lower().endswith('period') or key=='windowSessions' else '%' if key.endswith('Pct') else '배' if key in ('multiple','stddev') else ''
            parts.append(labels[key]+': '+(options.get(value,value) if isinstance(value,str) else f'{value:g}')+suffix)
    return ' · '.join(parts)


def readable_values(values,kind,params,currency):
    values=dict(values)
    if kind=='daily_change':values['threshold']=params['thresholdPct'];values.pop('previousThreshold',None)
    if kind=='range_breakout':values.pop('threshold',None);values.pop('previousThreshold',None)
    labels={'previousValue':'전일 값','value':'당일 값','previousThreshold':'전일 기준','threshold':'기준',
        'close':'종가','previousHigh':'직전 최고 종가','previousLow':'직전 최저 종가'}
    unit='%' if kind in ('daily_change','drawdown_cross') else '주' if kind=='volume_spike' else '' if kind=='rsi_cross' else ' '+currency
    return ' · '.join(f"{labels[key]}: {value:.6g}{' '+currency if key in ('close','previousHigh','previousLow') else unit}" for key,value in values.items() if key in labels)


def send_payload(identity, payload):
    if not email_configured():
        raise HTTPException(503, "Resend 발신 도메인·키와 해지 설정이 필요합니다.")
    try:
        record("emailCalls")
        response = httpx.post("https://api.resend.com/emails", json=payload,
                              headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"],
                                       "Idempotency-Key": "stock-alerts/" + str(identity)}, timeout=20)
        if response.status_code == 429 or response.status_code >= 500:
            if response.status_code == 429:
                record("email429")
            try:
                delay = min(86400, max(1, int(response.headers.get("Retry-After", "300"))))
            except ValueError:
                delay = 300
            return ("retryable_failed" if response.status_code == 429 else "unknown"), None, delay
        if response.status_code >= 400:
            return "permanent_failed", None
        return "accepted", response.json()["id"]
    except (httpx.HTTPError, ValueError, KeyError):
        return "unknown", None, 300


def lock_delivery(conn, provider_id, identity=None):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,810231))", (provider_id,))
    row = conn.execute("SELECT user_id FROM alert_digests WHERE " + ("id=%s" if identity else "provider_id=%s"), (identity or provider_id,)).fetchone()
    if row:
        conn.execute("SELECT id FROM alert_users WHERE id=%s FOR UPDATE", (row["user_id"],))


def apply_receipts(conn, provider_id):
    row = conn.execute("SELECT * FROM alert_digests WHERE provider_id=%s FOR UPDATE", (provider_id,)).fetchone()
    if not row:
        return
    receipts = conn.execute("SELECT kind FROM alert_webhook_receipts WHERE provider_id=%s", (provider_id,)).fetchall()
    kinds = {x["kind"] for x in receipts}
    suppress = next((x for x in ("complained", "bounced", "suppressed") if "email."+x in kinds), None)
    if suppress:
        conn.execute("UPDATE alert_digests SET status=%s WHERE id=%s", (suppress, row["id"]))
        conn.execute("UPDATE alert_users SET consent=false,suppressed=%s,revision=revision+1 WHERE id=%s AND suppressed IS DISTINCT FROM %s", (suppress, row["user_id"], suppress))
    elif "email.delivered" in kinds and row["status"] not in ("bounced", "complained", "suppressed"):
        conn.execute("UPDATE alert_digests SET status='delivered' WHERE id=%s", (row["id"],))


def process_webhook(raw, headers):
    secret = os.getenv("RESEND_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "메일 웹훅 설정이 필요합니다.")
    try:
        event = Webhook(secret).verify(raw, headers)
        event_id = headers["svix-id"]
        kind = event["type"]
        provider_id = event["data"]["email_id"]
    except (WebhookVerificationError, ValueError, KeyError, TypeError):
        raise HTTPException(400, "메일 이벤트 서명을 확인할 수 없습니다.") from None
    with database() as conn:
        lock_delivery(conn, provider_id)
        inserted = conn.execute("INSERT INTO alert_webhook_receipts(id,provider_id,kind) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                                (event_id, provider_id, kind)).fetchone()
        if not inserted:
            apply_receipts(conn, provider_id)
            return {"duplicate": True}
        apply_receipts(conn, provider_id)
    return {"accepted": True}
