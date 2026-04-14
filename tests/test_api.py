from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app, get_backtest_service
from app.schemas import BacktestRequest
from app.services.backtest import BacktestService


class FakeProvider:
    def fetch_adjusted_close(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        return pd.DataFrame(
            {
                "AAA": [100, 101, 102],
                "BBB": [100, 99, 98],
            },
            index=index,
            dtype=float,
        ).loc[:, tickers]


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
    assert set(body.keys()) == {"summary", "equityCurve", "holdingsSnapshot", "rebalanceEvents"}
    assert body["summary"]["initialCapital"] == 1000
    assert len(body["equityCurve"]) == 3
    assert len(body["holdingsSnapshot"]) == 2

    app.dependency_overrides.clear()

