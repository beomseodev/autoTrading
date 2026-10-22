from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from time import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yfinance as yf

from app.dashboard_schemas import PortfolioSnapshotRequest


MARKETS = [("^GSPC", "S&P 500", "pt"), ("^IXIC", "나스닥", "pt"), ("^VIX", "변동성 · VIX", "pt"), ("KRW=X", "달러 / 원", "KRW")]
MACRO = [
    ("DFF", "미국 실효 연방금리", "은행 간 하루 자금 거래의 실제 금리 · 일별"),
    ("DGS10", "미국 10년 국채금리", "장기 국채 수익률 · 일별"),
    ("CPIAUCSL", "미국 소비자물가", "계절조정 CPI의 전년 동월 대비 변화율 · 월별"),
    ("UNRATE", "미국 실업률", "경제활동인구 중 실업자 비율 · 월별"),
]


def finite_number(value, *, positive=False):
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("유효하지 않은 시세입니다.")
    return number


@lru_cache(maxsize=256)
def quote(symbol: str, bucket: int) -> dict:
    ticker = yf.Ticker(symbol)
    frame = ticker.history(period="5d", auto_adjust=False, raise_errors=True, timeout=12)
    close = frame["Close"].dropna()
    if len(close) < 2:
        raise ValueError("최근 두 거래일의 시세가 없습니다.")
    value = finite_number(close.iloc[-1], positive=True)
    previous = finite_number(close.iloc[-2], positive=True)
    currency = ticker.get_history_metadata().get("currency")
    return {
        "symbol": symbol, "value": value, "previous": previous,
        "changePct": (value / previous - 1) * 100,
        "asOf": close.index[-1].date().isoformat(),
        "previousAsOf": close.index[-2].date().isoformat(), "currency": currency,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }


def parse_fred_csv(text: str, series: str) -> list[tuple[date, float]]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_date = row.get("observation_date", row.get("DATE"))
        raw_value = row.get(series)
        if not raw_date or raw_value in (None, "", "."):
            continue
        rows.append((date.fromisoformat(raw_date), finite_number(raw_value)))
    if not rows:
        raise ValueError("공표된 관측값이 없습니다.")
    return sorted(rows)


@lru_cache(maxsize=32)
def macro_observation(series: str, bucket: int) -> dict:
    params = urlencode({"id": series, "cosd": (date.today() - timedelta(days=800)).isoformat()})
    request = Request("https://fred.stlouisfed.org/graph/fredgraph.csv?" + params,
                      headers={"User-Agent": "PortfolioDashboard/1.0"})
    with urlopen(request, timeout=12) as response:
        observations = parse_fred_csv(response.read(2_000_000).decode("utf-8-sig"), series)
    if series == "CPIAUCSL":
        by_month = {(d.year, d.month): value for d, value in observations}
        observations = [(d, (value / by_month[(d.year - 1, d.month)] - 1) * 100)
                        for d, value in observations
                        if (d.year - 1, d.month) in by_month and by_month[(d.year - 1, d.month)] > 0]
    if len(observations) < 2:
        raise ValueError("비교 가능한 관측값이 부족합니다.")
    last_date, value = observations[-1]
    return {"value": value, "asOf": last_date.isoformat(),
            "change": value - observations[-2][1], "unit": "%",
            "fetchedAt": datetime.now(timezone.utc).isoformat()}


class DashboardService:
    def get_quote(self, symbol: str) -> dict:
        return quote(symbol, int(time() // 300)).copy()

    def get_macro(self, series: str) -> dict:
        return macro_observation(series, int(time() // 1800)).copy()

    def market_overview(self) -> dict:
        def market_item(spec):
            symbol, label, unit = spec
            source = "https://finance.yahoo.com/quote/" + symbol.replace("^", "%5E") + "/"
            item = {"id": symbol, "label": label, "unit": unit, "source": source}
            try:
                return {**item, **self.get_quote(symbol), "available": True}
            except Exception:
                return {**item, "available": False, "error": "시세를 불러오지 못했습니다."}

        def macro_item(spec):
            series, label, description = spec
            item = {"id": series, "label": label, "description": description,
                    "source": "https://fred.stlouisfed.org/series/" + series}
            try:
                return {**item, **self.get_macro(series), "available": True}
            except Exception:
                return {**item, "available": False, "error": "공표 데이터를 불러오지 못했습니다."}

        with ThreadPoolExecutor(max_workers=8) as pool:
            markets = list(pool.map(market_item, MARKETS))
            macros = list(pool.map(macro_item, MACRO))
        return {"markets": markets, "macro": macros}

    def portfolio_snapshot(self, request: PortfolioSnapshotRequest) -> dict:
        fx = None
        try:
            fx = self.get_quote("KRW=X")
        except Exception:
            pass
        multiplier = 1 if request.baseCurrency == "USD" else fx["value"] if fx else None
        prev_multiplier = 1 if request.baseCurrency == "USD" else fx["previous"] if fx else None

        def holding(position):
            item = position.model_dump()
            try:
                price = self.get_quote(position.ticker)
                if price.get("currency") != "USD":
                    raise ValueError("미국 달러로 거래되는 종목만 지원합니다.")
                value = position.quantity * price["value"]
                cost = position.quantity * position.averageCost
                return {**item, "available": True, "price": price["value"],
                        "previousPrice": price["previous"], "asOf": price["asOf"],
                        "previousAsOf": price["previousAsOf"], "valueUSD": value,
                        "costUSD": cost, "profitUSD": value - cost,
                        "profitPct": (value / cost - 1) * 100 if cost else None}
            except Exception:
                return {**item, "available": False, "error": "USD 시세를 확인할 수 없습니다. 미국 상장 종목 코드를 확인해주세요."}

        with ThreadPoolExecutor(max_workers=6) as pool:
            holdings = list(pool.map(holding, request.positions))
        if request.baseCurrency == "KRW":
            cash = request.cashKRW + request.cashUSD * fx["value"] if fx else request.cashKRW if request.cashUSD == 0 else None
            previous_cash = request.cashKRW + request.cashUSD * fx["previous"] if fx else cash
        else:
            cash = request.cashUSD + request.cashKRW / fx["value"] if fx else request.cashUSD if request.cashKRW == 0 else None
            previous_cash = request.cashUSD + request.cashKRW / fx["previous"] if fx else cash
        complete = all(h["available"] for h in holdings) and cash is not None and (not holdings or multiplier is not None)
        total = profit = profit_pct = day_change = day_change_pct = None
        if complete:
            equity = sum(h["valueUSD"] for h in holdings) * (multiplier or 1)
            cost = sum(h["costUSD"] for h in holdings) * (multiplier or 1)
            total = equity + cash
            profit = equity - cost
            profit_pct = profit / cost * 100 if cost else None
            # Current quantities are held fixed; this is not a transaction-based daily return.
            same_previous_date = len({(h["asOf"], h["previousAsOf"]) for h in holdings}) <= 1
            if same_previous_date:
                previous_value = sum(h["quantity"] * h["previousPrice"] for h in holdings) * (prev_multiplier or 1) + previous_cash
                day_change = total - previous_value
                day_change_pct = day_change / previous_value * 100 if previous_value > 0 else None
        for item in holdings:
            item["value"] = item["valueUSD"] * multiplier if item["available"] and multiplier is not None else None
            item["weightPct"] = item["value"] / total * 100 if item["value"] is not None and total else None
        return {
            "baseCurrency": request.baseCurrency, "complete": complete, "holdings": holdings,
            "totalValue": total, "cashValue": cash, "unrealizedProfit": profit,
            "unrealizedProfitPct": profit_pct, "dayChange": day_change, "dayChangePct": day_change_pct,
            "fx": fx, "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "hasAssets": bool(holdings) or request.cashKRW > 0 or request.cashUSD > 0,
        }
