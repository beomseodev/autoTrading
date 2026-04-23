from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app, get_backtest_service
from app.services.backtest import BacktestService
from app.services.data_provider import DataProviderError, MarketData


class FakeProvider:
    def fetch_market_data(self, tickers: list[str], start_date: date, end_date: date) -> MarketData:
        index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        prices = pd.DataFrame(
            {
                "AAA": [100, 101, 102],
                "BBB": [100, 99, 98],
            },
            index=index,
            dtype=float,
        ).loc[:, tickers]
        dividends = pd.DataFrame(0.0, index=index, columns=tickers, dtype=float)
        return MarketData(prices=prices, dividends=dividends)


class FailingProvider:
    """수정: 2026-04-23 — 비교 API에서 DataProviderError 시 라벨 전달 검증용."""

    def fetch_market_data(self, tickers: list[str], start_date: date, end_date: date) -> MarketData:
        raise DataProviderError("simulated provider failure")


def _minimal_backtest_json() -> dict:
    return {
        "positions": [
            {"ticker": "AAA", "targetWeight": 50},
            {"ticker": "BBB", "targetWeight": 50},
        ],
        "initialCapital": 1000,
        "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
        "rebalance": {"mode": "calendar", "frequency": "monthly"},
        "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
    }


def test_compare_backtests_returns_expected_shape() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests/compare",
        json={
            "runs": [
                {"label": "All AAA", "request": {**_minimal_backtest_json(), "positions": [{"ticker": "AAA", "targetWeight": 100}]}},
                {"label": "50/50", "request": _minimal_backtest_json()},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"runs"}
    assert len(body["runs"]) == 2
    assert body["runs"][0]["label"] == "All AAA"
    assert body["runs"][1]["label"] == "50/50"
    for item in body["runs"]:
        assert set(item["result"].keys()) == {
            "summary",
            "equityCurve",
            "realEquityCurve",
            "holdingsSnapshot",
            "rebalanceEvents",
        }

    app.dependency_overrides.clear()


def test_compare_backtests_rejects_single_run() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests/compare",
        json={"runs": [{"label": "Only one", "request": _minimal_backtest_json()}]},
    )

    assert response.status_code == 422

    app.dependency_overrides.clear()


def test_compare_backtests_rejects_duplicate_labels() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests/compare",
        json={
            "runs": [
                {"label": "Same", "request": _minimal_backtest_json()},
                {"label": "Same", "request": _minimal_backtest_json()},
            ],
        },
    )

    assert response.status_code == 422

    app.dependency_overrides.clear()


def test_compare_backtests_provider_error_includes_label() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FailingProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests/compare",
        json={
            "runs": [
                {"label": "First scenario", "request": _minimal_backtest_json()},
                {"label": "Second scenario", "request": _minimal_backtest_json()},
            ],
        },
    )

    assert response.status_code == 400
    assert "[First scenario]" in response.json()["detail"]

    app.dependency_overrides.clear()


def test_backtest_api_returns_expected_shape() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests",
        json={
            "positions": [
                {"ticker": "AAA", "targetWeight": 50},
                {"ticker": "BBB", "targetWeight": 50},
            ],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"summary", "equityCurve", "realEquityCurve", "holdingsSnapshot", "rebalanceEvents"}
    assert body["summary"]["initialCapital"] == 1000
    assert body["summary"]["monthlyContribution"] == 0
    assert body["summary"]["totalContributed"] == 1000
    assert body["summary"]["totalExpensePaid"] == 0
    assert body["summary"]["inflationRatePct"] == 3
    assert "realFinalValue" in body["summary"]
    assert "realTotalReturnPct" in body["summary"]
    assert body["summary"]["xirrPct"] is not None
    assert len(body["equityCurve"]) == 3
    assert len(body["realEquityCurve"]) == 3
    assert len(body["holdingsSnapshot"]) == 2

    app.dependency_overrides.clear()


def test_backtest_api_accepts_single_rsi_trigger_ticker() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests",
        json={
            "positions": [
                {"ticker": "AAA", "targetWeight": 50},
                {"ticker": "BBB", "targetWeight": 50},
            ],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {
                "mode": "rsi",
                "rsiPeriod": 2,
                "lower": 30,
                "upper": 70,
                "rsiSignalScope": "single",
                "rsiTriggerTicker": "AAA",
            },
            "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"summary", "equityCurve", "realEquityCurve", "holdingsSnapshot", "rebalanceEvents"}

    app.dependency_overrides.clear()


def test_backtest_api_accepts_monthly_contribution_and_hides_cagr() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests",
        json={
            "positions": [
                {"ticker": "AAA", "targetWeight": 50},
                {"ticker": "BBB", "targetWeight": 50},
            ],
            "initialCapital": 1000,
            "monthlyContribution": 100,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {"fractionalShares": True, "feeRate": 0, "slippageRate": 0},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["monthlyContribution"] == 100
    assert body["summary"]["totalContributed"] == 1000
    assert body["summary"]["cagrPct"] is None
    assert body["summary"]["xirrPct"] is not None
    assert body["summary"]["realFinalValue"] <= body["summary"]["finalValue"]
    assert len(body["realEquityCurve"]) == len(body["equityCurve"])

    app.dependency_overrides.clear()


def test_backtest_api_accepts_dividend_reinvestment_flag() -> None:
    app.dependency_overrides[get_backtest_service] = lambda: BacktestService(FakeProvider())
    client = TestClient(app)

    response = client.post(
        "/api/backtests",
        json={
            "positions": [
                {"ticker": "AAA", "targetWeight": 50},
                {"ticker": "BBB", "targetWeight": 50},
            ],
            "initialCapital": 1000,
            "period": {"startDate": "2024-01-02", "endDate": "2024-01-04"},
            "rebalance": {"mode": "calendar", "frequency": "monthly"},
            "execution": {
                "fractionalShares": True,
                "dividendReinvestment": False,
                "feeRate": 0,
                "slippageRate": 0,
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"summary", "equityCurve", "realEquityCurve", "holdingsSnapshot", "rebalanceEvents"}

    app.dependency_overrides.clear()
