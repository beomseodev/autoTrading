import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from threading import Lock

import exchange_calendars as xcals
import httpx
import pandas as pd
from fastapi import HTTPException

from app.services.alert_store import database
from app.services.alert_metrics import record

POLICY = "raw-split-only-v1"
_calendar_lock = Lock()
MIC = {"XNAS": ("USD", "America/New_York", "XNYS"), "XNMS": ("USD", "America/New_York", "XNYS"), "XNYS": ("USD", "America/New_York", "XNYS"),
       "XASE": ("USD", "America/New_York", "XNYS"), "ARCX": ("USD", "America/New_York", "XNYS"),
       "XKRX": ("KRW", "Asia/Seoul", "XKRX"), "XKOS": ("KRW", "Asia/Seoul", "XKRX")}


class DataPending(Exception):
    def __init__(self, code, retry=True, delay=300):
        self.code, self.retry, self.delay = code, retry, delay


@lru_cache(maxsize=16)
def calendar(name, year):
    # XKRX initialization mutates shared holiday tables and must run serially.
    with _calendar_lock:
        return xcals.get_calendar(name, start=f"{year - 10}-01-01", end=f"{year + 1}-12-31")


def expected_session(meta, cutoff):
    cal = calendar(meta["calendar"], cutoff.year)
    schedule = cal.schedule
    eligible = schedule[schedule["close"] <= pd.Timestamp(cutoff - timedelta(minutes=90))]
    if eligible.empty:
        raise DataPending("calendar_unavailable", False)
    return eligible.index[-1].date()


def session_close(meta, session):
    return calendar(meta["calendar"], session.year).session_close(pd.Timestamp(session)).to_pydatetime()


def reserve_credits(cost):
    try:
        limit = int(os.getenv("TWELVE_DATA_CREDITS_PER_MINUTE", "0"))
    except ValueError:
        limit = 0
    if limit < cost:
        raise DataPending("provider_quota_not_configured", False)
    with database() as conn:
        conn.execute("INSERT INTO alert_provider_credits(minute) VALUES(date_trunc('minute',now())) ON CONFLICT DO NOTHING")
        row = conn.execute("""UPDATE alert_provider_credits SET used=used+%s
            WHERE minute=date_trunc('minute',now()) AND used+%s<=%s RETURNING used""", (cost, cost, limit)).fetchone()
        if not row:
            raise DataPending("provider_rate_limited", True, 60)


def provider_get(path, params, cost=1):
    key = os.getenv("TWELVE_DATA_API_KEY")
    if not key:
        raise HTTPException(503, "관심종목 시세 공급자 설정이 필요합니다.")
    reserve_credits(cost)
    try:
        record("dataCalls")
        response = httpx.get("https://api.twelvedata.com/" + path, params={**params, "apikey": key}, timeout=25)
        if response.status_code == 429:
            record("data429")
            raise DataPending("provider_rate_limited", True, min(3600, int(response.headers.get("Retry-After", "60"))))
        if response.status_code >= 500:
            raise DataPending("provider_unavailable")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise DataPending("invalid_provider_data", False)
        if payload.get("status") == "error":
            code = int(payload.get("code", 500))
            if code == 429:
                record("data429")
            raise DataPending("provider_rate_limited" if code == 429 else "provider_rejected", code == 429 or code >= 500)
        return payload
    except (httpx.HTTPError, ValueError, TypeError):
        raise DataPending("provider_unavailable") from None


def resolve_instrument(symbol):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"(?:[A-Z][A-Z0-9.-]{0,14}|\d{6}\.(?:KS|KQ))", symbol):
        raise HTTPException(422, "미국 종목 코드 또는 005930.KS 형식으로 입력해주세요.")
    suffix = "XKRX" if symbol.endswith(".KS") else "XKOS" if symbol.endswith(".KQ") else None
    if not suffix:
        symbol = symbol.replace(".", "-")
    provider_symbol = symbol[:-3] if suffix else symbol.replace("-", ".")
    try:
        result = provider_get("symbol_search", {"symbol": provider_symbol, "outputsize": 120})
        matches = [x for x in result.get("data", []) if x.get("symbol") == provider_symbol and
                   x.get("mic_code") in MIC and (x["mic_code"] == suffix if suffix else x.get("country") == "United States") and
                   x.get("instrument_type") in ("Common Stock", "ETF")]
        if len(matches) != 1:
            raise HTTPException(422, "지원 거래소의 주식·ETF를 유일하게 확인하지 못했습니다.")
        item = matches[0]
        currency, zone, cal = MIC[item["mic_code"]]
        if item.get("currency") != currency or item.get("exchange_timezone") != zone:
            raise HTTPException(422, "종목 통화·시간대를 검증하지 못했습니다.")
        return {"symbol": symbol, "providerSymbol": provider_symbol, "name": item["instrument_name"],
                "mic": item["mic_code"], "currency": currency, "timezone": zone, "calendar": cal,
                "type": item["instrument_type"], "policy": POLICY}
    except DataPending as exc:
        raise HTTPException(503, "종목 검증을 완료하지 못했습니다: " + exc.code) from None


def parse_snapshot(meta, prices, splits, target, confirmed_halts=()):
    for payload in (prices, splits):
        info = payload.get("meta", {})
        if (info.get("symbol") != meta["providerSymbol"] or info.get("mic_code") != meta["mic"] or
                info.get("currency") != meta["currency"] or info.get("exchange_timezone") != meta["timezone"]):
            raise DataPending("metadata_mismatch", False)
    raw, seen = [], set()
    try:
        for value in prices["values"]:
            day = date.fromisoformat(value["datetime"])
            if day in seen:
                raise ValueError("duplicate")
            seen.add(day)
            values = {k: float(value[k]) for k in ("open", "high", "low", "close", "volume")}
            if not all(math.isfinite(x) for x in values.values()) or min(values[k] for k in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0:
                raise ValueError("invalid OHLCV")
            if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
                raise ValueError("invalid range")
            if day <= target:
                raw.append({"date": day.isoformat(), **values})
        raw.sort(key=lambda x: x["date"])
        if target.isoformat() in confirmed_halts:
            raise DataPending("confirmed_suspension")
        if not raw or raw[-1]["date"] != target.isoformat():
            raise DataPending("latest_bar_pending")
        cal = calendar(meta["calendar"], target.year)
        sessions = {x.date().isoformat() for x in cal.sessions_in_range(raw[0]["date"], target)}
        if sessions - set(confirmed_halts) != {x["date"] for x in raw}:
            raise DataPending("data_gap")
        actions = []
        split_days = set()
        for action in splits["splits"]:
            day = date.fromisoformat(action["date"])
            ratio = float(action["ratio"])
            # Twelve Data's 4-for-1 fixture has ratio=.25: historical PRICE multiplier.
            if day in split_days or not math.isfinite(ratio) or ratio <= 0:
                raise ValueError("invalid split")
            split_days.add(day)
            if day <= target:
                actions.append({"date": day.isoformat(), "priceRatio": ratio})
        return {"raw": raw, "splits": sorted(actions, key=lambda x: x["date"]), "policy": POLICY,
                "target": target.isoformat()}
    except (KeyError, ValueError, TypeError, OverflowError):
        raise DataPending("invalid_provider_data", False) from None


def adjusted_bars(snapshot, target):
    data = []
    for raw in snapshot["raw"]:
        if raw["date"] > target:
            continue
        factor = math.prod(x["priceRatio"] for x in snapshot["splits"] if raw["date"] < x["date"] <= target)
        data.append({**raw, **{k: raw[k] * factor for k in ("open", "high", "low", "close")},
                     "volume": raw["volume"] / factor, "rawClose": raw["close"]})
    return data


def fingerprint(snapshot):
    return sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_daily_bars(meta, cutoff, previous=None):
    if meta.get('type')=='Forex':return load_fx_bars(cutoff,previous)
    target = expected_session(meta, cutoff)
    params = {"symbol": meta["providerSymbol"], "mic_code": meta["mic"], "end_date": target.isoformat()}
    if previous:
        params["start_date"] = previous["raw"][0]["date"]
    prices = provider_get("time_series", {**params, "interval": "1day", "adjust": "none", "outputsize": 5000 if previous else 1500})
    splits = provider_get("splits", {**params, "range": "full"}, cost=20)
    halt_file = Path(__file__).parents[2] / "config" / "alert_trading_halts.json"
    try:
        confirmed = json.loads(halt_file.read_text(encoding="utf-8")).get(meta["symbol"], [])
        halts = [x["date"] for x in confirmed if x.get("source", "").startswith("https://") and date.fromisoformat(x["date"])]
    except (OSError, ValueError, TypeError, KeyError):
        raise DataPending("halt_registry_invalid", False) from None
    snapshot = parse_snapshot(meta, prices, splits, target, halts)
    if previous and snapshot["raw"][0]["date"] != previous["raw"][0]["date"]:
        raise DataPending("history_anchor_changed", False)
    return snapshot


def expected_fx_day(cutoff):
    target=cutoff.astimezone(timezone.utc).date()-timedelta(days=1)
    while target.weekday()>4:target-=timedelta(days=1)
    return target


def load_fx_bars(cutoff,previous=None):
    target=expected_fx_day(cutoff)
    rates={b['date']:b for b in previous['raw']} if previous else {}
    # shortcut: recheck the preceding FX point; older FX revisions need a provider revision feed.
    days={str(target)}|({previous['target']} if previous else set())
    for day in sorted(days):
        point=day+'T23:59:59'
        response=provider_get('exchange_rate',{'symbol':'USD/KRW','date':point,'timezone':'UTC'})
        try:
            rate=float(response['rate'])
            observed=datetime.fromtimestamp(response['timestamp'],timezone.utc)
            if response['symbol']!='USD/KRW' or not math.isfinite(rate) or rate<=0 or observed.date().isoformat()!=day or observed>datetime.fromisoformat(point).replace(tzinfo=timezone.utc):
                raise ValueError('invalid historical FX point')
            rates[day]={'date':day,'observedAt':observed.isoformat(),**{k:rate for k in ('open','high','low','close')},'volume':0}
        except (KeyError,ValueError,TypeError,OverflowError,OSError):
            raise DataPending('invalid_fx_data',False) from None
    return {'raw':[rates[d] for d in sorted(rates)],'splits':[],'target':str(target),'policy':'historical-utc-fx-point-v1'}
