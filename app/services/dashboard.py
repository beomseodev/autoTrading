from __future__ import annotations

import csv
import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from time import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from curl_cffi import requests
import yfinance as yf

from app.dashboard_schemas import PortfolioSnapshotRequest
from app.services.rebalance_alerts import evaluate_rebalance, quote_date_problem
from app.services.symbol_search import stock_name


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
    frame = ticker.history(period="1mo", auto_adjust=False, raise_errors=True, timeout=12)
    close = frame["Close"].dropna().sort_index()
    if close.empty:
        raise ValueError("최근 두 거래일의 시세가 없습니다.")
    value = finite_number(close.iloc[-1], positive=True)
    as_of = close.index[-1].date()
    metadata = ticker.get_history_metadata()
    now = datetime.now(timezone.utc)
    # Yahoo can publish a regular-market quote before filling the latest daily bar.
    try:
        market_time = datetime.fromtimestamp(float(metadata["regularMarketTime"]), timezone.utc)
        market_date = market_time.astimezone(ZoneInfo(metadata["exchangeTimezoneName"])).date()
        market_value = finite_number(metadata["regularMarketPrice"], positive=True)
        if market_time <= now and market_date > as_of:
            value, as_of = market_value, market_date
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    prior = close.loc[[stamp.date() < as_of for stamp in close.index]]
    if prior.empty:
        raise ValueError("최근 두 거래일의 시세가 없습니다.")
    previous = finite_number(prior.iloc[-1], positive=True)
    problem = quote_date_problem(
        {"holdings": [] if symbol == "KRW=X" else [{"ticker": symbol, "asOf": as_of.isoformat()}],
         "fx": {"asOf": as_of.isoformat()}}, symbol == "KRW=X", now)
    if problem:
        raise ValueError(problem)
    return {
        "symbol": symbol, "value": value, "previous": previous,
        "name": stock_name(symbol, bucket) if not symbol.startswith("^") and symbol != "KRW=X" else symbol,
        "changePct": (value / previous - 1) * 100,
        "asOf": as_of.isoformat(),
        "previousAsOf": prior.index[-1].date().isoformat(), "currency": metadata.get("currency"),
        "fetchedAt": now.isoformat(),
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
    # Browser-compatible TLS avoids FRED read timeouts in the serverless runtime.
    response = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?" + params,
                            impersonate="chrome", timeout=12)
    response.raise_for_status()
    observations = parse_fred_csv(response.content.decode("utf-8-sig"), series)
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
        fetch = quote.__wrapped__ if getattr(self, "_refresh", False) else quote
        return fetch(symbol, getattr(self, "_quote_bucket", int(time() // 300))).copy()

    def get_macro(self, series: str) -> dict:
        return macro_observation(series, int(time() // 1800)).copy()

    def market_overview(self, *, refresh: bool = False) -> dict:
        provider = copy(self)
        provider._refresh = refresh
        def market_item(spec):
            symbol, label, unit = spec
            source = "https://finance.yahoo.com/quote/" + symbol.replace("^", "%5E") + "/"
            item = {"id": symbol, "label": label, "unit": unit, "source": source}
            try:
                return {**item, **provider.get_quote(symbol), "available": True}
            except Exception:
                logging.getLogger(__name__).exception("Market quote failed: %s", symbol)
                return {**item, "available": False, "error": "시세를 불러오지 못했습니다."}

        def macro_item(spec):
            series, label, description = spec
            item = {"id": series, "label": label, "description": description,
                    "source": "https://fred.stlouisfed.org/series/" + series}
            try:
                return {**item, **self.get_macro(series), "available": True}
            except Exception:
                logging.getLogger(__name__).exception("FRED observation failed: %s", series)
                return {**item, "available": False, "error": "공표 데이터를 불러오지 못했습니다."}

        with ThreadPoolExecutor(max_workers=8) as pool:
            markets = list(pool.map(market_item, MARKETS))
            macros = list(pool.map(macro_item, MACRO))
        return {"markets": markets, "macro": macros}

    def portfolio_snapshot(self, request: PortfolioSnapshotRequest, *, refresh: bool = False) -> dict:
        # Freeze the cache bucket per request without sharing mutable state across requests.
        provider = copy(self)
        provider._quote_bucket = int(time() // 300)
        provider._refresh = refresh
        fx = None
        try:
            fx = provider.get_quote("KRW=X")
        except Exception:
            logging.getLogger(__name__).exception("FX quote failed")
        def convert(value, currency, *, previous=False):
            if currency == request.baseCurrency:
                return value
            if fx is None:
                return None
            rate = fx["previous" if previous else "value"]
            return value * rate if currency == "USD" else value / rate

        def holding(position):
            item = position.model_dump()
            try:
                price = provider.get_quote(position.ticker)
                currency = "KRW" if position.ticker.endswith((".KS", ".KQ")) else "USD"
                if price.get("currency") != currency:
                    raise ValueError("종목의 거래 통화를 확인할 수 없습니다.")
                value = position.quantity * price["value"]
                cost = position.quantity * position.averageCost
                return {**item, "name": price.get("name") or position.ticker, "currency": currency, "available": True, "price": price["value"],
                        "previousPrice": price["previous"], "asOf": price["asOf"],
                        "previousAsOf": price["previousAsOf"],
                        "fetchedAt": price.get("fetchedAt"),
                        "valueUSD": value if currency == "USD" else value / fx["value"] if fx else None,
                        "costUSD": cost if currency == "USD" else cost / fx["value"] if fx else None,
                        "profitUSD": (value - cost) if currency == "USD" else (value - cost) / fx["value"] if fx else None,
                        "value": convert(value, currency), "cost": convert(cost, currency),
                        "previousValue": convert(position.quantity * price["previous"], currency, previous=True),
                        "profitPct": (value / cost - 1) * 100 if cost else None}
            except Exception:
                logging.getLogger(__name__).exception("Holding quote failed: %s", position.ticker)
                return {**item, "currency": "KRW" if position.ticker.endswith((".KS", ".KQ")) else "USD",
                        "available": False, "value": None,
                        "error": "최신 시세를 확인할 수 없습니다. 잠시 후 다시 조회해주세요."}

        with ThreadPoolExecutor(max_workers=6) as pool:
            holdings = list(pool.map(holding, request.positions))
        if request.baseCurrency == "KRW":
            cash = request.cashKRW + request.cashUSD * fx["value"] if fx else request.cashKRW if request.cashUSD == 0 else None
            previous_cash = request.cashKRW + request.cashUSD * fx["previous"] if fx else cash
        else:
            cash = request.cashUSD + request.cashKRW / fx["value"] if fx else request.cashUSD if request.cashKRW == 0 else None
            previous_cash = request.cashUSD + request.cashKRW / fx["previous"] if fx else cash
        complete = all(h["available"] for h in holdings) and cash is not None and all(h["value"] is not None for h in holdings)
        total = profit = profit_pct = day_change = day_change_pct = None
        if complete:
            equity = sum(h["value"] for h in holdings)
            cost = sum(h["cost"] for h in holdings)
            total = equity + cash
            profit = equity - cost
            profit_pct = profit / cost * 100 if cost else None
            # Current quantities are held fixed; this is not a transaction-based daily return.
            periods = {(h["asOf"], h["previousAsOf"]) for h in holdings}
            foreign_cash = request.cashUSD if request.baseCurrency == "KRW" else request.cashKRW
            if foreign_cash or any(h["currency"] != request.baseCurrency for h in holdings):
                periods.add((fx["asOf"], fx["previousAsOf"]))
            if len(periods) <= 1:
                previous_value = sum(h["previousValue"] for h in holdings) + previous_cash
                day_change = total - previous_value
                day_change_pct = day_change / previous_value * 100 if previous_value > 0 else None
        for item in holdings:
            item["weightPct"] = item["value"] / total * 100 if item["value"] is not None and total else None
        snapshot = {
            "baseCurrency": request.baseCurrency, "complete": complete, "holdings": holdings,
            "totalValue": total, "cashValue": cash, "unrealizedProfit": profit,
            "unrealizedProfitPct": profit_pct, "dayChange": day_change, "dayChangePct": day_change_pct,
            "fx": fx, "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "hasAssets": bool(holdings) or request.cashKRW > 0 or request.cashUSD > 0,
        }
        snapshot["rebalance"] = evaluate_rebalance(request, snapshot)
        return snapshot
