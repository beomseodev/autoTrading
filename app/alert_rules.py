"""Closing-bar alert contracts and deterministic, unrounded signal evaluation."""
from typing import Literal
from math import isfinite

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


def normalize_rule(kind, params):
    if kind not in PARAMS:
        raise ValueError("지원하지 않는 알림 유형입니다.")
    return PARAMS[kind].model_validate(params).model_dump()


def minimum_bars(kind, p):
    if kind == "ma_cross":
        return p["longPeriod"] + 1
    if kind == "macd_cross":
        return p["slowPeriod"] + (p["signalPeriod"] if p["crossMode"] == "signal" else 1)
    return p.get("period", 1) + (2 if kind == "rsi_cross" else 1)


def average(series, period, mode):
    return (series.rolling(period).mean() if mode == "sma" else
            series.ewm(span=period, adjust=False, min_periods=period).mean())


def evaluate_rule(kind, params, bars):
    """bars are one split-adjusted snapshot through the target completed session."""
    p = normalize_rule(kind, params)
    result = {"status": "insufficient_data", "triggers": [], "values": {}, "reason": "계산 자료 부족"}
    if len(bars) < minimum_bars(kind, p):
        return result
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
