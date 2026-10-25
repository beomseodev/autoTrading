"""Closing-bar alert contracts and deterministic, unrounded signal evaluation."""
from typing import Literal
from math import isfinite
import json

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.indicators import compute_rsi


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Direction(Params):
    direction: Literal["up", "down", "both"] = "both"


class Average(Direction):
    average: Literal["sma", "ema"] = "sma"
    period: int = Field(200, ge=2, le=252, strict=True)


class Averages(Direction):
    average: Literal["sma", "ema"] = "sma"
    shortPeriod: int = Field(50, ge=2, le=252, strict=True)
    longPeriod: int = Field(200, ge=2, le=252, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.shortPeriod >= self.longPeriod:
            raise ValueError("단기 기간은 장기 기간보다 작아야 합니다.")
        return self


class Price(Direction):
    price: float = Field(gt=0, le=1_000_000_000)


class Change(Direction):
    thresholdPct: float = Field(5, ge=.1, le=50)


class RSI(Direction):
    period: int = Field(14, ge=2, le=252, strict=True)
    level: float = Field(30, ge=1, le=99)
    direction: Literal["up", "down", "both"] = "down"


class Range(Direction):
    period: int = Field(252, ge=2, le=252, strict=True)


class Volume(Params):
    period: int = Field(20, ge=2, le=252, strict=True)
    multiple: float = Field(2, ge=1.1, le=20)


class MACD(Direction):
    fastPeriod: int = Field(12, ge=2, le=252, strict=True)
    slowPeriod: int = Field(26, ge=2, le=252, strict=True)
    signalPeriod: int = Field(9, ge=2, le=252, strict=True)
    crossMode: Literal["signal", "zero"] = "signal"

    @model_validator(mode="after")
    def ordered(self):
        if self.fastPeriod >= self.slowPeriod:
            raise ValueError("MACD fast 기간은 slow 기간보다 작아야 합니다.")
        return self


class Bollinger(Params):
    period: int = Field(20, ge=2, le=252, strict=True)
    stddev: float = Field(2, ge=.5, le=5)
    event: Literal["upper_exit", "lower_exit", "upper_reentry", "lower_reentry"] = "upper_exit"


class Drawdown(Params):
    period: int = Field(60, ge=2, le=252, strict=True)
    thresholdPct: float = Field(10, ge=.1, le=90)


PARAMS = {"price_ma_cross": Average, "ma_cross": Averages, "price_cross": Price,
          "daily_change": Change, "rsi_cross": RSI, "range_breakout": Range,
          "volume_spike": Volume, "macd_cross": MACD, "bollinger_cross": Bollinger,
          "drawdown_cross": Drawdown}
LABELS = dict(zip(PARAMS, ["종가와 이동평균 교차", "이동평균선 교차", "지정가격 돌파",
                         "일간 급등락", "RSI 교차", "기간 신고가·신저가", "거래량 급증",
                         "MACD 교차", "볼린저밴드 돌파·복귀", "고점 대비 낙폭 진입"]))

CORE_TYPES = tuple(PARAMS)


class Condition(Params):
    type: str
    params: dict

    @model_validator(mode="after")
    def normalize(self):
        if self.type not in CORE_TYPES:
            raise ValueError("기존 종가 조건만 결합할 수 있습니다.")
        self.params = normalize_rule(self.type, self.params)
        return self


class AllOf(Params):
    conditions: list[Condition] = Field(default_factory=lambda: [
        Condition(type="price_ma_cross",params={}),Condition(type="volume_spike",params={})], min_length=2,max_length=3)

    @model_validator(mode="after")
    def canonical(self):
        key = lambda c: json.dumps(c.model_dump(),sort_keys=True)
        if len({key(c) for c in self.conditions}) != len(self.conditions):
            raise ValueError("같은 하위 조건을 중복해서 넣을 수 없습니다.")
        self.conditions.sort(key=key)
        return self


PARAMS["all_of"] = AllOf
LABELS["all_of"] = "같은 날 모든 조건 충족 (AND)"


class Retest(Params):
    trigger: Condition = Field(default_factory=lambda: Condition(type="price_ma_cross",params={"direction":"up"}))
    windowSessions: int = Field(5,ge=1,le=30,strict=True)
    tolerancePct: float = Field(1,gt=0,le=10)

    @model_validator(mode="after")
    def price_source(self):
        if self.trigger.type not in ("price_cross","price_ma_cross","range_breakout"):
            raise ValueError("재접근 기준은 가격·이동평균·기간 고저가 돌파만 가능합니다.")
        return self


PARAMS["retest"] = Retest
LABELS["retest"] = "돌파 후 고정 가격 재접근"


class Relative(Direction):
    benchmark: str = Field('SPY',min_length=1,max_length=20,pattern=r'^(?:[A-Za-z][A-Za-z0-9.\-]*|[0-9]{6}\.[Kk][SsQq])$')
    period: int = Field(20,ge=2,le=252,strict=True)
    thresholdPct: float = Field(0,ge=-1000,le=1000)

    @model_validator(mode="after")
    def symbol(self):
        self.benchmark=self.benchmark.upper()
        if not self.benchmark.endswith(('.KS','.KQ')):self.benchmark=self.benchmark.replace('.','-')
        return self


PARAMS['relative_performance']=Relative
LABELS['relative_performance']='비교 종목 대비 수익률 차이'


class HoldingReturn(Direction):
    useSavedPortfolio: bool = Field(False,strict=True)
    thresholdPct: float = Field(10,ge=-100,le=10000)


class HoldingWeight(HoldingReturn):
    thresholdPct: float = Field(20,ge=0,le=100)


HOLDING_TYPES=('holding_return','holding_weight')
PARAMS.update(holding_return=HoldingReturn,holding_weight=HoldingWeight)
LABELS.update(holding_return='저장 평단 대비 수익률',holding_weight='저장 총자산 대비 보유 비중')


def contains_price_rule(kind, params):
    return kind in ("price_cross","retest") or (kind == "all_of" and any(c["type"] == "price_cross" for c in params["conditions"]))


def normalize_rule(kind, params):
    if kind not in PARAMS:
        raise ValueError("지원하지 않는 알림 유형입니다.")
    return PARAMS[kind].model_validate(params).model_dump()


def minimum_bars(kind, p):
    if kind=='relative_performance':return p['period']+2
    if kind == "retest":
        return minimum_bars(p["trigger"]["type"],p["trigger"]["params"])
    if kind == "all_of":
        return max(minimum_bars(c["type"],c["params"]) for c in p["conditions"])
    if kind == "ma_cross":
        return p["longPeriod"] + 1
    if kind == "macd_cross":
        return p["slowPeriod"] + (p["signalPeriod"] if p["crossMode"] == "signal" else 1)
    return p.get("period", 1) + (2 if kind == "rsi_cross" else 1)


def average(series, period, mode):
    return (series.rolling(period).mean() if mode == "sma" else
            series.ewm(span=period, adjust=False, min_periods=period).mean())


def evaluate_rule(kind, params, bars, *, state=None, session_age=None):
    """bars are one split-adjusted snapshot through the target completed session."""
    p = normalize_rule(kind, params)
    result = {"status": "insufficient_data", "triggers": [], "values": {}, "reason": "계산 자료 부족"}
    if kind in HOLDING_TYPES:return {**result,'reason':'계정에 저장한 보유자산 평가가 필요합니다.'}
    if len(bars) < minimum_bars(kind, p):
        return result
    if kind == "retest":
        source=p["trigger"]
        underlying=evaluate_rule(source["type"],source["params"],bars)
        before=state or {}
        if underlying["status"] not in ("signal","no_signal"):
            return {**underlying,"state":before,"stateBefore":before}
        result={"status":"no_signal","triggers":[],"values":{"close":float(bars[-1]["close"])},
                "state":before,"stateBefore":before,"reason":"새 돌파를 기다립니다."}
        dates=[b.get("date") for b in bars]
        if not all(dates) or dates!=sorted(set(dates)):
            return {**result,"status":"invalid_data","reason":"순서가 확인된 거래일이 필요합니다."}
        if before:
            if before["date"] not in dates:
                return {**result,"status":"insufficient_data","reason":"돌파일 자료가 필요합니다."}
            age=session_age if session_age is not None else len(dates)-1-dates.index(before["date"])
            level=before["level"]
            result["values"]["threshold"]=level
            if age>p["windowSessions"]:
                fresh=evaluate_rule(kind,p,bars,state={})
                return {**fresh,"stateBefore":before,"reason":fresh["reason"] if fresh["state"] else "재접근 대기 기간이 만료됐습니다."}
            distance=abs(float(bars[-1]["close"])/level-1)*100
            if age>0 and distance <= p["tolerancePct"]+1e-12:
                return {**result,"status":"signal","triggers":["retest"],"state":{},"reason":"돌파일 고정 가격의 허용폭에 처음 재접근했습니다."}
            return {**result,"reason":"돌파일 고정 가격으로 재접근을 기다립니다."}
        if underlying["triggers"]:
            direction=underlying["triggers"][0]
            level=(underlying["values"]["previousHigh" if direction=="up" else "previousLow"]
                   if source["type"]=="range_breakout" else underlying["values"]["threshold"])
            return {**result,"state":{"date":dates[-1],"level":level},"reason":"돌파를 확인했습니다. 다음 거래일부터 재접근을 점검합니다."}
        return result
    if kind == "all_of":
        children = [{**c,"result":evaluate_rule(c["type"],c["params"],bars)} for c in p["conditions"]]
        unavailable = next((c["result"] for c in children if c["result"]["status"] not in ("signal","no_signal")),None)
        if unavailable:
            return {**unavailable,"conditions":children}
        matched = all(c["result"]["status"] == "signal" for c in children)
        return {"status":"signal" if matched else "no_signal","triggers":["all"] if matched else [],
                "values":{"close":float(bars[-1]["close"])},"conditions":children,
                "reason":"같은 거래일의 모든 조건이 충족되었습니다." if matched else "같은 거래일의 모든 조건이 충족되지는 않았습니다."}
    frame = pd.DataFrame(bars)
    try:
        close = pd.to_numeric(frame["close"], errors="raise").astype(float)
        vol = pd.to_numeric(frame["volume"], errors="raise").astype(float)
        if not close.map(lambda x: isfinite(x) and x > 0).all() or not vol.map(lambda x: isfinite(x) and x >= 0).all():
            raise ValueError("invalid bars")
        a, b = close, pd.Series(0., index=close.index)
        triggers = []
        if kind == "price_ma_cross":
            b = average(close, p["period"], p["average"])
        elif kind == "ma_cross":
            a = average(close, p["shortPeriod"], p["average"])
            b = average(close, p["longPeriod"], p["average"])
        elif kind == "price_cross":
            a = pd.Series([x.get("rawClose", x["close"]) for x in bars], dtype=float)
            b[:] = p["price"]
        elif kind == "rsi_cross":
            a, b = compute_rsi(close, p["period"]), b + p["level"]
        elif kind == "relative_performance":
            if 'referenceClose' not in frame or frame['referenceClose'].isna().any():return result
            reference=pd.to_numeric(frame['referenceClose'],errors='raise').astype(float)
            if not reference.map(lambda x:isfinite(x) and x>0).all():raise ValueError('invalid reference')
            a=(close.pct_change(p['period'],fill_method=None)-reference.pct_change(p['period'],fill_method=None))*100
            b[:]=p['thresholdPct']
        elif kind == "macd_cross":
            a = average(close, p["fastPeriod"], "ema") - average(close, p["slowPeriod"], "ema")
            b = average(a, p["signalPeriod"], "ema") if p["crossMode"] == "signal" else b
        elif kind == "bollinger_cross":
            mid = close.rolling(p["period"]).mean()
            spread = close.rolling(p["period"]).std(ddof=0) * p["stddev"]
            b = mid + spread if p["event"].startswith("upper") else mid - spread
        elif kind == "daily_change":
            a = close.pct_change(fill_method=None) * 100
            if a.iloc[-1] >= p["thresholdPct"]:
                triggers.append("up")
            if a.iloc[-1] <= -p["thresholdPct"]:
                triggers.append("down")
        elif kind == "range_breakout":
            high = close.shift().rolling(p["period"]).max().iloc[-1]
            low = close.shift().rolling(p["period"]).min().iloc[-1]
            if close.iloc[-1] > high:
                triggers.append("up")
            if close.iloc[-1] < low:
                triggers.append("down")
            result["values"].update(previousHigh=float(high), previousLow=float(low))
        elif kind == "volume_spike":
            baseline = vol.shift().rolling(p["period"]).mean().iloc[-1]
            if baseline <= 0:
                return {**result, "status": "invalid_data", "reason": "평균 거래량이 0입니다."}
            a, b = vol, vol.shift().rolling(p["period"]).mean() * p["multiple"]
            if vol.iloc[-1] >= b.iloc[-1]:
                triggers.append("volume")
        elif kind == "drawdown_cross":
            peak = close.rolling(p["period"]).max()
            a = 100 * (peak - close) / peak
            b[:] = p["thresholdPct"]
        if kind not in ("daily_change", "range_breakout", "volume_spike"):
            prior_a, now_a, prior_b, now_b = map(float, (a.iloc[-2], a.iloc[-1], b.iloc[-2], b.iloc[-1]))
            if not all(map(isfinite, (prior_a, now_a, prior_b, now_b))):
                return result
            up = prior_a <= prior_b and now_a > now_b
            down = prior_a >= prior_b and now_a < now_b
            if kind == "drawdown_cross":
                triggers = ["drawdown"] if prior_a < prior_b and now_a >= now_b else []
            elif kind == "bollinger_cross":
                goes_up = p["event"] in ("upper_exit", "lower_reentry")
                triggers = [p["event"]] if (up if goes_up else down) else []
            else:
                triggers = (["up"] if up else []) + (["down"] if down else [])
            result["values"].update(previousValue=prior_a, previousThreshold=prior_b)
        if p.get("direction", "both") != "both":
            triggers = [x for x in triggers if x == p["direction"]]
        current = float(a.iloc[-1])
        threshold = float(b.iloc[-1])
        if not isfinite(current) or not isfinite(threshold):
            return result
        result["values"].update(value=current, threshold=threshold, close=float(close.iloc[-1]))
        return {**result, "status": "signal" if triggers else "no_signal", "triggers": triggers,
                "reason": LABELS[kind] + (" 새 신호" if triggers else " 새 신호 없음")}
    except (ValueError, TypeError, KeyError, OverflowError):
        return {**result, "status": "invalid_data", "reason": "유효하지 않은 가격·거래량입니다."}
